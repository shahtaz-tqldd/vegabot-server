from django.db.models.signals import post_delete
from django.dispatch import receiver

from app.services.r2 import schedule_delete_image


@receiver(post_delete, sender="accounts.UserProfile")
@receiver(post_delete, sender="chatbot.Chatbot")
def delete_public_asset_with_record(sender, instance, **kwargs):
    url_field = (
        "avatar_url" if sender._meta.label_lower == "accounts.userprofile" else "logo"
    )
    schedule_delete_image(image_url=getattr(instance, url_field, "") or "")
