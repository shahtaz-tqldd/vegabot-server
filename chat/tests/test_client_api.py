from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType, ChatSessionStatus
from chatbot.models import Chatbot

User = get_user_model()

SESSION_CREATE_URL = reverse("chat-session-create")
SESSION_LIST_URL = reverse("chat-session-list")
SESSION_DETAILS_URL = reverse("chat-session-detail")
SESSION_DELETE_URL = reverse("chat-session-delete")
MESSAGE_LIST_URL = reverse("chat-message-list")
MESSAGE_SEND_URL = reverse("chat-message-send")
STATS_URL = reverse("session-stats")


class ChatClientAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user@example.com",
            password="StrongPass123!",
            name="User",
        )
        self.superadmin = User.objects.create_user(
            email="superadmin@example.com",
            password="StrongPass123!",
            name="Super Admin",
            is_staff=True,
            is_superuser=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
            name="Other User",
        )
        self.own_chatbot = self.create_chatbot(self.user, "My Store Bot")
        self.demo_chatbot = self.create_chatbot(self.superadmin, "Demo Bot")
        self.other_chatbot = self.create_chatbot(self.other_user, "Private Bot")
        self.client.force_authenticate(self.user)

    def create_chatbot(self, created_by, name):
        return Chatbot.objects.create(
            chatbot_name=name,
            business_type="ecommerce",
            created_by=created_by,
        )

    def create_session(self, *, chatbot, created_by):
        return ChatSession.objects.create(
            chatbot=chatbot,
            created_by=created_by,
        )

    def create_message(self, session, *, sender_type, content, response_time_ms=None):
        return ChatMessage.objects.create(
            chat_session=session,
            sender_type=sender_type,
            content=content,
            response_time_ms=response_time_ms,
        )


