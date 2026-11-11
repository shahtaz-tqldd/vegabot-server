from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.utils.choices import AccountProvider, AccountStatus
from accounts.models import UserProfile, phone_regex
from accounts.services.password import (
    resolve_password_reset_user,
    send_user_password_reset_email,
)
from accounts.services.verification import (
    InvalidVerificationOTP,
    complete_email_verification,
    issue_email_verification_otp,
    verify_email_otp,
)
from app.utils.validators import validate_timezone_name
from app.services.r2 import delete_image, schedule_delete_image, upload_image

User = get_user_model()


def get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def is_unclaimed_invitation_account(user):
    """Identify passwordless accounts created by the legacy invitation flow."""
    return bool(
        user
        and user.is_active
        and user.provider == AccountProvider.PASSWORD
        and not user.is_email_verified
        and not user.has_usable_password()
    )


def build_auth_token_payload(user):
    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
    }


class UserSerializer(serializers.ModelSerializer):
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
    city = serializers.CharField(source="profile.city", read_only=True, default="")
    country = serializers.CharField(
        source="profile.country", read_only=True, default=""
    )
    timezone = serializers.CharField(
        source="profile.timezone",
        read_only=True,
        default="UTC",
    )
    status = serializers.CharField(
        source="profile.status",
        read_only=True,
        default=AccountStatus.ACTIVE,
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "provider",
            "status",
            "is_email_verified",
            "is_active",
            "phone",
            "avatar_url",
            "city",
            "country",
            "timezone",
            "created_at",
            "updated_at",
            "last_login",
        )
        read_only_fields = fields


class UserDetailsSerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields


class UserUpdateSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
        validators=[phone_regex],
    )
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    timezone = serializers.CharField(
        required=False,
        max_length=64,
        validators=[validate_timezone_name],
    )
    profile_picture = serializers.ImageField(write_only=True, required=False)
    clear_profile_picture = serializers.BooleanField(
        write_only=True, required=False, default=False
    )
    avatar_url = serializers.URLField(
        source="profile.avatar_url", read_only=True, default=""
    )

    class Meta:
        model = User
        fields = (
            "name",
            "phone",
            "country",
            "city",
            "timezone",
            "profile_picture",
            "clear_profile_picture",
            "avatar_url",
        )

    def validate(self, attrs):
        if attrs.get("profile_picture") and attrs.get("clear_profile_picture"):
            raise serializers.ValidationError(
                {
                    "clear_profile_picture": (
                        "Cannot clear and replace the profile picture together."
                    )
                }
            )
        return attrs

    def update(self, instance, validated_data):
        profile_picture = validated_data.pop("profile_picture", None)
        clear_profile_picture = validated_data.pop("clear_profile_picture", False)
        profile = get_or_create_profile(instance)
        previous_avatar_url = profile.avatar_url
        name_changed = "name" in validated_data

        if name_changed:
            instance.name = validated_data.pop("name")

        profile_fields = {"phone", "country", "city", "timezone"}
        changed_profile_fields = []
        for field in profile_fields.intersection(validated_data):
            setattr(profile, field, validated_data[field])
            changed_profile_fields.append(field)

        if profile_picture is not None:
            upload = upload_image(
                profile_picture,
                folder=f"{settings.R2_IMAGES_PREFIX}/users",
                public_id=f"user-{instance.pk}-{uuid4().hex}",
            )
            profile.avatar_url = upload["url"]
            changed_profile_fields.append("avatar_url")
        elif clear_profile_picture and profile.avatar_url:
            profile.avatar_url = ""
            changed_profile_fields.append("avatar_url")

        try:
            with transaction.atomic():
                if name_changed:
                    instance.save(update_fields=["name", "updated_at"])
                if changed_profile_fields:
                    profile.save(update_fields=sorted(set(changed_profile_fields)))
        except Exception:
            if profile_picture is not None:
                delete_image(public_id=upload["key"])
            raise

        if previous_avatar_url and previous_avatar_url != profile.avatar_url:
            schedule_delete_image(image_url=previous_avatar_url)

        return instance


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(max_length=254)
    name = serializers.CharField(required=False, allow_blank=True, max_length=50)
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=50,
        validators=[phone_regex],
    )
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = (
            "email",
            "name",
            "phone",
            "password",
            "confirm_password",
        )

    def validate_email(self, value):
        email = User.objects.normalize_email(value).strip().casefold()
        existing_user = User.objects.filter(email__iexact=email).first()
        if existing_user and not is_unclaimed_invitation_account(existing_user):
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )
        validate_password(
            attrs["password"],
            User(email=attrs["email"], name=attrs.get("name", "")),
        )
        return attrs

    def create(self, validated_data):
        phone = validated_data.pop("phone", None)
        validated_data.pop("confirm_password")
        password = validated_data.pop("password")

        try:
            with transaction.atomic():
                user = (
                    User.objects.select_for_update()
                    .filter(email__iexact=validated_data["email"])
                    .first()
                )
                if user is None:
                    user = User.objects.create_user(
                        password=password,
                        provider=AccountProvider.PASSWORD,
                        **validated_data,
                    )
                else:
                    if not is_unclaimed_invitation_account(user):
                        raise serializers.ValidationError(
                            {"email": "A user with this email already exists."}
                        )
                    user.name = validated_data.get("name", "")
                    user.provider = AccountProvider.PASSWORD
                    user.set_password(password)
                    user.save(
                        update_fields=[
                            "name",
                            "provider",
                            "password",
                            "updated_at",
                        ]
                    )
                profile = get_or_create_profile(user)
                profile.phone = phone
                profile.save(update_fields=["phone"])
        except IntegrityError as exc:
            if User.objects.filter(email__iexact=validated_data["email"]).exists():
                raise serializers.ValidationError(
                    {"email": "A user with this email already exists."}
                ) from exc
            raise serializers.ValidationError(
                {"non_field_errors": "Could not create user. Please try again."}
            ) from exc

        issue_email_verification_otp(user)
        return user


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.RegexField(
        regex=r"^\d{4}$",
        error_messages={"invalid": "OTP must be exactly 4 digits."},
    )

    def validate(self, attrs):
        try:
            user = verify_email_otp(email=attrs["email"], otp=attrs["otp"])
        except InvalidVerificationOTP as exc:
            raise serializers.ValidationError({"otp": str(exc)}) from exc
        attrs["user"] = user
        return attrs

    def save(self, **kwargs):
        return build_auth_token_payload(self.validated_data["user"])


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs["email"].strip().casefold()
        user = authenticate(
            request=self.context.get("request"),
            email=email,
            password=attrs["password"],
        )

        if not user:
            candidate = (
                User.objects.filter(email__iexact=email)
                .select_related("profile")
                .first()
            )
            can_reactivate = bool(
                candidate
                and candidate.deleted_at
                and candidate.check_password(attrs["password"])
                and get_or_create_profile(candidate).status == AccountStatus.DEACTIVATED
            )
            if not can_reactivate:
                raise serializers.ValidationError({"error": "Invalid credentials."})
            candidate.reactivate()
            user = candidate
        if not user.is_active:
            raise serializers.ValidationError({"error": "User is disabled."})

        profile = get_or_create_profile(user)
        if profile.status == AccountStatus.SUSPENDED:
            raise serializers.ValidationError({"error": "User is suspended."})
        if profile.status == AccountStatus.DEACTIVATED or user.deleted_at:
            user.reactivate()

        if user.provider == AccountProvider.PASSWORD and not user.is_email_verified:
            raise serializers.ValidationError({"error": "Email is not verified."})

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        return build_auth_token_payload(user)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
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


class RequestPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def save(self, **kwargs):
        user = User.objects.filter(
            email__iexact=self.validated_data["email"],
            is_active=True,
            provider=AccountProvider.PASSWORD,
            profile__status__in=(AccountStatus.ACTIVE, AccountStatus.PREMIUM),
        ).first()
        if user:
            send_user_password_reset_email(user)


class ResetPasswordSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            user = resolve_password_reset_user(attrs["uid"], attrs["token"])
        except ValueError as exc:
            raise serializers.ValidationError({"token": str(exc)}) from exc

        validate_password(attrs["new_password"], user)
        attrs["user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["new_password"])
        user.is_email_verified = True
        user.save(update_fields=["password", "is_email_verified"])
        return user
