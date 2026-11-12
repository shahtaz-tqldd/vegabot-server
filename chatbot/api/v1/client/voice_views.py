"""Voice APIs: prebuilt catalog and previews, custom voices, selection."""

import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from app.constants import (
    CUSTOM_VOICE_LIVE_SAMPLE_SECONDS,
    CUSTOM_VOICE_MAX_CONSENT_SECONDS,
    CUSTOM_VOICE_MAX_SAMPLE_SECONDS,
    CUSTOM_VOICE_MAX_UPLOAD_MB,
    CUSTOM_VOICE_MIN_SAMPLE_SECONDS,
    MAX_CUSTOM_VOICES_PER_CHATBOT,
)
from app.services.r2 import R2Storage
from app.utils.response import APIResponse
from chatbot.api.v1.client.voice_serializers import (
    CustomVoiceCreateSerializer,
    CustomVoiceQuerySerializer,
    VoicePreviewQuerySerializer,
    VoiceSelectSerializer,
)
from chatbot.api.v1.utils import ChatbotObjectMixin, validation_error_response
from chatbot.models import Chatbot
from chatbot.services.voices import (
    VoiceError,
    create_custom_voice,
    custom_voice_payload,
    delete_custom_voice,
    preview_urls,
    prebuilt_voice_payload,
    select_voice,
    selected_voice,
    voice_preview_url,
)
from chatbot.utils.voices import (
    CONSENT_STATEMENT,
    PREBUILT_VOICES,
    SAMPLE_SCRIPT,
    SpeakingPace,
    VoiceGender,
)

logger = logging.getLogger(__name__)


def _choices(choices):
    return [{"value": value, "label": label} for value, label in choices.choices]


class ManagedChatbotMixin(ChatbotObjectMixin):
    """Voices can only be changed on chatbots the user created."""

    def get_chatbot_queryset(self):
        return Chatbot.objects.filter(
            is_deleted=False, created_by=self.request.user,
        )


class VoiceOptionsView(GenericAPIView):
    """Everything a voice picker needs: the 10 prebuilt voices with gender,
    style and preview link, the pace options, and the custom voice
    recording requirements."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        previews = preview_urls()
        return APIResponse.success(
            data={
                "prebuilt_voices": [
                    prebuilt_voice_payload(entry, previews)
                    for entry in PREBUILT_VOICES
                ],
                "speaking_paces": _choices(SpeakingPace),
                "genders": _choices(VoiceGender),
                "custom_voice": {
                    "consent_statement": CONSENT_STATEMENT,
                    "sample_script": SAMPLE_SCRIPT,
                    "format": "WAV (PCM); converted to 24 kHz mono 16-bit",
                    "sample_min_seconds": CUSTOM_VOICE_MIN_SAMPLE_SECONDS,
                    "sample_max_seconds": CUSTOM_VOICE_MAX_SAMPLE_SECONDS,
                    "sample_used_seconds": CUSTOM_VOICE_LIVE_SAMPLE_SECONDS,
                    "consent_max_seconds": CUSTOM_VOICE_MAX_CONSENT_SECONDS,
                    "max_upload_mb": CUSTOM_VOICE_MAX_UPLOAD_MB,
                    "max_per_chatbot": MAX_CUSTOM_VOICES_PER_CHATBOT,
                },
            },
            message="Voice options fetched successfully.",
        )


class VoicePreviewView(GenericAPIView):
    """A listening sample of a prebuilt voice. Generated with the Live
    model on first request (a few seconds), then served from storage."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        query = VoicePreviewQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return validation_error_response(query.errors, "Invalid voice.")
        voice_name = query.validated_data["voice"]
        try:
            url = voice_preview_url(voice_name)
        except Exception:
            logger.exception("Voice preview failed for %s", voice_name)
            return APIResponse.error(
                errors={"voice": "The preview could not be generated."},
                message="The preview could not be generated. Try again shortly.",
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return APIResponse.success(
            data={"voice": voice_name, "preview_url": url},
            message="Voice preview fetched successfully.",
        )


class ChatbotVoicesView(ChatbotObjectMixin, GenericAPIView):
    """The chatbot's current voice, pace, and custom voices.

    Visible on the user's own chatbots and demo chatbots; sample links of
    custom voices are only included for the chatbot's owner.
    """

    permission_classes = [IsAuthenticated]

    def get_chatbot_queryset(self):
        return Chatbot.objects.filter(is_deleted=False).filter(
            Q(created_by=self.request.user) | Q(created_by__is_staff=True),
        ).select_related("custom_voice")

    def get(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        is_owner = chatbot.created_by_id == request.user.pk
        storage = R2Storage() if is_owner else None
        return APIResponse.success(
            data={
                "selected": selected_voice(chatbot),
                "speaking_pace": chatbot.speaking_pace,
                "custom_voices": [
                    custom_voice_payload(
                        voice, include_sample=is_owner, storage=storage,
                    )
                    | {"is_selected": voice.pk == chatbot.custom_voice_id}
                    for voice in chatbot.custom_voices.all()
                ],
            },
            message="Chatbot voices fetched successfully.",
        )


class CustomVoiceCreateView(ManagedChatbotMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomVoiceCreateSerializer
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Custom voice creation failed.",
            )
        data = serializer.validated_data
        try:
            voice = create_custom_voice(
                chatbot=chatbot,
                name=data["name"],
                gender=data["gender"],
                sample_audio=data["sample_audio"],
                consent_audio=data["consent_audio"],
                user=request.user,
            )
        except VoiceError as exc:
            return validation_error_response(
                {"error": str(exc)}, "Custom voice creation failed.",
            )
        return APIResponse.success(
            data=custom_voice_payload(voice, include_sample=True),
            message="Custom voice created successfully.",
            status=status.HTTP_201_CREATED,
        )


class CustomVoiceDeleteView(ManagedChatbotMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        query = CustomVoiceQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return validation_error_response(query.errors, "Invalid voice.")
        voice = get_object_or_404(
            chatbot.custom_voices, pk=query.validated_data["voice"],
        )
        delete_custom_voice(voice)
        return APIResponse.success(message="Custom voice deleted successfully.")


class VoiceSelectView(ManagedChatbotMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = VoiceSelectSerializer
    parser_classes = [JSONParser, FormParser]

    def patch(self, request, *args, **kwargs):
        chatbot = self.get_chatbot()
        serializer = self.get_serializer(
            data=request.data, context={"chatbot": chatbot, "request": request},
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Voice selection failed.",
            )
        data = serializer.validated_data
        select_voice(
            chatbot,
            voice_type=data["voice_type"],
            voice_name=data.get("voice_name", ""),
            custom_voice=data.get("custom_voice"),
            user=request.user,
        )
        if "speaking_pace" in data:
            chatbot.speaking_pace = data["speaking_pace"]
            chatbot.save(update_fields=["speaking_pace", "updated_at"])
        return APIResponse.success(
            data={
                "selected": selected_voice(chatbot),
                "speaking_pace": chatbot.speaking_pace,
            },
            message="Voice selected successfully.",
        )
