from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from chat.models import (
    ChatEscalation,
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
)


class ChatMessageAttachmentInline(admin.TabularInline):
    model = ChatMessageAttachment
    extra = 0
    fields = (
        "attachment_type",
        "file_name",
        "file_url_link",
        "mime_type",
        "file_size",
        "status",
    )
    readonly_fields = ("file_url_link",)

    @admin.display(description="File Link")
    def file_url_link(self, obj):
        if not obj.file_url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">View Resource</a>',
            obj.file_url,
        )


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "created_at",
        "sender_type",
        "content_preview",
        "response_time_ms",
    )
    readonly_fields = (
        "created_at",
        "sender_type",
        "content_preview",
        "response_time_ms",
    )

    @admin.display(description="Message")
    def content_preview(self, obj):
        if not obj.content:
            attachments = obj.attachments.count() if obj.pk else 0
            return format_html("<em>[{} attachment(s)]</em>", attachments)
        return (obj.content[:75] + "...") if len(obj.content) > 75 else obj.content


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id_short",
        "chatbot",
        "created_by",
        "status",
        "message_count",
        "last_activity_at",
        "created_at",
    )
    list_filter = ("status", "chatbot", "created_at")
    list_select_related = ("chatbot", "created_by")
    search_fields = (
        "id",
        "chatbot__chatbot_name",
        "created_by__email",
        "created_by__name",
    )
    readonly_fields = ("last_activity_at", "created_at", "updated_at")
    autocomplete_fields = ("chatbot", "created_by")
    inlines = [ChatMessageInline]

    @admin.display(description="Session")
    def id_short(self, obj):
        return str(obj.pk)[:8]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_message_count=Count("messages"))
        )

    @admin.display(description="Messages", ordering="_message_count")
    def message_count(self, obj):
        return obj._message_count


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = (
        "chat_session_link",
        "sender_badge",
        "message_preview",
        "attachment_count",
        "response_time_ms",
        "created_at",
    )
    list_filter = ("sender_type", "created_at")
    search_fields = ("content", "chat_session__id")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("chat_session",)
    inlines = [ChatMessageAttachmentInline]

    @admin.display(description="Session")
    def chat_session_link(self, obj):
        return str(obj.chat_session_id)[:8]

    @admin.display(description="Sender Type")
    def sender_badge(self, obj):
        colors = {
            "user": "#17a2b8",
            "agent": "#6f42c1",
        }
        color = colors.get(obj.sender_type, "#000000")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_sender_type_display(),
        )

    @admin.display(description="Content")
    def message_preview(self, obj):
        return (obj.content[:90] + "...") if len(obj.content) > 90 else obj.content

    @admin.display(description="Attachments")
    def attachment_count(self, obj):
        return obj.attachments.count()


@admin.register(ChatEscalation)
class ChatEscalationAdmin(admin.ModelAdmin):
    list_display = (
        "question_preview",
        "chatbot",
        "reason",
        "status",
        "created_at",
    )
    list_filter = ("reason", "status", "created_at")
    list_select_related = ("chat_session__chatbot",)
    search_fields = ("question", "chat_session__chatbot__chatbot_name")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Question")
    def question_preview(self, obj):
        return (obj.question[:90] + "...") if len(obj.question) > 90 else obj.question

    @admin.display(description="Chatbot")
    def chatbot(self, obj):
        return obj.chat_session.chatbot.chatbot_name
