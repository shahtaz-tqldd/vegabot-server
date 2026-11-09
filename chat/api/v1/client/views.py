from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.services.r2 import upload_file
from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from chat.api.v1.client.serializers import (
    ChatEscalationListSerializer,
    ChatEscalationQuerySerializer,
    ChatEscalationResolveSerializer,
    ChatMessageCreateSerializer,
    ChatMessageSerializer,
    ChatSessionCreateSerializer,
    ChatSessionDetailSerializer,
    ChatSessionListSerializer,
    ChatSessionQuerySerializer,
)
from chat.services.messages import create_user_message
from chat.services.sessions import visible_sessions
from chat.models import (
    BYTES_PER_MEGABYTE,
    MAX_ATTACHMENT_SIZE_MB,
    MAX_IMAGE_ATTACHMENT_SIZE_MB,
    ChatEscalation,
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
)
from chat.services.attachments import process_attachment
from chat.services.escalations import resolve_escalation
from chat.utils.choices import (
    AttachmentProcessStatus,
    ChatMessageAttachmentType,
    ChatMessageSenderType,
    EscalationStatus,
)


def session_list_queryset(user):
    last_message = (
        ChatMessage.objects.filter(chat_session=OuterRef("pk"))
        .order_by("-created_at", "-id")
    )
    return (
        visible_sessions(user)
        .annotate(
            message_count=Count("messages"),
            last_message_content=Subquery(
                last_message.values("content")[:1],
            ),
            last_message_sender_type=Subquery(
                last_message.values("sender_type")[:1],
            ),
            last_message_created_at=Subquery(
                last_message.values("created_at")[:1],
            ),
        )
        .order_by(
            Coalesce("last_activity_at", "created_at").desc(),
            "-created_at",
        )
    )


class PaginatedListMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message=message,
        )


class ChatSessionObjectMixin:
    """Resolve a session by the ``session`` uuid query parameter."""

    _session = None

    def get_session_queryset(self):
        return visible_sessions(self.request.user)

    def get_session(self):
        if self._session is None:
            query_serializer = ChatSessionQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._session = get_object_or_404(
                self.get_session_queryset(),
                pk=query_serializer.validated_data["session"],
            )
        return self._session


class ChatSessionCreateView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatSessionCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]
        session = ChatSession.objects.create(
            chatbot=chatbot,
            created_by=request.user,
        )
        return APIResponse.success(
            data=ChatSessionDetailSerializer(session).data,
            message="Chat session created successfully.",
            status=status.HTTP_201_CREATED,
        )


class ChatSessionListView(PaginatedListMixin, GenericAPIView):
    """List the requester's sessions; superusers see every session."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatSessionListSerializer

    def get_queryset(self):
        return session_list_queryset(self.request.user)

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Chat sessions fetched successfully.",
        )


class ChatSessionDetailView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChatSessionDetailSerializer

    def get(self, request, *args, **kwargs):
        session = self.get_session()
        return APIResponse.success(
            data=self.get_serializer(session).data,
            message="Chat session fetched successfully.",
        )


class ChatSessionDeleteView(ChatSessionObjectMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        session = self.get_session()
        session.delete()
        return APIResponse.success(
            message="Chat session deleted successfully.",
        )


class ChatMessageListView(PaginatedListMixin, GenericAPIView):
    """List the message timeline of one session."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatMessageSerializer

    def get_queryset(self):
        query_serializer = ChatSessionQuerySerializer(
            data=self.request.query_params,
        )
        query_serializer.is_valid(raise_exception=True)
        session = get_object_or_404(
            visible_sessions(self.request.user),
            pk=query_serializer.validated_data["session"],
        )
        return session.messages.all()

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Chat messages fetched successfully.",
        )


IMAGE_MIME_PREFIX = "image/"
AUDIO_MIME_PREFIX = "audio/"
VIDEO_MIME_PREFIX = "video/"
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/json",
    "application/xml",
    "text/html",
}


def classify_attachment(uploaded_file):
    """Map an upload to its attachment kind and enforce size limits."""
    mime_type = (uploaded_file.content_type or "").split(";")[0].strip().lower()
    if mime_type.startswith(IMAGE_MIME_PREFIX):
        if uploaded_file.size > MAX_IMAGE_ATTACHMENT_SIZE_MB * BYTES_PER_MEGABYTE:
            raise ValueError(
                f"{uploaded_file.name}: images cannot exceed "
                f"{MAX_IMAGE_ATTACHMENT_SIZE_MB} MB."
            )
        return ChatMessageAttachmentType.IMAGE, mime_type
    if mime_type in DOCUMENT_MIME_TYPES or mime_type.startswith("text/"):
        if uploaded_file.size > MAX_ATTACHMENT_SIZE_MB * BYTES_PER_MEGABYTE:
            raise ValueError(
                f"{uploaded_file.name}: files cannot exceed "
                f"{MAX_ATTACHMENT_SIZE_MB} MB."
            )
        return ChatMessageAttachmentType.DOCUMENT, mime_type
    if mime_type.startswith(AUDIO_MIME_PREFIX):
        return ChatMessageAttachmentType.AUDIO, mime_type
    if mime_type.startswith(VIDEO_MIME_PREFIX):
        return ChatMessageAttachmentType.VIDEO, mime_type
    raise ValueError(
        f"{uploaded_file.name}: unsupported file type"
        f"{f' ({mime_type})' if mime_type else ''}."
    )


