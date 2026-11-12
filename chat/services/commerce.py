"""Cart and order taking for a chat session.

The agent's cart tools call these services; every change is stored in
``ChatCartItem`` / ``ChatOrder`` rows linked to the session, so a session
can be queried for what was added to the cart and what was purchased.
A product can also be ordered directly, without the cart; if it was in
the cart, that cart line becomes the order line and leaves the cart.
Payment is out of scope: an order records what the user confirmed.
"""

from decimal import Decimal
from uuid import uuid4

from django.db import transaction

from chat.models import ChatCartItem, ChatEscalation, ChatOrder
from chat.utils.choices import CartItemStatus

MAX_CART_QUANTITY = 99


class CartError(ValueError):
    """A cart or order request the agent should explain to the user."""


def active_cart_items(session):
    return ChatCartItem.objects.filter(
        chat_session=session,
        status=CartItemStatus.IN_CART,
    ).order_by("created_at", "id")


def _cart_totals(items):
    """(total quantity, total Decimal) of in-cart items."""
    return (
        sum(item.quantity for item in items),
        sum((item.line_total for item in items), start=Decimal("0")),
    )


def cart_summary(items):
    """Totals of a list of in-cart items (one currency per cart)."""
    items = list(items)
    item_count, total = _cart_totals(items)
    return {
        "item_count": item_count,
        "total": f"{total:.2f}",
        "currency": items[0].currency if items else None,
    }


def _clean_quantity(quantity):
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise CartError("Quantity must be a whole number.") from exc
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")
    if quantity > MAX_CART_QUANTITY:
        raise CartError(
            f"At most {MAX_CART_QUANTITY} of one product can be ordered."
        )
    return quantity


def _require_price(product):
    if product.price is None:
        raise CartError(
            f"{product.name} has no price, so it cannot be ordered in chat."
        )


def _product_snapshot(product):
    """Catalog details copied onto a cart or order line."""
    return {
        "product_name": product.name,
        "sku": product.sku,
        "product_url": product.product_url,
        "image_url": product.image_url,
        "unit_price": product.price,
        "currency": product.currency,
    }


@transaction.atomic
def add_to_cart(*, session, product, quantity=1):
    """Add ``quantity`` of ``product``; merges into an existing cart line."""
    quantity = _clean_quantity(quantity)
    _require_price(product)

    cart = list(active_cart_items(session).select_for_update())
    other_currency = next(
        (item.currency for item in cart if item.currency != product.currency),
        None,
    )
    if other_currency:
        raise CartError(
            f"The cart is in {other_currency}; {product.name} is priced in "
            f"{product.currency}. Place or clear the current order first."
        )

    item = next((item for item in cart if item.product_id == product.pk), None)
    new_quantity = quantity + (item.quantity if item else 0)
    if new_quantity > MAX_CART_QUANTITY:
        raise CartError(
            f"At most {MAX_CART_QUANTITY} of one product can be ordered."
        )

    if item is not None:
        item.quantity = new_quantity
        item.save(update_fields=["quantity", "updated_at"])
        return item

    return ChatCartItem.objects.create(
        chat_session=session,
        product=product,
        quantity=quantity,
        created_by=session.created_by,
        **_product_snapshot(product),
    )


@transaction.atomic
def remove_from_cart(*, session, product_id):
    """Remove a product's line from the cart; None when it is not there."""
    item = (
        active_cart_items(session)
        .select_for_update()
        .filter(product_id=product_id)
        .first()
    )
    if item is None:
        return None
    item.status = CartItemStatus.REMOVED
    item.save(update_fields=["status", "updated_at"])
    return item


def _order_number():
    return f"VB-{uuid4().hex[:10].upper()}"


@transaction.atomic
def place_order(*, session):
    """Turn every in-cart item into one confirmed order."""
    items = list(active_cart_items(session).select_for_update())
    if not items:
        raise CartError("The cart is empty.")

    item_count, total = _cart_totals(items)
    order = ChatOrder.objects.create(
        chat_session=session,
        order_number=_order_number(),
        item_count=item_count,
        total_amount=total,
        currency=items[0].currency,
        created_by=session.created_by,
    )
    ChatCartItem.objects.filter(pk__in=[item.pk for item in items]).update(
        status=CartItemStatus.ORDERED,
        order=order,
    )
    return order


@transaction.atomic
def order_product(*, session, product, quantity=1):
    """Order ``quantity`` of one product directly, without the cart.

    Uses the current catalog price. If the product is in the cart, its
    cart line becomes the order line (status ``ordered``), so it leaves
    the cart; other cart items stay. Returns ``(order, was_in_cart)``.
    """
    quantity = _clean_quantity(quantity)
    _require_price(product)

    line = (
        active_cart_items(session)
        .select_for_update()
        .filter(product_id=product.pk)
        .first()
    )
    order = ChatOrder.objects.create(
        chat_session=session,
        order_number=_order_number(),
        item_count=quantity,
        total_amount=product.price * quantity,
        currency=product.currency,
        created_by=session.created_by,
    )
    values = {
        **_product_snapshot(product),
        "quantity": quantity,
        "status": CartItemStatus.ORDERED,
        "order": order,
    }
    if line is None:
        ChatCartItem.objects.create(
            chat_session=session,
            product=product,
            added_to_cart=False,
            created_by=session.created_by,
            **values,
        )
    else:
        for field, value in values.items():
            setattr(line, field, value)
        line.save(update_fields=[*values, "updated_at"])
    return order, line is not None


def session_activity_counts(session_id):
    """The per-session numbers shown on session payloads and events."""
    cart_items = ChatCartItem.objects.filter(chat_session_id=session_id)
    return {
        # Lines that went through the cart; direct orders are not counted.
        "cart_item_count": cart_items.filter(added_to_cart=True).count(),
        "purchased_item_count": cart_items.filter(
            status=CartItemStatus.ORDERED,
        ).count(),
        "order_count": ChatOrder.objects.filter(
            chat_session_id=session_id,
        ).count(),
        "escalation_count": ChatEscalation.objects.filter(
            chat_session_id=session_id,
        ).count(),
    }
