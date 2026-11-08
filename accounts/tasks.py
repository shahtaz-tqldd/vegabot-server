from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from accounts.utils.choices import AccountStatus

User = get_user_model()


@shared_task
def ping():
    return "pong"


@shared_task
def send_password_reset_email(recipient_email, subject, message, html_message=None):
    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient_email],
    )
    if html_message:
        email.attach_alternative(html_message, "text/html")
    return email.send(fail_silently=False)


@shared_task
def send_email_verification_otp(recipient_email, subject, message, html_message=None):
    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient_email],
    )
    if html_message:
        email.attach_alternative(html_message, "text/html")
    return email.send(fail_silently=False)


def delete_expired_accounts():
    cutoff = timezone.now() - timedelta(days=14)
    queryset = User.objects.filter(
        profile__status=AccountStatus.DEACTIVATED,
        deleted_at__lte=cutoff,
    )
    user_count = queryset.count()
    queryset.delete()
    return user_count


@shared_task
def permanently_delete_expired_accounts():
    return delete_expired_accounts()