class ChatMessageCreateView(GenericAPIView):
    """Send a user message, optionally with image/file attachments.

    Answered in the background in text mode; the reply streams to the
    session socket as ``agent.response.*`` events and is stored as an
    agent message. Documents are text-extracted so the chatbot can answer
    from file contents. Plain typed messages should go over the socket.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ChatMessageCreateSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = get_object_or_404(
            ChatSession.objects.filter(
                created_by=request.user,
                chatbot__is_deleted=False,
            ),
            pk=serializer.validated_data["session"],
        )
        uploaded_files = serializer.validated_data["attachments"]

        from agent.helpers.lock import voice_active

        if voice_active(session.pk):
            return APIResponse.error(
                errors={"session": "Voice mode is active for this session."},
                message=(
                    "Switch the session socket to text mode before sending "
                    "messages over REST."
                ),
                status=status.HTTP_409_CONFLICT,
            )

        try:
            classified = [
                (file_obj, *classify_attachment(file_obj))
                for file_obj in uploaded_files
            ]
        except ValueError as exc:
            return APIResponse.error(
                errors={"attachments": str(exc)},
                message="Message send failed.",
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            message = ChatMessage.objects.create(
                chat_session=session,
                sender_type=ChatMessageSenderType.USER,
                content=serializer.validated_data["content"],
            )
            for file_obj, attachment_type, mime_type in classified:
                upload = upload_file(
                    file_obj,
                    folder=f"{settings.R2_FILES_PREFIX}/chat-attachments",
                    public_id=f"chat-attachment-{message.pk}-{uuid4().hex}",
                )
                file_obj.seek(0)
                attachment = ChatMessageAttachment.objects.create(
                    chat_message=message,
                    attachment_type=attachment_type,
                    file_url=upload["url"],
                    file_key=upload["key"],
                    file_name=file_obj.name or "",
                    mime_type=mime_type,
                    file_size=file_obj.size,
                    status=AttachmentProcessStatus.PENDING,
                )
                # Multimodal processing is best-effort: extraction errors
                # mark the attachment failed but never block the message.
                process_attachment(attachment, source=file_obj)

        # Answer with the agent once the message and its attachments are
        # committed; the reply arrives over the session socket broadcast.
        from chat.services.messages import dispatch_agent_reply

        dispatch_agent_reply(message)

        message = ChatMessage.objects.prefetch_related("attachments").get(
            pk=message.pk,
        )
        return APIResponse.success(
            data=ChatMessageSerializer(message).data,
            message="Message sent successfully.",
            status=status.HTTP_201_CREATED,
        )


class SessionStatsAPIView(GenericAPIView):
    """Conversation totals and average agent response time.

    Regular users get stats for their own sessions; superusers get
    platform-wide stats.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        sessions = visible_sessions(request.user)
        messages = ChatMessage.objects.filter(chat_session__in=sessions)
        escalations = ChatEscalation.objects.filter(chat_session__in=sessions)
        agent_messages = messages.filter(
            sender_type=ChatMessageSenderType.AGENT,
            response_time_ms__isnull=False,
        )
        avg_response_time_ms = agent_messages.aggregate(
            avg=Avg("response_time_ms"),
        )["avg"]

        return APIResponse.success(
            data={
                "total_sessions": sessions.count(),
                "total_messages": messages.count(),
                "avg_response_time_ms": (
                    round(avg_response_time_ms)
                    if avg_response_time_ms is not None
                    else None
                ),
                "total_escalations": escalations.count(),
                "open_escalations": escalations.filter(
                    status=EscalationStatus.OPEN,
                ).count(),
            },
            message="Chat stats fetched successfully.",
        )


def visible_escalations(user):
    """Escalations the user may see.

    Users see escalations from their own chats; chatbot owners see
    every escalation raised on their chatbots (including demo chatbot
    sessions chatted by others); superusers see everything.
    """
    queryset = (
        ChatEscalation.objects.select_related(
            "chat_session__chatbot",
            "chat_session__created_by",
        ).filter(chat_session__chatbot__is_deleted=False)
    )
    if not user.is_superuser:
        queryset = queryset.filter(
            Q(chat_session__created_by=user)
            | Q(chat_session__chatbot__created_by=user),
        )
    return queryset


class ChatEscalationListView(PaginatedListMixin, GenericAPIView):
    """List queries the chatbot could not answer."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatEscalationListSerializer

    def get_queryset(self):
        queryset = visible_escalations(self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Escalations fetched successfully.",
        )


class ChatEscalationResolveView(GenericAPIView):
    """Mark an escalation resolved.

    Only the chatbot owner or a superadmin may resolve escalations.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ChatEscalationResolveSerializer

    def get_queryset(self):
        if self.request.user.is_superuser:
            return ChatEscalation.objects.all()
        return ChatEscalation.objects.filter(
            chat_session__chatbot__created_by=self.request.user,
        )

    def patch(self, request, *args, **kwargs):
        query_serializer = ChatEscalationQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        escalation = get_object_or_404(
            self.get_queryset(),
            pk=query_serializer.validated_data["escalation"],
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resolve_escalation(
            escalation,
            resolution_note=serializer.validated_data["resolution_note"],
            resolved_by=request.user,
        )
        return APIResponse.success(
            data=ChatEscalationListSerializer(escalation).data,
            message="Escalation resolved successfully.",
        )
