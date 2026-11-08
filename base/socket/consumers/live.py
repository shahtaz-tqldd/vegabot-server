from channels.db import database_sync_to_async

from base.socket.consumers.chat import ChatSessionConsumer
from base.socket.events import (
    LIVE_ERROR,
    LIVE_STARTED,
    LIVE_STOPPED,
)
from base.socket.services.live_agent import (
    LiveAgentError,
    create_live_agent_session,
    get_live_model_config,
)


class LiveChatConsumer(ChatSessionConsumer):
    """Realtime voice conversation over one chat session.

    Protocol (frontend <-> here):
    - JSON ``{"type": "live.start"}`` opens the upstream agent session.
    - Binary frames carry user audio chunks upstream.
    - JSON ``{"type": "live.stop"}`` and ``{"type": "live.interrupt"}``
      control the upstream turn.
    - Downstream: JSON control/transcript events and binary audio frames.

    The upstream agent (Gemini Live API via ADK) is provided by the
    agent module through ``base.socket.services.live_agent``.
    """

    async def connect(self):
        self.agent = None
        await super().connect()

    async def disconnect(self, close_code):
        await self._close_agent()
        await super().disconnect(close_code)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error("Expected a JSON object.")
            return
        event_type = content.get("type")

        if event_type == "live.start":
            await self._start_agent()
            return
        if event_type == "live.stop":
            await self._close_agent()
            await self.send_json({"type": LIVE_STOPPED})
            return
        if event_type == "live.interrupt":
            await self._interrupt_agent()
            return
        await super().receive_json(content, **kwargs)

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if bytes_data:
            await self._send_audio(bytes_data)
            return
        await super().receive(text_data=text_data, bytes_data=bytes_data, **kwargs)

    async def _start_agent(self):
        if self.agent is not None:
            await self.send_json({
                "type": LIVE_ERROR,
                "message": "Live session already started.",
            })
            return
        model_config = await get_live_model_config()
        self.agent = create_live_agent_session(
            self,
            session=self.session,
            model_config=model_config,
        )
        try:
            await self.agent.start()
        except Exception as exc:  # configuration, provider, or runtime issues
            await self.send_json({
                "type": LIVE_ERROR,
                "message": f"Could not start the live agent: {exc}",
            })
            await self._close_agent()
            return
        await self.send_json({
            "type": LIVE_STARTED,
            "data": {"session_id": str(self.session.pk)},
        })

    async def _send_audio(self, chunk):
        if self.agent is None:
            await self.send_error("Start the live session before sending audio.")
            return
        try:
            await self.agent.send_audio(chunk)
        except LiveAgentError as exc:
            await self.send_json({"type": LIVE_ERROR, "message": str(exc)})

    async def _interrupt_agent(self):
        if self.agent is None:
            return
        try:
            await self.agent.interrupt()
        except LiveAgentError as exc:
            await self.send_json({"type": LIVE_ERROR, "message": str(exc)})

    async def _close_agent(self):
        if self.agent is None:
            return
        agent, self.agent = self.agent, None
        try:
            await agent.close()
        except LiveAgentError:
            pass
