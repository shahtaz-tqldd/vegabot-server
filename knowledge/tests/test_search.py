from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from knowledge.services.catalog import (
    build_product_metadata,
    build_product_text,
    build_service_metadata,
    build_service_text,
)
from knowledge.services.search import search_knowledge
from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.services.vectorize import VectorSearchResult

from .test_catalog_api import KnowledgeTestBase

User = get_user_model()

SEARCH_URL = reverse("knowledge-search")


class StructuredTextTests(KnowledgeTestBase):
    def test_product_text_and_metadata(self):
        product = self.create_product(price="29.99")

        text = build_product_text(product)
        metadata = build_product_metadata(product)

        self.assertIn("Product: Wireless Mouse", text)
        self.assertIn("SKU: WM-001", text)
        self.assertIn("Price: 29.99 USD", text)
        self.assertEqual(metadata["sku"], "WM-001")
        self.assertEqual(metadata["price"], "29.99")

    def test_service_text_and_metadata(self):
        from knowledge.models import Service

        service = Service.objects.create(
            chatbot=self.chatbot,
            name="Consulting",
            service_type="consulting",
            cost="120.00",
            created_by=self.user,
        )

        text = build_service_text(service)
        metadata = build_service_metadata(service)

        self.assertIn("Service: Consulting", text)
        self.assertIn("Type: consulting", text)
        self.assertIn("Cost: 120.00 USD", text)
        self.assertEqual(metadata["service_type"], "consulting")


class FakeSearchVectorService:
    """In-memory retrieval used to test the search entry point."""

    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    def search(self, query, *, chatbot_id=None, space=None, limit=10):
        self.calls.append(
            {"query": query, "chatbot_id": chatbot_id, "space": space, "limit": limit},
        )
        return self.results


def make_result(space, content, **extra_metadata):
    metadata = {"name": "item", **extra_metadata}
    return VectorSearchResult(
        id="result-1",
        space=space,
        chatbot_id="chatbot-1",
        content=content,
        metadata=metadata,
        rrf_score=0.032,
    )


class SearchServiceTests(KnowledgeTestBase):
    def test_search_knowledge_passes_space_and_scope(self):
        fake = FakeSearchVectorService(
            results=[make_result(KnowledgeSpaceTypes.PRODUCT, "Wireless Mouse")],
        )

        results = search_knowledge(
            "mouse",
            chatbot=self.chatbot,
            space=KnowledgeSpaceTypes.PRODUCT,
            limit=5,
            vector_service=fake,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].space, "product")
        self.assertEqual(
            fake.calls,
            [
                {
                    "query": "mouse",
                    "chatbot_id": self.chatbot.pk,
                    "space": KnowledgeSpaceTypes.PRODUCT,
                    "limit": 5,
                }
            ],
        )

    def test_search_knowledge_rejects_unknown_space(self):
        fake = FakeSearchVectorService()
        with self.assertRaises(Exception):
            search_knowledge("q", chatbot=self.chatbot, space="bogus", vector_service=fake)
        self.assertEqual(fake.calls, [])

    def test_search_endpoint_scoped_and_serialized(self):
        fake = FakeSearchVectorService(
            results=[
                make_result(
                    KnowledgeSpaceTypes.SERVICE,
                    "Consulting: 120.00 USD",
                    name="Consulting",
                ),
            ],
        )

        with patch(
            "knowledge.services.search.KnowledgeVectorService",
            return_value=fake,
        ):
            response = self.client.post(
                SEARCH_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "query": "how much for consulting",
                    "type": "service",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["space"], "service")
        self.assertEqual(data[0]["content"], "Consulting: 120.00 USD")
        self.assertEqual(fake.calls[0]["space"], "service")
        self.assertEqual(fake.calls[0]["chatbot_id"], self.chatbot.pk)

    def test_search_endpoint_rejects_foreign_chatbot(self):
        response = self.client.post(
            SEARCH_URL,
            {
                "chatbot": self.other_chatbot.slug,
                "query": "anything",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
