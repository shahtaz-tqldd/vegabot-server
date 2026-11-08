from rest_framework.generics import GenericAPIView

from app.utils.permission import IsAdmin
from app.utils.response import APIResponse
from app.services.r2 import schedule_delete_image
from chatbot.api.v1.admin.serializers import (
    ChatbotAdminDeleteSerializer,
    ChatbotAdminListSerializer,
    ChatbotAdminUpdateSerializer,
)
from chatbot.api.v1.utils import (
    ChatbotObjectMixin,
    PaginatedListMixin,
    validation_error_response,
)
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes


class ChatbotAdminListAPIView(PaginatedListMixin, GenericAPIView):
    """List every chatbot in the platform."""

    permission_classes = [IsAdmin]
    serializer_class = ChatbotAdminListSerializer

    def get_queryset(self):
        return (
            Chatbot.objects.select_related("created_by")
            .filter(is_deleted=False)
            .order_by("-created_at")
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Chatbots fetched successfully.",
        )


class ChatbotAdminUpdateView(ChatbotObjectMixin, GenericAPIView):
    """Update any chatbot, including chatbots created by other users."""

    permission_classes = [IsAdmin]
    serializer_class = ChatbotAdminUpdateSerializer

    def get_chatbot_queryset(self):
        return Chatbot.objects.filter(is_deleted=False)

    def _update(self, request, *, partial):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(
            chatbot,
            data=request.data,
            partial=partial,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot update failed.",
            )
        chatbot = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot updated successfully.",
        )

    def put(self, request, *args, **kwargs):
        return self._update(request, partial=False)

    def patch(self, request, *args, **kwargs):
        return self._update(request, partial=True)


class ChatbotAdminDeleteView(ChatbotObjectMixin, GenericAPIView):
    """Delete any chatbot, including chatbots created by other users."""

    permission_classes = [IsAdmin]
    serializer_class = ChatbotAdminDeleteSerializer

    def get_chatbot_queryset(self):
        return Chatbot.objects.filter(is_deleted=False)

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        previous_logo_url = chatbot.logo
        chatbot.is_deleted = True
        chatbot.logo = ""
        chatbot.status = ChatbotStatusTypes.DISABLED
        chatbot.updated_by = request.user
        chatbot.save(
            update_fields=[
                "is_deleted",
                "logo",
                "status",
                "updated_by",
                "updated_at",
            ]
        )
        schedule_delete_image(image_url=previous_logo_url)
        return APIResponse.success(
            message="Chatbot deleted successfully.",
        )
