from django.contrib import admin

from base.models import VegabotConfig


@admin.register(VegabotConfig)
class VegabotConfigAdmin(admin.ModelAdmin):
    list_display = (
        "maintenance_mode",
        "notify_banner_enabled",
        "updated_at",
    )
    readonly_fields = ("updated_by", "created_at", "updated_at")
