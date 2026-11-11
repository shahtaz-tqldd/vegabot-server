"""Message creation shared by the REST API, socket, and the agent."""

import logging

from django.db import transaction
from django.utils import timezone

from chat.models import ChatMessage
from chat.utils.choices import ChatMessageSenderType, ChatMode

logger = logging.getLogger(__name__)

MAX_USER_MESSAGE_LENGTH = 8000


def validate_user_message(content):
    content = (content or "").strip()
    if not content:
        raise ValueError("Message content cannot be empty.")
    if len(content) > MAX_USER_MESSAGE_LENGTH:
        raise ValueError(
            f"Message content cannot exceed {MAX_USER_MESSAGE_LENGTH} characters.",
        )
    return content


def dispatch_agent_reply(message):
    """Answer a user message with the agent (connected clients receive
    the reply via the message broadcast). Never raises: a broken agent
    dispatch must not fail the send, now or on commit."""

    def _safe_dispatch():
        try:
            from agent.reply import dispatch_agent_reply as dispatch

            dispatch(message)
        except Exception:
            logger.exception(
                "Could not dispatch agent reply for message %s", message.pk,
            )

    try:
        transaction.on_commit(_safe_dispatch)
    except Exception:
        logger.exception(
            "Could not register agent reply dispatch for message %s",
            message.pk,
        )


def create_user_message(
    session, content, *, mode=ChatMode.TEXT, dispatch_reply=True,
):
    """Store a message sent by the user chatting in the session.

    ``dispatch_reply=False`` is for callers that run the agent turn
    themselves (the chat socket streams text replies; voice replies come
    from the live session).
    """
    message = ChatMessage.objects.create(
        chat_session=session,
        sender_type=ChatMessageSenderType.USER,
        content=validate_user_message(content),
        mode=mode,
    )
    if dispatch_reply:
        dispatch_agent_reply(message)
    return message


def elapsed_ms(start, end):
    """Milliseconds from ``start`` to ``end`` (datetimes), never negative;
    None when either is unknown."""
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def create_agent_message(
    session,
    content,
    *,
    preceding_message=None,
    mode=ChatMode.TEXT,
    recommendations=None,
    response_time_ms=None,
):
    """Store the agent's reply and its response latency.

    ``response_time_ms`` is the delay between the user's input and the
    agent's first output (first streamed text or audio), as measured by
    the caller. Without it, the delay is measured from the preceding user
    message to now, i.e. to the end of the reply.
    """
    if response_time_ms is None:
        if preceding_message is None:
            preceding_message = (
                session.messages.filter(
                    sender_type=ChatMessageSenderType.USER,
                )
                .order_by("-created_at", "-id")
                .first()
            )
        if preceding_message is not None:
            response_time_ms = elapsed_ms(
                preceding_message.created_at, timezone.now(),
            )

    return ChatMessage.objects.create(
        chat_session=session,
        sender_type=ChatMessageSenderType.AGENT,
        content=content,
        mode=mode,
        recommendations=recommendations or [],
        response_time_ms=response_time_ms,
    )
