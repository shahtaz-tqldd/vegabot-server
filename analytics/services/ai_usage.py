from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from analytics.models import AIUsage

AI_USAGE_COST_QUANTUM = Decimal("0.00000001")


def _normalize_cost(cost):
    try:
        return Decimal(str(cost)).quantize(
            AI_USAGE_COST_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"cost": ["Enter a valid cost."]}) from exc


def record_ai_usage(
    *,
    chatbot,
    usage_type,
    cost,
    token_usage,
    chat_session=None,
    chat_message=None,
    model=""
):
    if (
        chat_session is not None
        and str(chat_session.chatbot_id) != str(chatbot.id)
    ):
        raise ValidationError(
            {"chat_session": ["The session must belong to the chatbot."]}
        )
    if (
        chat_message is not None
        and (
            chat_session is None
            or chat_message.chat_session_id != chat_session.id
        )
    ):
        raise ValidationError(
            {"chat_message": ["The message must belong to the session."]}
        )

    usage = AIUsage(
        chatbot=chatbot,
        chat_session=chat_session,
        chat_message=chat_message,
        usage_type=usage_type,
        cost=_normalize_cost(cost),
        tokens=token_usage.get("total_tokens", 0),
        input_tokens=token_usage.get("input_tokens", 0),
        output_tokens=token_usage.get("output_tokens", 0),
        thinking_tokens=token_usage.get("thinking_tokens", 0),
        cached_input_tokens=token_usage.get("cached_input_tokens", 0),
        model=model
    )
    usage.full_clean(validate_unique=False, validate_constraints=False)
    usage.save()
    return usage
