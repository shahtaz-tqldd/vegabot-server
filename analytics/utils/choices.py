from django.db import models


class AIUsageType(models.TextChoices):
    CHAT = "chat", "Chat"
