from decimal import Decimal

from django.urls import reverse
from rest_framework import status

from chat.models import ChatCartItem
from chat.services import record_escalation
from chat.services.commerce import (
    CartError,
    add_to_cart,
    active_cart_items,
    order_product,
    place_order,
    remove_from_cart,
    session_activity_counts,
)
from chat.utils.choices import CartItemStatus
from knowledge.models import Product

from .test_client_api import (
    SESSION_DETAILS_URL,
    SESSION_LIST_URL,
    ChatClientAPITestCase,
)

CART_ITEMS_URL = reverse("chat-session-cart-items")
ORDERS_URL = reverse("chat-session-orders")
ESCALATIONS_URL = reverse("chat-session-escalations")


class CommerceTestCase(ChatClientAPITestCase):
    def setUp(self):
        super().setUp()
        self.session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.widget = self.create_product("Widget", "9.99")
        self.gadget = self.create_product("Gadget", "20.00")

    def create_product(self, name, price, currency="USD"):
        return Product.objects.create(
            chatbot=self.own_chatbot,
            name=name,
            price=Decimal(price) if price is not None else None,
            currency=currency,
            created_by=self.user,
        )

    def fill_session(self):
        """Widget x2 + Gadget ordered, a removed line, an open cart line,
        and one escalation."""
        add_to_cart(session=self.session, product=self.widget, quantity=2)
        add_to_cart(session=self.session, product=self.gadget)
        order = place_order(session=self.session)
        removed = self.create_product("Removed", "1.00")
        add_to_cart(session=self.session, product=removed)
        remove_from_cart(session=self.session, product_id=removed.pk)
        add_to_cart(session=self.session, product=self.widget)
        record_escalation(session=self.session, question="Do you ship abroad?")
        return order


class CartServiceTests(CommerceTestCase):
    def test_add_merges_quantity_per_product(self):
        add_to_cart(session=self.session, product=self.widget)
        add_to_cart(session=self.session, product=self.widget, quantity=2)

        item = ChatCartItem.objects.get(chat_session=self.session)
        self.assertEqual(item.quantity, 3)

    def test_rejects_unpriced_products_and_mixed_currencies(self):
        with self.assertRaises(CartError):
            add_to_cart(
                session=self.session,
                product=self.create_product("Free", None),
            )

        add_to_cart(session=self.session, product=self.widget)
        with self.assertRaises(CartError):
            add_to_cart(
                session=self.session,
                product=self.create_product("Euro", "5.00", currency="EUR"),
            )

    def test_rejects_invalid_quantities(self):
        for quantity in (0, -1, "two", 100):
            with self.assertRaises(CartError):
                add_to_cart(
                    session=self.session, product=self.widget, quantity=quantity,
                )

    def test_place_order_totals_the_cart(self):
        order = self.fill_session()

        self.assertEqual(order.total_amount, Decimal("39.98"))
        self.assertEqual(order.item_count, 3)
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(
            set(order.items.values_list("status", flat=True)),
            {CartItemStatus.ORDERED},
        )

    def test_order_product_not_in_cart(self):
        add_to_cart(session=self.session, product=self.gadget)

        order, was_in_cart = order_product(
            session=self.session, product=self.widget, quantity=2,
        )

        self.assertFalse(was_in_cart)
        self.assertEqual(order.total_amount, Decimal("19.98"))
        self.assertEqual(order.item_count, 2)
        line = order.items.get()
        self.assertEqual(line.product, self.widget)
        self.assertEqual(line.status, CartItemStatus.ORDERED)
        self.assertFalse(line.added_to_cart)
        # The cart is untouched.
        self.assertEqual(
            [item.product for item in active_cart_items(self.session)],
            [self.gadget],
        )
        counts = session_activity_counts(self.session.pk)
        self.assertEqual(counts["cart_item_count"], 1)
        self.assertEqual(counts["purchased_item_count"], 1)

    def test_order_product_in_cart_removes_it_from_cart(self):
        cart_line = add_to_cart(session=self.session, product=self.widget, quantity=3)
        add_to_cart(session=self.session, product=self.gadget)
        self.widget.price = Decimal("8.00")
        self.widget.save()

        order, was_in_cart = order_product(
            session=self.session, product=self.widget, quantity=1,
        )

        self.assertTrue(was_in_cart)
        cart_line.refresh_from_db()
        # The cart line itself became the order line, at today's price.
        self.assertEqual(cart_line.order, order)
        self.assertEqual(cart_line.status, CartItemStatus.ORDERED)
        self.assertEqual(cart_line.quantity, 1)
        self.assertEqual(cart_line.unit_price, Decimal("8.00"))
        self.assertTrue(cart_line.added_to_cart)
        self.assertEqual(order.total_amount, Decimal("8.00"))
        self.assertEqual(
            [item.product for item in active_cart_items(self.session)],
            [self.gadget],
        )
        # Ordering the rest of the cart no longer includes the widget.
        rest = place_order(session=self.session)
        self.assertEqual(rest.total_amount, Decimal("20.00"))

    def test_order_product_rejects_invalid_requests(self):
        for quantity in (0, "two", 100):
            with self.assertRaises(CartError):
                order_product(
                    session=self.session, product=self.widget, quantity=quantity,
                )
        with self.assertRaises(CartError):
            order_product(
                session=self.session, product=self.create_product("Free", None),
            )
        self.assertFalse(self.session.orders.exists())

    def test_session_activity_counts(self):
        self.fill_session()

        self.assertEqual(
            session_activity_counts(self.session.pk),
            {
                "cart_item_count": 4,
                "purchased_item_count": 2,
                "order_count": 1,
                "escalation_count": 1,
            },
        )


