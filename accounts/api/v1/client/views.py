from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.api.v1.client.serializers import (
    ChangePasswordSerializer,
    GoogleLoginSerializer,
    LoginSerializer,
    RegisterSerializer,
    RequestPasswordResetSerializer,
    ResetPasswordSerializer,
    UserDetailsSerializer,
    UserSerializer,
    UserUpdateSerializer,
    VerifyOTPSerializer,
)
from app.utils.response import APIResponse


def first_error_message(errors, fallback="Request failed."):
    """Return the first human-readable message from nested serializer errors."""
    if isinstance(errors, dict):
        for value in errors.values():
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    if isinstance(errors, (list, tuple)):
        for value in errors:
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    return str(errors) if errors else fallback


def validation_error_response(errors, fallback):
    return APIResponse.error(
        errors=errors,
        message=first_error_message(errors, fallback=fallback),
        status=status.HTTP_400_BAD_REQUEST,
    )


class CreateNewUserView(GenericAPIView):
    """Register a password-based account and send its verification OTP."""

    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors, "Registration failed.")

        try:
            user = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(exc.detail, "Registration failed.")

        return APIResponse.success(
            data=UserSerializer(user, context=self.get_serializer_context()).data,
            message="User created successfully. A verification OTP was sent.",
            status=status.HTTP_201_CREATED,
        )


class VerifyOTPView(GenericAPIView):
    """Verify a registration OTP and return an access/refresh token pair."""

    serializer_class = VerifyOTPSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "OTP verification failed."
            )
        return APIResponse.success(
            data=serializer.save(),
            message="Email verified successfully.",
        )


class LoginView(GenericAPIView):
    """Log in a password-based account."""

    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors, "Login failed.")
        return APIResponse.success(
            data=serializer.validated_data,
            message="User logged in.",
        )


class GoogleLoginView(GenericAPIView):
    """Create or log in an account from a Firebase Google identity."""

    serializer_class = GoogleLoginSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors, "Google login failed.")

        try:
            tokens = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(exc.detail, "Google login failed.")

        return APIResponse.success(data=tokens, message="User logged in.")


class RefreshTokenView(TokenRefreshView):
    """Exchange a refresh token for a new access token."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code >= status.HTTP_400_BAD_REQUEST:
            return APIResponse.error(
                errors=response.data,
                message=first_error_message(
                    response.data, fallback="Token refresh failed."
                ),
                status=response.status_code,
            )
        return APIResponse.success(
            data=response.data,
            message="Token refreshed successfully.",
            status=response.status_code,
        )


class UserDetailsView(GenericAPIView):
    """Return the authenticated user's account and profile details."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserDetailsSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(data=self.get_serializer(request.user).data)


class UserDetailsUpdateView(GenericAPIView):
    """Update the authenticated user's name, profile fields, or avatar."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserUpdateSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Profile update failed."
            )

        try:
            user = serializer.save()
        except drf_serializers.ValidationError as exc:
            return validation_error_response(exc.detail, "Profile update failed.")

        return APIResponse.success(
            data=UserSerializer(user, context=self.get_serializer_context()).data,
            message="User updated successfully.",
        )


class ChangePasswordView(GenericAPIView):
    """Change the authenticated user's password."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Password change failed."
            )
        serializer.save()
        return APIResponse.success(message="Password changed successfully.")


class DeleteAccountView(GenericAPIView):
    """Soft-delete the authenticated account for the configured retention period."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def delete(self, request, *args, **kwargs):
        request.user.mark_deleted()
        return APIResponse.success(
            data={"deleted_at": request.user.deleted_at},
            message="Account scheduled for deletion.",
        )


class RequestPasswordResetView(GenericAPIView):
    """Send a password reset link when an eligible account exists."""

    serializer_class = RequestPasswordResetSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Password reset request failed."
            )
        serializer.save()
        return APIResponse.success(
            message=(
                "If an account exists for that email, "
                "a password reset link has been sent."
            )
        )


class ResetPasswordView(GenericAPIView):
    """Set a new password using a valid password reset token."""

    serializer_class = ResetPasswordSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors, "Password reset failed."
            )
        serializer.save()
        return APIResponse.success(message="Password reset successfully.")
