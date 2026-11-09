"""Product/service catalog helpers: templates, import parsing, and the
structured text/metadata builders used for vector training."""

import csv
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings

PRODUCT_TEMPLATE_COLUMNS = [
    "name",
    "sku",
    "category",
    "description",
    "price",
    "currency",
    "product_url",
    "image_url",
]

PRODUCT_TEMPLATE_SAMPLE_ROWS = [
    {
        "name": "Wireless Mouse",
        "sku": "WM-001",
        "category": "Accessories",
        "description": "Ergonomic wireless mouse with USB-C charging.",
        "price": "29.99",
        "currency": "USD",
        "product_url": "https://example.com/products/wm-001",
        "image_url": "https://example.com/images/wm-001.jpg",
    },
    {
        "name": "Mechanical Keyboard",
        "sku": "MK-002",
        "category": "Accessories",
        "description": "Hot-swappable 75% mechanical keyboard.",
        "price": "89.00",
        "currency": "USD",
        "product_url": "https://example.com/products/mk-002",
        "image_url": "https://example.com/images/mk-002.jpg",
    },
]

SERVICE_TEMPLATE_COLUMNS = [
    "name",
    "service_type",
    "description",
    "cost",
    "currency",
]

SERVICE_TEMPLATE_SAMPLE_ROWS = [
    {
        "name": "Laptop Repair",
        "service_type": "repair",
        "description": "Screen and battery replacement.",
        "cost": "75.00",
        "currency": "USD",
    },
    {
        "name": "IT Consulting",
        "service_type": "consulting",
        "description": "One hour of on-site technical consulting.",
        "cost": "120.00",
        "currency": "USD",
    },
]

MAX_IMPORT_ROWS = 5000
VALID_CURRENCIES = {"USD", "EUR", "GBP", "BDT", "INR", "AUD", "CAD", "JPY"}


class CatalogImportError(ValueError):
    pass


# Historical name kept for callers that predate the shared catalog import.
ProductImportError = CatalogImportError


