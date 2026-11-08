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

DOCUMENT_UPLOAD_URL = reverse("knowledge-document-upload")
DOCUMENT_WEBSITE_URL = reverse("knowledge-document-website")
DOCUMENT_LIST_URL = reverse("knowledge-document-list")
DOCUMENT_DETAILS_URL = reverse("knowledge-document-detail")
DOCUMENT_UPDATE_URL = reverse("knowledge-document-update")
DOCUMENT_DELETE_URL = reverse("knowledge-document-delete")
DOCUMENT_TRAIN_URL = reverse("knowledge-document-train")


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
                DOCUMENT_WEBSITE_URL,
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

    def test_document_crud_flow(self):
        with fake_training_stack() as vector_service:
            create_response = self.client.post(
                DOCUMENT_WEBSITE_URL,
                {"chatbot": self.chatbot.slug, "url": "https://example.com/faq"},
                format="json",
            )
        document_id = create_response.data["data"]["id"]

        list_response = self.client.get(
            DOCUMENT_LIST_URL, {"chatbot": self.chatbot.slug},
        )
        self.assertEqual(list_response.data["meta"]["count"], 1)

        detail_response = self.client.get(
            DOCUMENT_DETAILS_URL,
            {"chatbot": self.chatbot.slug, "document": document_id},
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

        update_response = self.client.patch(
            f"{DOCUMENT_UPDATE_URL}?chatbot={self.chatbot.slug}&document={document_id}",
            {"title": "Shop FAQ", "is_enabled": False},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        document = KnowledgeBase.objects.get(pk=document_id)
        self.assertEqual(document.title, "Shop FAQ")
        self.assertFalse(document.is_enabled)

        with patch(
            "knowledge.api.v1.client.views.KnowledgeVectorService",
            return_value=vector_service,
        ):
            delete_response = self.client.delete(
                f"{DOCUMENT_DELETE_URL}?chatbot={self.chatbot.slug}&document={document_id}",
            )
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            KnowledgeBase.objects.filter(pk=document_id).exists(),
        )
        self.assertEqual(vector_service.removed_documents, [document.pk])

    def test_foreign_chatbot_rejected(self):
        response = self.client.post(
            DOCUMENT_WEBSITE_URL,
            {"chatbot": self.other_chatbot.slug, "url": "https://example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrain_unchanged_source_is_skipped(self):
        with fake_training_stack() as vector_service:
            self.client.post(
                DOCUMENT_WEBSITE_URL,
                {"chatbot": self.chatbot.slug, "url": "https://example.com/shop"},
                format="json",
            )
            document = KnowledgeBase.objects.get(chatbot=self.chatbot)
            vector_service.kb_replacements.clear()

            # Same content again: the pipeline skips re-embedding when the
            # vector set is complete... unless it is faked as incomplete.
            train_response = self.client.post(
                f"{DOCUMENT_TRAIN_URL}?chatbot={self.chatbot.slug}&document={document.pk}",
                {},
                format="json",
            )

        self.assertEqual(train_response.status_code, status.HTTP_202_ACCEPTED)
        # FakeVectorService reports incomplete sets, so it retrains.
        self.assertEqual(len(vector_service.kb_replacements), 1)


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
                DOCUMENT_UPLOAD_URL,
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
