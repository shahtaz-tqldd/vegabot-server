from itertools import count
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django.test import SimpleTestCase

from vector_store.services.vectorize import (
    KnowledgeVectorService,
    VectorSearchResult,
)

# Distinct, non-adjacent chunk indexes so fused results never merge
# unless a test asks for neighbours.
_chunk_indexes = count(0, 10)


def fake_document(pk, *, chunk_index=None, knowledge_base_id="kb1", content=None):
    return SimpleNamespace(
        id=pk,
        space="document",
        chatbot_id="c1",
        knowledge_base_id=knowledge_base_id,
        product_id=None,
        service_id=None,
        chunk_index=next(_chunk_indexes) if chunk_index is None else chunk_index,
        token_count=10,
        content=content if content is not None else f"content-{pk}",
        metadata={"name": f"doc-{pk}"},
    )


def candidate(pk, *, distance=None, text_rank=None, **document):
    return SimpleNamespace(
        document=fake_document(pk, **document),
        distance=distance,
        text_rank=text_rank,
    )


def result(pk, *, chunk_index, content, knowledge_base_id="kb1", token_count=10):
    return VectorSearchResult(
        id=pk,
        space="document",
        chatbot_id="c1",
        knowledge_base_id=knowledge_base_id,
        chunk_index=chunk_index,
        token_count=token_count,
        content=content,
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
        self.assertEqual(result.chunk_ids, ["a"])
        self.assertEqual(result.chunk_indexes, [result.chunk_index])
        self.assertIsNone(result.rerank_score)
        self.assertEqual(result.score, result.rrf_score)


class FakeReranker:
    def __init__(self, scores=None, error=None):
        self.scores = scores or {}
        self.error = error
        self.calls = []

    def rank(self, query, records):
        records = list(records)
        self.calls.append((query, records))
        if self.error:
            raise self.error
        return {
            record.id: self.scores[record.id]
            for record in records
            if record.id in self.scores
        }


class RerankedSearchTests(SimpleTestCase):
    """search() with the retrievers stubbed: a, b, c, d, e in RRF order."""

    def setUp(self):
        patcher = patch("vector_store.services.vectorize.KNOWLEDGE_RERANK_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, scores=None, *, error=None, limit=10, **attributes):
        reranker = FakeReranker(scores, error=error)
        service = KnowledgeVectorService(reranker=reranker)
        for name, value in attributes.items():
            setattr(service, name, value)
        vector = [
            candidate(pk, distance=index / 10)
            for index, pk in enumerate("abcde")
        ]
        with patch.object(service, "_vector_candidates", return_value=vector), \
                patch.object(service, "_full_text_candidates", return_value=[]):
            results = service.search("return policy", limit=limit)
        return results, reranker

    def ids(self, results):
        return [item.id for item in results]

    def test_reranker_reorders_results(self):
        results, reranker = self.search(
            {"a": 0.4, "b": 0.6, "c": 0.9, "d": 0.75, "e": 0.5},
        )

        self.assertEqual(self.ids(results), ["c", "d", "b", "e"])
        self.assertEqual(results[0].rerank_score, 0.9)
        self.assertEqual(results[0].score, 0.9)
        query, records = reranker.calls[0]
        self.assertEqual(query, "return policy")
        self.assertEqual([record.id for record in records], list("abcde"))
        self.assertEqual(records[0].title, "doc-a")

    def test_single_relevant_chunk_returns_one_result(self):
        results, _ = self.search({"a": 0.1, "b": 0.92, "c": 0.2, "d": 0.05})

        self.assertEqual(self.ids(results), ["b"])

    def test_nothing_relevant_returns_empty(self):
        results, _ = self.search({"a": 0.29, "b": 0.2, "c": 0.1})

        self.assertEqual(results, [])

    def test_large_score_drop_cuts_the_rest(self):
        # 0.55 passes the 0.3 floor and 0.5 x 0.95, but drops 0.3 from 0.85.
        results, _ = self.search({"a": 0.95, "b": 0.85, "c": 0.55, "d": 0.5})

        self.assertEqual(self.ids(results), ["a", "b"])

    def test_score_drop_rule_can_be_disabled(self):
        results, _ = self.search(
            {"a": 0.95, "b": 0.85, "c": 0.55, "d": 0.5},
            max_score_drop=None,
        )

        self.assertEqual(self.ids(results), ["a", "b", "c", "d"])

    def test_limit_caps_relevant_results(self):
        results, _ = self.search(
            {"a": 0.9, "b": 0.88, "c": 0.86, "d": 0.84, "e": 0.82},
            limit=2,
        )

        self.assertEqual(self.ids(results), ["a", "b"])

    def test_records_without_a_score_are_dropped(self):
        results, _ = self.search({"c": 0.8, "e": 0.7})

        self.assertEqual(self.ids(results), ["c", "e"])

    def test_reranker_error_falls_back_to_rrf_order(self):
        results, _ = self.search(error=RuntimeError("boom"), limit=3)

        self.assertEqual(self.ids(results), ["a", "b", "c"])
        self.assertIsNone(results[0].rerank_score)
        self.assertEqual(results[0].score, results[0].rrf_score)

    def test_disabled_reranker_uses_rrf_order(self):
        with patch("vector_store.services.vectorize.KNOWLEDGE_RERANK_ENABLED", False):
            results, reranker = self.search({"e": 0.9}, limit=2)

        self.assertEqual(self.ids(results), ["a", "b"])
        self.assertEqual(reranker.calls, [])

    def test_sends_rerank_candidate_limit_records(self):
        service = KnowledgeVectorService(reranker=FakeReranker())
        vector = [candidate(f"v{index}") for index in range(25)]
        text = [candidate(f"t{index}") for index in range(25)]
        with patch.object(service, "_vector_candidates", return_value=vector), \
                patch.object(service, "_full_text_candidates", return_value=text):
            service.search("q", limit=5)

        _, records = service.reranker.calls[0]
        self.assertEqual(len(records), service.rerank_candidate_limit)


class FullTextQueryTests(TestCase):
    def query_text(self, query):
        search_query = KnowledgeVectorService._full_text_query(query)
        return None if search_query is None else search_query.source_expressions[-1].value

    def test_terms_are_joined_with_or(self):
        self.assertEqual(
            self.query_text("what is your return policy"),
            "what or is or your or return or policy",
        )

    def test_quoted_phrases_stay_phrases(self):
        self.assertEqual(
            self.query_text('"return policy" shipping'),
            '"return policy" or shipping',
        )

    def test_negation_and_operator_words_are_dropped(self):
        self.assertEqual(
            self.query_text("refund OR -exchange and gift"),
            "refund or exchange or gift",
        )

    def test_no_terms_returns_none(self):
        self.assertIsNone(self.query_text(""))
        self.assertIsNone(self.query_text('  or AND - "" '))

    def test_query_uses_websearch_english(self):
        search_query = KnowledgeVectorService._full_text_query("refund")
        self.assertEqual(search_query.function, "websearch_to_tsquery")
        self.assertEqual(search_query.config.config.value, "english")


class MergeNeighboursTests(TestCase):
    def setUp(self):
        self.service = KnowledgeVectorService()

    def test_adjacent_chunks_merge_without_repeating_the_overlap(self):
        first = result(
            "x", chunk_index=4,
            content="Returns are accepted within 30 days of delivery.",
        )
        second = result(
            "y", chunk_index=3,
            content="Our policy is simple. Returns are accepted within",
        )
        second.rerank_score = 0.5
        first.rerank_score = 0.9

        merged = self.service._merge_neighbours([first, second])

        self.assertEqual(len(merged), 1)
        item = merged[0]
        self.assertEqual(
            item.content,
            "Our policy is simple. Returns are accepted within 30 days of delivery.",
        )
        # Takes the best-ranked chunk's identity and scores.
        self.assertEqual(item.id, "x")
        self.assertEqual(item.rerank_score, 0.9)
        self.assertEqual(item.chunk_index, 3)
        self.assertEqual(item.chunk_indexes, [3, 4])
        self.assertEqual(item.chunk_ids, ["y", "x"])
        self.assertLess(item.token_count, 20)

    def test_non_adjacent_chunks_stay_separate(self):
        items = [
            result("x", chunk_index=1, content="alpha"),
            result("y", chunk_index=3, content="gamma"),
            result("z", chunk_index=2, content="beta", knowledge_base_id="kb2"),
        ]

        merged = self.service._merge_neighbours(items)

        self.assertEqual([item.id for item in merged], ["x", "y", "z"])

    def test_chunks_without_overlap_join_with_newline(self):
        merged = self.service._merge_neighbours([
            result("x", chunk_index=0, content="First section."),
            result("y", chunk_index=1, content="Second section."),
        ])

        self.assertEqual(merged[0].content, "First section.\nSecond section.")
        self.assertEqual(merged[0].token_count, 20)

    def test_three_chunk_run_merges_into_the_best_position(self):
        items = [
            result("other", chunk_index=0, content="Unrelated.", knowledge_base_id="kb2"),
            result("m", chunk_index=6, content="middle part and the end part"),
            result("s", chunk_index=5, content="the start part and middle part"),
            result("e", chunk_index=7, content="the end part, finally done."),
        ]

        merged = self.service._merge_neighbours(items)

        self.assertEqual([item.id for item in merged], ["other", "m"])
        self.assertEqual(
            merged[1].content,
            "the start part and middle part and the end part, finally done.",
        )
        self.assertEqual(merged[1].chunk_indexes, [5, 6, 7])
        self.assertEqual(merged[1].chunk_index, 5)
