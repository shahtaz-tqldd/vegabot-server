from django.db import models


class ChatSessionStatus(models.TextChoices):
    """Lifecycle of the conversation."""

    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class ChatMessageSenderType(models.TextChoices):
    USER = "user", "User"
    AGENT = "agent", "Agent"


class ChatMode(models.TextChoices):
    """How a turn was exchanged; selects the agent configuration.

    ``text`` runs the (cheap) chat model; ``audio`` runs the (costly)
    live model with streamed speech in both directions.
    """

    TEXT = "text", "Text"
    AUDIO = "audio", "Audio"


class ChatMessageAttachmentType(models.TextChoices):
    IMAGE = "image", "Image"
    DOCUMENT = "document", "Document"
    AUDIO = "audio", "Audio"
    VIDEO = "video", "Video"
    OTHER = "other", "Other"


class AttachmentProcessStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class EscalationReason(models.TextChoices):
    """Why a chatbot could not handle a query."""

    NO_ANSWER = "no_answer", "No answer in knowledge"
    LOW_CONFIDENCE = "low_confidence", "Low confidence answer"
    USER_REQUESTED = "user_requested", "User requested a human"


class EscalationStatus(models.TextChoices):
    OPEN = "open", "Open"
    RESOLVED = "resolved", "Resolved"
