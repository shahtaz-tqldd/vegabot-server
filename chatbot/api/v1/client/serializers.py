from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from app.services.r2 import delete_image, schedule_delete_image, upload_image
from app.utils.storage_fields import R2ImageField
from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes

User = get_user_model()


class ChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class ChatbotOwnerSerializer(serializers.ModelSerializer):
    """Brief public representation of the user who owns a chatbot."""

    class Meta:
        model = User
        fields = ("id", "name", "email")
        read_only_fields = fields


class ChatbotBaseSerializer(serializers.ModelSerializer):
    logo = R2ImageField(required=False)
    clear_logo = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )

    class Meta:
        model = Chatbot
        fields = (
            "id",
            "chatbot_name",
            "business_type",
            "description",
            "slug",
            "tone",
            "welcome_message",
            "fallback_message",
            "instructions",
            "escalation_rule",
            "never_answer",
            "language",
            "timezone",
            "other_settings",
            "logo",
            "clear_logo",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "slug", "created_at", "updated_at")

    def validate_chatbot_name(self, value):
        chatbot_name = value.strip()
        if not chatbot_name:
            raise serializers.ValidationError("This field is required.")
        return chatbot_name

    # Client APIs may only manage these statuses; moderators use the
    # admin API for DISABLED_BY_ADMIN.
    allowed_statuses = (
        ChatbotStatusTypes.DRAFT,
        ChatbotStatusTypes.ACTIVE,
        ChatbotStatusTypes.DISABLED,
    )

    def validate(self, attrs):
        if attrs.get("logo") is not None and attrs.get("clear_logo"):
            raise serializers.ValidationError(
                {"clear_logo": "Cannot clear and replace the logo together."}
            )

        if "chatbot_name" in attrs:
            # Names only have to be unique per owner, so different users
            # (and admins) may reuse the same chatbot name.
            owner = (
                self.instance.created_by
                if self.instance is not None
                else self.context["request"].user
            )
            name_conflicts = Chatbot.objects.filter(
                created_by=owner,
                chatbot_name__iexact=attrs["chatbot_name"],
                is_deleted=False,
            )
            if self.instance is not None:
                name_conflicts = name_conflicts.exclude(pk=self.instance.pk)
            if name_conflicts.exists():
                raise serializers.ValidationError(
                    {
                        "chatbot_name": (
                            "You already have a chatbot with this name."
                        )
                    }
                )

        if "status" in attrs and attrs["status"] not in self.allowed_statuses:
            raise serializers.ValidationError(
                {"status": "This status cannot be set from the client API."}
            )
        return attrs

    def _upload_logo(self, logo, *, suffix=""):
        return upload_image(
            logo,
            folder=f"{settings.R2_IMAGES_PREFIX}/chatbots",
            public_id=f"chatbot-{suffix}{uuid4().hex}",
        )

    def create(self, validated_data):
        logo = validated_data.pop("logo", None)
        validated_data.pop("clear_logo", False)
        upload = None
        if logo is not None:
            upload = self._upload_logo(logo)
            validated_data["logo"] = upload["url"]
        validated_data["created_by"] = self.context["request"].user

        try:
            return Chatbot.objects.create(**validated_data)
        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise

    def update(self, instance, validated_data):
        logo = validated_data.pop("logo", None)
        clear_logo = validated_data.pop("clear_logo", False)
        previous_logo_url = instance.logo
        upload = None

        if logo is not None:
            upload = self._upload_logo(logo, suffix=f"{instance.pk}-")
            validated_data["logo"] = upload["url"]
        elif clear_logo:
            validated_data["logo"] = ""

        instance.updated_by = self.context["request"].user
        try:
            with transaction.atomic():
                for field, value in validated_data.items():
                    setattr(instance, field, value)
                update_fields = [
                    *validated_data.keys(),
                    "updated_by",
                    "updated_at",
                ]
                instance.save(update_fields=update_fields)
        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise

        if previous_logo_url and previous_logo_url != instance.logo:
            schedule_delete_image(image_url=previous_logo_url)
        return instance


class ChatbotListSerializer(ChatbotBaseSerializer):
    """Compact chatbot summaries returned by the list endpoints."""

    created_by = ChatbotOwnerSerializer(read_only=True)
    is_admin_created = serializers.SerializerMethodField()
    trained_knowledge = serializers.SerializerMethodField()

    class Meta(ChatbotBaseSerializer.Meta):
        fields = (
            "id",
            "chatbot_name",
            "business_type",
            "description",
            "slug",
            "tone",
            "logo",
            "status",
            "is_admin_created",
            "trained_knowledge",
            "created_by",
            "created_at",
        )
        read_only_fields = fields

    def get_is_admin_created(self, obj):
        return bool(obj.created_by and obj.created_by.is_staff)

    def get_trained_knowledge(self, obj):
        def _count(annotation, relation):
            value = getattr(obj, annotation, None)
            if value is None:
                value = getattr(obj, relation).count()
            return value

        return {
            "product_count": _count("product_count", "products"),
            "service_count": _count("service_count", "services"),
            "knowledge_source_count": _count(
                "knowledge_source_count", "knowledge_bases",
            ),
        }


class ChatbotCreateSerializer(ChatbotBaseSerializer):
    """Validate and serialize chatbot creation."""


class ChatbotDetailSerializer(ChatbotBaseSerializer):
    """Serialize the complete details of a chatbot."""

    created_by = ChatbotOwnerSerializer(read_only=True)

    class Meta(ChatbotBaseSerializer.Meta):
        fields = (
            *ChatbotBaseSerializer.Meta.fields,
            "created_by",
        )


class ChatbotUpdateSerializer(ChatbotBaseSerializer):
    """Validate and serialize chatbot updates."""


class ChatbotDeleteSerializer(serializers.Serializer):
    """Represent the body-less chatbot delete operation."""
