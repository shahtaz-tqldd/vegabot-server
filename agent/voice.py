"""Audio mode of the agent: one ADK live run per voice period.

The socket consumer owns transport; this module owns the agent. While
audio mode is on, user audio (and any typed text) flows into one live
run through a LiveRequestQueue. Downstream, per agent turn:

- the user's speech is captioned (``voice.transcript``) and its final
  transcript stored as a user message,
- the agent's speech streams as binary audio frames plus its transcript
  as ``agent.response.delta`` (same events as text mode),
- recommended products/services follow the text, and the spoken reply is
  stored as an agent message with its usage and cost.

The live run shares the ADK session with text turns, so it starts with
the full conversation history and text turns later see what was said.
"""

import asyncio

from asgiref.sync import sync_to_async
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import LiveRequestQueue
from google.genai import types

from agent.client import load_session_for_turn
from agent.helpers.lock import (
    VOICE_TTL_SECONDS,
    AgentBusyError,
    aacquire_session_lock,
    arefresh_session_lock,
    arelease_session_lock,
)
from agent.helpers.model import (
    load_model_config,
    model_name_for,
    vertex_client_scope,
    vertex_location_for,
)
from agent.helpers.persistence import persist_reply
from agent.helpers.profile import cached_agent_profile
from agent.helpers.runtime import (
    APP_NAME,
    build_runner,
    build_session_service,
    get_or_create_session,
    scoped_user_id,
)
from agent.helpers.stream import TurnStream, emit_event
from agent.helpers.usage import add_event_usage, build_response
from agent.root_agent import build_root_agent
from agent.schema import AgentResultSchema, TokenUsageSchema
from agent.tools import RECOMMENDATION_TOOL
from app.utils.logger import logger
from base.socket.events import VOICE_TRANSCRIPT
from chat.services.messages import create_user_message
from chat.utils.choices import ChatMode

AUDIO_MIME_TYPE = "audio/pcm;rate=16000"
FAILED_VOICE_MESSAGE = "The voice session ended unexpectedly."
HEARTBEAT_SECONDS = 30


