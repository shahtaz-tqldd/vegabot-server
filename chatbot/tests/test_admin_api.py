from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes

User = get_user_model()

ADMIN_LIST_URL = reverse("admin-chatbot-list")
ADMIN_UPDATE_URL = reverse("admin-chatbot-update")
ADMIN_DELETE_URL = reverse("admin-chatbot-delete")


class ChatbotAdminAPITestCase(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Owner",
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
            name="Other Owner",
        )
        self.client.force_authenticate(self.admin_user)

    def create_chatbot(self, *, created_by, name="Support Bot", **kwargs):
        return Chatbot.objects.create(
            chatbot_name=name,
            business_type=kwargs.pop("business_type", "ecommerce"),
            created_by=created_by,
            **kwargs,
        )

    def test_admin_list_returns_everyones_chatbots(self):
        own = self.create_chatbot(created_by=self.admin_user, name="Demo")
        mine = self.create_chatbot(created_by=self.user, name="Mine")
        theirs = self.create_chatbot(created_by=self.other_user, name="Theirs")

        response = self.client.get(ADMIN_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        slugs = {item["slug"] for item in response.data["data"]}
        self.assertEqual(slugs, {own.slug, mine.slug, theirs.slug})

    def test_admin_list_excludes_deleted_chatbots(self):
        deleted = self.create_chatbot(created_by=self.user, name="Gone")
        deleted.is_deleted = True
        deleted.save(update_fields=["is_deleted"])

        response = self.client.get(ADMIN_LIST_URL)

        slugs = [item["slug"] for item in response.data["data"]]
        self.assertNotIn(deleted.slug, slugs)

    def test_admin_list_forbidden_for_regular_user(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(ADMIN_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_updates_any_chatbot(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.patch(
            f"{ADMIN_UPDATE_URL}?chatbot={chatbot.slug}",
            {"instructions": "Be concise."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertEqual(chatbot.instructions, "Be concise.")
        self.assertEqual(chatbot.updated_by, self.admin_user)

    def test_admin_can_apply_moderation_status(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.patch(
            f"{ADMIN_UPDATE_URL}?chatbot={chatbot.slug}",
            {"status": ChatbotStatusTypes.DISABLED_BY_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertEqual(chatbot.status, ChatbotStatusTypes.DISABLED_BY_ADMIN)
        self.assertFalse(chatbot.is_active)

    def test_admin_update_forbidden_for_regular_user(self):
        self.client.force_authenticate(self.user)
        chatbot = self.create_chatbot(created_by=self.other_user)

        response = self.client.patch(
            f"{ADMIN_UPDATE_URL}?chatbot={chatbot.slug}",
            {"chatbot_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_deletes_any_chatbot(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.delete(f"{ADMIN_DELETE_URL}?chatbot={chatbot.slug}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertTrue(chatbot.is_deleted)

    def test_admin_delete_forbidden_for_regular_user(self):
        self.client.force_authenticate(self.user)
        chatbot = self.create_chatbot(created_by=self.other_user)

        response = self.client.delete(f"{ADMIN_DELETE_URL}?chatbot={chatbot.slug}")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        chatbot.refresh_from_db()
        self.assertFalse(chatbot.is_deleted)
