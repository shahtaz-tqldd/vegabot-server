from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from google.adk.events import Event
from google.genai import types

from agent.helpers.lock import AgentBusyError, acquire_session_lock, session_lock_holder
from agent.tools import RECOMMENDATION_TOOL
from agent.voice import VoiceSession
from chat.models import ChatSession
from chatbot.models import Chatbot

User = get_user_model()

CARD = {
    "type": "service", "id": "s1", "name": "Repair", "description": "",
    "price": "75.00", "currency": "USD", "url": "", "image_url": "",
    "sku": "", "category": "", "service_type": "repair",
}


class Recorder:
    def __init__(self):
        self.json_events = []
        self.audio = []

    async def send_json(self, payload):
        self.json_events.append(payload)

    async def send_bytes(self, data):
        self.audio.append(data)

    def types(self):
        return [event["type"] for event in self.json_events]


class VoiceFixtureMixin:
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email="voice@example.com", password="x")
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Voice Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot, created_by=self.user,
        )
        self.recorder = Recorder()
        self.voice = VoiceSession(
            session=self.session,
            send_json=self.recorder.send_json,
            send_bytes=self.recorder.send_bytes,
            owner="test-channel",
        )


class VoiceStartTests(VoiceFixtureMixin, TestCase):
    def test_start_builds_agent_off_loop_and_holds_the_session_lock(self):
        async def scenario():
            with patch(
                "agent.voice.get_or_create_session",
                new=AsyncMock(return_value=SimpleNamespace(id="adk-session")),
            ), patch.object(VoiceSession, "_consume_events", new=AsyncMock()):
                await self.voice.start()
                holder = session_lock_holder(self.session.pk)
                await self.voice.close()
            return holder

        self.voice.session_service = object()
        holder = async_to_sync(scenario)()

        self.assertEqual(holder, "voice:test-channel")
        self.assertEqual(self.voice.agent.name, "assistant")
        self.assertIsNone(session_lock_holder(self.session.pk))

    def test_start_refuses_a_busy_session(self):
        acquire_session_lock(self.session.pk, "text:other", 60)

        with self.assertRaises(AgentBusyError):
            async_to_sync(self.voice.start)()


