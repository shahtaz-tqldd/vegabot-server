import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from base.socket.events import CHAT_MESSAGE_CREATED
from base.socket.services.groups import chat_session_group
from app.utils.logger import logger


async def _group_send_all(channel_layer, groups, payload):
    for group in set(groups):
        try:
            await channel_layer.group_send(group, payload)
        except Exception:
            logger.exception(
                "Failed to publish %s to %s", payload.get("type"), group,
            )


def broadcast(groups, payload):
    """Best-effort group delivery; durable state belongs to domain services.

    Works from both worker threads (no running loop) and from inside an
    event loop, where scheduling a task is the only valid option.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        loop.create_task(_group_send_all(channel_layer, groups, payload))
    else:
        async_to_sync(_group_send_all)(channel_layer, groups, payload)


def publish_chat_message(message_data, session_id):
    """Push a newly created chat message to everyone in the session."""
    broadcast(
        [chat_session_group(session_id)],
        {
            "type": "chat.message.event",
            "event": {
                "type": CHAT_MESSAGE_CREATED,
                "session_id": str(session_id),
                "message": message_data,
            },
        },
    )
