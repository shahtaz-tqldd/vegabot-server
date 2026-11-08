"""Reply persistence. These are sync (ORM); call via sync_to_async."""

from django.conf import settings
from django.db import transaction

from analytics.services.ai_usage import record_ai_usage
from analytics.utils.choices import AIUsageType
from chat.services.messages import create_agent_message


def agent_reply_exists(session, user_message_id):
    """Idempotency: an agent message was already generated for this turn."""
    user_message = session.messages.filter(pk=user_message_id).first()
    if user_message is None:
        return True  # the trigger message vanished; nothing to answer
    return session.messages.filter(
        sender_type="agent",
        created_at__gt=user_message.created_at,
    ).exists()


@transaction.atomic
def persist_reply(
    *,
    chatbot,
    session,
    user_message,
    response,
    source_metadata,
):
    """Persist one generated message and its usage in one transaction."""
    message = create_agent_message(
        session,
        response["result"]["content"],
        preceding_message=user_message,
    )

    if response["token"].get("total_tokens", 0):
        record_ai_usage(
            chatbot=chatbot,
            chat_session=session,
            chat_message=message,
            usage_type=AIUsageType.CHAT,
            cost=response["cost"],
            token_usage=response["token"],
            model=settings.GEMINI_CHAT_MODEL,
        )

    return {
        "message_id": str(message.id),
        "content": message.content,
        "response_time_ms": message.response_time_ms,
        "sources": source_metadata,
    }
