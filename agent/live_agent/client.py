"""The live (voice) agent session on ADK's experimental live pipeline.

The socket consumer owns transport (see base/socket/services/live_agent);
this module owns the agent: one Runner in live mode per websocket
connection, fed by a LiveRequestQueue. User audio flows upstream as PCM
chunks; the model's audio, transcripts, and tool activity flow back down
through the consumer, and final spoken replies are persisted as agent
chat messages so the session history stays complete.
"""

import asyncio

from django.conf import settings
from asgiref.sync import sync_to_async
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import LiveRequestQueue, Runner
from google.genai import types

from agent.helpers.model import vertex_client_scope
from agent.helpers.profile import cached_agent_profile
from agent.helpers.runtime import (
    build_app,
    build_session_service,
    get_or_create_session,
)
from agent.schema import TokenUsageSchema
from agent.helpers.usage import add_event_usage, build_response
from agent.live_agent.root_agent import build_live_root_agent
from app.utils.logger import logger
from chat.services.messages import create_agent_message, create_user_message
from chat.utils.choices import ChatSessionStatus

AUDIO_MIME_TYPE = "audio/pcm;rate=16000"


class AdkLiveAgentSession:
    """Adapter between the socket consumer and an ADK live run."""

    app_name = "dison_live_agent"
    MAX_LLM_CALLS = 12

    def __init__(self, consumer, *, session, model_config=None):
        self.consumer = consumer
        self.session = session
        self.model_config = model_config or {}
        self.chatbot = session.chatbot
        self.queue = None
        self._task = None
        self.usage = TokenUsageSchema()
        self._seen_usage_events = set()

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        profile = await sync_to_async(
            cached_agent_profile, thread_sensitive=True,
        )(self.chatbot)
        # build_live_root_agent reads DisonConfig via live_model(); keep
        # every ORM access off the socket's event loop.
        self.agent = await sync_to_async(
            build_live_root_agent, thread_sensitive=True,
        )(
            self.chatbot,
            self.session,
            profile=profile,
            business_policy=profile.get("policy", ""),
            model_config=self.model_config,
        )
        session_service = build_session_service()
        app = build_app(
            name=self.app_name,
            root_agent=self.agent,
            compaction_interval=4,
            compaction_overlap=1,
            cache_min_tokens=2048,
            cache_ttl_seconds=600,
            cache_intervals=5,
        )
        self.runner = Runner(app=app, session_service=session_service)
        self.scoped_user = f"{self.chatbot.id}:{self.session.created_by_id}"
        self.adk_session = await get_or_create_session(
            session_service,
            app_name=self.app_name,
            user_id=self.scoped_user,
            session_id=self.session.id,
        )

        self.queue = LiveRequestQueue()
        self._task = asyncio.create_task(self._consume_events())

    async def close(self):
        if self.queue is not None:
            try:
                self.queue.close()
            except Exception:
                pass
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    # ------------------------------------------------------------- upstream
    async def send_audio(self, chunk: bytes):
        """One audio chunk from the user; streamed into the live run."""
        if self.queue is None:
            raise RuntimeError("The live session is not started.")
        self.queue.send_realtime(
            types.Blob(
                data=chunk,
                mime_type=self.model_config.get(
                    "audio_mime_type", AUDIO_MIME_TYPE,
                ),
            ),
        )

    async def interrupt(self):
        """The user started speaking again; submit a turn boundary."""
        if self.queue is None:
            return
        try:
            self.queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=".")],
                ),
            )
        except Exception:
            logger.debug("Interrupt ignored on a closed live queue.")

    # ----------------------------------------------------------- downstream
    async def _consume_events(self):
        run_config = RunConfig(
            max_llm_calls=self.MAX_LLM_CALLS,
            streaming_mode=StreamingMode.BIDI,
            # Stream spoken-word transcripts for both directions; without
            # these the live API sends audio only.
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )
        try:
            async with vertex_client_scope(
                location=settings.GOOGLE_CLOUD_LIVE_CHAT_LOCATION,
            ):
                async for event in self.runner.run_live(
                    user_id=self.scoped_user,
                    session_id=self.adk_session.id,
                    live_request_queue=self.queue,
                    run_config=run_config,
                ):
                    if event.error_code:
                        raise RuntimeError(
                            f"Live agent failed: {event.error_code}",
                        )
                    self._track_usage(event)
                    await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Live agent run failed chatbot_id=%s session_id=%s",
                self.chatbot.id,
                self.session.id,
            )
            await self.consumer.send_json({
                "type": "live.error",
                "message": "The live session ended unexpectedly.",
            })

    def _track_usage(self, event):
        if (
            event.usage_metadata
            and not event.partial
            and event.id not in self._seen_usage_events
        ):
            self._seen_usage_events.add(event.id)
            add_event_usage(self.usage, event.usage_metadata)

    async def _handle_event(self, event):
        await self._handle_transcription_event(event)
        content = event.content
        if not content:
            return
        for part in content.parts or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                await self.consumer.send(bytes(inline.data))
            elif part.text and not getattr(part, "thought", False):
                if event.author == self.agent.name and not event.partial:
                    await self._persist_agent_reply(part.text)
                # Streaming captions come from the transcription events;
                # partial turn text is not duplicated to the socket.

    async def _handle_transcription_event(self, event):
        """Stream speech transcripts and persist the finals.

        Input (user) transcription is the only text form user audio
        takes, so its final becomes a stored user message. Output
        (agent) transcription only captions the spoken audio; the
        agent's stored reply comes from the final text content.
        """
        transcription = event.input_transcription or event.output_transcription
        if transcription is None or not (transcription.text or "").strip():
            return
        is_user = event.input_transcription is not None
        await self.consumer.send_json({
            "type": "live.transcript",
            "partial": bool(event.partial),
            "channel": "user" if is_user else "agent",
            "text": transcription.text,
        })
        if event.partial or not transcription.finished:
            return
        if is_user:
            await sync_to_async(create_user_message, thread_sensitive=True)(
                self.session, transcription.text,
            )

    async def _persist_agent_reply(self, text):
        """Persist one final spoken reply with its usage and cost."""
        session = await sync_to_async(
            lambda: type(self.session)
            .objects.select_related("chatbot")
            .get(pk=self.session.pk),
            thread_sensitive=True,
        )()
        if session.status != ChatSessionStatus.OPEN:
            return

        response = build_response_from_text(text, self.usage)
        self.usage = TokenUsageSchema()
        self._seen_usage_events.clear()

        def _persist():
            message = create_agent_message(session, text)
            if response["token"].get("total_tokens", 0):
                record_live_usage(chatbot=session.chatbot, session=session,
                                  message=message, response=response)
            return {"message_id": str(message.id), "content": text}

        await sync_to_async(_persist, thread_sensitive=True)()


def build_response_from_text(text, usage):
    from agent.schema import AgentResultSchema

    return build_response(AgentResultSchema(content=text), usage)


def record_live_usage(*, chatbot, session, message, response):
    from django.conf import settings as django_settings

    from analytics.services.ai_usage import record_ai_usage
    from analytics.utils.choices import AIUsageType

    record_ai_usage(
        chatbot=chatbot,
        chat_session=session,
        chat_message=message,
        usage_type=AIUsageType.CHAT,
        cost=response["cost"],
        token_usage=response["token"],
        model=django_settings.GEMINI_CHAT_MODEL,
    )


def create_live_agent_session(consumer, *, session, model_config=None):
    """Factory used by base.socket.services.live_agent."""
    return AdkLiveAgentSession(
        consumer,
        session=session,
        model_config=model_config,
    )
