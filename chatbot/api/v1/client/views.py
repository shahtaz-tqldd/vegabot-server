from django.db.models import Q
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.services.r2 import schedule_delete_image
from app.utils.response import APIResponse
from chatbot.api.v1.client.serializers import (
    ChatbotCreateSerializer,
    ChatbotDeleteSerializer,
    ChatbotDetailSerializer,
    ChatbotListSerializer,
    ChatbotUpdateSerializer,
)
from chatbot.api.v1.utils import (
    ChatbotObjectMixin,
    PaginatedListMixin,
    validation_error_response,
)
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes


class ChatbotCreateView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatbotCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                "Chatbot creation failed.",
            )
        chatbot = serializer.save()
        return APIResponse.success(
            data=self.get_serializer(chatbot).data,
            message="Chatbot created successfully.",
            status=status.HTTP_201_CREATED,
        )


class ChatbotListAPIView(PaginatedListMixin, GenericAPIView):
    """List chatbots created by admins (demo chatbots) and by the requester."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatbotListSerializer

    def get_queryset(self):
        return (
            Chatbot.objects.select_related("created_by")
            .with_knowledge_counts()
            .filter(
                is_deleted=False,
            )
            .filter(
                Q(created_by=self.request.user)
                | Q(created_by__is_staff=True),
            )
            .order_by("-created_at")
        )

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Chatbots fetched successfully.",
        )


class ChatbotDetailView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatbotDetailSerializer

    def get_chatbot_queryset(self):
        # Users can view their own chatbots and admin demo chatbots.
        return Chatbot.objects.filter(
            is_deleted=False,
        ).filter(
            Q(created_by=self.request.user)
            | Q(created_by__is_staff=True),
        )

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(self.get_chatbot()).data,
            message="Chatbot fetched successfully.",
        )


class ChatbotUpdateView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatbotUpdateSerializer

    def get_chatbot_queryset(self):
        # Users can only update chatbots they created.
        return Chatbot.objects.filter(
            is_deleted=False,
            created_by=self.request.user,
        )

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


class ChatbotDeleteView(ChatbotObjectMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatbotDeleteSerializer

    def get_chatbot_queryset(self):
        # Users can only delete chatbots they created.
        return Chatbot.objects.filter(
            is_deleted=False,
            created_by=self.request.user,
        )

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
