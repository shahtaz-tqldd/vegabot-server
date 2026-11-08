from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from base.socket.services.broadcaster import publish_chat_message
from chat.api.v1.client.serializers import ChatMessageSerializer
from chat.models import ChatMessage


@receiver(post_save, sender=ChatMessage)
def publish_new_chat_message(sender, instance, created, **kwargs):
    """Push every new message (user or agent) to the session's socket group."""
    if not created:
        return

    def _publish():
        publish_chat_message(
            ChatMessageSerializer(instance).data,
            instance.chat_session_id,
        )

    transaction.on_commit(_publish)
