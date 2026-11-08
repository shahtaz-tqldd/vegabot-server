from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.chat_agent.reply import generate_agent_reply
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatSessionStatus
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
