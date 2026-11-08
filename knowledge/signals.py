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
