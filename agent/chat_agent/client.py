import inspect
from contextlib import aclosing

from asgiref.sync import async_to_sync, sync_to_async
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.genai import types

from agent.chat_agent.root_agent import build_root_agent
from agent.helpers.model import vertex_client_scope
from agent.helpers.persistence import agent_reply_exists, persist_reply
from agent.helpers.profile import cached_agent_profile
from agent.helpers.public_reply import build_public_reply
from agent.helpers.runtime import (
    build_app,
    build_session_service,
    get_or_create_session,
)
from agent.helpers.turn_collector import TurnCollector
from agent.helpers.usage import build_response
from app.utils.logger import logger
from chat.services.multimodal import build_message_parts


class ChatAgentClient:
    """Orchestrate one chat turn: validate -> run agent -> persist reply.

    The chatbot profile (identity, policy, knowledge-space flags) is read
    through the cache, so building a client costs no database queries on
    the hot path.
    """

    app_name = "dison_chat_agent"
    MESSAGE_TEXT_LIMIT = 8000
    MAX_LLM_CALLS = 12
    MIN_TOKENS_FOR_CONTEXT = 2048
    CACHE_TTL_SECONDS = 600
    CACHE_INTERVALS = 5
    COMPACTION_INTERVAL = 4
    COMPACTION_OVERLAP = 1

    def __init__(self, chatbot, session, *, profile=None, session_service=None):
        if str(session.chatbot_id) != str(chatbot.id):
            raise ValueError("The conversation does not belong to this chatbot.")

        self.chatbot = chatbot
        self.session = session
        self.profile = profile or cached_agent_profile(chatbot)
        self.session_service = session_service or build_session_service()
        self.chat_agent = build_root_agent(
            chatbot,
            session,
            profile=self.profile,
            business_policy=self.profile.get("policy", ""),
        )
        self.app = build_app(
            name=self.app_name,
            root_agent=self.chat_agent,
            compaction_interval=self.COMPACTION_INTERVAL,
            compaction_overlap=self.COMPACTION_OVERLAP,
            cache_min_tokens=self.MIN_TOKENS_FOR_CONTEXT,
            cache_ttl_seconds=self.CACHE_TTL_SECONDS,
            cache_intervals=self.CACHE_INTERVALS,
        )
        self.runner = Runner(app=self.app, session_service=self.session_service)

        logger.info(
            "Chat agent initialized chatbot_id=%s session_id=%s root_tools=%s",
            chatbot.id,
            session.id,
            [tool.name for tool in self.chat_agent.tools],
        )

    # --- validation ---
    def _check_validation(self, message):
        if self.chatbot.is_deleted:
            raise ValueError("Chatbot is deleted.")
        if not self.chatbot.is_active:
            raise ValueError("Chatbot is deactivated.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must not be empty.")
        if len(message) > self.MESSAGE_TEXT_LIMIT:
            raise ValueError(
                f"message exceeds {self.MESSAGE_TEXT_LIMIT} character limit."
            )

    def _scoped_user(self, user_id):
        return f"{self.chatbot.id}:{user_id if user_id is not None else self.session.id}"

    # --- multimodal turn text ---
    @staticmethod
    def _turn_text(user_message):
        """Flatten one message (text + attachments) into turn text.

        Extracted document text is inlined so the model can answer from
        files; images are noted (visual analysis arrives with the live
        agent's native audio/video pipeline).
        """
        sections = []
        for part in build_message_parts(user_message):
            if part["type"] == "text":
                sections.append(part["text"])
            elif part["type"] == "file_text":
                sections.append(
                    f"[Attached file: {part.get('name') or 'document'}]\n"
                    f"{part['text']}"
                )
            elif part["type"] == "image":
                sections.append(f"[Image attached: {part.get('name') or 'image'}]")
        return "\n\n".join(sections)

    # --- core turn ---
    async def _run_turn(self, turn_text, user_id, *, on_delta=None):
        """Run one turn and return the internal response envelope.

        With ``on_delta`` (sync or async callable taking one text chunk),
        the model streams: partial chunks are forwarded as produced and
        the final aggregated response is returned/persisted as usual.
        """
        scoped_user = self._scoped_user(user_id)
        adk_session = await get_or_create_session(
            self.session_service,
            app_name=self.app_name,
            user_id=scoped_user,
            session_id=self.session.id,
        )

        collector = TurnCollector(root_name=self.chat_agent.name)
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=turn_text)],
        )
        run_config = RunConfig(
            max_llm_calls=self.MAX_LLM_CALLS,
            streaming_mode=(
                StreamingMode.SSE if on_delta is not None else StreamingMode.NONE
            ),
        )

        async with vertex_client_scope(), aclosing(
            self.runner.run_async(
                user_id=scoped_user,
                session_id=adk_session.id,
                new_message=new_message,
                run_config=run_config,
            )
        ) as events:
            async for event in events:
                if on_delta is not None:
                    await self._forward_stream_delta(event, on_delta)
                collector.observe(event)

        fallback = self.chatbot.fallback_message
        result, source = collector.build_result(
            session_state=adk_session.state, fallback_message=fallback,
        )
        collector.log_summary(
            chatbot_id=self.chatbot.id,
            session_id=self.session.id,
            source=source,
            fallback_message=fallback,
        )
        return build_response(result, collector.usage), collector

    # --- streaming ---
    async def _forward_stream_delta(self, event, on_delta):
        """Forward one partial text chunk to the callback; best-effort."""
        if not event.partial or event.author != self.chat_agent.name:
            return
        content = event.content
        if not content or any(
            part.function_call for part in content.parts or []
        ):
            return
        chunk = "".join(
            part.text
            for part in content.parts or []
            if part.text and not part.thought
        )
        if not chunk:
            return
        try:
            outcome = on_delta(chunk)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            logger.exception("Stream delta callback failed")

    # --- reply generation ---
    async def generate_reply(self, user_message, *, on_delta=None):
        """Answer one user message; returns the persisted reply payload."""
        if str(user_message.chat_session_id) != str(self.session.id):
            raise ValueError("The message does not belong to this session.")

        if await sync_to_async(agent_reply_exists, thread_sensitive=True)(
            self.session, user_message.id,
        ):
            return None

        self._check_validation(user_message.content)
        response, collector = await self._run_turn(
            self._turn_text(user_message),
            user_message.created_by_id,
            on_delta=on_delta,
        )
        public_reply = build_public_reply(response, collector.results)
        return await sync_to_async(persist_reply, thread_sensitive=True)(
            chatbot=self.chatbot,
            session=self.session,
            user_message=user_message,
            response=response,
            source_metadata=public_reply["sources"],
        )

    def generate_reply_sync(self, **kwargs):
        return async_to_sync(self.generate_reply)(**kwargs)

    def stream_reply_sync(self, **kwargs):
        """generate_reply_sync with streaming: pass an ``on_delta``
        callable to receive partial text."""
        return async_to_sync(self.generate_reply)(**kwargs)