def build_catalog_template_csv(columns, sample_rows):
    """The downloadable CSV template users fill and re-upload."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in sample_rows:
        writer.writerow(row)
    return buffer.getvalue()


def build_product_template_csv():
    return build_catalog_template_csv(
        PRODUCT_TEMPLATE_COLUMNS,
        PRODUCT_TEMPLATE_SAMPLE_ROWS,
    )


def build_service_template_csv():
    return build_catalog_template_csv(
        SERVICE_TEMPLATE_COLUMNS,
        SERVICE_TEMPLATE_SAMPLE_ROWS,
    )


def build_product_text(product):
    """Structured text embedded into the product vector space.

    URLs (product_url, image_url) are deliberately excluded: they do not
    help semantic retrieval and are returned to the agent via metadata.
    """
    parts = [f"Product: {product.name}"]
    if product.sku:
        parts.append(f"SKU: {product.sku}")
    if product.category:
        parts.append(f"Category: {product.category}")
    if product.price is not None:
        parts.append(f"Price: {product.price} {product.currency}")
    if product.description:
        parts.append(f"Description: {product.description}")
    return "\n".join(parts)


def build_product_metadata(product):
    return {
        "product_id": str(product.id),
        "chatbot_id": str(product.chatbot_id),
        "name": product.name,
        "sku": product.sku,
        "category": product.category,
        "price": str(product.price) if product.price is not None else None,
        "currency": product.currency,
        "product_url": product.product_url,
        "image_url": product.image_url,
    }


def build_service_text(service):
    """Structured text embedded into the service vector space."""
    parts = [f"Service: {service.name}"]
    if service.service_type:
        parts.append(f"Type: {service.service_type}")
    if service.cost is not None:
        parts.append(f"Cost: {service.cost} {service.currency}")
    if service.description:
        parts.append(f"Description: {service.description}")
    return "\n".join(parts)


def build_service_metadata(service):
    return {
        "service_id": str(service.id),
        "chatbot_id": str(service.chatbot_id),
        "name": service.name,
        "service_type": service.service_type,
        "cost": str(service.cost) if service.cost is not None else None,
        "currency": service.currency,
    }


def _clean_row(row):
    return {
        (key or "").strip().lower(): (value if value is not None else "")
        for key, value in row.items()
    }


def _reject_unknown_columns(cleaned, columns, row_number):
    unknown = set(cleaned) - set(columns) - {""}
    if unknown:
        raise CatalogImportError(
            f"Row {row_number}: unknown column(s): {', '.join(sorted(unknown))}. "
            "Download the template for the expected format.",
        )


def _parse_amount(raw, *, field, row_number):
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise CatalogImportError(
            f"Row {row_number}: invalid {field} '{raw}'.",
        ) from exc
    if amount < 0:
        raise CatalogImportError(
            f"Row {row_number}: {field} cannot be negative.",
        )
    return amount


def _parse_currency(raw, row_number):
    currency = str(raw).strip().upper() or "USD"
    if len(currency) != 3 or not currency.isalpha():
        raise CatalogImportError(
            f"Row {row_number}: currency must be a 3-letter code (e.g. USD).",
        )
    return currency


def _normalize_product_row(row, row_number):
    cleaned = _clean_row(row)
    _reject_unknown_columns(cleaned, PRODUCT_TEMPLATE_COLUMNS, row_number)

    name = cleaned.get("name", "").strip()
    if not name:
        raise CatalogImportError(f"Row {row_number}: 'name' is required.")

    price_raw = str(cleaned.get("price", "")).strip()
    price = None
    if price_raw:
        price = _parse_amount(price_raw, field="price", row_number=row_number)

    return {
        "name": name[:255],
        "sku": str(cleaned.get("sku", "")).strip()[:120],
        "category": str(cleaned.get("category", "")).strip()[:120],
        "description": str(cleaned.get("description", "")).strip(),
        "price": price,
        "currency": _parse_currency(cleaned.get("currency", ""), row_number),
        "product_url": str(cleaned.get("product_url", "")).strip(),
        "image_url": str(cleaned.get("image_url", "")).strip(),
    }


def _normalize_service_row(row, row_number):
    cleaned = _clean_row(row)
    _reject_unknown_columns(cleaned, SERVICE_TEMPLATE_COLUMNS, row_number)

    name = cleaned.get("name", "").strip()
    if not name:
        raise CatalogImportError(f"Row {row_number}: 'name' is required.")

    cost_raw = str(cleaned.get("cost", "")).strip()
    cost = None
    if cost_raw:
        cost = _parse_amount(cost_raw, field="cost", row_number=row_number)

    return {
        "name": name[:255],
        "service_type": str(cleaned.get("service_type", "")).strip()[:120],
        "description": str(cleaned.get("description", "")).strip(),
        "cost": cost,
        "currency": _parse_currency(cleaned.get("currency", ""), row_number),
    }


def _rows_from_csv(file_obj):
    text = file_obj.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _rows_from_xlsx(file_obj):
    from openpyxl import load_workbook

    workbook = load_workbook(file_obj, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            return []
        keys = [str(cell).strip() if cell is not None else "" for cell in header]
        return [
            dict(zip(keys, row))
            for row in rows
            if any(cell is not None and str(cell).strip() for cell in row)
        ]
    finally:
        workbook.close()


def _parse_catalog_upload(file_obj, normalize_row):
    suffix = Path(file_obj.name or "").suffix.lower()
    if suffix == ".xlsx":
        rows = _rows_from_xlsx(file_obj)
    elif suffix == ".csv":
        rows = _rows_from_csv(file_obj)
    else:
        raise CatalogImportError(
            "Unsupported file type. Upload the template as CSV or XLSX.",
        )

    if len(rows) > MAX_IMPORT_ROWS:
        raise CatalogImportError(
            f"Import cannot exceed {MAX_IMPORT_ROWS} rows.",
        )
    if not rows:
        raise CatalogImportError("The uploaded file contains no data rows.")
    if settings.KNOWLEDGE_MAX_CSV_ROWS and len(rows) > settings.KNOWLEDGE_MAX_CSV_ROWS:
        raise CatalogImportError(
            f"Import cannot exceed {settings.KNOWLEDGE_MAX_CSV_ROWS} rows.",
        )

    items_data = []
    errors = []
    for row_number, row in enumerate(rows, start=2):
        try:
            items_data.append(normalize_row(row, row_number))
        except CatalogImportError as exc:
            errors.append(str(exc))
    return items_data, errors


def parse_product_upload(file_obj):
    """Parse an uploaded CSV/XLSX product sheet.

    Returns ``(products_data, errors)`` where valid rows are dictionaries
    ready for ``Product.objects.create`` and errors are per-row messages.
    """
    return _parse_catalog_upload(file_obj, _normalize_product_row)


def parse_service_upload(file_obj):
    """Parse an uploaded CSV/XLSX service sheet.

    Returns ``(services_data, errors)`` where valid rows are dictionaries
    ready for ``Service.objects.create`` and errors are per-row messages.
    """
    return _parse_catalog_upload(file_obj, _normalize_service_row)
