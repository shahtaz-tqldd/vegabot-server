from django.contrib import admin

from knowledge.models import (
    KnowledgeBase,
    KnowledgeTrainingLog,
    Product,
    Service,
)


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("name", "chatbot", "source_type", "status", "created_at")
    list_filter = ("source_type", "status", "created_at")
    search_fields = ("title", "url", "original_filename", "chatbot__chatbot_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "chatbot", "sku", "category", "price", "currency", "status")
    list_filter = ("status", "created_at")
    search_fields = ("name", "sku", "category", "chatbot__chatbot_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "chatbot", "service_type", "cost", "currency", "status")
    list_filter = ("status", "created_at")
    search_fields = ("name", "service_type", "chatbot__chatbot_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(KnowledgeTrainingLog)
class KnowledgeTrainingLogAdmin(admin.ModelAdmin):
    list_display = (
        "chatbot",
        "space",
        "stage",
        "progress",
        "total_chunks",
        "processed_chunks",
        "created_at",
    )
    list_filter = ("space", "stage", "created_at")
    search_fields = ("chatbot__chatbot_name",)
    readonly_fields = ("created_at", "updated_at")
