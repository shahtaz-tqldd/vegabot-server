"""Background replies for messages sent over REST (e.g. with attachments).

The socket answers typed messages itself; messages created through
``POST /api/v1/chat/messages/send/`` are answered here, in text mode, and
streamed to every socket of the session through the channel layer.
"""

import threading
import time
from functools import partial

from celery import shared_task

from app.utils.logger import logger
from base.socket.services.broadcaster import apublish_session_event
from chat.models import ChatMessage

REPLY_TASK_NAME = "agent.reply.generate_agent_reply"
LOCK_WAIT_SECONDS = 60
LOCK_POLL_SECONDS = 0.5


@shared_task(name=REPLY_TASK_NAME)
def generate_agent_reply(message_id):
    """Answer one user message; idempotent and safe to retry."""
    # Imported lazily: keeps the chat module free of agent imports.
    from agent.client import AgentClient, AgentUnavailableError
    from agent.helpers.lock import (
        TEXT_TURN_TTL_SECONDS,
        acquire_session_lock,
        release_session_lock,
    )
    from agent.helpers.persistence import agent_reply_exists

    message = (
        ChatMessage.objects.select_related("chat_session")
        .filter(sender_type="user")
        .get(pk=message_id)
    )
    session_id = message.chat_session_id

    owner = f"task:{message.pk}"
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not acquire_session_lock(session_id, owner, TEXT_TURN_TTL_SECONDS):
        if time.monotonic() >= deadline:
            logger.warning("Agent busy; message %s not answered", message.pk)
            return {"message_id": str(message.pk), "skipped": "busy"}
        time.sleep(LOCK_POLL_SECONDS)

    try:
        try:
            client = AgentClient.for_session(session_id)
        except AgentUnavailableError as exc:
            return {"message_id": str(message.pk), "skipped": str(exc)}
        if agent_reply_exists(client.session, message.pk):
            return {"message_id": str(message.pk), "reply": None}
        reply = client.generate_reply_sync(
            user_message=message,
            emit=partial(apublish_session_event, session_id),
        )
        return {"message_id": str(message.pk), "reply": reply}
    finally:
        release_session_lock(session_id, owner)


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
