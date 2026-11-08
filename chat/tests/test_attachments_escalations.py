from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chat.services import build_message_parts, record_escalation
from chat.services.escalations import resolve_escalation
from chat.models import ChatEscalation, ChatMessageAttachment
from chat.utils.choices import (
    AttachmentProcessStatus,
    ChatMessageSenderType,
    EscalationReason,
    EscalationStatus,
)
from chatbot.models import Chatbot

from .test_client_api import ChatClientAPITestCase

MESSAGE_SEND_URL = reverse("chat-message-send")
ESCALATION_LIST_URL = reverse("chat-escalation-list")
STATS_URL = reverse("session-stats")

User = get_user_model()


def r2_upload_stub(folder=None, public_id=None):
    return {
        "url": f"https://files.example.com/{public_id}",
        "key": f"{folder}/{public_id}",
        "public_id": public_id,
        "content_type": "application/octet-stream",
    }


class AttachmentSendTests(ChatClientAPITestCase):
    def setUp(self):
        super().setUp()
        self.session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )

    @patch(
        "chat.api.v1.client.views.upload_file",
        side_effect=lambda file_obj, folder=None, public_id=None: r2_upload_stub(
            folder, public_id,
        ),
    )
    def test_send_message_with_image_attachment(self, _upload_mock):
        image = SimpleUploadedFile(
            "product.png", b"\x89PNG\r\n\x1a\n", content_type="image/png",
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "content": "What is in this image?", "attachments": [image]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attachments = response.data["data"]["attachments"]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["attachment_type"], "image")
        self.assertEqual(attachments[0]["status"], "processed")
        self.assertFalse(attachments[0]["text_preview"])

    @patch(
        "chat.api.v1.client.views.upload_file",
        side_effect=lambda file_obj, folder=None, public_id=None: r2_upload_stub(
            folder, public_id,
        ),
    )
    def test_attachment_only_message_allowed(self, _upload_mock):
        image = SimpleUploadedFile(
            "chart.jpg", b"jpg-bytes", content_type="image/jpeg",
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "attachments": [image]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["content"], "")
        self.assertEqual(len(response.data["data"]["attachments"]), 1)

    @patch(
        "chat.api.v1.client.views.upload_file",
        side_effect=lambda file_obj, folder=None, public_id=None: r2_upload_stub(
            folder, public_id,
        ),
    )
    def test_document_text_extracted(self, _upload_mock):
        document = SimpleUploadedFile(
            "policy.txt",
            "Return policy: 30 days full refund.".encode(),
            content_type="text/plain",
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "attachments": [document]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attachment = response.data["data"]["attachments"][0]
        self.assertEqual(attachment["attachment_type"], "document")
        self.assertEqual(attachment["status"], "processed")
        self.assertIn("Return policy", attachment["text_preview"])

        stored = ChatMessageAttachment.objects.get(pk=attachment["id"])
        self.assertIn("30 days full refund", stored.extracted_text)

    def test_unsupported_file_type_rejected(self):
        executable = SimpleUploadedFile(
            "setup.exe", b"MZ...", content_type="application/x-msdownload",
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "attachments": [executable]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("attachments", response.data["errors"])

    def test_oversize_image_rejected(self):
        big_image = SimpleUploadedFile(
            "huge.png", b"x" * (5 * 1024 * 1024 + 1), content_type="image/png",
        )

        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "attachments": [big_image]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_content_or_attachment_required(self):
        response = self.client.post(
            MESSAGE_SEND_URL,
            {"session": self.session.pk, "content": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MultimodalPartsTests(ChatClientAPITestCase):
    @patch(
        "chat.api.v1.client.views.upload_file",
        side_effect=lambda file_obj, folder=None, public_id=None: r2_upload_stub(
            folder, public_id,
        ),
    )
    def test_build_message_parts(self, _upload_mock):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        image = SimpleUploadedFile(
            "product.png", b"\x89PNG\r\n\x1a\n", content_type="image/png",
        )
        document = SimpleUploadedFile(
            "shipping.txt", "We ship to 64 countries.".encode(),
            content_type="text/plain",
        )

        self.client.post(
            MESSAGE_SEND_URL,
            {
                "session": session.pk,
                "content": "Answer from these, please.",
                "attachments": [image, document],
            },
            format="multipart",
        )

        message = session.messages.order_by("created_at").first()
        parts = build_message_parts(message)

        self.assertEqual(
            [part["type"] for part in parts],
            ["text", "image", "file_text"],
        )
        self.assertEqual(parts[0]["text"], "Answer from these, please.")
        self.assertTrue(parts[1]["url"].startswith("https://files.example.com/"))
        self.assertIn("64 countries", parts[2]["text"])


class EscalationServiceTests(ChatClientAPITestCase):
    def setUp(self):
        super().setUp()
        self.session = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.user,
        )
        self.message = self.create_message(
            self.session,
            sender_type=ChatMessageSenderType.USER,
            content="Do you support BDT payments?",
        )

    def test_record_escalation_stores_the_unanswered_query(self):
        escalation = record_escalation(
            session=self.session,
            question="Do you support BDT payments?",
            message=self.message,
            reason=EscalationReason.NO_ANSWER,
        )

        self.assertEqual(escalation.status, EscalationStatus.OPEN)
        self.assertEqual(escalation.chat_session, self.session)
        self.assertEqual(escalation.chat_message, self.message)
        self.assertEqual(escalation.created_by, self.user)

    def test_record_escalation_is_idempotent_per_open_question(self):
        first = record_escalation(
            session=self.session, question="Do you support BDT payments?",
        )
        second = record_escalation(
            session=self.session, question="Do you support BDT payments?",
        )

        self.assertEqual(first.pk, second.pk)

    def test_different_questions_create_separate_escalations(self):
        first = record_escalation(
            session=self.session, question="Do you support BDT payments?",
        )
        second = record_escalation(
            session=self.session, question="Do you ship on Fridays?",
        )

        self.assertNotEqual(first.pk, second.pk)

    def test_resolve_escalation(self):
        escalation = record_escalation(
            session=self.session, question="Do you support BDT payments?",
        )

        resolve_escalation(
            escalation,
            resolution_note="Added payment FAQ to knowledge.",
            resolved_by=self.superadmin,
        )

        escalation.refresh_from_db()
        self.assertEqual(escalation.status, EscalationStatus.RESOLVED)
        self.assertEqual(
            escalation.resolution_note,
            "Added payment FAQ to knowledge.",
        )


class EscalationAPITests(ChatClientAPITestCase):
    def setUp(self):
        super().setUp()
        # A user chats on the admin's demo chatbot and hits a dead end.
        self.demo_session = self.create_session(
            chatbot=self.demo_chatbot, created_by=self.user,
        )
        self.escalation = record_escalation(
            session=self.demo_session,
            question="Do you ship to Antarctica?",
        )
        # The owner's own session also escalates.
        self.own_session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        self.own_escalation = record_escalation(
            session=self.own_session,
            question="What are your holiday hours?",
        )

    def test_chatter_sees_own_session_escalations(self):
        response = self.client.get(ESCALATION_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(
            ids,
            {str(self.escalation.pk), str(self.own_escalation.pk)},
        )

    def test_chatbot_owner_sees_escalations_on_their_chatbots(self):
        # A staff owner (not superuser) sees escalations raised on their
        # chatbot by other users, but not those on private chatbots.
        staff_owner = User.objects.create_user(
            email="staff-owner@example.com",
            password="StrongPass123!",
            name="Staff Owner",
            is_staff=True,
        )
        staff_chatbot = self.create_chatbot(staff_owner, "Staff Demo Bot")
        staff_session = self.create_session(
            chatbot=staff_chatbot, created_by=self.user,
        )
        staff_escalation = record_escalation(
            session=staff_session,
            question="Do you offer bulk discounts?",
        )

        self.client.force_authenticate(staff_owner)
        response = self.client.get(ESCALATION_LIST_URL)

        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(ids, {str(staff_escalation.pk)})

    def test_superadmin_sees_all_escalations(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.get(ESCALATION_LIST_URL)

        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(
            ids,
            {str(self.escalation.pk), str(self.own_escalation.pk)},
        )

    def test_unrelated_user_sees_only_their_own_chatbot_escalations(self):
        other_private_session = self.create_session(
            chatbot=self.other_chatbot, created_by=self.user,
        )
        own_escalation = record_escalation(
            session=other_private_session, question="Can I track my order?",
        )

        self.client.force_authenticate(self.other_user)
        response = self.client.get(ESCALATION_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["data"]}
        self.assertEqual(ids, {str(own_escalation.pk)})

    def test_status_filter(self):
        response = self.client.get(ESCALATION_LIST_URL, {"status": "resolved"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"], [])

    def test_owner_resolves_escalation(self):
        self.client.force_authenticate(self.superadmin)

        response = self.client.patch(
            f"{reverse('chat-escalation-resolve')}?escalation={self.escalation.pk}",
            {"resolution_note": "Shipping FAQ updated."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.escalation.refresh_from_db()
        self.assertEqual(self.escalation.status, EscalationStatus.RESOLVED)

    def test_chatter_cannot_resolve_escalation(self):
        response = self.client.patch(
            f"{reverse('chat-escalation-resolve')}?escalation={self.escalation.pk}",
            {"resolution_note": "hijack"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.escalation.refresh_from_db()
        self.assertEqual(self.escalation.status, EscalationStatus.OPEN)


class EscalationStatsTests(ChatClientAPITestCase):
    def test_stats_include_escalation_counts(self):
        session = self.create_session(
            chatbot=self.own_chatbot, created_by=self.user,
        )
        record_escalation(session=session, question="Q1?")
        resolved = record_escalation(session=session, question="Q2?")
        resolve_escalation(resolved, resolution_note="done", resolved_by=self.user)

        response = self.client.get(STATS_URL)

        data = response.data["data"]
        self.assertEqual(data["total_escalations"], 2)
        self.assertEqual(data["open_escalations"], 1)
