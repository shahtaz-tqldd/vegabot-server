from django.contrib import admin

from notification.models import Notification, NotificationRead


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "chatbot", "notification_type", "created_at")
    list_filter = ("notification_type", "created_at")
    search_fields = (
        "title",
        "message",
        "chatbot__chatbot_name",
    )
    autocomplete_fields = ("chatbot",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationRead)
class NotificationReadAdmin(admin.ModelAdmin):
    list_display = ("notification", "user", "read_at")
    search_fields = ("notification__title", "user__email")
    autocomplete_fields = ("notification", "user")
