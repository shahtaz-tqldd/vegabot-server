from django.contrib import admin

from analytics.models import AIUsage


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot_name",
        "usage_type",
        "model",
        "tokens",
        "cost",
        "created_at",
    )
    list_filter = ("usage_type", "model", "created_at")
    search_fields = ("chatbot__chatbot_name", "chat_session__id")

    # Usage records are immutable financial data.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Chatbot")
    def chatbot_name(self, obj):
        return obj.chatbot.chatbot_name if obj.chatbot_id else obj.chatbot_id_snapshot
