"""Live agent adapter for realtime voice conversations.

The socket layer owns transport; the agent module owns the model. A real
implementation (Gemini Live API via ADK, ``agent.live``) is plugged in
by providing ``create_live_agent_session(consumer, *, session,
model_config)`` that returns a ``LiveAgentSession``. Until then the
placeholder session politely reports that live voice is not available.
"""

from channels.db import database_sync_to_async

from base.socket.events import LIVE_NOT_AVAILABLE


class LiveAgentError(Exception):
    pass


class LiveAgentSession:
    """Interface the agent module implements for live conversations.

    - ``start()`` opens the upstream live session.
    - ``send_audio(chunk)`` forwards one audio chunk from the frontend.
    - ``interrupt()`` resets the upstream turn (user interrupted).
    - ``close()`` releases upstream resources.

    Replies are pushed back through the consumer: ``send_json`` for
    control/transcript events and ``send`` (bytes) for audio.
    """

    async def start(self):
        raise NotImplementedError

    async def send_audio(self, chunk):
        raise NotImplementedError

    async def interrupt(self):
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError


class UnavailableLiveAgentSession(LiveAgentSession):
    """Placeholder until the Gemini Live / ADK agent module lands."""

    def __init__(self, consumer):
        self._consumer = consumer
        self._notified = False

    async def start(self):
        await self._notify_unavailable()

    async def send_audio(self, chunk):
        await self._notify_unavailable()

    async def interrupt(self):
        pass

    async def close(self):
        pass

    async def _notify_unavailable(self):
        if self._notified:
            return
        self._notified = True
        await self._consumer.send_json({
            "type": LIVE_NOT_AVAILABLE,
            "message": (
                "Live voice is not available yet; the agent module "
                "provides the Gemini Live integration."
            ),
        })


def create_live_agent_session(consumer, *, session, model_config=None):
    """Return the agent module's live session, or the placeholder stub."""
    try:
        from agent.live_agent import (  # noqa: PLC0415 - plugin point
            create_live_agent_session as create_agent_session,
        )
    except ImportError:
        return UnavailableLiveAgentSession(consumer)
    return create_agent_session(
        consumer, session=session, model_config=model_config or {},
    )


@database_sync_to_async
def get_live_model_config():
    """Live model configuration from the DisonConfig singleton."""
    from base.models import DisonConfig

    config = DisonConfig.objects.first()
    if config is None:
        return {}
    live_config = config.live_llm_model
    return live_config if isinstance(live_config, dict) else {}
