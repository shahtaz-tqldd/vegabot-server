from django.contrib import admin

from base.models import DisonConfig


@admin.register(DisonConfig)
class DisonConfigAdmin(admin.ModelAdmin):
    list_display = (
        "maintenance_mode",
        "notify_banner_enabled",
        "updated_at",
    )
    readonly_fields = ("updated_by", "created_at", "updated_at")
