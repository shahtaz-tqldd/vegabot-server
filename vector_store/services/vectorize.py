import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from app.constants import KNOWLEDGE_RERANK_ENABLED
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import close_old_connections, connection, transaction
from django.db.models import F, Q
from pgvector.django import CosineDistance, HalfVector

from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.models import VectorDocument

from .gemini_embeddings import GeminiEmbeddingService
from .google_reranker import GoogleRankingService, RankRecord

logger = logging.getLogger(__name__)

# A quoted phrase, or a run of characters that are neither space nor quote.
_FTS_TOKEN = re.compile(r'"([^"]+)"|([^\s"]+)')
_FTS_OPERATOR_WORDS = {"or", "and"}
# Overlap search between neighbouring chunks, in characters.
_MERGE_OVERLAP_WINDOW = 2000
_MERGE_MIN_OVERLAP = 10


def searchable_condition():
    """Rows a search may return: the source is enabled and the chatbot is
    not deleted. It needs joins, so it is stored in ``is_searchable``."""
    enabled = (
        Q(space=KnowledgeSpaceTypes.DOCUMENT, knowledge_base__is_enabled=True)
        | Q(space=KnowledgeSpaceTypes.PRODUCT, product__is_enabled=True)
        | Q(space=KnowledgeSpaceTypes.SERVICE, service__is_enabled=True)
    )
    return enabled & Q(chatbot__is_deleted=False)


def sync_searchable(queryset):
    """Recompute ``is_searchable`` for the vectors in ``queryset``.

    Module-level rather than a service method so it still runs when tests
    replace ``KnowledgeVectorService`` with a fake. Only rows whose value
    changes are written.
    """
    searchable_ids = queryset.filter(searchable_condition()).values("id")
    queryset.filter(is_searchable=True).exclude(
        id__in=searchable_ids,
    ).update(is_searchable=False)
    queryset.filter(is_searchable=False, id__in=searchable_ids).update(
        is_searchable=True,
    )


@dataclass(slots=True)
class VectorSearchResult:
    id: str
    space: str
    chatbot_id: str
    knowledge_base_id: str | None = None
    product_id: str | None = None
    service_id: str | None = None
    chunk_index: int = 0
    token_count: int = 0
    content: str = ""
    metadata: dict = field(default_factory=dict)
    distance: float | None = None
    text_rank: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    # The chunks this result covers; several after neighbour merging.
    chunk_indexes: list[int] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.chunk_indexes:
            self.chunk_indexes = [self.chunk_index]
        if not self.chunk_ids:
            self.chunk_ids = [self.id]

    @property
    def score(self):
        """The re-rank score when re-ranked, else the RRF score."""
        if self.rerank_score is not None:
            return self.rerank_score
        return self.rrf_score


@dataclass(slots=True)
class _RankedCandidate:
    document: VectorDocument
    distance: float | None = None
    text_rank: float | None = None


