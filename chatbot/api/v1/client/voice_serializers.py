from rest_framework import serializers

from chatbot.utils.voices import (
    SpeakingPace,
    VoiceGender,
    VoiceType,
    prebuilt_voice,
)


class VoicePreviewQuerySerializer(serializers.Serializer):
    voice = serializers.CharField(max_length=40)

    def validate_voice(self, value):
        entry = prebuilt_voice(value)
        if entry is None:
            raise serializers.ValidationError("Unknown prebuilt voice.")
        return entry["name"]


class CustomVoiceQuerySerializer(serializers.Serializer):
    voice = serializers.UUIDField()


class CustomVoiceCreateSerializer(serializers.Serializer):
    """Multipart upload of a custom voice: a 10-60 s voice sample and the
    owner reading the consent statement, both as WAV files."""

    name = serializers.CharField(max_length=60)
    gender = serializers.ChoiceField(choices=VoiceGender.choices)
    sample_audio = serializers.FileField()
    consent_audio = serializers.FileField()
    consent_confirmed = serializers.BooleanField()

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field is required.")
        return value

    def validate_consent_confirmed(self, value):
        if not value:
            raise serializers.ValidationError(
                "Confirm that the voice owner recorded the consent statement "
                "and agrees to the voice being replicated."
            )
        return value


class VoiceSelectSerializer(serializers.Serializer):
    """Choose the chatbot's current voice (and optionally its pace)."""

    voice_type = serializers.ChoiceField(choices=VoiceType.choices)
    voice_name = serializers.CharField(max_length=40, required=False)
    custom_voice = serializers.UUIDField(required=False)
    speaking_pace = serializers.ChoiceField(
        choices=SpeakingPace.choices, required=False,
    )

    def validate(self, attrs):
        chatbot = self.context["chatbot"]
        if attrs["voice_type"] == VoiceType.PREBUILT:
            entry = prebuilt_voice(attrs.get("voice_name"))
            if entry is None:
                raise serializers.ValidationError(
                    {"voice_name": "Choose one of the listed prebuilt voices."}
                )
            attrs["voice_name"] = entry["name"]
        else:
            voice_id = attrs.get("custom_voice")
            voice = (
                chatbot.custom_voices.filter(pk=voice_id).first()
                if voice_id
                else None
            )
            if voice is None:
                raise serializers.ValidationError(
                    {"custom_voice": "Choose one of this chatbot's custom voices."}
                )
            attrs["custom_voice"] = voice
        return attrs
