from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.tools import (
    KNOWLEDGE_REFS_KEY,
    KNOWLEDGE_RESULTS_KEY,
    EscalationTools,
    KnowledgeSearchTools,
    RecommendationTools,
    build_tools,
)
from chat.models import ChatEscalation, ChatSession
from chatbot.models import Chatbot
from knowledge.utils.choices import KnowledgeSpaceTypes

User = get_user_model()


def make_result(content, *, space="product", name="Widget", price="9.99",
                source_id=None, **metadata):
    return SimpleNamespace(
        id=source_id or "vd-1",
        space=space,
        chatbot_id="bot-1",
        knowledge_base_id=(
            source_id if space == KnowledgeSpaceTypes.DOCUMENT else None
        ),
        product_id=source_id if space == KnowledgeSpaceTypes.PRODUCT else None,
        service_id=source_id if space == KnowledgeSpaceTypes.SERVICE else None,
        chunk_index=0,
        token_count=10,
        content=content,
        metadata={"name": name, "price": price, "currency": "USD", **metadata},
        distance=0.1,
        text_rank=None,
        rrf_score=0.03,
    )


class FakeVectorService:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, chatbot_id=None, space=None, limit=10):
        self.calls.append(
            {"query": query, "chatbot_id": chatbot_id, "space": space,
             "limit": limit},
        )
        return self.results


def fake_tool_context(state=None):
    state = dict(state or {})

    class StateDict(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)

    return SimpleNamespace(state=StateDict(state))


class KnowledgeSearchToolsTests(TestCase):
    async def test_product_search_returns_structured_sources(self):
        vector_service = FakeVectorService(
            [make_result(
                "Product: Widget\nPrice: 9.99 USD",
                source_id="p-1",
                sku="W-1",
            )],
        )
        tools = KnowledgeSearchTools("bot-1", ["product"], vector_service=vector_service)
        context = fake_tool_context()

        response = await tools.search_products("widget", context)

        self.assertEqual(response["status"], "ok")
        source = response["sources"][0]
        self.assertEqual(source["ref"], "K1")
        self.assertEqual(source["name"], "Widget")
        self.assertEqual(source["price"], "9.99")
        self.assertEqual(source["sku"], "W-1")
        self.assertEqual(vector_service.calls[0]["space"], "product")
        self.assertEqual(
            context.state[KNOWLEDGE_REFS_KEY],
            {"K1": "p-1"},
        )
        self.assertIn("p-1", context.state[KNOWLEDGE_RESULTS_KEY])

    async def test_ref_numbers_continue_across_searches(self):
        vector_service = FakeVectorService(
            [make_result("doc text", space="document", source_id="kb-1")],
        )
        tools = KnowledgeSearchTools("bot-1", ["document"], vector_service=vector_service)
        context = fake_tool_context({KNOWLEDGE_REFS_KEY: {"K1": "p-1"}})

        await tools.search_documents("policy", context)

        self.assertEqual(
            context.state[KNOWLEDGE_REFS_KEY],
            {"K1": "p-1", "K2": "kb-1"},
        )

    async def test_not_found_and_error_shapes(self):
        tools = KnowledgeSearchTools(
            "bot-1", ["service"], vector_service=FakeVectorService([]),
        )
        response = await tools.search_services("consulting", fake_tool_context())
        self.assertEqual(response, {"status": "not_found", "sources": []})

        class BrokenVectorService:
            def search(self, *args, **kwargs):
                raise RuntimeError("vector store down")

        tools = KnowledgeSearchTools(
            "bot-1", ["service"], vector_service=BrokenVectorService(),
        )
        response = await tools.search_services("consulting", fake_tool_context())
        self.assertEqual(response["status"], "error")

    def test_tools_registered_conditionally_per_space(self):
        tools = KnowledgeSearchTools("bot-1", ["product", "service"])
        names = [tool.name for tool in tools.get_tools()]
        self.assertEqual(
            sorted(names),
            ["search_products", "search_services"],
        )


class RecommendationToolsTests(TestCase):
    async def test_service_cards_carry_cost_and_description(self):
        result = make_result(
            "Service: Repair\nType: repair\nDescription: Screen fixes  in a day.",
            space=KnowledgeSpaceTypes.SERVICE,
            name="Repair",
            price=None,
            cost="75.00",
            service_type="repair",
            source_id="svc-1",
        )
        context = fake_tool_context()
        await KnowledgeSearchTools(
            "bot-1", ["service"], vector_service=FakeVectorService([result]),
        ).search_services("repair", context)

        response = await RecommendationTools().show_recommendations(
            ["K1", "K9"], context,
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(
            response["items"],
            [{
                "type": "service", "id": "svc-1", "name": "Repair",
                "description": "Screen fixes in a day.", "price": "75.00",
                "currency": "USD", "url": "", "sku": "", "category": "",
                "service_type": "repair",
            }],
        )

    async def test_documents_are_never_recommended(self):
        context = fake_tool_context()
        await KnowledgeSearchTools(
            "bot-1", ["document"],
            vector_service=FakeVectorService([
                make_result("Returns policy", space="document", source_id="kb-1"),
            ]),
        ).search_documents("returns", context)

        response = await RecommendationTools().show_recommendations(["K1"], context)

        self.assertEqual(response["status"], "not_found")


class BuildToolsTests(TestCase):
    def make_session(self):
        user = User.objects.create_user(email="modes@example.com", password="x")
        return ChatSession.objects.create(
            chatbot=Chatbot.objects.create(
                chatbot_name="M", business_type="ecommerce", created_by=user,
            ),
            created_by=user,
        )

    def test_recommendation_tool_only_in_audio_mode_with_catalog(self):
        session = self.make_session()

        def names(spaces, mode):
            return [
                tool.name
                for tool in build_tools(session.chatbot_id, session, spaces=spaces, mode=mode)
            ]

        self.assertIn("show_recommendations", names(["product"], "audio"))
        self.assertIn("show_recommendations", names(["service"], "audio"))
        self.assertNotIn("show_recommendations", names(["product"], "text"))
        self.assertNotIn("show_recommendations", names(["document"], "audio"))
        self.assertEqual(names([], "audio"), ["request_human_escalation"])

    def test_escalation_always_registered(self):
        session = ChatSession.objects.create(
            chatbot=Chatbot.objects.create(
                chatbot_name="B",
                business_type="ecommerce",
                created_by=User.objects.create_user(
                    email="b@example.com", password="x",
                ),
            ),
            created_by=User.objects.get(email="b@example.com"),
        )
        tools = build_tools(session.chatbot_id, session, spaces=[])
        self.assertEqual(
            [tool.name for tool in tools],
            ["request_human_escalation"],
        )


class EscalationToolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="escalate@example.com", password="x",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="B",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.user,
        )

    async def test_escalation_records_chat_escalation(self):
        from asgiref.sync import sync_to_async

        tools = EscalationTools(self.chatbot.id, self.session)

        response = await tools.request_human_escalation(
            "Do you ship to Antarctica?",
        )

        self.assertEqual(response["status"], "recorded")
        escalation = await sync_to_async(ChatEscalation.objects.get)(
            pk=response["escalation_id"],
        )
        self.assertEqual(
            escalation.question,
            "Do you ship to Antarctica?",
        )

    async def test_escalation_rejects_empty_reason(self):
        tools = EscalationTools(self.chatbot.id, self.session)
        with self.assertRaises(Exception):
            await tools.request_human_escalation("   ")
