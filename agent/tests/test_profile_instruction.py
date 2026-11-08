from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from agent.helpers.global_instruction import global_instruction
from agent.helpers.instruction import build_instruction
from agent.helpers.profile import (
    PROFILE_VERSION_KEY,
    agent_profile,
    cached_agent_profile,
    invalidate_agent_profile,
)
from chatbot.models import Chatbot
from knowledge.models import Product, Service

User = get_user_model()


class AgentProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="profile@example.com", password="x",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Profile Bot",
            business_type="ecommerce",
            tone="friendly",
            created_by=self.user,
        )

    def test_profile_reports_space_flags(self):
        Product.objects.create(
            chatbot=self.chatbot,
            name="Widget",
            created_by=self.user,
        )

        profile = agent_profile(self.chatbot)

        self.assertTrue(profile["spaces"]["product"])
        self.assertFalse(profile["spaces"]["document"])
        self.assertFalse(profile["spaces"]["service"])
        self.assertEqual(profile["chatbot"]["chatbot_name"], "Profile Bot")
        self.assertEqual(profile["chatbot"]["tone"], "friendly")
        self.assertIn("Profile Bot", profile["policy"])

    def test_profile_is_cached_until_invalidated(self):
        cached_agent_profile(self.chatbot)

        # A new product arrives; with signal-driven invalidation muted the
        # cached profile still reports the stale (no products) flags.
        with patch("agent.helpers.profile.invalidate_agent_profile"):
            Product.objects.create(
                chatbot=self.chatbot,
                name="Widget",
                created_by=self.user,
            )
            profile = cached_agent_profile(self.chatbot)
            self.assertFalse(profile["spaces"]["product"])

        # Invalidation (signal-driven in the app) retires the cache.
        invalidate_agent_profile(self.chatbot.id)
        profile = cached_agent_profile(self.chatbot)
        self.assertTrue(profile["spaces"]["product"])


class InstructionTests(TestCase):
    def make_profile(self, *, document=False, product=False, service=False):
        return {
            "spaces": {
                "document": document,
                "product": product,
                "service": service,
            },
        }

    def test_sections_injected_conditionally(self):
        instruction = build_instruction(
            self.make_profile(product=True, document=True),
            business_policy="POLICY",
        )

        self.assertIn("POLICY", instruction)
        self.assertIn("search_products", instruction)
        self.assertIn("search_documents", instruction)
        self.assertNotIn("search_services", instruction)

    def test_no_search_sections_without_knowledge(self):
        instruction = build_instruction(self.make_profile())

        self.assertIn("request_human_escalation", instruction)
        self.assertNotIn("search_products", instruction)
        self.assertNotIn("search_documents", instruction)
        self.assertNotIn("search_services", instruction)


class GlobalInstructionTests(TestCase):
    def test_identity_and_rules(self):
        user = User.objects.create_user(
            email="identity@example.com", password="x",
        )
        chatbot = Chatbot.objects.create(
            chatbot_name="Shop Helper",
            business_type="ecommerce",
            tone="professional",
            instructions="Only answer about the shop.",
            never_answer="Never discuss politics.",
            fallback_message="I cannot answer that.",
            escalation_rule="Hand off on refund complaints.",
            created_by=user,
        )

        policy = global_instruction(chatbot)

        self.assertIn("Shop Helper", policy)
        self.assertIn("professional", policy)
        self.assertIn("Only answer about the shop.", policy)
        self.assertIn("Never discuss politics.", policy)
        self.assertIn("Hand off on refund complaints.", policy)
        self.assertIn("request_human_escalation", policy)
