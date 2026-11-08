from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from app.core.models import BaseMinModel
from knowledge.utils.choices import KnowledgeSpaceTypes


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
    # Postgres-only (pgvector + full-text). Skipped on other backends —
    # see the conditional migration.
    embedding = VectorField(dimensions=1536)
    search_vector = models.GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "vector_documents"
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "space"],
                name="vector_chatbot_space_idx",
            ),
            models.Index(
                fields=["knowledge_base", "chunk_index"],
                name="vector_kb_chunk_idx",
            ),
            # Postgres-only indexes; the conditional migration skips them
            # on other backends.
            GinIndex(fields=["metadata"], name="vector_metadata_gin"),
            GinIndex(
                name="vector_docs_search_vector_gin",
                fields=["search_vector"],
            ),
            HnswIndex(
                name="vector_docs_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.chatbot_id}:{self.space}:{self.chunk_index}"
