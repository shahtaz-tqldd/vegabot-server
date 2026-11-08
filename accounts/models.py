import uuid

from django.apps import apps
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.utils.choices import (
    AccountProvider,
    AccountStatus,
)
from app.core.models import BaseMinModel
from app.utils.validators import validate_timezone_name

phone_regex = RegexValidator(
    regex=r"^\+?\d{6,15}$",
    message=_("Phone number must be between 6 and 15 digits and may start with '+'."),
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    @transaction.atomic
    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email field is required.")

        profile_status = extra_fields.pop("status", AccountStatus.ACTIVE)
        email = self.normalize_email(email).strip().casefold()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        apps.get_model("accounts", "UserProfile").objects.create(
            user=user,
            status=profile_status,
        )

        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", AccountStatus.ACTIVE)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", AccountStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, verbose_name=_("Email address"))
    name = models.CharField(max_length=50, blank=True, verbose_name=_("Full Name"))
    is_email_verified = models.BooleanField(
        default=False, verbose_name=_("Email verified")
    )
    provider = models.CharField(
        max_length=20,
        choices=AccountProvider.choices,
        default=AccountProvider.PASSWORD,
        verbose_name=_("Auth provider"),
    )
    firebase_uid = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        null=True,
        verbose_name=_("Firebase UID"),
    )
    firebase_id_token = models.TextField(
        blank=True, verbose_name=_("Firebase ID token")
    )
    google_access_token = models.TextField(
        blank=True, verbose_name=_("Google access token")
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Active"))
    is_staff = models.BooleanField(default=False, verbose_name=_("Staff status"))
    last_active = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last active"),
    )
    deleted_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_("Deleted at")
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated at"))

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or self.email

    @transaction.atomic
    def mark_deleted(self):
        profile, _ = UserProfile.objects.get_or_create(user=self)
        profile.status = AccountStatus.DEACTIVATED
        self.is_active = False
        self.deleted_at = timezone.now()
        profile.save(update_fields=["status"])
        self.save(update_fields=["is_active", "deleted_at", "updated_at"])

    @transaction.atomic
    def reactivate(self):
        profile, _ = UserProfile.objects.get_or_create(user=self)
        profile.status = AccountStatus.ACTIVE
        self.is_active = True
        self.deleted_at = None
        profile.save(update_fields=["status"])
        self.save(update_fields=["is_active", "deleted_at", "updated_at"])


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        validators=[phone_regex],
    )
    avatar_url = models.URLField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_timezone_name],
        help_text="IANA timezone for localized activity, for example Asia/Dhaka.",
    )
    status = models.CharField(
        max_length=16,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        verbose_name=_("Account status"),
    )

    class Meta:
        verbose_name = _("User profile")
        verbose_name_plural = _("User profiles")
        ordering = ["user__created_at"]

    def __str__(self):
        return self.user.email

    @property
    def location(self):
        return ", ".join(filter(None, [self.city, self.country]))


class EmailVerificationOTP(BaseMinModel):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="email_verification_otp",
    )
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = _("Email verification OTP")
        verbose_name_plural = _("Email verification OTPs")

    def __str__(self):
        return f"Email verification OTP for {self.user.email}"
