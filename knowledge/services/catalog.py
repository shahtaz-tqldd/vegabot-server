"""Product/service catalog helpers: template, import parsing, and the
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
    },
    {
        "name": "Mechanical Keyboard",
        "sku": "MK-002",
        "category": "Accessories",
        "description": "Hot-swappable 75% mechanical keyboard.",
        "price": "89.00",
        "currency": "USD",
        "product_url": "https://example.com/products/mk-002",
    },
]

MAX_IMPORT_ROWS = 5000
VALID_CURRENCIES = {"USD", "EUR", "GBP", "BDT", "INR", "AUD", "CAD", "JPY"}


class ProductImportError(ValueError):
    pass


def build_product_template_csv():
    """The downloadable CSV template users fill and re-upload."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PRODUCT_TEMPLATE_COLUMNS)
    writer.writeheader()
    for row in PRODUCT_TEMPLATE_SAMPLE_ROWS:
        writer.writerow(row)
    return buffer.getvalue()


def build_product_text(product):
    """Structured text embedded into the product vector space."""
    parts = [f"Product: {product.name}"]
    if product.sku:
        parts.append(f"SKU: {product.sku}")
    if product.category:
        parts.append(f"Category: {product.category}")
    if product.price is not None:
        parts.append(f"Price: {product.price} {product.currency}")
    if product.product_url:
        parts.append(f"URL: {product.product_url}")
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


def _normalize_row(row, row_number):
    cleaned = {
        (key or "").strip().lower(): (value if value is not None else "")
        for key, value in row.items()
    }
    unknown = set(cleaned) - set(PRODUCT_TEMPLATE_COLUMNS) - {""}
    if unknown:
        raise ProductImportError(
            f"Row {row_number}: unknown column(s): {', '.join(sorted(unknown))}. "
            "Download the template for the expected format.",
        )

    name = cleaned.get("name", "").strip()
    if not name:
        raise ProductImportError(f"Row {row_number}: 'name' is required.")

    price_raw = str(cleaned.get("price", "")).strip()
    price = None
    if price_raw:
        try:
            price = Decimal(price_raw)
        except InvalidOperation as exc:
            raise ProductImportError(
                f"Row {row_number}: invalid price '{price_raw}'.",
            ) from exc
        if price < 0:
            raise ProductImportError(f"Row {row_number}: price cannot be negative.")

    currency = str(cleaned.get("currency", "")).strip().upper() or "USD"
    if len(currency) != 3 or not currency.isalpha():
        raise ProductImportError(
            f"Row {row_number}: currency must be a 3-letter code (e.g. USD).",
        )

    return {
        "name": name[:255],
        "sku": str(cleaned.get("sku", "")).strip()[:120],
        "category": str(cleaned.get("category", "")).strip()[:120],
        "description": str(cleaned.get("description", "")).strip(),
        "price": price,
        "currency": currency,
        "product_url": str(cleaned.get("product_url", "")).strip(),
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


def parse_product_upload(file_obj):
    """Parse an uploaded CSV/XLSX product sheet.

    Returns ``(products_data, errors)`` where valid rows are dictionaries
    ready for ``Product.objects.create`` and errors are per-row messages.
    """
    suffix = Path(file_obj.name or "").suffix.lower()
    if suffix == ".xlsx":
        rows = _rows_from_xlsx(file_obj)
    elif suffix == ".csv":
        rows = _rows_from_csv(file_obj)
    else:
        raise ProductImportError(
            "Unsupported file type. Upload the template as CSV or XLSX.",
        )

    if len(rows) > MAX_IMPORT_ROWS:
        raise ProductImportError(
            f"Import cannot exceed {MAX_IMPORT_ROWS} rows.",
        )
    if not rows:
        raise ProductImportError("The uploaded file contains no data rows.")
    if settings.KNOWLEDGE_MAX_CSV_ROWS and len(rows) > settings.KNOWLEDGE_MAX_CSV_ROWS:
        raise ProductImportError(
            f"Import cannot exceed {settings.KNOWLEDGE_MAX_CSV_ROWS} rows.",
        )

    products_data = []
    errors = []
    for row_number, row in enumerate(rows, start=2):
        try:
            products_data.append(_normalize_row(row, row_number))
        except ProductImportError as exc:
            errors.append(str(exc))
    return products_data, errors