class ChatSessionCreateTests(ChatClientAPITestCase):
    def test_create_session_on_own_chatbot(self):
        response = self.client.post(
            SESSION_CREATE_URL,
            {"chatbot": self.own_chatbot.slug},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session = ChatSession.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(session.chatbot, self.own_chatbot)
        self.assertEqual(session.created_by, self.user)
        self.assertEqual(session.status, ChatSessionStatus.OPEN)

    def test_create_session_on_admin_demo_chatbot(self):
        response = self.client.post(
            SESSION_CREATE_URL,
            {"chatbot": self.demo_chatbot.slug},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_create_session_on_other_users_chatbot(self):
        response = self.client.post(
            SESSION_CREATE_URL,
            {"chatbot": self.other_chatbot.slug},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            ChatSession.objects.filter(chatbot=self.other_chatbot).exists()
        )

    def test_create_session_requires_authentication(self):
        self.client.force_authenticate(None)

        response = self.client.post(
            SESSION_CREATE_URL,
            {"chatbot": self.demo_chatbot.slug},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ChatSessionListTests(ChatClientAPITestCase):
    def test_user_sees_only_own_sessions(self):
        own = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        demo_session = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.other_user,
        )
        self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )

        response = self.client.get(SESSION_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(ids, {str(own.pk)})
        self.assertNotIn(str(demo_session.pk), ids)

    def test_superadmin_sees_all_sessions(self):
        own = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        other = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )
        demo = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.superadmin,
        )

        self.client.force_authenticate(self.superadmin)
        response = self.client.get(SESSION_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(ids, {str(own.pk), str(other.pk), str(demo.pk)})

    def test_list_includes_message_count_and_last_message(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.USER,
            content="Do you ship internationally?",
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.AGENT,
            content="Yes, we ship worldwide.",
            response_time_ms=900,
        )

        response = self.client.get(SESSION_LIST_URL)

        item = next(
            item for item in response.data["data"] if item["id"] == str(session.pk)
        )
        self.assertEqual(item["message_count"], 2)
        self.assertEqual(item["last_message"]["sender_type"], "agent")
        self.assertEqual(item["last_message"]["content"], "Yes, we ship worldwide.")


class ChatSessionDetailTests(ChatClientAPITestCase):
    def test_owner_sees_session_with_messages(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.USER,
            content="Hello",
        )

        response = self.client.get(
            SESSION_DETAILS_URL, {"session": session.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(session.pk))
        self.assertEqual(len(response.data["data"]["messages"]), 1)
        self.assertEqual(response.data["data"]["messages"][0]["content"], "Hello")

    def test_superadmin_sees_any_session(self):
        session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )

        self.client.force_authenticate(self.superadmin)
        response = self.client.get(SESSION_DETAILS_URL, {"session": session.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_other_user_gets_404(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

        self.client.force_authenticate(self.other_user)
        response = self.client.get(SESSION_DETAILS_URL, {"session": session.pk})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ChatSessionDeleteTests(ChatClientAPITestCase):
    def test_owner_deletes_session_with_messages(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.USER,
            content="Hello",
        )

        response = self.client.delete(
            f"{SESSION_DELETE_URL}?session={session.pk}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ChatSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(chat_session=session).exists())

    def test_other_user_cannot_delete(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

        self.client.force_authenticate(self.other_user)
        response = self.client.delete(
            f"{SESSION_DELETE_URL}?session={session.pk}",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(ChatSession.objects.filter(pk=session.pk).exists())

    def test_superadmin_deletes_any_session(self):
        session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )

        self.client.force_authenticate(self.superadmin)
        response = self.client.delete(
            f"{SESSION_DELETE_URL}?session={session.pk}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ChatSession.objects.filter(pk=session.pk).exists())


class ChatMessageTests(ChatClientAPITestCase):
    def test_send_message_as_session_owner(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": session.pk, "content": "What is your return policy?"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["sender_type"], "user")
        message = ChatMessage.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(
            message.content,
            "What is your return policy?",
        )
        session.refresh_from_db()
        self.assertIsNotNone(session.last_activity_at)

    def test_send_message_on_demo_session(self):
        session = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.user,
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": session.pk, "content": "Hi demo bot"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_send_message_to_other_users_session(self):
        session = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.other_user,
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": session.pk, "content": "intruding"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            ChatMessage.objects.filter(chat_session=session).exists()
        )

    def test_blank_message_rejected(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": session.pk, "content": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_message_list_scoped_to_visible_session(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.USER,
            content="Question",
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.AGENT,
            content="Answer",
            response_time_ms=750,
        )

        response = self.client.get(MESSAGE_LIST_URL, {"session": session.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        contents = [item["content"] for item in response.data["data"]]
        self.assertEqual(contents, ["Question", "Answer"])
        self.assertEqual(response.data["data"][1]["response_time_ms"], 750)

    def test_message_list_hidden_for_other_users(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

        self.client.force_authenticate(self.other_user)
        response = self.client.get(MESSAGE_LIST_URL, {"session": session.pk})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SessionStatsTests(ChatClientAPITestCase):
    def test_user_stats_cover_own_sessions_only(self):
        own_session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        other_session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )
        self.create_message(
            own_session,
            sender_type=ChatMessageSenderType.USER,
            content="Q1",
        )
        self.create_message(
            own_session,
            sender_type=ChatMessageSenderType.AGENT,
            content="A1",
            response_time_ms=800,
        )
        self.create_message(
            other_session,
            sender_type=ChatMessageSenderType.AGENT,
            content="not mine",
            response_time_ms=5000,
        )

        response = self.client.get(STATS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["total_messages"], 2)
        self.assertEqual(data["avg_response_time_ms"], 800)

    def test_superadmin_stats_cover_everything(self):
        own_session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        other_session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.other_user,
        )
        self.create_message(
            own_session,
            sender_type=ChatMessageSenderType.AGENT,
            content="A1",
            response_time_ms=800,
        )
        self.create_message(
            other_session,
            sender_type=ChatMessageSenderType.AGENT,
            content="A2",
            response_time_ms=1200,
        )

        self.client.force_authenticate(self.superadmin)
        response = self.client.get(STATS_URL)

        data = response.data["data"]
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["total_messages"], 2)
        self.assertEqual(data["avg_response_time_ms"], 1000)

    def test_avg_response_time_none_without_agent_messages(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.create_message(
            session,
            sender_type=ChatMessageSenderType.USER,
            content="Q",
        )

        response = self.client.get(STATS_URL)

        data = response.data["data"]
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["total_messages"], 1)
        self.assertIsNone(data["avg_response_time_ms"])
