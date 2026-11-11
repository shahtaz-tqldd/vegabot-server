"""The per-chatbot inputs an agent turn needs, cached across turns.

The profile carries the chatbot's identity fields, which knowledge spaces
(documents/products/services) it uses (enabled on the chatbot AND trained),
and its add-on features (cart/order taking), so agents are built without
per-turn database reads. Invalidation is signal-driven
(chatbot/knowledge writes bump the version); the TTL is a safety net.
"""

from django.conf import settings
from django.core.cache import cache

from agent.helpers.global_instruction import global_instruction
from app.utils.cache import bump_cache_version, versioned_key
from knowledge.models import KnowledgeBase, Product, Service
from knowledge.utils.choices import KnowledgeSpaceTypes

PROFILE_VERSION_KEY = "chatbot:agent-profile:version"
PROFILE_TTL_SECONDS = getattr(settings, "CHATBOT_CACHE_TTL_SECONDS", 300)

_CHATBOT_FIELDS = (
    "id",
    "chatbot_name",
    "business_type",
    "tone",
    "instructions",
    "welcome_message",
    "fallback_message",
    "escalation_rule",
    "never_answer",
    "language",
    "timezone",
    "status",
    "is_deleted",
    "knowledge_enabled",
    "products_enabled",
    "services_enabled",
    "product_ordering_enabled",
    "speaking_pace",
)


def invalidate_agent_profile(chatbot_id):
    """Retire the cached profile; the next turn recomputes it."""
    bump_cache_version(PROFILE_VERSION_KEY)
    cache.delete(_profile_key(chatbot_id))


def _profile_key(chatbot_id):
    return versioned_key(
        "chatbot:agent-profile", PROFILE_VERSION_KEY, chatbot_id,
    )


def agent_profile(chatbot):
    """Compute the profile from live data; pure, no caching."""
    chatbot_fields = {
        field: getattr(chatbot, field) for field in _CHATBOT_FIELDS
    }
    has_products = chatbot.products_enabled and Product.objects.filter(
        chatbot_id=chatbot.id,
    ).exists()
    return {
        "chatbot": chatbot_fields,
        "policy": global_instruction(chatbot),
        "spaces": {
            KnowledgeSpaceTypes.DOCUMENT: chatbot.knowledge_enabled
            and KnowledgeBase.objects.filter(chatbot_id=chatbot.id).exists(),
            KnowledgeSpaceTypes.PRODUCT: has_products,
            KnowledgeSpaceTypes.SERVICE: chatbot.services_enabled
            and Service.objects.filter(chatbot_id=chatbot.id).exists(),
        },
        "features": {
            "ordering": has_products and chatbot.ordering_enabled,
        },
    }


def cached_agent_profile(chatbot):
    """Return agent_profile(chatbot), read through the cache."""
    key = _profile_key(chatbot.id)
    profile = cache.get(key)
    if profile is None:
        profile = agent_profile(chatbot)
        cache.set(key, profile, PROFILE_TTL_SECONDS)
    return profile
