from django.contrib import admin

from chatbot.models import Chatbot, ChatbotVoice, VoicePreview


@admin.register(Chatbot)
class ChatbotAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot_name",
        "business_type",
        "status",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("chatbot_name",)
    readonly_fields = ("id", "slug", "created_at", "updated_at")


@admin.register(ChatbotVoice)
class ChatbotVoiceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "gender",
        "chatbot",
        "sample_duration_seconds",
        "created_by",
        "created_at",
    )
    list_filter = ("gender", "created_at")
    search_fields = ("name", "chatbot__chatbot_name")
    raw_id_fields = ("chatbot",)
    readonly_fields = (
        "sample_audio_key",
        "consent_audio_key",
        "consent_statement",
        "created_at",
        "updated_at",
    )


@admin.register(VoicePreview)
class VoicePreviewAdmin(admin.ModelAdmin):
    list_display = ("voice_name", "audio_url", "created_at")
