from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from accounts.tasks import send_password_reset_email


User = get_user_model()
password_reset_token_generator = PasswordResetTokenGenerator()


def build_password_reset_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = password_reset_token_generator.make_token(user)
    return (
        f"{settings.USER_FRONTEND_URL.rstrip('/')}"
        f"{settings.PASSWORD_RESET_PATH}"
        f"?uid={uid}&token={token}"
    )


def send_user_password_reset_email(user):
    reset_link = build_password_reset_link(user)
    context = {
        "recipient_name": user.name or user.email,
        "reset_link": reset_link,
    }
    subject = "Reset your Vegabot password"
    message = render_to_string("emails/password_reset.txt", context)
    html_message = render_to_string("emails/password_reset.html", context)

    try:
        send_password_reset_email.delay(
            recipient_email=user.email,
            subject=subject,
            message=message,
            html_message=html_message,
        )
    except Exception:
        send_password_reset_email(
            recipient_email=user.email,
            subject=subject,
            message=message,
            html_message=html_message,
        )


def resolve_password_reset_user(uid, token):
    try:
        user_id = force_str(urlsafe_base64_decode(uid))
        user = User.objects.get(pk=user_id)
    except Exception as exc:
        raise ValueError("Invalid password reset link.") from exc

    if not password_reset_token_generator.check_token(user, token):
        raise ValueError("Invalid or expired password reset link.")

    return user
