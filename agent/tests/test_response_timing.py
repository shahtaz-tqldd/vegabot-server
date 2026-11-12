"""Response time is the delay to the agent's FIRST output, not to the
end of the reply; agent messages carry their token usage and cost."""

from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from google.adk.events import Event
from google.genai import types

from agent.client import AgentClient
from agent.helpers.usage import build_response
from agent.schema import AgentResultSchema, TokenUsageSchema
from agent.tests.test_reply import ReplyFixtureMixin
from agent.tests.test_voice import VoiceFixtureMixin
from chat.models import ChatMessage
from chat.services.messages import create_agent_message, elapsed_ms

T0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=dt_timezone.utc)


class FakeClock:
    """Patches django.utils.timezone.now (used by models, the stream, and
    the voice session) with a clock the test advances."""

    def __init__(self, start=T0):
        self.now = start
        self._patch = patch("django.utils.timezone.now", side_effect=lambda: self.now)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class ElapsedTests(TestCase):
    def test_elapsed_ms(self):
        self.assertEqual(elapsed_ms(T0, T0 + timedelta(seconds=2.5)), 2500)
        self.assertEqual(elapsed_ms(T0, T0 - timedelta(seconds=1)), 0)
        self.assertIsNone(elapsed_ms(None, T0))
        self.assertIsNone(elapsed_ms(T0, None))


class TextResponseTimeTests(ReplyFixtureMixin, TestCase):
    email = "timing-text@example.com"

    def test_measured_to_first_streamed_chunk(self):
        ChatMessage.objects.filter(pk=self.message.pk).update(created_at=T0)
        self.message.refresh_from_db()
        client = AgentClient.__new__(AgentClient)
        client.chatbot = self.chatbot
        client.session = self.session
        client.model_name = "gemini-test"

        with FakeClock() as clock:
            async def fake_run_turn(turn_text, *, on_delta=None, on_debug=None):
                clock.advance(2)      # thinking / tool calls
                await on_delta("Hello ")
                clock.advance(15)     # the rest of the reply streams
                await on_delta("there.")
                usage = TokenUsageSchema(
                    input_tokens=1000, output_tokens=200, total_tokens=1200,
                )
                return (
                    build_response(AgentResultSchema(content="Hello there."), usage),
                    SimpleNamespace(results={}, selected_tools=[]),
                )

            client._run_turn = fake_run_turn
            with override_settings(
                GEMINI_INPUT_COST_PER_MILLION=0.1,
                GEMINI_OUTPUT_COST_PER_MILLION=0.4,
            ):
                reply = client.generate_reply_sync(
                    user_message=self.message, emit=lambda payload: None,
                )

        self.assertEqual(reply["message"]["response_time_ms"], 2000)
        usage = reply["message"]["usage"]
        self.assertEqual(usage["total_tokens"], 1200)
        self.assertEqual(usage["model"], "gemini-test")
        self.assertAlmostEqual(usage["cost"], 0.00018)


