from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver(post_save, sender="chatbot.Chatbot")
@receiver(post_delete, sender="chatbot.Chatbot")
def invalidate_chatbot_agent_profile(sender, instance, **kwargs):
    """Chatbot edits change the agent profile (identity, rules); retire it."""
    from agent.helpers.profile import invalidate_agent_profile

    invalidate_agent_profile(instance.id)


@receiver(post_save, sender="chatbot.Chatbot")
def sync_vector_searchability(sender, instance, created, update_fields=None, **kwargs):
    """Hide a deleted chatbot's vectors from search (and show them again
    if it is restored).

    Bulk ``queryset.update(is_deleted=...)`` skips signals; call
    ``sync_searchable`` after such an update.
    """
    if created or (update_fields is not None and "is_deleted" not in update_fields):
        return
    from vector_store.models import VectorDocument
    from vector_store.services.vectorize import sync_searchable

    sync_searchable(VectorDocument.objects.filter(chatbot_id=instance.pk))
