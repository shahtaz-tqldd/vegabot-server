"""Socket events for session activity: cart changes, orders, escalations.

Every event has the agent stream's shape, ``{"type": ..., "data": {...}}``,
and ``data`` always carries ``session_id`` and the session's updated
``counts`` so the frontend can show a notification and refresh its
counters from one event:

    chat.cart.item_added     data.item, data.cart
    chat.cart.item_removed   data.item, data.cart
    chat.order.placed        data.order, data.cart
    chat.escalation.created  data.escalation

Builders read the database (call them from sync code); publishing is the
caller's job, see ``base.socket.services.broadcaster``.
"""

from base.socket.events import (
    CART_ITEM_ADDED,
    CART_ITEM_REMOVED,
    ESCALATION_CREATED,
    ORDER_PLACED,
)
from chat.api.v1.client.serializers import (
    ChatCartItemSerializer,
    ChatEscalationListSerializer,
    ChatOrderSerializer,
)
from chat.services.commerce import (
    active_cart_items,
    cart_summary,
    session_activity_counts,
)


def _event(event_type, session_id, **data):
    return {
        "type": event_type,
        "data": {
            "session_id": str(session_id),
            **data,
            "counts": session_activity_counts(session_id),
        },
    }


def cart_state(session):
    """The current cart: its items plus totals."""
    items = list(active_cart_items(session))
    return {
        "items": ChatCartItemSerializer(items, many=True).data,
        **cart_summary(items),
    }


def cart_item_event(session, item, *, removed=False):
    return _event(
        CART_ITEM_REMOVED if removed else CART_ITEM_ADDED,
        session.pk,
        item=ChatCartItemSerializer(item).data,
        cart=cart_state(session),
    )


def order_placed_event(session, order):
    order = type(order).objects.prefetch_related("items").get(pk=order.pk)
    return _event(
        ORDER_PLACED,
        session.pk,
        order=ChatOrderSerializer(order).data,
        # Ordered products leave the cart; this is what remains.
        cart=cart_state(session),
    )


def escalation_created_event(session, escalation):
    return _event(
        ESCALATION_CREATED,
        session.pk,
        escalation=ChatEscalationListSerializer(escalation).data,
    )
