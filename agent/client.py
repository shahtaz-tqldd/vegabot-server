"""Text mode of the agent: one streamed reply per typed message.

The voice counterpart is ``agent.voice.VoiceSession``; both run the same
root agent (``agent.root_agent``) against the same ADK session, so the
user can switch between typing and talking mid-conversation.
"""

from contextlib import aclosing
from functools import partial

from asgiref.sync import async_to_sync, sync_to_async
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from agent.helpers.debug import card_summary, emit_debug, tool_diagnostics
from agent.helpers.model import (
    load_model_config,
    model_name_for,
    vertex_client_scope,
)
from agent.helpers.persistence import agent_reply_exists, persist_reply
from agent.helpers.profile import cached_agent_profile
from agent.helpers.public_reply import build_source_metadata
from agent.helpers.runtime import (
    APP_NAME,
    build_runner,
    build_session_service,
    get_or_create_session,
    scoped_user_id,
)
from agent.helpers.stream import TurnStream
from agent.helpers.turn_collector import TurnCollector
from agent.helpers.usage import build_response
from agent.root_agent import build_root_agent
from app.utils.logger import logger
from chat.models import ChatSession
from chat.services.multimodal import build_message_parts
from chat.utils.choices import ChatMode, ChatSessionStatus

FAILED_REPLY_MESSAGE = "The assistant could not answer right now. Please try again."


class AgentUnavailableError(ValueError):
    """The session is closed or its chatbot cannot answer."""


def load_session_for_turn(session_id):
    """A fresh session + chatbot, checked for answering (sync ORM)."""
    session = ChatSession.objects.select_related("chatbot").get(pk=session_id)
    chatbot = session.chatbot
    if session.status != ChatSessionStatus.OPEN or chatbot.is_deleted:
        raise AgentUnavailableError("The chat session is closed.")
    if not chatbot.is_active:
        raise AgentUnavailableError("The chatbot is deactivated.")
    return session


class AgentClient:
    """Orchestrate one text turn: run agent (streamed) -> persist reply.

    The chatbot profile (identity, policy, knowledge-space flags) is read
    through the cache. Construct in sync code (or via sync_to_async): it
    reads the model configuration from the database.
    """

    mode = ChatMode.TEXT
    MAX_LLM_CALLS = 12

    def __init__(
        self,
        chatbot,
        session,
        *,
        profile=None,
        model_config=None,
        session_service=None,
    ):
        if str(session.chatbot_id) != str(chatbot.id):
            raise ValueError("The conversation does not belong to this chatbot.")

        self.chatbot = chatbot
        self.session = session
        self.profile = profile or cached_agent_profile(chatbot)
        self.model_config = (
            load_model_config(self.mode) if model_config is None else model_config
        )
        self.model_name = model_name_for(self.mode, self.model_config)
        self.session_service = session_service or build_session_service()
        self.agent = build_root_agent(
            chatbot,
            session,
            profile=self.profile,
            mode=self.mode,
            model_config=self.model_config,
        )
        self.runner = build_runner(self.agent, self.session_service)

        logger.info(
            "Agent initialized mode=%s chatbot_id=%s session_id=%s tools=%s",
            self.mode,
            chatbot.id,
            session.id,
            [tool.name for tool in self.agent.tools],
        )

    @classmethod
    def for_session(cls, session_id, **kwargs):
        session = load_session_for_turn(session_id)
        return cls(session.chatbot, session, **kwargs)

    # --- multimodal turn text ---
    @staticmethod
    def _turn_text(user_message):
        """Flatten one message (text + attachments) into turn text.

        Extracted document text is inlined so the model can answer from
        files; images are noted by name.
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
    async def _run_turn(self, turn_text, *, on_delta=None, on_debug=None):
        """Run one turn; partial text chunks go to ``on_delta``, tool
        diagnostics to ``on_debug(stage, **data)``."""
        scoped_user = scoped_user_id(self.session)
        adk_session = await get_or_create_session(
            self.session_service,
            app_name=APP_NAME,
            user_id=scoped_user,
            session_id=self.session.id,
        )

        collector = TurnCollector(root_name=self.agent.name)
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=turn_text)],
        )
        run_config = RunConfig(
            max_llm_calls=self.MAX_LLM_CALLS,
            streaming_mode=StreamingMode.SSE,
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
                    chunk = self._stream_chunk(event)
                    if chunk:
                        await on_delta(chunk)
                if on_debug is not None:
                    for stage, data in tool_diagnostics(event):
                        await on_debug(stage, **data)
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
        return build_response(result, collector.usage, self.mode), collector

    def _stream_chunk(self, event):
        """The visible text of one partial event (no tool calls/thoughts)."""
        if not event.partial or event.author != self.agent.name:
            return ""
        content = event.content
        if not content or any(part.function_call for part in content.parts or []):
            return ""
        return "".join(
            part.text
            for part in content.parts or []
            if part.text and not part.thought
        )

    # --- reply generation ---
    async def generate_reply(self, user_message, *, emit=None):
        """Answer one user message and stream it through ``emit``.

        ``emit(payload)`` (sync or async) receives the agent.response.*
        events; returns the persisted reply payload, or None when the
        message was already answered.
        """
        if str(user_message.chat_session_id) != str(self.session.id):
            raise ValueError("The message does not belong to this session.")

        if await sync_to_async(agent_reply_exists, thread_sensitive=True)(
            self.session, user_message.id,
        ):
            return None

        turn_text = await sync_to_async(self._turn_text, thread_sensitive=True)(
            user_message,
        )
        if not turn_text.strip():
            raise ValueError("message must not be empty.")

        stream = TurnStream(
            emit,
            mode=self.mode,
            turn_id=user_message.id,
            reply_to=user_message.id,
            strip_citations=True,
        )
        await stream.start()
        debug = partial(
            emit_debug, emit, mode=self.mode, turn_id=stream.turn_id,
        )
        try:
            response, collector = await self._run_turn(
                turn_text, on_delta=stream.delta, on_debug=debug,
            )
            sources = build_source_metadata(response["result"], collector.results)
            reply = await sync_to_async(persist_reply, thread_sensitive=True)(
                chatbot=self.chatbot,
                session=self.session,
                user_message=user_message,
                response=response,
                model_name=self.model_name,
                mode=self.mode,
                source_metadata=sources,
            )
        except Exception:
            await stream.fail(FAILED_REPLY_MESSAGE)
            raise

        recommendations = response["result"]["recommendations"]
        await debug(
            "recommendations_sent" if recommendations else "no_recommendations",
            items=card_summary(recommendations),
            selected_tools=collector.selected_tools,
        )
        await stream.finish(
            message=reply["message"],
            sources=reply["sources"],
            recommendations=response["result"]["recommendations"],
        )
        return reply

    def generate_reply_sync(self, **kwargs):
        return async_to_sync(self.generate_reply)(**kwargs)
