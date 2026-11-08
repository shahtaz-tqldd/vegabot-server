from django.db import models

from app.core.models import BaseMinModel
from analytics.utils.choices import AIUsageType


class AIUsage(BaseMinModel):
    """One billable AI operation with optional conversation context."""

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usages",
    )
    chat_session = models.ForeignKey(
        "chat_session.ChatSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usages",
    )
    chat_message = models.OneToOneField(
        "chat_session.ChatMessage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usage",
        help_text="The generated message, when this usage produced one.",
    )
    chatbot_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
        help_text="Original chatbot ID retained after the chatbot is deleted.",
    )
    chat_session_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        editable=False,
        help_text="Original session ID retained after the session is deleted.",
    )
    chat_message_id_snapshot = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        help_text="Original generated-message ID retained after deletion.",
    )

    usage_type = models.CharField(
        max_length=30,
        choices=AIUsageType.choices,
        db_index=True,
    )

    cost = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        default=0,
    )

    tokens = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    thinking_tokens = models.PositiveIntegerField(default=0)
    cached_input_tokens = models.PositiveIntegerField(default=0)
    model = models.CharField(max_length=120, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "usage_type", "-created_at"],
                name="ai_usage_bot_type_idx",
            ),
            models.Index(
                fields=["chat_session", "-created_at"],
                name="ai_usage_session_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.chatbot_id and not self.chatbot_id_snapshot:
            self.chatbot_id_snapshot = self.chatbot_id
        if self.chat_session_id and not self.chat_session_id_snapshot:
            self.chat_session_id_snapshot = self.chat_session_id
        if self.chat_message_id and not self.chat_message_id_snapshot:
            self.chat_message_id_snapshot = self.chat_message_id
        super().save(*args, **kwargs)
