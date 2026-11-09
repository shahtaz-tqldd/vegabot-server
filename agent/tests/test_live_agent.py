import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync

from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.live_agent.client import AdkLiveAgentSession
from agent.schema import TokenUsageSchema
from chat.models import ChatSession
from chatbot.models import Chatbot

User = get_user_model()


class AdkLiveAgentSessionStartTests(TestCase):
    """start() must not touch the ORM from the event loop.

    build_live_root_agent reads DisonConfig (live_model); calling it
    directly raised SynchronousOnlyOperation inside the socket loop.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="live-start@example.com", password="x",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Live Start Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.user,
        )

    def test_start_builds_agent_without_sync_orm(self):
        agent_session = AdkLiveAgentSession.__new__(AdkLiveAgentSession)
        agent_session.consumer = None
        agent_session.session = self.session
        agent_session.model_config = {}
        agent_session.chatbot = self.chatbot
        agent_session.usage = TokenUsageSchema()
        agent_session._seen_usage_events = set()
        agent_session.queue = None
        agent_session._task = None

        async def scenario():
            with patch(
                "agent.live_agent.client.build_session_service",
                return_value=object(),
            ), patch(
                "agent.live_agent.client.get_or_create_session",
                new=AsyncMock(return_value=SimpleNamespace(id="adk-session")),
            ):
                async def idle():
                    await asyncio.sleep(0)

                agent_session._consume_events = idle
                await agent_session.start()
            await agent_session.close()

        async_to_sync(scenario)()
        self.assertIsNotNone(agent_session.agent)


class RecordingConsumer:
    def __init__(self):
        self.json_events = []

    async def send_json(self, payload):
        self.json_events.append(payload)

    async def send(self, data):
        pass


def make_event(**kwargs):
    defaults = dict(
        content=None,
        input_transcription=None,
        output_transcription=None,
        partial=False,
        author="user",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TranscriptionEventTests(TestCase):
    """Transcription events stream as live.transcript; finals persist."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="live-transcript@example.com", password="x",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Transcript Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.user,
        )
        self.consumer = RecordingConsumer()
        self.agent_session = AdkLiveAgentSession.__new__(AdkLiveAgentSession)
        self.agent_session.consumer = self.consumer
        self.agent_session.session = self.session
        self.agent_session.model_config = {}
        self.agent_session.chatbot = self.chatbot
        self.agent_session.agent = SimpleNamespace(name="live_agent")
        self.agent_session.usage = TokenUsageSchema()
        self.agent_session._seen_usage_events = set()

    def test_input_transcription_streams_and_persists_user_message(self):
        async def scenario():
            await self.agent_session._handle_event(make_event(
                input_transcription=SimpleNamespace(
                    text="what are", finished=False,
                ),
                partial=True,
            ))
            await self.agent_session._handle_event(make_event(
                input_transcription=SimpleNamespace(
                    text="What are your opening hours?", finished=True,
                ),
            ))

        async_to_sync(scenario)()

        self.assertEqual(
            self.consumer.json_events,
            [
                {
                    "type": "live.transcript",
                    "partial": True,
                    "channel": "user",
                    "text": "what are",
                },
                {
                    "type": "live.transcript",
                    "partial": False,
                    "channel": "user",
                    "text": "What are your opening hours?",
                },
            ],
        )
        self.assertTrue(
            self.session.messages.filter(
                sender_type="user",
                content="What are your opening hours?",
            ).exists(),
        )

    def test_output_transcription_streams_without_persisting(self):
        async def scenario():
            await self.agent_session._handle_event(make_event(
                output_transcription=SimpleNamespace(
                    text="We are open", finished=False,
                ),
                partial=True,
                author="live_agent",
            ))

        async_to_sync(scenario)()

        self.assertEqual(
            self.consumer.json_events,
            [{
                "type": "live.transcript",
                "partial": True,
                "channel": "agent",
                "text": "We are open",
            }],
        )
        self.assertEqual(self.session.messages.count(), 0)

    def test_agent_final_content_text_persists_without_caption(self):
        async def scenario():
            await self.agent_session._handle_event(make_event(
                content=SimpleNamespace(parts=[
                    SimpleNamespace(text="We open at 9am.", thought=False),
                ]),
                author="live_agent",
            ))

        async_to_sync(scenario)()

        self.assertEqual(self.consumer.json_events, [])
        self.assertTrue(
            self.session.messages.filter(
                sender_type="agent",
                content="We open at 9am.",
            ).exists(),
        )
