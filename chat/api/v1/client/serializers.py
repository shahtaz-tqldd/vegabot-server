from rest_framework import serializers

from chat.models import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    ChatEscalation,
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
)
from chatbot.models import Chatbot


class ChatSessionCreateSerializer(serializers.Serializer):
    """Start a session on a chatbot the user owns or an admin demo chatbot."""

    chatbot = serializers.SlugField()

    def validate_chatbot(self, value):
        request = self.context["request"]
        chatbot = (
            Chatbot.objects.accessible_to(request.user).filter(slug=value).first()
        )
        if chatbot is None:
            raise serializers.ValidationError(
                "Chatbot not found or not accessible."
            )
        return chatbot


class ChatSessionQuerySerializer(serializers.Serializer):
    session = serializers.UUIDField()


class ChatMessageAttachmentSerializer(serializers.ModelSerializer):
    text_preview = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessageAttachment
        fields = (
            "id",
            "attachment_type",
            "file_url",
            "file_name",
            "mime_type",
            "file_size",
            "status",
            "text_preview",
            "created_at",
        )
        read_only_fields = fields

    def get_text_preview(self, obj):
        return obj.extracted_text[:200]


class ChatMessageCreateSerializer(serializers.Serializer):
    session = serializers.UUIDField()
    content = serializers.CharField(
        max_length=8000,
        trim_whitespace=False,
        required=False,
        allow_blank=True,
        default="",
    )
    attachments = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        default=list,
        max_length=MAX_ATTACHMENTS_PER_MESSAGE,
    )

    def validate_content(self, value):
        if value and not value.strip():
            raise serializers.ValidationError("Message content cannot be blank.")
        return value

    def validate(self, attrs):
        if not attrs["content"] and not attrs["attachments"]:
            raise serializers.ValidationError(
                {"content": "Provide a message or at least one attachment."}
            )
        return attrs


class ChatSessionChatbotSerializer(serializers.Serializer):
    """Brief chatbot reference embedded in session payloads."""

    slug = serializers.SlugField(read_only=True)
    chatbot_name = serializers.CharField(read_only=True)
    tone = serializers.CharField(read_only=True)

    class Meta:
        model = Chatbot
        fields = ("slug", "chatbot_name", "tone")
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    attachments = ChatMessageAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "sender_type",
            "content",
            "attachments",
            "response_time_ms",
            "created_at",
        )
        read_only_fields = fields


class ChatSessionListSerializer(serializers.ModelSerializer):
    """Compact session summaries returned by the session list endpoint."""

    chatbot = ChatSessionChatbotSerializer(read_only=True)
    message_count = serializers.IntegerField(read_only=True, default=0)
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot",
            "status",
            "message_count",
            "last_message",
            "last_activity_at",
            "created_at",
        )
        read_only_fields = fields

    def get_last_message(self, obj):
        content = getattr(obj, "last_message_content", None)
        if content is None:
            return None
        return {
            "sender_type": getattr(obj, "last_message_sender_type", ""),
            "content": content[:120],
            "created_at": getattr(obj, "last_message_created_at", None),
        }


class ChatSessionDetailSerializer(serializers.ModelSerializer):
    """A session with its full message timeline."""

    chatbot = ChatSessionChatbotSerializer(read_only=True)
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot",
            "status",
            "messages",
            "last_activity_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatEscalationListSerializer(serializers.ModelSerializer):
    """An unanswered query stored for human follow-up."""

    session = serializers.UUIDField(source="chat_session_id", read_only=True)
    message = serializers.UUIDField(
        source="chat_message_id", read_only=True, allow_null=True,
    )
    chatbot = ChatSessionChatbotSerializer(read_only=True)

    class Meta:
        model = ChatEscalation
        fields = (
            "id",
            "session",
            "message",
            "chatbot",
            "question",
            "reason",
            "status",
            "resolution_note",
            "created_at",
        )
        read_only_fields = fields


class ChatEscalationQuerySerializer(serializers.Serializer):
    escalation = serializers.UUIDField()


class ChatEscalationResolveSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=True,
        default="",
    )
