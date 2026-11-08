import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import close_old_connections, transaction
from django.db.models import F, Q
from pgvector.django import CosineDistance

from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.models import VectorDocument

from .gemini_embeddings import GeminiEmbeddingService

logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class _RankedCandidate:
    document: VectorDocument
    distance: float | None = None
    text_rank: float | None = None


class KnowledgeVectorService:
    """Persistence and hybrid (vector + full-text) search for knowledge
    chunks, scoped by chatbot and knowledge space."""

    candidate_limit = 20
    rrf_rank_constant = 60

    def __init__(self, *, embedding_service=None):
        self.embedding_service = embedding_service

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    # ------------------------------------------------------------- writes

    def replace_knowledge_base(self, knowledge_base_id, documents):
        with transaction.atomic():
            VectorDocument.objects.filter(
                knowledge_base_id=knowledge_base_id,
            ).delete()
            return VectorDocument.objects.bulk_create(
                documents,
                batch_size=100,
            )

    def replace_catalog_items(self, *, space, item_ids, documents):
        """Retrain a batch of products or services in place."""
        item_ids = list(item_ids)
        if not item_ids:
            return []
        source_field = (
            "product_id" if space == KnowledgeSpaceTypes.PRODUCT else "service_id"
        )
        with transaction.atomic():
            VectorDocument.objects.filter(
                **{f"{source_field}__in": item_ids},
            ).delete()
            return VectorDocument.objects.bulk_create(
                documents,
                batch_size=100,
            )

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
        to one knowledge space (document/product/service)."""
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
        return self._reciprocal_rank_fusion(
            candidates.get("vector", []),
            candidates.get("full_text", []),
            limit=limit,
        )

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
        query_embedding = self._get_embedding_service().embed_query(query)
        queryset = VectorDocument.objects.annotate(
            distance=CosineDistance("embedding", query_embedding),
        )
        queryset = self._apply_search_filters(
            queryset,
            chatbot_id=chatbot_id,
            space=space,
        )
        documents = list(
            queryset.order_by("distance", "-updated_at")[: self.candidate_limit]
        )
        return [
            _RankedCandidate(document=item, distance=float(item.distance))
            for item in documents
        ]

    def _full_text_candidates(
        self,
        query,
        *,
        chatbot_id=None,
        space=None,
    ):
        search_query = SearchQuery(
            query,
            config="english",
            search_type="websearch",
        )
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
    def _apply_search_filters(queryset, *, chatbot_id=None, space=None):
        enabled_filter = Q()
        if space == KnowledgeSpaceTypes.DOCUMENT or space is None:
            enabled_filter |= Q(
                space=KnowledgeSpaceTypes.DOCUMENT,
                knowledge_base__is_enabled=True,
            )
        if space == KnowledgeSpaceTypes.PRODUCT or space is None:
            enabled_filter |= Q(
                space=KnowledgeSpaceTypes.PRODUCT,
                product__is_enabled=True,
            )
        if space == KnowledgeSpaceTypes.SERVICE or space is None:
            enabled_filter |= Q(
                space=KnowledgeSpaceTypes.SERVICE,
                service__is_enabled=True,
            )

        queryset = queryset.filter(
            enabled_filter,
            chatbot__is_deleted=False,
        )
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
