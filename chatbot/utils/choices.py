from django.db import models


class ChatbotStatusTypes(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    DISABLED_BY_ADMIN = "disabled_by_admin", "Disabled by Admin"


class ChatbotToneTypes(models.TextChoices):
    PROFESSIONAL = "professional", "Professional"
    FRIENDLY = "friendly", "Friendly"
    CASUAL = "casual", "Casual"
    FORMAL = "formal", "Formal"
    WITTY = "witty", "Witty"
