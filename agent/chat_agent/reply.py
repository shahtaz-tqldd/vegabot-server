"""Generate agent replies for user messages.

The chat module dispatches here after a user message is created; the
reply is persisted by the agent and delivered to the frontend through
the ChatMessage post-save socket broadcast.
"""

import threading

from celery import shared_task

from app.utils.logger import logger
from chat.models import ChatMessage
from chat.utils.choices import ChatSessionStatus

REPLY_TASK_NAME = "agent.chat_agent.reply.generate_agent_reply"


@shared_task(name=REPLY_TASK_NAME)
def generate_agent_reply(message_id):
    """Answer one user message; idempotent and safe to retry."""
    message = (
        ChatMessage.objects.select_related(
            "chat_session__chatbot",
        ).filter(sender_type="user").get(pk=message_id)
    )
    session = message.chat_session
    chatbot = session.chatbot

    if session.status != ChatSessionStatus.OPEN or chatbot.is_deleted:
        return {"message_id": str(message.pk), "skipped": "closed"}
    if not chatbot.is_active:
        return {"message_id": str(message.pk), "skipped": "inactive"}

    from agent.helpers.persistence import agent_reply_exists

    if agent_reply_exists(session, message.pk):
        return {"message_id": str(message.pk), "reply": None}

    # Imported lazily: keeps the chat module free of agent imports.
    from agent.chat_agent.client import ChatAgentClient

    reply = ChatAgentClient(chatbot, session).generate_reply_sync(
        user_message=message,
    )
    return {
        "message_id": str(message.pk),
        "reply": reply,
    }


def dispatch_agent_reply(user_message):
    """Run the reply task without blocking the request: Celery when a
    broker is available, otherwise a background thread."""
    try:
        generate_agent_reply.delay(str(user_message.pk))
    except Exception:
        logger.warning(
            "Celery unavailable; answering message %s in a background thread.",
            user_message.pk,
        )
        thread = threading.Thread(
            target=_run_reply_task,
            args=(str(user_message.pk),),
            daemon=True,
        )
        thread.start()


def _run_reply_task(message_id):
    from django.db import close_old_connections

    try:
        generate_agent_reply(message_id)
    except Exception:
        logger.exception("Background agent reply failed for message %s", message_id)
    finally:
        close_old_connections()
