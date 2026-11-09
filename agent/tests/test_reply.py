from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from agent.client import AgentClient
from agent.helpers.lock import acquire_session_lock
from agent.reply import generate_agent_reply
from agent.schema import AgentResultSchema, TokenUsageSchema
from agent.helpers.usage import build_response
from chat.models import ChatMessage, ChatMessageAttachment, ChatSession
from chat.utils.choices import (
    AttachmentProcessStatus,
    ChatMessageAttachmentType,
    ChatSessionStatus,
)
from chatbot.models import Chatbot

User = get_user_model()


class ReplyFixtureMixin:
    email = "reply@example.com"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email=self.email, password="x")
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Reply Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.user,
        )
        self.message = ChatMessage.objects.create(
            chat_session=self.session,
            sender_type="user",
            content="What products do you sell?",
        )


class GenerateAgentReplyTaskTests(ReplyFixtureMixin, TestCase):
    def test_skips_closed_sessions(self):
        self.session.status = ChatSessionStatus.CLOSED
        self.session.save(update_fields=["status"])

        result = generate_agent_reply(str(self.message.pk))

        self.assertIn("closed", result["skipped"])

    def test_skips_when_agent_already_replied(self):
        ChatMessage.objects.create(
            chat_session=self.session,
            sender_type="agent",
            content="Here is the catalog.",
        )

        with patch.object(AgentClient, "generate_reply_sync") as generate, patch(
            "agent.client.build_session_service", return_value=object(),
        ):
            result = generate_agent_reply(str(self.message.pk))

        generate.assert_not_called()
        self.assertEqual(result["reply"], None)

    def test_streams_reply_to_the_session_group(self):
        with patch.object(
            AgentClient, "generate_reply_sync", return_value={"message": {"id": "m"}},
        ) as generate, patch(
            "agent.client.build_session_service", return_value=object(),
        ):
            result = generate_agent_reply(str(self.message.pk))

        generate.assert_called_once()
        kwargs = generate.call_args.kwargs
        self.assertEqual(kwargs["user_message"], self.message)
        self.assertEqual(kwargs["emit"].args, (self.session.pk,))
        self.assertEqual(result["reply"], {"message": {"id": "m"}})

    def test_waits_for_and_gives_up_on_a_busy_session(self):
        acquire_session_lock(self.session.pk, "voice:other", 60)

        with patch("agent.reply.LOCK_WAIT_SECONDS", 0):
            result = generate_agent_reply(str(self.message.pk))

        self.assertEqual(result["skipped"], "busy")


class GenerateReplyTests(ReplyFixtureMixin, TestCase):
    """generate_reply builds turn text off the event loop, streams the
    agent.response.* sequence, and persists the reply with its cards."""

    email = "turntext@example.com"

    def setUp(self):
        super().setUp()
        ChatMessageAttachment.objects.create(
            chat_message=self.message,
            attachment_type=ChatMessageAttachmentType.DOCUMENT,
            file_url="https://example.com/menu.txt",
            file_key="menu.txt",
            file_name="menu.txt",
            mime_type="text/plain",
            file_size=16,
            status=AttachmentProcessStatus.PROCESSED,
            extracted_text="We sell shoes.",
        )
        # Bypass __init__: the ADK stack is not needed here.
        self.client = AgentClient.__new__(AgentClient)
        self.client.chatbot = self.chatbot
        self.client.session = self.session
        self.client.model_name = "gemini-test"
        self.card = {
            "type": "product", "id": "p1", "name": "Runner", "description": "",
            "price": "50.00", "currency": "USD", "url": "", "sku": "",
            "category": "", "service_type": "",
        }

    def _fake_turn(self, captured):
        async def fake_run_turn(turn_text, *, on_delta=None):
            captured["turn_text"] = turn_text
            await on_delta("Try the Runner [K")
            await on_delta("1].")
            result = AgentResultSchema(
                content="Try the Runner.",
                source_ids=["p1"],
                recommendations=[self.card],
            )
            return (
                build_response(result, TokenUsageSchema()),
                SimpleNamespace(results={"p1": {"space": "product", "name": "Runner"}}),
            )

        return fake_run_turn

    def test_streams_and_persists_reply_with_recommendations(self):
        captured = {}
        events = []
        self.client._run_turn = self._fake_turn(captured)

        reply = self.client.generate_reply_sync(
            user_message=self.message, emit=events.append,
        )

        self.assertIn("What products do you sell?", captured["turn_text"])
        self.assertIn("We sell shoes.", captured["turn_text"])
        self.assertEqual(
            [event["type"] for event in events],
            [
                "agent.response.started",
                "agent.response.delta",
                "agent.response.delta",
                "agent.response.recommendations",
                "agent.response.completed",
            ],
        )
        streamed = "".join(
            e["data"]["delta"] for e in events if e["type"] == "agent.response.delta"
        )
        self.assertEqual(streamed, "Try the Runner .")  # markers never streamed
        self.assertEqual(events[3]["data"]["items"], [self.card])
        completed = events[-1]["data"]
        self.assertEqual(completed["turn_id"], str(self.message.pk))
        self.assertEqual(completed["mode"], "text")
        self.assertEqual(completed["message"]["recommendations"], [self.card])
        self.assertEqual(completed["sources"][0]["id"], "p1")

        stored = self.session.messages.get(sender_type="agent")
        self.assertEqual(stored.content, "Try the Runner.")
        self.assertEqual(stored.mode, "text")
        self.assertEqual(stored.recommendations, [self.card])
        self.assertEqual(reply["message"]["id"], str(stored.pk))

    def test_failure_emits_failed_event(self):
        events = []

        async def broken_turn(turn_text, *, on_delta=None):
            raise RuntimeError("model down")

        self.client._run_turn = broken_turn

        with self.assertRaises(RuntimeError):
            self.client.generate_reply_sync(
                user_message=self.message, emit=events.append,
            )

        self.assertEqual(
            [event["type"] for event in events],
            ["agent.response.started", "agent.response.failed"],
        )
        self.assertFalse(self.session.messages.filter(sender_type="agent").exists())
