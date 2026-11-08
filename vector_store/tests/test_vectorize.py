from types import SimpleNamespace
from unittest import TestCase

from vector_store.services.vectorize import KnowledgeVectorService


def fake_document(pk):
    return SimpleNamespace(
        id=pk,
        space="document",
        chatbot_id="c1",
        knowledge_base_id="kb1",
        product_id=None,
        service_id=None,
        chunk_index=0,
        token_count=10,
        content=f"content-{pk}",
        metadata={"name": f"doc-{pk}"},
    )


def candidate(pk, *, distance=None, text_rank=None):
    return SimpleNamespace(
        document=fake_document(pk),
        distance=distance,
        text_rank=text_rank,
    )


class ReciprocalRankFusionTests(TestCase):
    def setUp(self):
        self.service = KnowledgeVectorService()

    def test_documents_found_by_both_searches_rank_first(self):
        fused = self.service._reciprocal_rank_fusion(
            [candidate("a", distance=0.1), candidate("b", distance=0.2)],
            [candidate("c", text_rank=0.9), candidate("a", text_rank=0.8)],
            limit=3,
        )

        self.assertEqual([item.id for item in fused], ["a", "c", "b"])
        # 'a' appears in both rankings: its fused score is the sum.
        self.assertGreater(fused[0].rrf_score, fused[1].rrf_score)

    def test_limit_is_applied(self):
        fused = self.service._reciprocal_rank_fusion(
            [candidate("a"), candidate("b")],
            [],
            limit=1,
        )

        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0].id, "a")

    def test_result_carries_source_references(self):
        fused = self.service._reciprocal_rank_fusion(
            [candidate("a", distance=0.4)],
            [],
            limit=1,
        )

        result = fused[0]
        self.assertEqual(result.space, "document")
        self.assertEqual(result.knowledge_base_id, "kb1")
        self.assertEqual(result.distance, 0.4)
        self.assertIsNone(result.text_rank)
