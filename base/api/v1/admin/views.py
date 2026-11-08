from django.db import transaction
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.utils.permission import IsSuperAdmin
from app.utils.response import APIResponse

from base.api.v1.utils import (
    CONFIG_SECTIONS,
    serialize_config_sections,
)
from base.api.v1.admin.serializers import DisonConfigUpdateSerializer
from base.models import DisonConfig


def get_config():
    config = DisonConfig.objects.first()
    if config is None:
        config = DisonConfig.objects.create()
    return config


class DisonConfigUpdateAPIView(GenericAPIView):
    """Update the singleton configuration, including multipart logo uploads."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = DisonConfigUpdateSerializer

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        config = get_config()
        serializer = self.get_serializer(
            config,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        return APIResponse.success(
            data=serialize_config_sections(config, list(CONFIG_SECTIONS)),
            message="Configuration updated successfully.",
        )
