from django.db import models
from django.db.models import Q

from app.core.models import BaseModel, BaseMinModel
from knowledge.utils.choices import (
    KnowledgeSourceTypes,
    KnowledgeSpaceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)

ACTIVE_TRAINING_STAGES = [
    KnowledgeTrainingStageTypes.QUEUED,
    KnowledgeTrainingStageTypes.EXTRACTING,
    KnowledgeTrainingStageTypes.CHUNKING,
    KnowledgeTrainingStageTypes.EMBEDDING,
    KnowledgeTrainingStageTypes.INDEXING,
]


class KnowledgeBase(BaseModel):
    """A document knowledge source: an uploaded file or a scraped website."""

    NAME_MAX_LENGTH = 80

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="knowledge_bases",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=500, blank=True)
    source_type = models.CharField(
        max_length=20,
        choices=KnowledgeSourceTypes.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusTypes.choices,
        default=StatusTypes.PENDING,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)

    # Website source
    url = models.URLField(blank=True, null=True)
    last_crawled_at = models.DateTimeField(blank=True, null=True)

    # File source. Only the private object key is persisted; clients receive a
    # short-lived presigned URL generated at serialization time.
    original_filename = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=20, blank=True)
    file_key = models.CharField(max_length=1024, blank=True)
    file_size = models.PositiveBigIntegerField(blank=True, null=True)

    # Extracted content is the normalized input used by the vector training
    # pipeline.
    extracted_content = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)

    error_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    @property
    def name(self):
        if self.title:
            value = self.title
        elif self.source_type == KnowledgeSourceTypes.FILE:
            value = self.original_filename
        elif self.source_type == KnowledgeSourceTypes.WEBSITE:
            value = self.url or ""
        else:
            value = ""

        value = " ".join(value.split())
        if len(value) > self.NAME_MAX_LENGTH:
            return f"{value[:self.NAME_MAX_LENGTH - 3].rstrip()}..."
        return value or "Untitled document"

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chatbot", "source_type"], name="kb_chatbot_source_idx"),
            models.Index(fields=["chatbot", "status"], name="kb_chatbot_status_idx"),
            models.Index(fields=["chatbot", "is_enabled"], name="kb_chatbot_enabled_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~Q(source_type=KnowledgeSourceTypes.WEBSITE)
                    | (Q(url__isnull=False) & ~Q(url=""))
                ),
                name="kb_website_requires_url",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(source_type=KnowledgeSourceTypes.FILE) | ~Q(file_key="")
                ),
                name="kb_file_requires_key",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"


class Product(BaseModel):
    """One product in a chatbot's product knowledge space.

    Products are created one by one or imported from the CSV/XLSX template;
    each trained product becomes a searchable vector in the product space.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="products",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=120, blank=True, default="")
    category = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Price in the product currency.",
    )
    currency = models.CharField(max_length=3, default="USD")
    product_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=StatusTypes.choices,
        default=StatusTypes.PENDING,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["chatbot", "status"], name="product_chatbot_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "sku"],
                condition=~Q(sku=""),
                name="unique_product_sku_per_chatbot",
            ),
        ]

    def __str__(self):
        return f"{self.name}{f' ({self.sku})' if self.sku else ''}"


class Service(BaseModel):
    """One service in a chatbot's service knowledge space.

    Aimed at business owners selling services: a short structured record
    the chatbot can quote prices and details from.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="services",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    service_type = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="For example: consulting, repair, subscription.",
    )
    description = models.TextField(blank=True, default="")
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cost in the service currency.",
    )
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20,
        choices=StatusTypes.choices,
        default=StatusTypes.PENDING,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["chatbot", "status"], name="service_chatbot_status_idx"),
        ]

    def __str__(self):
        return self.name


class KnowledgeTrainingLog(BaseMinModel):
    """Durable progress and audit record for one training run.

    Document runs point at their ``knowledge_base``; product and service
    runs are batches over a chatbot's pending catalog items.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="knowledge_training_logs",
        on_delete=models.CASCADE,
    )
    space = models.CharField(
        max_length=20,
        choices=KnowledgeSpaceTypes.choices,
        db_index=True,
    )
    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        related_name="training_logs",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    stage = models.CharField(
        max_length=20,
        choices=KnowledgeTrainingStageTypes.choices,
        default=KnowledgeTrainingStageTypes.QUEUED,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    total_chunks = models.PositiveIntegerField(default=0)
    processed_chunks = models.PositiveIntegerField(default=0)
    force_retrain = models.BooleanField(default=False)
    content_changed = models.BooleanField(null=True, blank=True)
    message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "space", "stage"],
                name="kb_log_chatbot_space_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge_base"],
                condition=Q(stage__in=ACTIVE_TRAINING_STAGES),
                name="unique_active_training_per_document",
            ),
            models.UniqueConstraint(
                fields=["chatbot", "space"],
                condition=Q(
                    stage__in=ACTIVE_TRAINING_STAGES,
                    knowledge_base__isnull=True,
                ),
                name="unique_active_training_per_catalog_batch",
            ),
        ]

    def __str__(self):
        return f"{self.chatbot} {self.space}: {self.stage} ({self.progress}%)"