class SessionActivityAPITests(CommerceTestCase):
    def test_cart_items_list(self):
        self.fill_session()

        response = self.client.get(CART_ITEMS_URL, {"session": self.session.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 4)
        first = response.data["data"][0]
        self.assertEqual(first["product_name"], "Widget")
        self.assertEqual(first["line_total"], "19.98")
        self.assertEqual(first["status"], CartItemStatus.ORDERED)

    def test_cart_items_status_filter(self):
        self.fill_session()

        in_cart = self.client.get(
            CART_ITEMS_URL, {"session": self.session.pk, "status": "in_cart"},
        )
        invalid = self.client.get(
            CART_ITEMS_URL, {"session": self.session.pk, "status": "nope"},
        )

        self.assertEqual(in_cart.data["meta"]["count"], 1)
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_orders_list_includes_items(self):
        order = self.fill_session()

        response = self.client.get(ORDERS_URL, {"session": self.session.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["order_number"], order.order_number)
        self.assertEqual(data[0]["total_amount"], "39.98")
        self.assertEqual(
            {item["product_name"] for item in data[0]["items"]},
            {"Widget", "Gadget"},
        )

    def test_escalations_list_is_session_scoped(self):
        self.fill_session()
        other_session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        record_escalation(session=other_session, question="Elsewhere?")

        response = self.client.get(ESCALATIONS_URL, {"session": self.session.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["question"] for item in response.data["data"]],
            ["Do you ship abroad?"],
        )
        self.assertEqual(
            response.data["data"][0]["chatbot"]["slug"], self.own_chatbot.slug,
        )

    def test_lists_hidden_for_other_users_sessions(self):
        other_session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )

        for url in (CART_ITEMS_URL, ORDERS_URL, ESCALATIONS_URL):
            response = self.client.get(url, {"session": other_session.pk})
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_session_list_and_detail_include_counts(self):
        self.fill_session()
        expected = {
            "cart_item_count": 4,
            "purchased_item_count": 2,
            "order_count": 1,
            "escalation_count": 1,
        }

        listed = self.client.get(SESSION_LIST_URL).data["data"]
        detail = self.client.get(
            SESSION_DETAILS_URL, {"session": self.session.pk},
        ).data["data"]

        row = next(item for item in listed if item["id"] == str(self.session.pk))
        for key, value in expected.items():
            self.assertEqual(row[key], value, key)
            self.assertEqual(detail[key], value, key)