class KnowledgeVectorService:
    """Persistence and hybrid (vector + full-text) search for knowledge
    chunks, scoped by chatbot and knowledge space, with re-ranking."""

    # Candidates per retriever (vector, full-text).
    candidate_limit = 25
    rrf_rank_constant = 60
    # Fused candidates sent to the re-ranker.
    rerank_candidate_limit = 40
    # Relevance cut-off on re-rank scores (0-1); ``limit`` is a maximum.
    # Results are kept from the top while each scores at least
    # ``min_relevance_score``, at least ``relative_score_ratio`` x the top
    # score, and at most ``max_score_drop`` below the previous kept result
    # (None turns the drop rule off).
    min_relevance_score = 0.3
    relative_score_ratio = 0.5
    max_score_drop = 0.25

    def __init__(self, *, embedding_service=None, reranker=None):
        self.embedding_service = embedding_service
        self.reranker = reranker

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    def _get_reranker(self):
        if self.reranker is None:
            self.reranker = GoogleRankingService()
        return self.reranker

    # ------------------------------------------------------------- writes

    def replace_knowledge_base(self, knowledge_base_id, documents):
        with transaction.atomic():
            vectors = VectorDocument.objects.filter(
                knowledge_base_id=knowledge_base_id,
            )
            vectors.delete()
            created = VectorDocument.objects.bulk_create(
                documents,
                batch_size=100,
            )
            sync_searchable(vectors)
            return created

    def replace_catalog_items(self, *, space, item_ids, documents):
        """Retrain a batch of products or services in place."""
        item_ids = list(item_ids)
        if not item_ids:
            return []
        source_field = (
            "product_id" if space == KnowledgeSpaceTypes.PRODUCT else "service_id"
        )
        with transaction.atomic():
            vectors = VectorDocument.objects.filter(
                **{f"{source_field}__in": item_ids},
            )
            vectors.delete()
            created = VectorDocument.objects.bulk_create(
                documents,
                batch_size=100,
            )
            sync_searchable(vectors)
            return created

    def remove_document(self, knowledge_base_id):
        return VectorDocument.objects.filter(
            knowledge_base_id=knowledge_base_id,
        ).delete()

    def remove_catalog_item(self, *, space, item_id):
        source_field = (
            "product_id" if space == KnowledgeSpaceTypes.PRODUCT else "service_id"
        )
        return VectorDocument.objects.filter(
            space=space,
            **{source_field: item_id},
        ).delete()

    def remove_catalog_items(self, *, space, item_ids):
        """Remove the vectors of many products or services in one query."""
        item_ids = list(item_ids)
        if not item_ids:
            return (0, {})
        source_field = (
            "product_id" if space == KnowledgeSpaceTypes.PRODUCT else "service_id"
        )
        return VectorDocument.objects.filter(
            space=space,
            **{f"{source_field}__in": item_ids},
        ).delete()

    def remove_chatbot(self, chatbot_id):
        return VectorDocument.objects.filter(chatbot_id=chatbot_id).delete()

    def has_complete_vector_set(self, knowledge_base_id):
        queryset = VectorDocument.objects.filter(
            knowledge_base_id=knowledge_base_id,
        )
        count = queryset.count()
        if count == 0:
            return False
        metadata = queryset.order_by("chunk_index").values_list(
            "metadata",
            flat=True,
        ).first()
        try:
            return count == int((metadata or {}).get("chunk_count", 0))
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------- search

    def search(
        self,
        query,
        *,
        chatbot_id=None,
        space=None,
        limit=10,
    ):
        """Hybrid retrieval over a chatbot's knowledge, optionally scoped
        to one knowledge space (document/product/service).

        ``limit`` is a maximum: re-ranked results below the relevance
        cut-off are dropped, so fewer (or no) results can come back.
        """
        limit = max(int(limit), 0)
        if limit == 0:
            return []

        filters = {"chatbot_id": chatbot_id, "space": space}
        searches = {
            "vector": lambda: self._vector_candidates(query, **filters),
            "full_text": lambda: self._full_text_candidates(query, **filters),
        }
        candidates = {}
        errors = {}
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="knowledge-hybrid-search",
        ) as executor:
            futures = {
                name: executor.submit(self._run_search, search)
                for name, search in searches.items()
            }
            for name, future in futures.items():
                try:
                    candidates[name] = future.result()
                except Exception as exc:
                    errors[name] = exc
                    logger.warning(
                        "Knowledge %s search failed; using remaining results. "
                        "error_type=%s",
                        name,
                        type(exc).__name__,
                    )
        if len(errors) == len(searches):
            raise errors["vector"]
        fused = self._reciprocal_rank_fusion(
            candidates.get("vector", []),
            candidates.get("full_text", []),
            limit=max(limit, self.rerank_candidate_limit),
        )
        if not fused:
            return []
        return self._rerank(query, fused, limit=limit)

    @staticmethod
    def _run_search(search):
        close_old_connections()
        try:
            return search()
        finally:
            close_old_connections()

    def _vector_candidates(
        self,
        query,
        *,
        chatbot_id=None,
        space=None,
    ):
        query_embedding = HalfVector(
            self._get_embedding_service().embed_query(query),
        )
        # The HNSW indexes are partial per space, so each space is its own
        # query; one query across spaces could not use them.
        spaces = [space] if space else list(KnowledgeSpaceTypes.values)
        documents = []
        with transaction.atomic():
            if connection.vendor == "postgresql":
                # Keep scanning the index until enough rows pass the
                # filters. Rows come back roughly ordered; re-sorted below.
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            for search_space in spaces:
                queryset = VectorDocument.objects.annotate(
                    distance=CosineDistance("embedding", query_embedding),
                )
                queryset = self._apply_search_filters(
                    queryset,
                    chatbot_id=chatbot_id,
                    space=search_space,
                )
                documents.extend(
                    queryset.order_by("distance")[: self.candidate_limit]
                )
        documents.sort(key=lambda item: item.distance)
        return [
            _RankedCandidate(document=item, distance=float(item.distance))
            for item in documents[: self.candidate_limit]
        ]

    def _full_text_candidates(
        self,
        query,
        *,
        chatbot_id=None,
        space=None,
    ):
        search_query = self._full_text_query(query)
        if search_query is None:
            return []
        queryset = (
            VectorDocument.objects.filter(search_vector=search_query)
            .annotate(
                text_rank=SearchRank(
                    F("search_vector"),
                    search_query,
                    cover_density=True,
                ),
            )
        )
        queryset = self._apply_search_filters(
            queryset,
            chatbot_id=chatbot_id,
            space=space,
        )
        documents = list(
            queryset.order_by("-text_rank", "-updated_at")[: self.candidate_limit]
        )
        return [
            _RankedCandidate(document=item, text_rank=float(item.text_rank))
            for item in documents
        ]

    @staticmethod
    def _full_text_query(query):
        """Match any query term (OR), not all of them.

        Quoted phrases stay phrases; a leading ``-`` (websearch NOT) is
        dropped, as are bare ``or``/``and``. Postgres removes stop words,
        so ``what is your return policy`` becomes ``'return' | 'polici'``.
        Returns None when there are no terms.
        """
        terms = []
        for phrase, word in _FTS_TOKEN.findall(query or ""):
            if phrase:
                phrase = phrase.strip()
                if phrase:
                    terms.append(f'"{phrase}"')
                continue
            word = word.lstrip("-")
            if word and word.lower() not in _FTS_OPERATOR_WORDS:
                terms.append(word)
        if not terms:
            return None
        return SearchQuery(
            " or ".join(terms),
            config="english",
            search_type="websearch",
        )

    @staticmethod
    def _apply_search_filters(queryset, *, chatbot_id=None, space=None):
        # No joins: enabled/deleted state is precomputed in is_searchable,
        # which the partial HNSW indexes also cover.
        queryset = queryset.filter(is_searchable=True)
        if chatbot_id:
            queryset = queryset.filter(chatbot_id=chatbot_id)
        if space:
            queryset = queryset.filter(space=space)
        return queryset

    def _reciprocal_rank_fusion(
        self,
        vector_candidates,
        text_candidates,
        *,
        limit,
    ):
        fused = {}
        for candidates in (vector_candidates, text_candidates):
            for rank, candidate in enumerate(candidates, start=1):
                document_id = str(candidate.document.id)
                entry = fused.setdefault(
                    document_id,
                    {
                        "document": candidate.document,
                        "distance": None,
                        "text_rank": None,
                        "score": 0.0,
                        "best_rank": rank,
                    },
                )
                entry["score"] += 1.0 / (self.rrf_rank_constant + rank)
                entry["best_rank"] = min(entry["best_rank"], rank)
                if candidate.distance is not None:
                    entry["distance"] = candidate.distance
                if candidate.text_rank is not None:
                    entry["text_rank"] = candidate.text_rank

        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item["score"],
                item["best_rank"],
                str(item["document"].id),
            ),
        )[:limit]
        return [self._to_result(item["document"], item) for item in ranked]

    # ---------------------------------------------------------- re-ranking

    def _rerank(self, query, results, *, limit):
        """Re-rank fused results, cut off the irrelevant ones, merge
        neighbouring chunks, and cap at ``limit``. Falls back to RRF order
        when re-ranking is disabled or fails."""
        if not KNOWLEDGE_RERANK_ENABLED:
            logger.warning("Knowledge re-ranking is disabled; using RRF order.")
            return self._merge_neighbours(results[:limit])
        try:
            scores = self._get_reranker().rank(
                query,
                [
                    RankRecord(
                        id=result.id,
                        content=result.content,
                        title=self._result_title(result),
                    )
                    for result in results
                ],
            )
        except Exception as exc:
            logger.warning(
                "Knowledge re-ranking failed; using RRF order. error_type=%s",
                type(exc).__name__,
            )
            return self._merge_neighbours(results[:limit])

        scored = [
            replace(result, rerank_score=float(scores.get(result.id, 0.0)))
            for result in results
        ]
        # Stable sort: equal scores keep their RRF order.
        scored.sort(key=lambda result: -result.rerank_score)
        kept = self._apply_relevance_cutoff(scored)
        return self._merge_neighbours(kept)[:limit]

    @staticmethod
    def _result_title(result):
        metadata = result.metadata or {}
        title = metadata.get("title") or metadata.get("name")
        return str(title) if title else None

    def _apply_relevance_cutoff(self, scored):
        """Keep the leading run of results that pass every score rule.
        ``scored`` is sorted by re-rank score, highest first."""
        if not scored:
            return []
        top_score = scored[0].rerank_score
        if top_score < self.min_relevance_score:
            return []
        floor = max(
            self.min_relevance_score,
            top_score * self.relative_score_ratio,
        )
        kept = []
        previous = None
        for result in scored:
            score = result.rerank_score
            if score < floor:
                break
            if (
                self.max_score_drop is not None
                and previous is not None
                and previous - score > self.max_score_drop
            ):
                break
            kept.append(result)
            previous = score
        return kept

    # ---------------------------------------------------- neighbour merging

    def _merge_neighbours(self, results):
        """Merge runs of consecutive chunks of the same document into one
        result, placed where its best-ranked chunk was."""
        groups = {}
        for position, result in enumerate(results):
            if (
                result.space == KnowledgeSpaceTypes.DOCUMENT
                and result.knowledge_base_id
            ):
                groups.setdefault(result.knowledge_base_id, []).append(
                    (position, result),
                )

        merged_at = {}
        absorbed = set()
        for items in groups.values():
            items.sort(key=lambda item: item[1].chunk_index)
            run = [items[0]]
            for item in items[1:]:
                if item[1].chunk_index == run[-1][1].chunk_index + 1:
                    run.append(item)
                    continue
                self._collect_run(run, merged_at, absorbed)
                run = [item]
            self._collect_run(run, merged_at, absorbed)

        return [
            merged_at.get(position, result)
            for position, result in enumerate(results)
            if position not in absorbed
        ]

    @classmethod
    def _collect_run(cls, run, merged_at, absorbed):
        if len(run) < 2:
            return
        best_position = min(position for position, _ in run)
        best = next(result for position, result in run if position == best_position)
        merged_at[best_position] = cls._merge_run(
            [result for _, result in run],
            best=best,
        )
        absorbed.update(
            position for position, _ in run if position != best_position
        )

    @staticmethod
    def _merge_run(chunks, *, best):
        """Join consecutive chunks, dropping the text they share. Takes the
        position and scores of ``best``."""
        content = chunks[0].content
        token_count = chunks[0].token_count
        for chunk in chunks[1:]:
            overlap = _overlap_length(content, chunk.content)
            if overlap:
                content += chunk.content[overlap:]
                # Approximate: drop the overlap's share of the chunk's tokens.
                shared_tokens = round(
                    chunk.token_count * overlap / max(len(chunk.content), 1),
                )
                token_count += chunk.token_count - shared_tokens
            else:
                content += "\n" + chunk.content
                token_count += chunk.token_count

        first_index = chunks[0].chunk_index
        metadata = dict(best.metadata or {})
        if "chunk_index" in metadata:
            metadata["chunk_index"] = first_index
        return replace(
            best,
            content=content,
            chunk_index=first_index,
            token_count=token_count,
            metadata=metadata,
            chunk_indexes=[
                index for chunk in chunks for index in chunk.chunk_indexes
            ],
            chunk_ids=[chunk_id for chunk in chunks for chunk_id in chunk.chunk_ids],
        )

    @staticmethod
    def _to_result(document, scores):
        return VectorSearchResult(
            id=str(document.id),
            space=document.space,
            chatbot_id=str(document.chatbot_id),
            knowledge_base_id=(
                str(document.knowledge_base_id)
                if document.knowledge_base_id
                else None
            ),
            product_id=str(document.product_id) if document.product_id else None,
            service_id=str(document.service_id) if document.service_id else None,
            chunk_index=document.chunk_index,
            token_count=document.token_count,
            content=document.content,
            metadata=document.metadata,
            distance=scores["distance"],
            text_rank=scores["text_rank"],
            rrf_score=scores["score"],
        )


def _overlap_length(left, right):
    """Length of the longest suffix of ``left`` that is also a prefix of
    ``right`` (at least ``_MERGE_MIN_OVERLAP`` characters), else 0."""
    window = min(len(left), len(right), _MERGE_OVERLAP_WINDOW)
    if window < _MERGE_MIN_OVERLAP:
        return 0
    tail = left[-window:]
    probe = right[:_MERGE_MIN_OVERLAP]
    # The earliest match in the tail is the longest overlap.
    start = tail.find(probe)
    while start != -1:
        if right.startswith(tail[start:]):
            return len(tail) - start
        start = tail.find(probe, start + 1)
    return 0
