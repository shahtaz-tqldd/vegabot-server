from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Q
from pgvector.django import HnswIndex, HalfVectorField

from app.core.models import BaseMinModel
from knowledge.utils.choices import KnowledgeSpaceTypes

DIMS = 1536

def _hnsw(space):
    return HnswIndex(
        name=f"vecdoc_{space}_hnsw",
        fields=["embedding"],
        m=16,
        ef_construction=64,
        opclasses=["halfvec_cosine_ops"],
        condition=Q(space=space, is_searchable=True),
    )


class VectorDocument(BaseMinModel):
    """One embedded chunk of knowledge living in a typed vector space.

    A row belongs to exactly one source: a document chunk
    (``knowledge_base``), one product, or one service. ``space`` selects
    the vector space so retrieval can be scoped per knowledge type.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="vector_documents",
        on_delete=models.CASCADE,
    )
    space = models.CharField(
        max_length=20,
        choices=KnowledgeSpaceTypes.choices,
        db_index=True,
    )
    knowledge_base = models.ForeignKey(
        "knowledge.KnowledgeBase",
        related_name="vector_documents",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    product = models.ForeignKey(
        "knowledge.Product",
        related_name="vector_documents",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    service = models.ForeignKey(
        "knowledge.Service",
        related_name="vector_documents",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    chunk_index = models.PositiveIntegerField(default=0)
    token_count = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    # Source enabled AND chatbot not deleted. Precomputed so search needs
    # no joins and can use the partial HNSW indexes; kept in sync by
    # vector_store.services.vectorize.sync_searchable.
    is_searchable = models.BooleanField(default=True)
    # Postgres-only (pgvector + full-text). Skipped on other backends —
    # see the conditional migration.
    embedding = HalfVectorField(dimensions=DIMS)
    search_vector = models.GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "vector_documents"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["chatbot", "space"], name="vector_chatbot_space_idx"),
            models.Index(fields=["knowledge_base", "chunk_index"], name="vector_kb_chunk_idx"),
            GinIndex(fields=["search_vector"], name="vector_docs_search_vector_gin"),
            _hnsw("document"),
            _hnsw("product"),
            _hnsw("service"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(space="document", knowledge_base__isnull=False, product__isnull=True, service__isnull=True)
                    | Q(space="product", product__isnull=False, knowledge_base__isnull=True, service__isnull=True)
                    | Q(space="service", service__isnull=False, knowledge_base__isnull=True, product__isnull=True)
                ),
                name="vector_doc_single_source",
            ),
        ]

    def __str__(self):
        return f"{self.chatbot_id}:{self.space}:{self.chunk_index}"
