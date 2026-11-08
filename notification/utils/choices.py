from django.db import models


class NotificationType(models.TextChoices):
    TRAINING_COMPLETE = "training_complete", "Training complete"
