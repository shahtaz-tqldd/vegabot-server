from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver(post_save, sender="chatbot.Chatbot")
@receiver(post_delete, sender="chatbot.Chatbot")
def invalidate_chatbot_agent_profile(sender, instance, **kwargs):
    """Chatbot edits change the agent profile (identity, rules); retire it."""
    from agent.helpers.profile import invalidate_agent_profile

    invalidate_agent_profile(instance.id)
