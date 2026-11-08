from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import AIUsage
from analytics.services.ai_usage import record_ai_usage
from chatbot.models import Chatbot
from chat.models import ChatSession
from analytics.utils.choices import AIUsageType

User = get_user_model()

AI_USAGE_URL = reverse("analytics-ai-usage")
GROWTH_URL = reverse("analytics-growth")


class AIUsageRecordingTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Usage Bot",
            business_type="ecommerce",
            created_by=self.superadmin,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.superadmin,
        )

    def test_record_ai_usage_from_agent_message(self):
        message = self.session.messages.create(
            sender_type="agent",
            content="Here is your answer.",
        )

        usage = record_ai_usage(
            chatbot=self.chatbot,
            chat_session=self.session,
            chat_message=message,
            usage_type=AIUsageType.CHAT,
            cost="0.0012",
            token_usage={
                "total_tokens": 420,
                "input_tokens": 300,
                "output_tokens": 120,
            },
            model="gemini-2.5-flash",
        )

        self.assertEqual(usage.usage_type, AIUsageType.CHAT)
        self.assertEqual(usage.tokens, 420)
        self.assertEqual(usage.input_tokens, 300)
        self.assertEqual(usage.output_tokens, 120)
        self.assertEqual(usage.cost, Decimal("0.00120000"))
        self.assertEqual(usage.chat_message, message)
        # Snapshots keep the context after the chat records are deleted.
        self.assertEqual(usage.chat_session_id_snapshot, self.session.pk)

    def test_message_must_belong_to_the_session(self):
        other_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.superadmin,
        )
        message = other_session.messages.create(
            sender_type="agent",
            content="Elsewhere.",
        )

        with self.assertRaises(Exception):
            record_ai_usage(
                chatbot=self.chatbot,
                chat_session=self.session,
                chat_message=message,
                usage_type=AIUsageType.CHAT,
                cost="0.001",
                token_usage={"total_tokens": 10},
            )


class AdminAnalyticsAPITests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.regular_user = User.objects.create_user(
            email="user@example.com",
            password="StrongPass123!",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Usage Bot",
            business_type="ecommerce",
            created_by=self.superadmin,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.superadmin,
        )
        self.client.force_authenticate(self.superadmin)

    def _record(self, *, tokens, cost, days_ago=0):
        message = self.session.messages.create(
            sender_type="agent",
            content="reply",
        )
        usage = AIUsage(
            chatbot=self.chatbot,
            chat_session=self.session,
            chat_message=message,
            usage_type=AIUsageType.CHAT,
            cost=Decimal(cost),
            tokens=tokens,
            input_tokens=tokens // 2,
            output_tokens=tokens - tokens // 2,
            model="gemini-2.5-flash",
        )
        if days_ago:
            created = timezone.now() - timedelta(days=days_ago)
            usage.save()
            AIUsage.objects.filter(pk=usage.pk).update(created_at=created)
        else:
            usage.save()

    def test_ai_usage_totals(self):
        self._record(tokens=100, cost="0.001")
        self._record(tokens=50, cost="0.002")

        response = self.client.get(AI_USAGE_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["total_message"], 2)
        self.assertEqual(data["total_tokens"], 150)
        self.assertAlmostEqual(data["total_cost"], 0.003)
        self.assertEqual(data["chat"]["tokens"], 150)

    def test_ai_usage_respects_date_range(self):
        self._record(tokens=100, cost="0.001", days_ago=10)
        self._record(tokens=50, cost="0.002")

        today = timezone.localdate()
        response = self.client.get(
            AI_USAGE_URL,
            {"start_date": today - timedelta(days=1), "end_date": today},
        )

        data = response.data["data"]
        self.assertEqual(data["total_message"], 1)
        self.assertEqual(data["total_tokens"], 50)

    def test_rejects_inverted_date_range(self):
        today = timezone.localdate()
        response = self.client.get(
            AI_USAGE_URL,
            {"start_date": today, "end_date": today - timedelta(days=1)},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_growth_monthly_points(self):
        response = self.client.get(GROWTH_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["granularity"], "month")
        self.assertEqual(len(data["points"]), 12)
        this_month = timezone.localdate().strftime("%Y-%m")
        counts = {point["period"]: point["count"] for point in data["points"]}
        self.assertEqual(counts[this_month], 2)  # admin + regular user

    def test_non_superadmin_forbidden(self):
        self.client.force_authenticate(self.regular_user)

        response = self.client.get(AI_USAGE_URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
