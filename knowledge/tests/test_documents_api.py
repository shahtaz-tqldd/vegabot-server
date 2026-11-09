from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from knowledge.models import KnowledgeBase
from knowledge.utils.choices import StatusTypes

from .fakes import (
    FakePrivateStorage,
    FakeVectorService,
    fake_training_stack,
)

User = get_user_model()

BASE_UPLOAD_FILE_URL = reverse("knowledge-base-upload-file")
BASE_UPLOAD_URL_URL = reverse("knowledge-base-upload-url")
BASE_UPLOAD_TEXT_URL = reverse("knowledge-base-upload-text")
BASE_LIST_URL = reverse("knowledge-base-list")
BASE_RETRAIN_URL = reverse("knowledge-base-retrain")
BASE_DELETE_URL = reverse("knowledge-base-delete")


class DocumentTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Owner",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Docs Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.other_chatbot = Chatbot.objects.create(
            chatbot_name="Hidden Bot",
            business_type="ecommerce",
            created_by=User.objects.create_user(
                email="other@example.com", password="StrongPass123!",
            ),
        )
        self.client.force_authenticate(self.user)


class WebsiteDocumentTests(DocumentTestBase):
    def test_add_website_trains_into_document_space(self):
        with fake_training_stack() as vector_service:
            response = self.client.post(
                BASE_UPLOAD_URL_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "url": "https://example.com/shop",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        document = KnowledgeBase.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(document.source_type, "website")
        self.assertEqual(document.status, StatusTypes.READY)
        self.assertEqual(document.title, "Example Shop")

        kb_id, documents = vector_service.kb_replacements[0]
        self.assertEqual(kb_id, document.pk)
        self.assertGreaterEqual(len(documents), 1)
        self.assertEqual(documents[0].space, "document")
        self.assertIn("return policy", documents[0].content)
        self.assertEqual(documents[0].embedding, [0.1, 0.2, 0.3])

    def test_base_list_and_delete_flow(self):
        with fake_training_stack() as vector_service:
            create_response = self.client.post(
                BASE_UPLOAD_URL_URL,
                {"chatbot": self.chatbot.slug, "url": "https://example.com/faq"},
                format="json",
            )
        document_id = create_response.data["data"]["id"]

        list_response = self.client.get(
            BASE_LIST_URL, {"chatbot": self.chatbot.slug},
        )
        self.assertEqual(list_response.data["meta"]["count"], 1)

        with patch(
            "knowledge.api.v1.client.views.KnowledgeVectorService",
            return_value=vector_service,
        ):
            delete_response = self.client.delete(
                f"{BASE_DELETE_URL}?chatbot={self.chatbot.slug}&document={document_id}",
            )
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            KnowledgeBase.objects.filter(pk=document_id).exists(),
        )
        self.assertEqual(
            [str(pk) for pk in vector_service.removed_documents],
            [document_id],
        )

    def test_foreign_chatbot_rejected(self):
        response = self.client.post(
            BASE_UPLOAD_URL_URL,
            {"chatbot": self.other_chatbot.slug, "url": "https://example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrain_url_source(self):
        with fake_training_stack() as vector_service:
            self.client.post(
                BASE_UPLOAD_URL_URL,
                {"chatbot": self.chatbot.slug, "url": "https://example.com/shop"},
                format="json",
            )
            document = KnowledgeBase.objects.get(chatbot=self.chatbot)
            vector_service.kb_replacements.clear()

            # Same content again: the pipeline skips re-embedding when the
            # vector set is complete... unless it is faked as incomplete.
            train_response = self.client.post(
                f"{BASE_RETRAIN_URL}?chatbot={self.chatbot.slug}&document={document.pk}",
                {},
                format="json",
            )

        self.assertEqual(train_response.status_code, status.HTTP_202_ACCEPTED)
        # FakeVectorService reports incomplete sets, so it retrains.
        self.assertEqual(len(vector_service.kb_replacements), 1)

    def test_retrain_rejects_non_url_sources(self):
        with fake_training_stack():
            self.client.post(
                BASE_UPLOAD_TEXT_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "title": "Policies",
                    "content": "Returns are accepted for 30 days.",
                },
                format="json",
            )
            document = KnowledgeBase.objects.get(chatbot=self.chatbot)

            response = self.client.post(
                f"{BASE_RETRAIN_URL}?chatbot={self.chatbot.slug}&document={document.pk}",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class FileDocumentTests(DocumentTestBase):
    def test_upload_csv_file_trains(self):
        # One storage instance shared by the upload and extraction paths.
        storage = FakePrivateStorage()

        class SharedStorage:
            @staticmethod
            def build_key(*, chatbot_id, filename):
                return storage.build_key(chatbot_id=chatbot_id, filename=filename)

            def upload(self, *args, **kwargs):
                return storage.upload(*args, **kwargs)

            def download(self, *args, **kwargs):
                return storage.download(*args, **kwargs)

        with fake_training_stack() as vector_service, patch(
            "knowledge.services.storage.PrivateKnowledgeStorage",
            SharedStorage,
        ), patch(
            "knowledge.services.extraction.PrivateKnowledgeStorage",
            SharedStorage,
        ):
            response = self.client.post(
                BASE_UPLOAD_FILE_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": SimpleUploadedFile(
                        "policy.csv",
                        b"Policy: 30 day returns for all products.",
                        content_type="text/csv",
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        document = KnowledgeBase.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(document.file_type, "csv")
        self.assertEqual(document.status, StatusTypes.READY)
        self.assertEqual(len(vector_service.kb_replacements), 1)
        content = vector_service.kb_replacements[0][1][0].content
        self.assertIn("30 day returns", content)


class TextDocumentTests(DocumentTestBase):
    def test_add_text_trains_into_document_space(self):
        with fake_training_stack() as vector_service:
            response = self.client.post(
                BASE_UPLOAD_TEXT_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "title": "Store policies",
                    "content": "We offer a 30 day return policy for all products.",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        document = KnowledgeBase.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(document.source_type, "text")
        self.assertEqual(document.status, StatusTypes.READY)

        _, documents = vector_service.kb_replacements[0]
        self.assertIn("return policy", documents[0].content)

    def test_empty_text_rejected(self):
        response = self.client.post(
            BASE_UPLOAD_TEXT_URL,
            {"chatbot": self.chatbot.slug, "content": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
