from django.contrib import admin

from chatbot.models import Chatbot


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
