from decimal import Decimal
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from agent.tests.test_tools import fake_tool_context
from agent.tools import (
    KNOWLEDGE_REFS_KEY,
    KNOWLEDGE_RESULTS_KEY,
    CartTools,
    EscalationTools,
    build_tools,
)
from base.socket.events import (
    CART_ITEM_ADDED,
    CART_ITEM_REMOVED,
    ESCALATION_CREATED,
    ORDER_PLACED,
)
from chat.models import ChatCartItem, ChatOrder, ChatSession
from chat.utils.choices import CartItemStatus
from chatbot.models import Chatbot
from knowledge.models import Product

User = get_user_model()

CART_TOOLS = {
    "add_to_cart", "remove_from_cart", "view_cart", "place_order", "order_product",
}


class CartToolsTests(TransactionTestCase):
    """Tools run DB work in sync_to_async threads, so use real commits."""

    def setUp(self):
        self.user = User.objects.create_user(email="cart@example.com", password="x")
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Shop",
            business_type="ecommerce",
            created_by=self.user,
            product_ordering_enabled=True,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot, created_by=self.user,
        )
        self.widget = Product.objects.create(
            chatbot=self.chatbot, name="Widget", sku="W-1",
            price=Decimal("9.99"), created_by=self.user,
        )
        self.gadget = Product.objects.create(
            chatbot=self.chatbot, name="Gadget", price=Decimal("20.00"),
            created_by=self.user,
        )
        self.context = fake_tool_context({
            KNOWLEDGE_REFS_KEY: {
                "K1": str(self.widget.pk),
                "K2": str(self.gadget.pk),
                "K3": "kb-1",
            },
            KNOWLEDGE_RESULTS_KEY: {
                str(self.widget.pk): {"space": "product", "name": "Widget"},
                str(self.gadget.pk): {"space": "product", "name": "Gadget"},
                "kb-1": {"space": "document", "name": "FAQ"},
            },
        })
        self.tools = CartTools(self.chatbot.id, self.session)
        patcher = patch("agent.tools.apublish_session_event", new_callable=AsyncMock)
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)

    def published_types(self):
        return [call.args[1]["type"] for call in self.publish.await_args_list]

    async def test_add_to_cart_stores_item_and_publishes_event(self):
        response = await self.tools.add_to_cart("K1", 2, self.context)
        await self.tools.add_to_cart("K1", 1, self.context)

        self.assertEqual(response["status"], "ok")
        item = await ChatCartItem.objects.aget(chat_session=self.session)
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.unit_price, Decimal("9.99"))
        self.assertEqual(self.published_types(), [CART_ITEM_ADDED, CART_ITEM_ADDED])

        session_id, event = self.publish.await_args.args
        self.assertEqual(session_id, self.session.id)
        self.assertEqual(event["data"]["item"]["product_name"], "Widget")
        self.assertEqual(event["data"]["cart"]["total"], "29.97")
        self.assertEqual(event["data"]["counts"]["cart_item_count"], 1)

    async def test_rejects_refs_that_are_not_searched_products(self):
        unknown = await self.tools.add_to_cart("K9", 1, self.context)
        document = await self.tools.add_to_cart("K3", 1, self.context)

        self.assertEqual(unknown["status"], "not_found")
        self.assertEqual(document["status"], "not_found")
        self.assertFalse(await ChatCartItem.objects.aexists())
        self.publish.assert_not_awaited()

    async def test_remove_from_cart(self):
        await self.tools.add_to_cart("K1", 1, self.context)

        response = await self.tools.remove_from_cart("K1", self.context)

        self.assertEqual(response["status"], "ok")
        item = await ChatCartItem.objects.aget(chat_session=self.session)
        self.assertEqual(item.status, CartItemStatus.REMOVED)
        self.assertEqual(self.published_types()[-1], CART_ITEM_REMOVED)

    async def test_view_cart_lists_refs_and_total(self):
        await self.tools.add_to_cart("K1", 2, self.context)
        await self.tools.add_to_cart("K2", 1, self.context)

        response = await self.tools.view_cart(self.context)

        cart = response["cart"]
        self.assertEqual([item["ref"] for item in cart["items"]], ["K1", "K2"])
        self.assertEqual(cart["total"], "39.98")
        self.assertEqual(cart["item_count"], 3)

    async def test_place_order_orders_the_cart(self):
        await self.tools.add_to_cart("K1", 2, self.context)
        await self.tools.add_to_cart("K2", 1, self.context)

        response = await self.tools.place_order()

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["total"], "39.98")
        order = await ChatOrder.objects.aget(chat_session=self.session)
        self.assertEqual(order.order_number, response["order_number"])
        self.assertEqual(order.item_count, 3)
        statuses = {
            item.status
            async for item in ChatCartItem.objects.filter(order=order)
        }
        self.assertEqual(statuses, {CartItemStatus.ORDERED})

        event = self.publish.await_args.args[1]
        self.assertEqual(event["type"], ORDER_PLACED)
        self.assertEqual(len(event["data"]["order"]["items"]), 2)
        self.assertEqual(event["data"]["counts"]["purchased_item_count"], 2)
        self.assertEqual(event["data"]["counts"]["order_count"], 1)
        self.assertEqual(event["data"]["cart"]["items"], [])

    async def test_order_product_takes_it_out_of_the_cart(self):
        await self.tools.add_to_cart("K1", 3, self.context)
        await self.tools.add_to_cart("K2", 1, self.context)

        response = await self.tools.order_product("K1", 1, self.context)

        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["removed_from_cart"])
        self.assertEqual(response["total"], "9.99")
        self.assertEqual([item["ref"] for item in response["cart"]["items"]], ["K2"])
        widget = await ChatCartItem.objects.aget(product=self.widget)
        self.assertEqual(widget.status, CartItemStatus.ORDERED)
        self.assertEqual(widget.quantity, 1)

        event = self.publish.await_args.args[1]
        self.assertEqual(event["type"], ORDER_PLACED)
        self.assertEqual(
            [item["product_name"] for item in event["data"]["cart"]["items"]],
            ["Gadget"],
        )

    async def test_order_product_without_cart(self):
        response = await self.tools.order_product("K2", 2, self.context)

        self.assertEqual(response["status"], "ok")
        self.assertFalse(response["removed_from_cart"])
        self.assertEqual(response["total"], "40.00")
        self.assertEqual(response["cart"]["items"], [])
        event = self.publish.await_args.args[1]
        self.assertEqual(event["data"]["counts"]["cart_item_count"], 0)
        self.assertEqual(event["data"]["counts"]["purchased_item_count"], 1)

    async def test_order_product_rejects_unknown_refs(self):
        response = await self.tools.order_product("K3", 1, self.context)

        self.assertEqual(response["status"], "not_found")
        self.assertFalse(await ChatOrder.objects.aexists())
        self.publish.assert_not_awaited()

    async def test_place_order_with_empty_cart_fails(self):
        response = await self.tools.place_order()

        self.assertEqual(response["status"], "error")
        self.assertFalse(await ChatOrder.objects.aexists())
        self.publish.assert_not_awaited()

    async def test_escalation_publishes_once_per_open_question(self):
        tools = EscalationTools(self.chatbot.id, self.session)

        await tools.request_human_escalation("Can I pay by invoice?")
        await tools.request_human_escalation("Can I pay by invoice?")

        self.assertEqual(self.published_types(), [ESCALATION_CREATED])
        event = self.publish.await_args.args[1]
        self.assertEqual(event["data"]["escalation"]["question"], "Can I pay by invoice?")
        self.assertEqual(event["data"]["counts"]["escalation_count"], 1)


class CartToolRegistrationTests(TransactionTestCase):
    def test_cart_tools_only_with_ordering_and_products(self):
        user = User.objects.create_user(email="reg@example.com", password="x")
        session = ChatSession.objects.create(
            chatbot=Chatbot.objects.create(
                chatbot_name="R", business_type="ecommerce", created_by=user,
            ),
            created_by=user,
        )

        def names(spaces, ordering):
            return {
                tool.name
                for tool in build_tools(
                    session.chatbot_id, session, spaces=spaces, ordering=ordering,
                )
            }

        self.assertTrue(CART_TOOLS <= names(["product"], True))
        self.assertFalse(CART_TOOLS & names(["product"], False))
        self.assertFalse(CART_TOOLS & names(["service"], True))
