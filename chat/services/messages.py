"""Message creation shared by the REST API, socket, and the agent."""

import logging

from django.db import transaction
from django.utils import timezone

from chat.models import ChatMessage
from chat.utils.choices import ChatMessageSenderType

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
            from agent.chat_agent.reply import (
                dispatch_agent_reply as dispatch,
            )

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


def create_user_message(session, content):
    """Store a message sent by the user chatting in the session."""
    message = ChatMessage.objects.create(
        chat_session=session,
        sender_type=ChatMessageSenderType.USER,
        content=validate_user_message(content),
    )
    dispatch_agent_reply(message)
    return message


def create_agent_message(session, content, *, preceding_message=None):
    """Store the agent's reply and its response latency.

    ``response_time_ms`` is measured from the preceding user message so
    the stats endpoint can report average response time.
    """
    response_time_ms = None
    if preceding_message is None:
        preceding_message = (
            session.messages.filter(
                sender_type=ChatMessageSenderType.USER,
            )
            .order_by("-created_at", "-id")
            .first()
        )
    if preceding_message is not None:
        delta = timezone.now() - preceding_message.created_at
        response_time_ms = max(0, int(delta.total_seconds() * 1000))

    return ChatMessage.objects.create(
        chat_session=session,
        sender_type=ChatMessageSenderType.AGENT,
        content=content,
        response_time_ms=response_time_ms,
    )
