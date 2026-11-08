from django.db import transaction

from base.socket.services.broadcaster import broadcast
from base.socket.services.groups import chatbot_dashboard_group
from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import Notification
from notification.utils.choices import NotificationType


def create_chatbot_notification(
    *,
    chatbot,
    title,
    message="",
    notification_type=NotificationType.TRAINING_COMPLETE,
    metadata=None,
    created_by=None,
):
    """Store a chatbot notification and broadcast it over the socket."""
    notification = Notification(
        chatbot=chatbot,
        notification_type=notification_type,
        title=title,
        message=message,
        metadata={} if metadata is None else metadata,
        created_by=created_by,
    )
    notification.full_clean()
    notification.save()
    transaction.on_commit(lambda: emit_notification(notification))
    return notification


def emit_notification(notification):
    notification_data = dict(NotificationSerializer(notification).data)
    notification_data["event"] = notification.notification_type
    group_name = chatbot_dashboard_group(notification.chatbot_id)
    broadcast(
        [group_name],
        {
            "type": "notification.created",
            "group": group_name,
            "notification": notification_data,
        },
    )
