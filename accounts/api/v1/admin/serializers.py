from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.utils.choices import AccountStatus
from accounts.models import UserProfile
from app.services.r2 import delete_image, schedule_delete_image, upload_image

User = get_user_model()


def get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            email=attrs["email"].strip().casefold(),
            password=attrs["password"],
        )

        if not user:
            raise serializers.ValidationError({"error": "Invalid credentials."})
        if not user.is_active:
            raise serializers.ValidationError({"error": "Admin account is disabled."})
        if not user.is_staff:
            raise serializers.ValidationError(
                {"error": "This account does not have admin access."}
            )

        profile = get_or_create_profile(user)
        if profile.status in {AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED}:
            raise serializers.ValidationError({"error": "Admin account is disabled."})

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        refresh = RefreshToken.for_user(user)
        return {
            "access_token": str(refresh.access_token),
            "refresh_token": str(refresh),
        }


class AdminDetailsSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(
        source="profile.phone",
        read_only=True,
        allow_null=True,
        default=None,
    )
    avatar_url = serializers.URLField(
        source="profile.avatar_url",
        read_only=True,
        default="",
    )
    status = serializers.CharField(
        source="profile.status",
        read_only=True,
        default=AccountStatus.ACTIVE,
    )
    last_active_at = serializers.DateTimeField(source="last_login", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "phone",
            "status",
            "provider",
            "avatar_url",
            "is_active",
            "is_staff",
            "is_superuser",
            "is_email_verified",
            "last_active_at",
            "created_at",
        )
        read_only_fields = fields


class UpdateAdminInfoSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50, required=False, allow_blank=False)
    avatar = serializers.ImageField(required=False)
    clear_avatar = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("avatar") and attrs.get("clear_avatar"):
            raise serializers.ValidationError(
                {"clear_avatar": "Cannot clear and replace the avatar together."}
            )
        if not any(key in attrs for key in ("name", "avatar", "clear_avatar")):
            raise serializers.ValidationError("Provide a name or avatar change.")
        return attrs

    def update(self, instance, validated_data):
        profile = get_or_create_profile(instance)
        previous_avatar_url = profile.avatar_url
        avatar = validated_data.get("avatar")

        if "name" in validated_data:
            instance.name = validated_data["name"]

        if avatar is not None:
            upload = upload_image(
                avatar,
                folder=f"{settings.R2_IMAGES_PREFIX}/users",
                public_id=f"admin-{instance.pk}-{uuid4().hex}",
            )
            profile.avatar_url = upload["url"]
        elif validated_data.get("clear_avatar"):
            profile.avatar_url = ""

        try:
            with transaction.atomic():
                if "name" in validated_data:
                    instance.save(update_fields=["name", "updated_at"])
                if profile.avatar_url != previous_avatar_url:
                    profile.save(update_fields=["avatar_url"])
        except Exception:
            if avatar is not None:
                delete_image(public_id=upload["key"])
            raise

        if previous_avatar_url and previous_avatar_url != profile.avatar_url:
            schedule_delete_image(image_url=previous_avatar_url)

        return instance


class UpdateAdminPasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_new_password"]:
            raise serializers.ValidationError(
                {"confirm_new_password": "Passwords do not match."}
            )
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {
                    "new_password": (
                        "New password must be different from the current password."
                    )
                }
            )
        validate_password(attrs["new_password"], self.context["request"].user)
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class AccountListFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.ListField(
        child=serializers.ChoiceField(choices=AccountStatus.choices),
        required=False,
        default=list,
    )
    is_email_verified = serializers.BooleanField(required=False)


class AccountListSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(
        source="profile.phone",
        read_only=True,
        allow_null=True,
        default=None,
    )
    country = serializers.CharField(
        source="profile.country", read_only=True, default=""
    )
    city = serializers.CharField(source="profile.city", read_only=True, default="")
    timezone = serializers.CharField(
        source="profile.timezone",
        read_only=True,
        default="UTC",
    )
    avatar_url = serializers.URLField(
        source="profile.avatar_url",
        read_only=True,
        default="",
    )
    status = serializers.CharField(
        source="profile.status",
        read_only=True,
        default=AccountStatus.ACTIVE,
    )
    last_active_at = serializers.DateTimeField(source="last_login", read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "phone",
            "avatar_url",
            "country",
            "city",
            "timezone",
            "status",
            "provider",
            "is_active",
            "is_email_verified",
            "last_active_at",
            "created_at",
        )
        read_only_fields = fields
