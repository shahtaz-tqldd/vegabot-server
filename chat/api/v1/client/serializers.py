from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from chat.models import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    ChatCartItem,
    ChatEscalation,
    ChatMessage,
    ChatMessageAttachment,
    ChatOrder,
    ChatSession,
)
from chat.services.commerce import session_activity_counts
from chat.utils.choices import ChatMessageSenderType
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
    """One message. Agent messages carry the token usage and estimated
    cost of generating them (``usage``); user messages have ``null``
    (the user's input tokens are counted in the reply's usage)."""

    attachments = ChatMessageAttachmentSerializer(many=True, read_only=True)
    usage = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "sender_type",
            "mode",
            "content",
            "attachments",
            "recommendations",
            "response_time_ms",
            "usage",
            "created_at",
        )
        read_only_fields = fields

    def get_usage(self, obj):
        if obj.sender_type != ChatMessageSenderType.AGENT:
            return None
        try:
            usage = obj.ai_usage
        except ObjectDoesNotExist:
            return None
        return {
            "total_tokens": usage.tokens,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "thinking_tokens": usage.thinking_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "audio_input_tokens": usage.audio_input_tokens,
            "audio_output_tokens": usage.audio_output_tokens,
            "audio_input_seconds": usage.audio_input_seconds,
            "audio_output_seconds": usage.audio_output_seconds,
            "cost": float(usage.cost),
            "model": usage.model,
        }


# Kept for existing imports; every message payload now includes usage.
ChatMessageUsageSerializer = ChatMessageSerializer


class ChatSessionListSerializer(serializers.ModelSerializer):
    """Compact session summaries returned by the session list endpoint."""

    chatbot = ChatSessionChatbotSerializer(read_only=True)
    message_count = serializers.IntegerField(read_only=True, default=0)
    last_message = serializers.SerializerMethodField()
    total_tokens = serializers.IntegerField(read_only=True, default=0)
    total_cost = serializers.SerializerMethodField()
    avg_response_time_ms = serializers.SerializerMethodField()
    cart_item_count = serializers.IntegerField(read_only=True, default=0)
    purchased_item_count = serializers.IntegerField(read_only=True, default=0)
    order_count = serializers.IntegerField(read_only=True, default=0)
    escalation_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot",
            "status",
            "message_count",
            "cart_item_count",
            "purchased_item_count",
            "order_count",
            "escalation_count",
            "last_message",
            "total_tokens",
            "total_cost",
            "avg_response_time_ms",
            "last_activity_at",
            "created_at",
        )
        read_only_fields = fields

    def get_total_cost(self, obj):
        return float(getattr(obj, "total_cost", None) or 0)

    def get_avg_response_time_ms(self, obj):
        value = getattr(obj, "avg_response_time_ms", None)
        return round(value) if value is not None else None

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

    def to_representation(self, instance):
        """Adds the session's activity counts (same keys as the list)."""
        data = super().to_representation(instance)
        data.update(session_activity_counts(instance.pk))
        return data


class ChatCartItemSerializer(serializers.ModelSerializer):
    """One product the agent added to a session's cart."""

    session = serializers.UUIDField(source="chat_session_id", read_only=True)
    product = serializers.UUIDField(
        source="product_id", read_only=True, allow_null=True,
    )
    order = serializers.UUIDField(
        source="order_id", read_only=True, allow_null=True,
    )
    line_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True,
    )

    class Meta:
        model = ChatCartItem
        fields = (
            "id",
            "session",
            "product",
            "order",
            "product_name",
            "sku",
            "product_url",
            "image_url",
            "unit_price",
            "currency",
            "quantity",
            "line_total",
            "status",
            "added_to_cart",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatOrderSerializer(serializers.ModelSerializer):
    """A confirmed order with the products it contains."""

    session = serializers.UUIDField(source="chat_session_id", read_only=True)
    items = ChatCartItemSerializer(many=True, read_only=True)

    class Meta:
        model = ChatOrder
        fields = (
            "id",
            "session",
            "order_number",
            "status",
            "item_count",
            "total_amount",
            "currency",
            "items",
            "created_at",
        )
        read_only_fields = fields


class ChatEscalationListSerializer(serializers.ModelSerializer):
    """An unanswered query stored for human follow-up."""

    session = serializers.UUIDField(source="chat_session_id", read_only=True)
    message = serializers.UUIDField(
        source="chat_message_id", read_only=True, allow_null=True,
    )
    chatbot = ChatSessionChatbotSerializer(
        source="chat_session.chatbot", read_only=True,
    )

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
