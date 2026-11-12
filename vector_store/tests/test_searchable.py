from unittest import skipUnless

from django.db import connection, transaction
from pgvector.django import CosineDistance, HalfVector

from knowledge.tests.test_catalog_api import KnowledgeTestBase
from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.models import VectorDocument
from vector_store.services.vectorize import KnowledgeVectorService

DIMS = 1536


def embedding(value=0.1):
    return [value] * DIMS


class StaticEmbeddingService:
    def embed_query(self, text):
        return embedding()


class AllRelevantReranker:
    def rank(self, query, records):
        return {record.id: 0.9 for record in records}


@skipUnless(connection.vendor == "postgresql", "pgvector needs PostgreSQL")
class SearchableVectorTests(KnowledgeTestBase):
    def setUp(self):
        super().setUp()
        self.vectors = KnowledgeVectorService(
            embedding_service=StaticEmbeddingService(),
            reranker=AllRelevantReranker(),
        )

    def train_product(self, product, content="Ergonomic wireless mouse."):
        self.vectors.replace_catalog_items(
            space=KnowledgeSpaceTypes.PRODUCT,
            item_ids=[product.pk],
            documents=[
                VectorDocument(
                    chatbot=self.chatbot,
                    space=KnowledgeSpaceTypes.PRODUCT,
                    product=product,
                    content=content,
                    metadata={"name": product.name},
                    embedding=embedding(),
                ),
            ],
        )
        return VectorDocument.objects.get(product=product)

    def test_disabling_and_enabling_product_toggles_searchable(self):
        product = self.create_product()
        vector = self.train_product(product)
        self.assertTrue(vector.is_searchable)

        product.is_enabled = False
        product.save(update_fields=["is_enabled"])
        vector.refresh_from_db()
        self.assertFalse(vector.is_searchable)

        product.is_enabled = True
        product.save()
        vector.refresh_from_db()
        self.assertTrue(vector.is_searchable)

    def test_vectors_for_disabled_product_are_hidden(self):
        product = self.create_product(is_enabled=False)

        vector = self.train_product(product)

        self.assertFalse(vector.is_searchable)

    def test_soft_deleted_chatbot_hides_its_vectors(self):
        vector = self.train_product(self.create_product())

        self.chatbot.is_deleted = True
        self.chatbot.save(update_fields=["is_deleted"])

        vector.refresh_from_db()
        self.assertFalse(vector.is_searchable)

    def test_search_returns_only_searchable_vectors(self):
        visible = self.create_product(name="Wireless Mouse", sku="WM-1")
        hidden = self.create_product(name="Wireless Keyboard", sku="WK-1")
        self.train_product(visible, "Wireless mouse with silent clicks.")
        self.train_product(hidden, "Wireless keyboard with backlight.")
        hidden.is_enabled = False
        hidden.save()

        # search() runs the retrievers in worker threads, whose connections
        # cannot see this test's uncommitted rows; call them directly.
        filters = {
            "chatbot_id": self.chatbot.pk,
            "space": KnowledgeSpaceTypes.PRODUCT,
        }
        vector = self.vectors._vector_candidates("wireless mouse", **filters)
        # OR query: "mouse" alone is enough to match.
        text = self.vectors._full_text_candidates("cordless mouse", **filters)

        expected = [visible.pk]
        self.assertEqual([item.document.product_id for item in vector], expected)
        self.assertEqual([item.document.product_id for item in text], expected)

    def test_vector_candidates_cover_every_space(self):
        product = self.create_product()
        self.train_product(product)

        candidates = self.vectors._vector_candidates(
            "mouse", chatbot_id=self.chatbot.pk,
        )

        self.assertEqual(
            [item.document.product_id for item in candidates], [product.pk],
        )

    def test_vector_query_uses_partial_hnsw_index(self):
        self.train_product(self.create_product())
        queryset = VectorDocument.objects.annotate(
            distance=CosineDistance("embedding", HalfVector(embedding())),
        )
        queryset = KnowledgeVectorService._apply_search_filters(
            queryset,
            chatbot_id=self.chatbot.pk,
            space=KnowledgeSpaceTypes.PRODUCT,
        ).order_by("distance")[:25]

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL enable_seqscan = off")
            plan = queryset.explain()

        self.assertIn("vecdoc_product_hnsw", plan)
