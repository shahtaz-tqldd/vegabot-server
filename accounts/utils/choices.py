from django.db import models
from django.utils.translation import gettext_lazy as _

class AccountStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    SUSPENDED = "SUSPENDED", _("Suspended")
    DEACTIVATED = "DEACTIVATED", _("Deactivated")
    PREMIUM = "PREMIUM", _("Premium")


class AccountProvider(models.TextChoices):
    PASSWORD = "password", _("Password")
    GOOGLE = "google", _("Google")