@override_settings(
    AGENT_CLIENT_DEBUG=False,
    GEMINI_LIVE_TEXT_INPUT_COST_PER_MILLION=0.75,
    GEMINI_LIVE_AUDIO_INPUT_COST_PER_MILLION=3.0,
    GEMINI_LIVE_TEXT_OUTPUT_COST_PER_MILLION=4.5,
    GEMINI_LIVE_AUDIO_OUTPUT_COST_PER_MILLION=12.0,
)
class VoiceResponseTimeTests(VoiceFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.voice.agent = SimpleNamespace(name="assistant")
        self.voice.model_name = "gemini-live-test"

    def step(self, *events):
        async def scenario():
            for event in events:
                self.voice._track_usage(event)
                await self.voice._handle_event(event)

        async_to_sync(scenario)()

    def test_measured_from_last_user_input_to_first_agent_audio(self):
        with FakeClock() as clock:
            self.step(Event(
                author="user", partial=True,
                input_transcription=types.Transcription(text="show me", finished=False),
            ))
            clock.advance(1)
            self.step(Event(
                author="user",
                input_transcription=types.Transcription(
                    text="Show me running shoes", finished=True,
                ),
            ))
            clock.advance(2.5)  # tool calls before speaking
            self.step(Event(
                author="assistant", partial=True,
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_bytes(data=b"pcm", mime_type="audio/pcm")],
                ),
            ))
            clock.advance(14)  # the spoken reply
            # Late user captions while the agent talks do not move the start.
            self.step(
                Event(
                    author="user", partial=True,
                    input_transcription=types.Transcription(text="mm", finished=False),
                ),
                Event(
                    author="assistant",
                    output_transcription=types.Transcription(
                        text="Here are two great running shoes.", finished=True,
                    ),
                    usage_metadata=types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=4000,
                        candidates_token_count=500,
                        total_token_count=4500,
                        prompt_tokens_details=[
                            types.ModalityTokenCount(modality="AUDIO", token_count=3000),
                            types.ModalityTokenCount(modality="TEXT", token_count=1000),
                        ],
                        candidates_tokens_details=[
                            types.ModalityTokenCount(modality="AUDIO", token_count=400),
                            types.ModalityTokenCount(modality="TEXT", token_count=100),
                        ],
                    ),
                ),
                Event(author="assistant", turn_complete=True),
            )

        stored = self.session.messages.get(sender_type="agent")
        self.assertEqual(stored.response_time_ms, 2500)

        completed = self.recorder.json_events[-1]["data"]["message"]
        self.assertEqual(completed["response_time_ms"], 2500)
        self.assertEqual(completed["usage"]["total_tokens"], 4500)
        self.assertEqual(completed["usage"]["model"], "gemini-live-test")
        usage = completed["usage"]
        # 3000 audio-in x $3 + 400 audio-out x $12 + 1000 text-in x $0.75
        # + 100 text-out x $4.50, per million tokens.
        self.assertAlmostEqual(usage["cost"], 0.015)
        self.assertEqual(usage["audio_input_tokens"], 3000)
        self.assertEqual(usage["audio_output_tokens"], 400)


class AgentMessageTimingTests(VoiceFixtureMixin, TestCase):
    def test_explicit_response_time_wins(self):
        message = create_agent_message(self.session, "Hi", response_time_ms=1234)
        self.assertEqual(message.response_time_ms, 1234)

    def test_user_messages_have_no_usage(self):
        from chat.api.v1.client.serializers import ChatMessageSerializer

        message = ChatMessage.objects.create(
            chat_session=self.session, sender_type="user", content="Hi",
        )
        self.assertIsNone(ChatMessageSerializer(message).data["usage"])


class LiveCostTests(TestCase):
    @override_settings(
        GEMINI_LIVE_AUDIO_INPUT_COST_PER_MINUTE=0.005,
        GEMINI_LIVE_AUDIO_OUTPUT_COST_PER_MINUTE=0.018,
        GEMINI_LIVE_TEXT_INPUT_COST_PER_MILLION=0.75,
        GEMINI_LIVE_TEXT_OUTPUT_COST_PER_MILLION=4.5,
    )
    def test_without_breakdown_audio_is_priced_per_minute(self):
        from agent.helpers.usage import compute_cost

        usage = TokenUsageSchema(
            input_tokens=5000, output_tokens=900, total_tokens=5900,
            audio_input_seconds=60, audio_output_seconds=30,
        )
        self.assertAlmostEqual(compute_cost(usage, "audio"), 0.005 + 0.009)

    def test_pcm_seconds_reads_the_rate(self):
        from agent.voice import pcm_seconds

        self.assertEqual(pcm_seconds(32000, "audio/pcm;rate=16000", 24000), 1)
        self.assertEqual(pcm_seconds(48000, "audio/pcm", 24000), 1)


class PaceInstructionTests(TestCase):
    def test_pace_only_in_voice_mode(self):
        from agent.helpers.instruction import build_instruction

        profile = {"spaces": {}, "chatbot": {"speaking_pace": "slow"}}
        self.assertIn("Speak slowly", build_instruction(profile, mode="audio"))
        self.assertNotIn("Speak slowly", build_instruction(profile, mode="text"))
        normal = {"spaces": {}, "chatbot": {"speaking_pace": "normal"}}
        self.assertNotIn("Speak slowly", build_instruction(normal, mode="audio"))
