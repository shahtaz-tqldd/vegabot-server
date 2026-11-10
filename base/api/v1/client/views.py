from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.serializers import ValidationError

from app.services.r2 import R2Storage, extract_key, upload_file
from app.utils.response import APIResponse
from base.api.v1.client.serializers import (
    FileDeleteSerializer,
    FileRetrieveSerializer,
    FileUploadSerializer,
)
from base.api.v1.utils import (
    CONFIG_SECTIONS,
    serialize_config_sections,
)

from base.models import VegabotConfig


def get_config():
    config = VegabotConfig.objects.first()
    if config is None:
        config = VegabotConfig.objects.create()
    return config


def storage_key_prefixes():
    prefixes = [
        (settings.R2_FILES_PREFIX or "").strip("/"),
        (settings.R2_IMAGES_PREFIX or "").strip("/"),
    ]
    return [prefix for prefix in prefixes if prefix]


def resolve_file_key(*, key=None, url=None):
    resolved_key = key or extract_key(url)
    if not resolved_key:
        raise ValidationError(
            {"url": ["URL does not belong to this application's storage."]}
        )
    prefixes = storage_key_prefixes()
    if prefixes and not any(
        resolved_key == prefix or resolved_key.startswith(f"{prefix}/")
        for prefix in prefixes
    ):
        raise ValidationError({"key": ["This file key is not permitted."]})
    return resolved_key


class VegabotConfigAPIView(GenericAPIView):
    """Return all configuration sections or the sections selected by query param."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        selected_sections = [
            section for section in CONFIG_SECTIONS if section in request.query_params
        ]
        if not selected_sections:
            selected_sections = list(CONFIG_SECTIONS)

        config = get_config()
        return APIResponse.success(
            data=serialize_config_sections(config, selected_sections),
            message="Configuration fetched successfully.",
        )


class RetrieveFileAPIView(GenericAPIView):
    """Return a short-lived presigned URL for a stored file."""

    permission_classes = [IsAuthenticated]
    serializer_class = FileRetrieveSerializer

    def get(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        key = resolve_file_key(
            key=serializer.validated_data.get("key"),
            url=serializer.validated_data.get("url"),
        )
        try:
            download_file_name = None
            if serializer.validated_data.get("download"):
                download_file_name = (
                    serializer.validated_data.get("file_name")
                    or Path(key).name
                )
            url = R2Storage().private_url(
                key,
                download_file_name=download_file_name,
            )
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={"key": key, "url": url},
            message="File fetched successfully.",
        )


class UploadFileAPIView(GenericAPIView):
    """Upload a file to storage and return its permanent URL and key."""

    permission_classes = [AllowAny]
    serializer_class = FileUploadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded_file = serializer.validated_data["file"]
        try:
            upload = upload_file(uploaded_file)
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={
                "file_url": upload["url"],
                "key": upload["key"],
                "file_name": Path(uploaded_file.name).name,
                "content_type": upload["content_type"],
                "file_size": uploaded_file.size,
            },
            message="File uploaded successfully.",
            status=status.HTTP_201_CREATED,
        )


class DeleteFileAPIView(GenericAPIView):
    """Delete a stored file by key or URL."""

    permission_classes = [IsAuthenticated]
    serializer_class = FileDeleteSerializer

    def delete(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data or request.query_params)
        serializer.is_valid(raise_exception=True)
        key = resolve_file_key(
            key=serializer.validated_data.get("key"),
            url=serializer.validated_data.get("url"),
        )
        try:
            R2Storage().delete(key)
        except ImproperlyConfigured:
            return APIResponse.error(
                message="File storage is not configured.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={"key": key},
            message="File deleted successfully.",
        )
