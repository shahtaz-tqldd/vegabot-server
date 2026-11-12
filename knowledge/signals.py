from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from knowledge.models import KnowledgeBase, Product, Service


@receiver(post_save, sender=KnowledgeBase)
@receiver(post_delete, sender=KnowledgeBase)
@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
@receiver(post_save, sender=Service)
@receiver(post_delete, sender=Service)
def invalidate_chatbot_agent_profile(sender, instance, **kwargs):
    """Knowledge changes alter which search tools an agent gets."""
    from agent.helpers.profile import invalidate_agent_profile

    invalidate_agent_profile(instance.chatbot_id)


_VECTOR_SOURCE_FIELDS = {
    KnowledgeBase: "knowledge_base_id",
    Product: "product_id",
    Service: "service_id",
}


@receiver(post_save, sender=KnowledgeBase)
@receiver(post_save, sender=Product)
@receiver(post_save, sender=Service)
def sync_vector_searchability(sender, instance, created, update_fields=None, **kwargs):
    """Hide or show a source's vectors when it is disabled or enabled.

    Bulk ``queryset.update(is_enabled=...)`` skips signals; call
    ``sync_searchable`` after such an update.
    """
    if created or (update_fields is not None and "is_enabled" not in update_fields):
        return
    from vector_store.models import VectorDocument
    from vector_store.services.vectorize import sync_searchable

    sync_searchable(
        VectorDocument.objects.filter(
            **{_VECTOR_SOURCE_FIELDS[sender]: instance.pk},
        ),
    )