class VoiceSession:
    """Adapter between the socket consumer and an ADK live run.

    ``send_json`` / ``send_bytes`` are async callables that deliver to
    the client; ``on_failure`` (async, optional) is told when the live
    run dies so the consumer can fall back to text mode.
    """

    mode = ChatMode.AUDIO

    def __init__(
        self,
        *,
        session,
        send_json,
        send_bytes,
        owner,
        on_failure=None,
        session_service=None,
    ):
        self.session = session
        self.chatbot = session.chatbot
        self.send_json = send_json
        self.send_bytes = send_bytes
        self.owner = f"voice:{owner}"
        self.on_failure = on_failure
        self.session_service = session_service
        self.queue = None
        self._tasks = []
        self._locked = False
        self._reset_turn()
        self.usage = TokenUsageSchema()
        self._seen_usage_events = set()

    def _reset_turn(self):
        self._stream = None
        self._segments = []
        self._recommendations = []

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        if not await aacquire_session_lock(
            self.session.pk, self.owner, VOICE_TTL_SECONDS,
        ):
            raise AgentBusyError("Another reply or voice session is running.")
        self._locked = True
        try:
            await self._build()
        except BaseException:
            await self.close()
            raise
        self.queue = LiveRequestQueue()
        self._tasks = [
            asyncio.create_task(self._consume_events()),
            asyncio.create_task(self._heartbeat()),
        ]

    async def _build(self):
        # Every ORM/cache read stays off the socket's event loop.
        def _load():
            session = load_session_for_turn(self.session.pk)
            model_config = load_model_config(self.mode)
            profile = cached_agent_profile(session.chatbot)
            agent = build_root_agent(
                session.chatbot,
                session,
                profile=profile,
                mode=self.mode,
                model_config=model_config,
            )
            return session, model_config, agent

        self.session, self.model_config, self.agent = await sync_to_async(
            _load, thread_sensitive=True,
        )()
        self.chatbot = self.session.chatbot
        self.model_name = model_name_for(self.mode, self.model_config)
        self.session_service = self.session_service or build_session_service()
        self.runner = build_runner(self.agent, self.session_service)
        self.scoped_user = scoped_user_id(self.session)
        self.adk_session = await get_or_create_session(
            self.session_service,
            app_name=APP_NAME,
            user_id=self.scoped_user,
            session_id=self.session.id,
        )

    async def close(self):
        if self.queue is not None:
            try:
                self.queue.close()
            except Exception:
                pass
        tasks, self._tasks = self._tasks, []
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        for task in tasks:
            if task is current:
                continue
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._locked:
            self._locked = False
            await arelease_session_lock(self.session.pk, self.owner)

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await arefresh_session_lock(
                self.session.pk, self.owner, VOICE_TTL_SECONDS,
            )

    # ------------------------------------------------------------- upstream
    async def send_audio(self, chunk: bytes):
        """One PCM chunk of the user's microphone."""
        if self.queue is None:
            raise RuntimeError("The voice session is not started.")
        self.queue.send_realtime(
            types.Blob(
                data=chunk,
                mime_type=self.model_config.get("audio_mime_type", AUDIO_MIME_TYPE),
            ),
        )

    async def send_text(self, text):
        """A typed message while in audio mode; answered by voice."""
        if self.queue is None:
            raise RuntimeError("The voice session is not started.")
        self.queue.send_content(
            types.Content(role="user", parts=[types.Part.from_text(text=text)]),
        )

    # ----------------------------------------------------------- downstream
    async def _consume_events(self):
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            # Transcripts for both directions; without these the live
            # API sends audio only.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        try:
            async with vertex_client_scope(location=vertex_location_for(self.mode)):
                async for event in self.runner.run_live(
                    user_id=self.scoped_user,
                    session_id=self.adk_session.id,
                    live_request_queue=self.queue,
                    run_config=run_config,
                ):
                    if event.error_code:
                        raise RuntimeError(f"Live agent failed: {event.error_code}")
                    self._track_usage(event)
                    await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Voice session failed chatbot_id=%s session_id=%s",
                self.chatbot.id,
                self.session.id,
            )
            if self._stream is not None:
                await self._stream.fail(FAILED_VOICE_MESSAGE)
            self._reset_turn()
            if self.on_failure is not None:
                try:
                    await self.on_failure(FAILED_VOICE_MESSAGE)
                except Exception:
                    logger.exception("Voice failure handler failed")

    def _track_usage(self, event):
        if (
            event.usage_metadata
            and not event.partial
            and event.id not in self._seen_usage_events
        ):
            self._seen_usage_events.add(event.id)
            add_event_usage(self.usage, event.usage_metadata)

    async def _handle_event(self, event):
        if event.input_transcription is not None:
            await self._on_user_transcript(event)
        if event.output_transcription is not None:
            await self._on_agent_transcript(event)

        for part in (event.content.parts if event.content else None) or []:
            inline = part.inline_data
            if inline and inline.data:
                await self._ensure_turn()
                await self.send_bytes(bytes(inline.data))

        for response in event.get_function_responses():
            if response.name == RECOMMENDATION_TOOL:
                items = (response.response or {}).get("items") or []
                known = {item["id"] for item in self._recommendations}
                self._recommendations.extend(
                    item for item in items if item.get("id") not in known
                )

        if event.turn_complete or event.interrupted:
            await self._finish_turn(interrupted=bool(event.interrupted))

    async def _ensure_turn(self):
        if self._stream is None:
            self._stream = TurnStream(self.send_json, mode=self.mode)
            await self._stream.start()

    async def _on_user_transcript(self, event):
        """Caption the user's speech; persist each final utterance.

        Partial events carry the next chunk of text (append); the final
        event carries the whole utterance (replace).
        """
        text = event.input_transcription.text or ""
        if not text.strip():
            return
        data = {"partial": bool(event.partial), "text": text}
        if not event.partial:
            try:
                message = await sync_to_async(
                    create_user_message, thread_sensitive=True,
                )(self.session, text, mode=self.mode, dispatch_reply=False)
                data["message_id"] = str(message.pk)
            except ValueError:
                logger.warning("Voice transcript not stored for session %s", self.session.pk)
        await emit_event(self.send_json, {"type": VOICE_TRANSCRIPT, "data": data})

    async def _on_agent_transcript(self, event):
        text = event.output_transcription.text or ""
        if not text:
            return
        if event.partial:
            await self._ensure_turn()
            await self._stream.delta(text)
        elif text.strip():
            self._segments.append(text.strip())

    async def _finish_turn(self, *, interrupted):
        """The agent finished (or was cut off by the user): persist what
        was said, then send the cards and the completed message."""
        stream = self._stream
        text = " ".join(self._segments).strip()
        recommendations = list(self._recommendations)
        self._reset_turn()
        if stream is None and not text:
            return
        if stream is None:
            stream = TurnStream(self.send_json, mode=self.mode)
            await stream.start()
            await stream.delta(text)

        message = None
        if text:
            usage, self.usage = self.usage, TokenUsageSchema()
            self._seen_usage_events.clear()
            response = build_response(
                AgentResultSchema(content=text, recommendations=recommendations),
                usage,
                self.mode,
            )
            try:
                reply = await sync_to_async(persist_reply, thread_sensitive=True)(
                    chatbot=self.chatbot,
                    session=self.session,
                    response=response,
                    model_name=self.model_name,
                    mode=self.mode,
                )
                message = reply["message"]
            except Exception:
                logger.exception("Voice reply not stored for session %s", self.session.pk)
        await stream.finish(
            message=message,
            recommendations=recommendations,
            interrupted=interrupted,
        )
