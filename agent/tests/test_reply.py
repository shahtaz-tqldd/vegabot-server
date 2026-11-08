from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.chat_agent.client import ChatAgentClient
from agent.chat_agent.reply import generate_agent_reply
from chat.models import ChatMessage, ChatMessageAttachment, ChatSession
from chat.utils.choices import (
    AttachmentProcessStatus,
    ChatMessageAttachmentType,
    ChatSessionStatus,
)
from chatbot.models import Chatbot

User = get_user_model()


class GenerateAgentReplyTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reply@example.com", password="x",
        )
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

    def test_skips_closed_sessions(self):
        self.session.status = ChatSessionStatus.CLOSED
        self.session.save(update_fields=["status"])

        result = generate_agent_reply(str(self.message.pk))

        self.assertEqual(result["skipped"], "closed")

    def test_skips_when_agent_already_replied(self):
        ChatMessage.objects.create(
            chat_session=self.session,
            sender_type="agent",
            content="Here is the catalog.",
        )

        with patch("agent.chat_agent.client.ChatAgentClient") as client_class:
            result = generate_agent_reply(str(self.message.pk))

        client_class.assert_not_called()
        self.assertEqual(result["reply"], None)

    def test_runs_the_agent_client_for_open_sessions(self):
        with patch("agent.chat_agent.client.ChatAgentClient") as client_class:
            client_class.return_value.generate_reply_sync.return_value = {
                "message_id": "agent-message",
            }
            result = generate_agent_reply(str(self.message.pk))

        client_class.assert_called_once()
        client_class.return_value.generate_reply_sync.assert_called_once_with(
            user_message=self.message,
        )
        self.assertEqual(
            result["reply"],
            {"message_id": "agent-message"},
        )


class GenerateReplyTurnTextTests(TestCase):
    """generate_reply must build turn text without sync ORM calls inside
    the event loop (celery fails with SynchronousOnlyOperation)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="turntext@example.com", password="x",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Turn Text Bot",
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

    def test_turn_text_built_from_async_without_sync_orm(self):
        # Bypass __init__: the ADK stack is not needed to exercise the
        # async wrapper around _turn_text.
        client = ChatAgentClient.__new__(ChatAgentClient)
        client.chatbot = self.chatbot
        client.session = self.session
        client.chat_agent = SimpleNamespace(name="Turn Text Bot")

        captured = {}

        async def fake_run_turn(turn_text, user_id, *, on_delta=None):
            captured["turn_text"] = turn_text
            return {"text": "ok"}, SimpleNamespace(results=[], usage={})

        client._run_turn = fake_run_turn

        with patch(
            "agent.chat_agent.client.build_public_reply",
            return_value={"sources": []},
        ), patch(
            "agent.chat_agent.client.persist_reply",
            return_value={"id": "agent-reply"},
        ) as persist:
            reply = client.generate_reply_sync(user_message=self.message)

        self.assertEqual(reply, {"id": "agent-reply"})
        persist.assert_called_once()
        self.assertIn("What products do you sell?", captured["turn_text"])
        self.assertIn("We sell shoes.", captured["turn_text"])
