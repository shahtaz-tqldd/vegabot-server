from django.conf import settings
from django.db import models

from app.core.models import BaseModel, BaseMinModel
from chat.utils.choices import (
    AttachmentProcessStatus,
    ChatMessageAttachmentType,
    ChatMessageSenderType,
    ChatMode,
    EscalationReason,
    EscalationStatus,
    ChatSessionStatus,
)

MAX_ATTACHMENTS_PER_MESSAGE = settings.CHAT_ATTACHMENT_MAX_COUNT
MAX_ATTACHMENT_SIZE_MB = settings.CHAT_ATTACHMENT_MAX_FILE_SIZE_MB
MAX_IMAGE_ATTACHMENT_SIZE_MB = 5
BYTES_PER_MEGABYTE = 1024 * 1024


class ChatSession(BaseModel):
    """A conversation between a user and a chatbot's agent.

    ``created_by`` is the user chatting; for demo chatbots created by
    admins, every user chats in their own sessions.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    status = models.CharField(
        max_length=20,
        choices=ChatSessionStatus.choices,
        default=ChatSessionStatus.OPEN,
        db_index=True,
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp of the newest message in this session.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "status"],
                name="chat_session_inbox_idx",
            ),
        ]

    def __str__(self):
        return f"Session {self.pk} on {self.chatbot}"


class ChatMessage(BaseMinModel):
    """One ordered message in a chat session."""

    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_type = models.CharField(
        max_length=12,
        choices=ChatMessageSenderType.choices,
        default=ChatMessageSenderType.USER,
    )
    content = models.TextField(
        blank=True,
        default="",
        help_text="May be blank for attachment-only messages.",
    )
    mode = models.CharField(
        max_length=10,
        choices=ChatMode.choices,
        default=ChatMode.TEXT,
        help_text="text: typed / chat model. audio: spoken / live model.",
    )
    recommendations = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "For agent messages: structured product/service cards the "
            "agent recommended in this reply."
        ),
    )
    response_time_ms = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "For agent messages: latency in milliseconds since the "
            "preceding user message."
        ),
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["chat_session", "created_at"],
                name="chat_message_timeline_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if not is_new:
            return

        # Bulk .update() instead of loading + saving the session: avoids a
        # second SELECT, a full model save, and any save-signal cascade on
        # the hottest write path.
        ChatSession.objects.filter(
            pk=self.chat_session_id,
        ).update(last_activity_at=self.created_at)

    def __str__(self):
        return f"{self.get_sender_type_display()} message in {self.chat_session_id}"


class ChatMessageAttachment(BaseMinModel):
    """A file or image attached to a chat message.

    Images are passed through to the multimodal agent as-is; documents
    get their text extracted (``extracted_text``) so the chatbot can
    answer from file contents.
    """

    chat_message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    attachment_type = models.CharField(
        max_length=12,
        choices=ChatMessageAttachmentType.choices,
    )
    file_url = models.URLField(max_length=2048)
    file_key = models.CharField(max_length=1024)
    file_name = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=100, blank=True, default="")
    file_size = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text="Size in bytes.",
    )

    # multimodal processing
    status = models.CharField(
        max_length=12,
        choices=AttachmentProcessStatus.choices,
        default=AttachmentProcessStatus.PENDING,
        db_index=True,
    )
    extracted_text = models.TextField(
        blank=True,
        default="",
        help_text="Text extracted from documents for retrieval and answering.",
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.file_name or self.file_url


class ChatEscalation(BaseModel):
    """A query the chatbot could not handle and needs a human follow-up."""

    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="escalations",
    )
    chat_message = models.ForeignKey(
        ChatMessage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="escalations",
    )
    question = models.TextField(
        help_text="The query the chatbot could not answer.",
    )
    reason = models.CharField(
        max_length=20,
        choices=EscalationReason.choices,
        default=EscalationReason.NO_ANSWER,
        db_index=True,
    )
    status = models.CharField(
        max_length=12,
        choices=EscalationStatus.choices,
        default=EscalationStatus.OPEN,
        db_index=True,
    )
    resolution_note = models.TextField(
        blank=True,
        default="",
        help_text="How the escalation was handled, once resolved.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "-created_at"],
                name="chat_escalation_queue_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["chat_session", "question", "status"],
                condition=models.Q(status=EscalationStatus.OPEN),
                name="unique_open_escalation_per_session_question",
            ),
        ]

    def __str__(self):
        return f"{self.get_reason_display()} in {self.chat_session_id}"
