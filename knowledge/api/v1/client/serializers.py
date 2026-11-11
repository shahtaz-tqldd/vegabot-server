from rest_framework import serializers

from knowledge.models import KnowledgeBase, KnowledgeTrainingLog, Product, Service
from knowledge.utils.choices import KnowledgeSpaceTypes
from knowledge.services.catalog import VALID_CURRENCIES

MAX_TEXT_LENGTH = 200_000


class ChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class ChatbotScopeMixin(serializers.Serializer):
    """Validate a chatbot slug the user may manage knowledge for."""

    chatbot = serializers.SlugField()

    def validate_chatbot(self, value):
        from chatbot.models import Chatbot

        request = self.context["request"]
        queryset = Chatbot.objects.filter(is_deleted=False)
        if not request.user.is_superuser:
            queryset = queryset.filter(created_by=request.user)
        chatbot = queryset.filter(slug=value).first()
        if chatbot is None:
            raise serializers.ValidationError(
                "Chatbot not found or not manageable by you."
            )
        return chatbot


class KnowledgeDocumentUploadSerializer(ChatbotScopeMixin):
    title = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    file = serializers.FileField()

    def validate_file(self, file_obj):
        from knowledge.services.validation import validate_knowledge_file

        return validate_knowledge_file(file_obj)


class KnowledgeDocumentWebsiteSerializer(ChatbotScopeMixin):
    title = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    url = serializers.URLField()

    def validate_url(self, value):
        from knowledge.services.validation import validate_public_url

        return validate_public_url(value)


class KnowledgeDocumentTextSerializer(ChatbotScopeMixin):
    title = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    content = serializers.CharField(
        max_length=MAX_TEXT_LENGTH,
        allow_blank=False,
        trim_whitespace=False,
    )

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Content cannot be empty.")
        return value


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = KnowledgeBase
        fields = (
            "id",
            "name",
            "title",
            "source_type",
            "status",
            "is_enabled",
            "url",
            "original_filename",
            "file_type",
            "file_size",
            "last_crawled_at",
            "error_message",
            "processed_at",
            "created_at",
        )
        read_only_fields = fields


class KnowledgeDocumentQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()
    document = serializers.UUIDField()


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "sku",
            "category",
            "description",
            "price",
            "currency",
            "product_url",
            "image_url",
            "status",
            "is_enabled",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "error_message", "created_at", "updated_at")


class ProductUploadSerializer(ChatbotScopeMixin):
    file = serializers.FileField()

    def validate_file(self, file_obj):
        if not file_obj.name or not file_obj.name.lower().endswith((".csv", ".xlsx")):
            raise serializers.ValidationError(
                "Upload the filled template as CSV or XLSX."
            )
        return file_obj


class ProductUpdateSerializer(ProductSerializer):
    def validate(self, attrs):
        sku = attrs.get("sku")
        if sku:
            conflict = Product.objects.filter(
                chatbot=self.instance.chatbot,
                sku__iexact=sku,
            ).exclude(pk=self.instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"sku": "A product with this SKU already exists."}
                )
        return attrs


class ProductDisableSerializer(ChatbotScopeMixin):
    products = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=200,
    )


class ProductBulkDeleteSerializer(ProductDisableSerializer):
    """Product IDs to delete permanently (with their vectors)."""


class ProductRetrainSerializer(ChatbotScopeMixin):
    """Products to retrain; omit ``products`` to retrain every failed or
    still-pending enabled product of the chatbot."""

    products = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=200,
        required=False,
    )


class CatalogQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()
    product = serializers.UUIDField(required=False)
    service = serializers.UUIDField(required=False)


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = (
            "id",
            "name",
            "service_type",
            "description",
            "cost",
            "currency",
            "status",
            "is_enabled",
            "error_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "status", "error_message", "created_at", "updated_at")


class ServiceCreateSerializer(ChatbotScopeMixin, ServiceSerializer):
    class Meta(ServiceSerializer.Meta):
        fields = ("chatbot", *ServiceSerializer.Meta.fields)
        read_only_fields = ServiceSerializer.Meta.read_only_fields


class ServiceBulkCreateSerializer(ChatbotScopeMixin):
    services = serializers.ListField(
        child=ServiceSerializer(),
        allow_empty=False,
        max_length=200,
    )


class ServiceUpdateSerializer(ServiceSerializer):
    pass


class ServiceUploadSerializer(ChatbotScopeMixin):
    file = serializers.FileField()

    def validate_file(self, file_obj):
        if not file_obj.name or not file_obj.name.lower().endswith((".csv", ".xlsx")):
            raise serializers.ValidationError(
                "Upload the filled template as CSV or XLSX."
            )
        return file_obj


class KnowledgeTrainSerializer(serializers.Serializer):
    force = serializers.BooleanField(required=False, default=False)


class TrainingLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeTrainingLog
        fields = (
            "id",
            "space",
            "stage",
            "progress",
            "total_chunks",
            "processed_chunks",
            "force_retrain",
            "content_changed",
            "message",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
        )
        read_only_fields = fields


class KnowledgeSearchSerializer(ChatbotScopeMixin):
    query = serializers.CharField(max_length=1000)
    source = serializers.ChoiceField(
        choices=KnowledgeSpaceTypes.choices,
        required=False,
        allow_null=True,
        default=None,
        help_text="Knowledge space to search: document (base), product, or service.",
    )
    limit = serializers.IntegerField(required=False, default=8, min_value=1, max_value=50)
