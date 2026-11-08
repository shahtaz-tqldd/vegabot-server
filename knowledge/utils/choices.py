from django.db import models


class KnowledgeSourceTypes(models.TextChoices):
    FILE = "file", "File"
    WEBSITE = "website", "Website"


class KnowledgeSpaceTypes(models.TextChoices):
    """The vector space a knowledge source is trained into.

    The agent uses the space to scope retrieval: products for shopping
    questions, services for booking/pricing questions, documents for
    everything else.
    """

    DOCUMENT = "document", "Document"
    PRODUCT = "product", "Product"
    SERVICE = "service", "Service"


class StatusTypes(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class KnowledgeTrainingStageTypes(models.TextChoices):
    QUEUED = "queued", "Queued"
    EXTRACTING = "extracting", "Extracting"
    CHUNKING = "chunking", "Chunking"
    EMBEDDING = "embedding", "Embedding"
    INDEXING = "indexing", "Indexing"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"
