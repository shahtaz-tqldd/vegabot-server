from django.conf import settings
from django.db import models

from app.core.models import BaseModel, BaseMinModel
from notification.utils.choices import NotificationType


class Notification(BaseModel):
    """A realtime notification attached to one chatbot.

    The only notification today is knowledge training completion; the
    row is the durable record while the socket broadcast is the delivery.
    """

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=24,
        choices=NotificationType.choices,
        default=NotificationType.TRAINING_COMPLETE,
        db_index=True,
    )
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "-created_at"],
                name="notif_chatbot_recent_idx",
            ),
        ]

    def __str__(self):
        return f"{self.title} -> {self.chatbot}"


class NotificationRead(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_reads",
        db_index=True,
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"],
                name="unique_notification_read_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.user} read {self.notification_id}"
