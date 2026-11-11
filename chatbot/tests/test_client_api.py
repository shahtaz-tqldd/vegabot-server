from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from chatbot.models import Chatbot
from chatbot.utils.choices import ChatbotStatusTypes

User = get_user_model()

CREATE_URL = reverse("chatbot-create")
LIST_URL = reverse("chatbot-list")
DETAILS_URL = reverse("chatbot-detail")
UPDATE_URL = reverse("chatbot-update")
DELETE_URL = reverse("chatbot-delete")


class ChatbotClientAPITestCase(APITestCase):
    def setUp(self):
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
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
            name="Admin",
            is_staff=True,
        )
        self.client.force_authenticate(self.user)

    def create_chatbot(self, *, created_by, name="Support Bot", **kwargs):
        return Chatbot.objects.create(
            chatbot_name=name,
            business_type=kwargs.pop("business_type", "ecommerce"),
            created_by=created_by,
            **kwargs,
        )

    def test_create_chatbot(self):
        response = self.client.post(
            CREATE_URL,
            {
                "chatbot_name": "Alpha Bot",
                "business_type": "ecommerce",
                "description": "Store assistant",
                "tone": "friendly",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        chatbot = Chatbot.objects.get(pk=data["id"])
        self.assertEqual(chatbot.created_by, self.user)
        self.assertEqual(chatbot.tone, "friendly")
        self.assertTrue(chatbot.slug)
        self.assertIn("Alpha Bot", chatbot.welcome_message)

    def test_create_requires_authentication(self):
        unauthenticated = APIClient()
        response = unauthenticated.post(
            CREATE_URL,
            {"chatbot_name": "Ghost", "business_type": "ecommerce"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_rejects_duplicate_name_for_same_owner(self):
        self.create_chatbot(created_by=self.user, name="Alpha")

        response = self.client.post(
            CREATE_URL,
            {"chatbot_name": "alpha", "business_type": "ecommerce"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("chatbot_name", response.data["errors"])

    def test_same_name_allowed_for_different_owners(self):
        self.create_chatbot(created_by=self.other_user, name="Alpha")

        response = self.client.post(
            CREATE_URL,
            {"chatbot_name": "Alpha", "business_type": "ecommerce"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_list_returns_own_and_admin_chatbots_only(self):
        own = self.create_chatbot(created_by=self.user, name="Mine")
        demo = self.create_chatbot(created_by=self.admin_user, name="Demo")
        self.create_chatbot(created_by=self.other_user, name="Theirs")

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]
        self.assertEqual(len(results), 2)
        slugs = {item["slug"] for item in results}
        self.assertEqual(slugs, {own.slug, demo.slug})
        by_slug = {item["slug"]: item for item in results}
        self.assertTrue(by_slug[demo.slug]["is_admin_created"])
        self.assertFalse(by_slug[own.slug]["is_admin_created"])

    def test_details_returns_own_chatbot(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.get(DETAILS_URL, {"chatbot": chatbot.slug})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["slug"], chatbot.slug)
        self.assertEqual(
            response.data["data"]["created_by"]["email"],
            self.user.email,
        )

    def test_details_returns_admin_demo_chatbot(self):
        demo = self.create_chatbot(created_by=self.admin_user, name="Demo")

        response = self.client.get(DETAILS_URL, {"chatbot": demo.slug})

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_details_hides_other_users_chatbot(self):
        theirs = self.create_chatbot(created_by=self.other_user)

        response = self.client.get(DETAILS_URL, {"chatbot": theirs.slug})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_details_requires_chatbot_query_param(self):
        response = self.client.get(DETAILS_URL)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_own_chatbot(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={chatbot.slug}",
            {
                "chatbot_name": "Renamed",
                "tone": "witty",
                "instructions": "Answer only from the knowledge base.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertEqual(chatbot.chatbot_name, "Renamed")
        self.assertEqual(chatbot.tone, "witty")
        self.assertEqual(
            chatbot.instructions,
            "Answer only from the knowledge base.",
        )

    def test_update_regenerates_default_welcome_message_on_rename(self):
        chatbot = self.create_chatbot(created_by=self.user, name="Alpha")
        self.assertIn("Alpha", chatbot.welcome_message)

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={chatbot.slug}",
            {"chatbot_name": "Beta"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertIn("Beta", chatbot.welcome_message)

    def test_update_keeps_custom_welcome_message_on_rename(self):
        chatbot = self.create_chatbot(
            created_by=self.user,
            name="Alpha",
            welcome_message="Custom greeting",
        )

        self.client.patch(
            f"{UPDATE_URL}?chatbot={chatbot.slug}",
            {"chatbot_name": "Beta"},
            format="json",
        )

        chatbot.refresh_from_db()
        self.assertEqual(chatbot.welcome_message, "Custom greeting")

    def test_update_rejects_other_users_chatbot(self):
        theirs = self.create_chatbot(created_by=self.other_user)

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={theirs.slug}",
            {"chatbot_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        theirs.refresh_from_db()
        self.assertEqual(theirs.chatbot_name, "Support Bot")

    def test_update_rejects_admin_chatbot_for_regular_user(self):
        demo = self.create_chatbot(created_by=self.admin_user, name="Demo")

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={demo.slug}",
            {"chatbot_name": "Vandalized"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_rejects_moderation_status(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={chatbot.slug}",
            {"status": ChatbotStatusTypes.DISABLED_BY_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_own_chatbot(self):
        chatbot = self.create_chatbot(created_by=self.user)

        response = self.client.delete(f"{DELETE_URL}?chatbot={chatbot.slug}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chatbot.refresh_from_db()
        self.assertTrue(chatbot.is_deleted)
        self.assertEqual(chatbot.status, ChatbotStatusTypes.DISABLED)

        response = self.client.get(LIST_URL)
        slugs = [item["slug"] for item in response.data["data"]]
        self.assertNotIn(chatbot.slug, slugs)

    def test_delete_rejects_other_users_chatbot(self):
        theirs = self.create_chatbot(created_by=self.other_user)

        response = self.client.delete(f"{DELETE_URL}?chatbot={theirs.slug}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_deleted)

    def test_delete_rejects_admin_chatbot_for_regular_user(self):
        demo = self.create_chatbot(created_by=self.admin_user, name="Demo")

        response = self.client.delete(f"{DELETE_URL}?chatbot={demo.slug}")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        demo.refresh_from_db()
        self.assertFalse(demo.is_deleted)


class ChatbotFeatureTests(ChatbotClientAPITestCase):
    def test_create_defaults_enable_all_sources_without_ordering(self):
        response = self.client.post(
            CREATE_URL,
            {"chatbot_name": "Defaults", "business_type": "ecommerce"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertTrue(data["knowledge_enabled"])
        self.assertTrue(data["products_enabled"])
        self.assertTrue(data["services_enabled"])
        self.assertFalse(data["product_ordering_enabled"])

    def test_create_with_chosen_features(self):
        response = self.client.post(
            CREATE_URL,
            {
                "chatbot_name": "Shop",
                "business_type": "ecommerce",
                "services_enabled": False,
                "product_ordering_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        chatbot = Chatbot.objects.get(pk=response.data["data"]["id"])
        self.assertFalse(chatbot.services_enabled)
        self.assertTrue(chatbot.ordering_enabled)

    def test_ordering_requires_products(self):
        response = self.client.post(
            CREATE_URL,
            {
                "chatbot_name": "Services Only",
                "business_type": "consulting",
                "products_enabled": False,
                "product_ordering_enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_ordering_enabled", response.data["errors"])

    def test_disabling_products_on_update_keeps_ordering_consistent(self):
        chatbot = self.create_chatbot(
            created_by=self.user, product_ordering_enabled=True,
        )

        response = self.client.patch(
            f"{UPDATE_URL}?chatbot={chatbot.slug}",
            {"products_enabled": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product_ordering_enabled", response.data["errors"])

    def test_at_least_one_source_required(self):
        response = self.client.post(
            CREATE_URL,
            {
                "chatbot_name": "Empty",
                "business_type": "ecommerce",
                "knowledge_enabled": False,
                "products_enabled": False,
                "services_enabled": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("knowledge_enabled", response.data["errors"])
