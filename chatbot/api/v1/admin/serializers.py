from chatbot.api.v1.client.serializers import (
    ChatbotDeleteSerializer as ChatbotAdminDeleteSerializer,
)
from chatbot.api.v1.client.serializers import (
    ChatbotListSerializer as ChatbotAdminListSerializer,
)
from chatbot.api.v1.client.serializers import ChatbotUpdateSerializer
from chatbot.utils.choices import ChatbotStatusTypes

__all__ = [
    "ChatbotAdminDeleteSerializer",
    "ChatbotAdminListSerializer",
    "ChatbotAdminUpdateSerializer",
]


class ChatbotAdminUpdateSerializer(ChatbotUpdateSerializer):
    """Admins may additionally moderate chatbots via DISABLED_BY_ADMIN."""

    allowed_statuses = (
        ChatbotStatusTypes.DRAFT,
        ChatbotStatusTypes.ACTIVE,
        ChatbotStatusTypes.DISABLED,
        ChatbotStatusTypes.DISABLED_BY_ADMIN,
    )
