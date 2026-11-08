from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class SafeJWTAuthentication(JWTAuthentication):
    activity_update_interval = timedelta(minutes=5)

    def get_user(self, validated_token):
        try:
            user = super().get_user(validated_token)
        except (ValidationError, ValueError) as exc:
            raise AuthenticationFailed("Invalid token user identifier.") from exc

        now = timezone.now()
        update_before = now - self.activity_update_interval
        if user.last_active is None or user.last_active < update_before:
            updated = (
                type(user)
                .objects.filter(pk=user.pk)
                .filter(
                    Q(last_active__isnull=True)
                    | Q(last_active__lt=update_before)
                )
                .update(last_active=now)
            )
            if updated:
                user.last_active = now
        return user
