"""Reply persistence. These are sync (ORM); call via sync_to_async."""

from django.db import transaction

from analytics.services.ai_usage import record_ai_usage
from analytics.utils.choices import AIUsageType
from chat.api.v1.client.serializers import ChatMessageSerializer
from chat.services.messages import create_agent_message
from chat.utils.choices import ChatMode


def agent_reply_exists(session, user_message_id):
    """Idempotency: an agent message was already generated for this turn."""
    user_message = session.messages.filter(pk=user_message_id).first()
    if user_message is None:
        return True  # the trigger message vanished; nothing to answer
    return session.messages.filter(
        sender_type="agent",
        created_at__gt=user_message.created_at,
    ).exists()


def record_usage(*, chatbot, session, message, response, model_name):
    if response["token"].get("total_tokens", 0):
        record_ai_usage(
            chatbot=chatbot,
            chat_session=session,
            chat_message=message,
            usage_type=AIUsageType.CHAT,
            cost=response["cost"],
            token_usage=response["token"],
            model=model_name,
        )


@transaction.atomic
def persist_reply(
    *,
    chatbot,
    session,
    response,
    model_name,
    user_message=None,
    mode=ChatMode.TEXT,
    source_metadata=(),
):
    """Persist one agent message and its usage in one transaction.

    Returns the client payload: the serialized message (with its
    recommendation cards) and the cited sources.
    """
    result = response["result"]
    message = create_agent_message(
        session,
        result["content"],
        preceding_message=user_message,
        mode=mode,
        recommendations=result.get("recommendations") or [],
    )
    record_usage(
        chatbot=chatbot,
        session=session,
        message=message,
        response=response,
        model_name=model_name,
    )
    return {
        "message": ChatMessageSerializer(message).data,
        "sources": list(source_metadata or ()),
    }
