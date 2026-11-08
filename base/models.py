from django.db import models
from django.conf import settings
from app.utils.validators import validate_json_object


class DisonConfig(models.Model):
    maintenance_mode = models.BooleanField(default=False)
    text_llm_model = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="LLM model configuration in JSON format. Example: {'provider': 'google', 'model_name': 'gemini-3.5-flash-lite'}",
    )
    live_llm_model = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
        help_text="LLM model configuration in JSON format. Example: {'provider': 'google', 'model_name': 'gemini-3.5-flash-lite'}",
    )

    # Announcement
    notify_banner_enabled = models.BooleanField(default=False)
    notify_banner_text = models.CharField(max_length=500, blank=True)
    notify_banner_url = models.URLField(blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated_records",
    )

    class Meta:
        verbose_name = "dison Chatbot Configuration"
        verbose_name_plural = "dison Chatbot Configuration"

    def __str__(self):
        return "dison Chatbot Configuration"

    def save(self, *args, **kwargs):
        # Keep this model as a singleton
        if not self.pk and DisonConfig.objects.exists():
            raise ValueError("Only one DisonConfig instance is allowed.")

        super().save(*args, **kwargs)