@override_settings(AGENT_CLIENT_DEBUG=False)
class VoiceEventTests(VoiceFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.voice.agent = SimpleNamespace(name="assistant")
        self.voice.model_name = "gemini-live-test"

    def handle(self, *events):
        async def scenario():
            for event in events:
                await self.voice._handle_event(event)

        async_to_sync(scenario)()

    def test_user_speech_is_captioned_and_final_stored(self):
        self.handle(
            Event(
                author="user",
                partial=True,
                input_transcription=types.Transcription(text="what are", finished=False),
            ),
            Event(
                author="user",
                input_transcription=types.Transcription(
                    text="What are your opening hours?", finished=True,
                ),
            ),
        )

        stored = self.session.messages.get(sender_type="user")
        self.assertEqual(stored.content, "What are your opening hours?")
        self.assertEqual(stored.mode, "audio")
        self.assertEqual(
            [event["data"] for event in self.recorder.json_events],
            [
                {"partial": True, "text": "what are"},
                {
                    "partial": False,
                    "text": "What are your opening hours?",
                    "message_id": str(stored.pk),
                },
            ],
        )
        self.assertEqual(self.recorder.types(), ["voice.transcript"] * 2)

    def test_agent_turn_streams_text_audio_then_cards_and_message(self):
        self.handle(
            Event(
                author="assistant",
                partial=True,
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_bytes(data=b"pcm", mime_type="audio/pcm")],
                ),
            ),
            Event(
                author="assistant",
                partial=True,
                output_transcription=types.Transcription(text="We offer ", finished=False),
            ),
            Event(
                author="assistant",
                content=types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=RECOMMENDATION_TOOL,
                            response={"status": "ok", "items": [CARD]},
                        ),
                    ],
                ),
            ),
            Event(
                author="assistant",
                partial=True,
                output_transcription=types.Transcription(text="repairs.", finished=False),
            ),
            Event(
                author="assistant",
                output_transcription=types.Transcription(
                    text="We offer repairs.", finished=True,
                ),
            ),
            Event(author="assistant", turn_complete=True),
        )

        self.assertEqual(self.recorder.audio, [b"pcm"])
        self.assertEqual(
            self.recorder.types(),
            [
                "agent.response.started",
                "agent.response.delta",
                "agent.response.delta",
                "agent.response.recommendations",
                "agent.response.completed",
            ],
        )
        completed = self.recorder.json_events[-1]["data"]
        self.assertEqual(completed["mode"], "audio")
        self.assertFalse(completed["interrupted"])
        self.assertEqual(completed["message"]["content"], "We offer repairs.")
        self.assertEqual(completed["message"]["recommendations"], [CARD])

        stored = self.session.messages.get(sender_type="agent")
        self.assertEqual(stored.mode, "audio")
        self.assertEqual(stored.recommendations, [CARD])

    def tool_result(self, name, response):
        return Event(
            author="assistant",
            content=types.Content(
                role="user",
                parts=[types.Part.from_function_response(name=name, response=response)],
            ),
        )

    def test_cards_survive_turn_complete_after_tool_rounds(self):
        """The Live API reports turn_complete after each tool round, before
        any speech; the chosen cards must reach the spoken reply."""
        self.handle(
            self.tool_result("search_products", {"status": "ok", "sources": []}),
            Event(author="assistant", turn_complete=True),
            self.tool_result(RECOMMENDATION_TOOL, {"status": "ok", "items": [CARD]}),
            Event(author="assistant", turn_complete=True),
            Event(
                author="assistant",
                partial=True,
                output_transcription=types.Transcription(text="Try our repair.", finished=False),
            ),
            Event(
                author="assistant",
                output_transcription=types.Transcription(text="Try our repair.", finished=True),
            ),
            Event(author="assistant", turn_complete=True),
        )

        self.assertEqual(
            self.recorder.types(),
            [
                "agent.response.started",
                "agent.response.delta",
                "agent.response.recommendations",
                "agent.response.completed",
            ],
        )
        stored = self.session.messages.get(sender_type="agent")
        self.assertEqual(stored.recommendations, [CARD])

    @override_settings(AGENT_CLIENT_DEBUG=True)
    def test_debug_events_share_the_reply_turn_id(self):
        self.handle(
            self.tool_result(RECOMMENDATION_TOOL, {"status": "ok", "items": [CARD]}),
            Event(author="assistant", turn_complete=True),
            Event(
                author="assistant",
                output_transcription=types.Transcription(text="Try our repair.", finished=True),
            ),
            Event(author="assistant", turn_complete=True),
        )

        debug = [e["data"] for e in self.recorder.json_events if e["type"] == "agent.debug"]
        self.assertEqual(
            [d["stage"] for d in debug],
            ["tool_result", "recommendations_pending", "recommendations_sent"],
        )
        self.assertEqual(debug[0]["items"], [{"id": "s1", "name": "Repair", "type": "service"}])
        completed = self.recorder.json_events[-1]["data"]
        self.assertEqual({d["turn_id"] for d in debug}, {completed["turn_id"]})

    def test_interrupted_turn_stores_what_was_said(self):
        self.handle(
            Event(
                author="assistant",
                partial=True,
                output_transcription=types.Transcription(text="We open", finished=False),
            ),
            Event(
                author="assistant",
                output_transcription=types.Transcription(text="We open", finished=True),
            ),
            Event(author="assistant", interrupted=True),
            Event(author="assistant", turn_complete=True),  # no second completion
        )

        completed = [
            e for e in self.recorder.json_events if e["type"] == "agent.response.completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0]["data"]["interrupted"])
        self.assertEqual(
            self.session.messages.get(sender_type="agent").content, "We open",
        )


class VoiceFailureTests(VoiceFixtureMixin, TestCase):
    def test_live_failure_notifies_the_consumer(self):
        failures = []

        async def on_failure(message):
            failures.append(message)

        async def broken_live(**kwargs):
            raise RuntimeError("socket dropped")
            yield  # pragma: no cover - makes this an async generator

        self.voice.on_failure = on_failure
        self.voice.runner = SimpleNamespace(run_live=broken_live)
        self.voice.scoped_user = "u"
        self.voice.adk_session = SimpleNamespace(id="adk")
        self.voice.queue = None

        async_to_sync(self.voice._consume_events)()

        self.assertEqual(failures, ["The voice session ended unexpectedly."])

