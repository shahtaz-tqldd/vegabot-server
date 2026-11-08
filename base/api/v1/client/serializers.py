from django.conf import settings
from rest_framework import serializers


class FileUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, file):
        max_bytes = settings.BASE_FILE_MAX_SIZE_MB * 1024 * 1024
        if file.size > max_bytes:
            raise serializers.ValidationError(
                f"File cannot exceed {settings.BASE_FILE_MAX_SIZE_MB} MB."
            )
        return file


class FileRetrieveSerializer(serializers.Serializer):
    key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1024,
    )
    url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=2048,
    )
    download = serializers.BooleanField(required=False)
    file_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
    )

    def validate(self, attrs):
        if bool(attrs.get("key")) == bool(attrs.get("url")):
            raise serializers.ValidationError(
                {"key": ["Provide exactly one of 'key' or 'url'."]}
            )
        return attrs


class FileDeleteSerializer(serializers.Serializer):
    key = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1024,
    )
    url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=2048,
    )

    def validate(self, attrs):
        if not attrs.get("key") and not attrs.get("url"):
            raise serializers.ValidationError(
                {"key": ["Either 'key' or 'url' is required."]}
            )
        return attrs
