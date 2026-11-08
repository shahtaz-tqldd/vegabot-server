from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import EmailVerificationOTP, User, UserProfile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-created_at",)
    list_display = (
        "email",
        "name",
        "provider",
        "account_status",
        "is_email_verified",
        "is_active",
        "last_active",
        "deleted_at",
        "is_staff",
        "is_superuser",
    )
    list_filter = (
        "provider",
        "profile__status",
        "is_staff",
        "is_superuser",
        "is_email_verified",
    )
    search_fields = ("email", "name", "profile__phone", "firebase_uid")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "last_login",
        "last_active",
        "deleted_at",
    )

    fieldsets = (
        ("Credentials", {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name",)}),
        (
            "Auth provider",
            {
                "fields": (
                    "provider",
                    "firebase_uid",
                    "firebase_id_token",
                    "google_access_token",
                )
            },
        ),
        (
            "Access",
            {
                "fields": (
                    "is_active",
                    "is_email_verified",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "last_active",
                    "deleted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "name", "password1", "password2"),
            },
        ),
    )

    @admin.display(description="Status", ordering="profile__status")
    def account_status(self, obj):
        return obj.profile.status


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "phone",
        "status",
        "timezone",
    )
    list_filter = (
        "status",
        "timezone",
    )
    search_fields = ("user__email", "city", "country")
    autocomplete_fields = ("user",)


@admin.register(EmailVerificationOTP)
class EmailVerificationOTPAdmin(admin.ModelAdmin):
    list_display = ("user", "expires_at", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("code_hash", "created_at", "updated_at")
