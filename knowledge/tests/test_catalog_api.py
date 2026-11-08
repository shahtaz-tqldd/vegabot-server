import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from knowledge.models import KnowledgeTrainingLog, Product, Service
from knowledge.utils.choices import KnowledgeSpaceTypes, StatusTypes

from .fakes import FakeVectorService, fake_training_stack

User = get_user_model()

PRODUCT_TEMPLATE_URL = reverse("product-template")
PRODUCT_UPLOAD_URL = reverse("product-upload")
PRODUCT_CREATE_URL = reverse("product-create")
PRODUCT_LIST_URL = reverse("product-list")
PRODUCT_DETAILS_URL = reverse("product-detail")
PRODUCT_UPDATE_URL = reverse("product-update")
PRODUCT_DELETE_URL = reverse("product-delete")
PRODUCT_TRAIN_URL = reverse("product-train")

SERVICE_CREATE_URL = reverse("service-create")
SERVICE_BULK_CREATE_URL = reverse("service-bulk-create")
SERVICE_LIST_URL = reverse("service-list")
SERVICE_DETAILS_URL = reverse("service-detail")
SERVICE_UPDATE_URL = reverse("service-update")
SERVICE_DELETE_URL = reverse("service-delete")
SERVICE_TRAIN_URL = reverse("service-train")

LOGS_URL = reverse("knowledge-training-log-list")


class KnowledgeTestBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
            name="Owner",
        )
        self.superadmin = User.objects.create_user(
            email="admin@example.com",
            password="StrongPass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Store Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.other_chatbot = Chatbot.objects.create(
            chatbot_name="Other Bot",
            business_type="ecommerce",
            created_by=self.other_user,
        )
        self.client.force_authenticate(self.user)

    def create_product(self, **kwargs):
        defaults = {
            "name": "Wireless Mouse",
            "sku": "WM-001",
            "category": "Accessories",
            "description": "Ergonomic wireless mouse.",
            "price": "29.99",
            "currency": "USD",
            "chatbot": self.chatbot,
            "created_by": self.user,
        }
        defaults.update(kwargs)
        return Product.objects.create(**defaults)


class ProductCRUDTests(KnowledgeTestBase):
    def test_create_product(self):
        response = self.client.post(
            PRODUCT_CREATE_URL,
            {
                "chatbot": self.chatbot.slug,
                "name": "Mechanical Keyboard",
                "sku": "MK-002",
                "price": "89.00",
                "category": "Accessories",
                "description": "Hot-swappable switches.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["status"], StatusTypes.PENDING)
        product = Product.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(product.chatbot, self.chatbot)
        self.assertEqual(str(product.price), "89.00")

    def test_create_rejects_duplicate_sku(self):
        self.create_product(sku="WM-001")

        response = self.client.post(
            PRODUCT_CREATE_URL,
            {
                "chatbot": self.chatbot.slug,
                "name": "Mouse Clone",
                "sku": "wm-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_rejected_for_foreign_chatbot(self):
        response = self.client.post(
            PRODUCT_CREATE_URL,
            {
                "chatbot": self.other_chatbot.slug,
                "name": "Not Mine",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_paginated_product_list_scoped_to_chatbot(self):
        self.create_product(name="A", sku="A1")
        self.create_product(name="B", sku="B1")
        Product.objects.create(
            chatbot=self.other_chatbot,
            name="Theirs",
            created_by=self.other_user,
        )

        response = self.client.get(
            PRODUCT_LIST_URL, {"chatbot": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data["data"]]
        self.assertEqual(sorted(names), ["A", "B"])
        self.assertEqual(response.data["meta"]["count"], 2)

    def test_product_details(self):
        product = self.create_product()

        response = self.client.get(
            PRODUCT_DETAILS_URL,
            {"chatbot": self.chatbot.slug, "product": product.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["sku"], "WM-001")

    def test_product_update_marks_pending_retrain(self):
        product = self.create_product(status=StatusTypes.READY)

        response = self.client.patch(
            f"{PRODUCT_UPDATE_URL}?chatbot={self.chatbot.slug}&product={product.pk}",
            {"price": "24.99"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product.refresh_from_db()
        self.assertEqual(str(product.price), "24.99")
        self.assertEqual(product.status, StatusTypes.PENDING)

    def test_product_delete_removes_vector(self):
        product = self.create_product()
        vector_service = FakeVectorService()

        with patch(
            "knowledge.api.v1.client.views.KnowledgeVectorService",
            return_value=vector_service,
        ):
            response = self.client.delete(
                f"{PRODUCT_DELETE_URL}?chatbot={self.chatbot.slug}&product={product.pk}",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())
        self.assertEqual(
            vector_service.removed_catalog_items,
            [(KnowledgeSpaceTypes.PRODUCT, product.pk)],
        )

    def test_foreign_chatbot_scoping(self):
        response = self.client.get(
            PRODUCT_LIST_URL, {"chatbot": self.other_chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ProductTemplateAndUploadTests(KnowledgeTestBase):
    def test_download_template(self):
        response = self.client.get(PRODUCT_TEMPLATE_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        header = list(rows[0].keys())
        self.assertEqual(
            header,
            [
                "name",
                "sku",
                "category",
                "description",
                "price",
                "currency",
                "product_url",
            ],
        )
        self.assertGreaterEqual(len(rows), 1)

    def _csv_file(self, rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "name", "sku", "category", "description",
                "price", "currency", "product_url",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
        buffer.seek(0)
        return buffer.getvalue()

    def test_upload_csv_creates_products_and_trains(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        csv_content = self._csv_file(
            [
                {
                    "name": "USB Hub",
                    "sku": "UH-100",
                    "category": "Accessories",
                    "description": "4-port USB-C hub.",
                    "price": "19.50",
                    "currency": "usd",
                    "product_url": "https://example.com/uh-100",
                },
                {"name": "", "sku": "BAD"},
                {"name": "No Price", "sku": "", "category": "Misc"},
            ]
        )

        with fake_training_stack() as vector_service:
            response = self.client.post(
                PRODUCT_UPLOAD_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": SimpleUploadedFile(
                        "products.csv",
                        csv_content.encode(),
                        content_type="text/csv",
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(data["created_count"], 2)
        self.assertEqual(len(data["errors"]), 1)

        hub = Product.objects.get(chatbot=self.chatbot, sku="UH-100")
        self.assertEqual(str(hub.price), "19.50")
        self.assertEqual(hub.currency, "USD")
        self.assertEqual(hub.status, StatusTypes.READY)  # eager training ran

        space, item_ids, documents = vector_service.catalog_replacements[0]
        self.assertEqual(space, KnowledgeSpaceTypes.PRODUCT)
        self.assertEqual(len(item_ids), 2)
        contents = [document.content for document in documents]
        self.assertTrue(
            any("USB Hub" in content and "19.50 USD" in content for content in contents),
        )

    def test_upload_rejects_unsupported_type(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            PRODUCT_UPLOAD_URL,
            {
                "chatbot": self.chatbot.slug,
                "file": SimpleUploadedFile(
                    "products.exe", b"binary", content_type="application/octet-stream",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_skips_duplicate_skus(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.create_product(sku="WM-001")
        csv_content = self._csv_file(
            [{"name": "Clone", "sku": "WM-001", "price": "1.00"}],
        )
        with fake_training_stack() as vector_service:
            response = self.client.post(
                PRODUCT_UPLOAD_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": SimpleUploadedFile(
                        "products.csv", csv_content.encode(), content_type="text/csv",
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["created_count"], 0)
        self.assertIn("WM-001", response.data["data"]["errors"][0])


class ServiceAPITests(KnowledgeTestBase):
    def test_create_service(self):
        response = self.client.post(
            SERVICE_CREATE_URL,
            {
                "chatbot": self.chatbot.slug,
                "name": "Laptop Repair",
                "service_type": "repair",
                "description": "Screen and battery replacement.",
                "cost": "75.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        service = Service.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(service.status, StatusTypes.PENDING)
        self.assertEqual(service.currency, "USD")

    def test_bulk_create_and_training(self):
        with fake_training_stack() as vector_service:
            response = self.client.post(
                SERVICE_BULK_CREATE_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "services": [
                        {
                            "name": "Consulting",
                            "service_type": "consulting",
                            "cost": "120.00",
                        },
                        {
                            "name": "Setup",
                            "service_type": "installation",
                            "cost": "40.00",
                            "description": "On-site device setup.",
                        },
                    ],
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["created_count"], 2)
        self.assertEqual(
            Service.objects.filter(chatbot=self.chatbot, status=StatusTypes.READY).count(),
            2,
        )
        space, item_ids, documents = vector_service.catalog_replacements[0]
        self.assertEqual(space, KnowledgeSpaceTypes.SERVICE)
        contents = [document.content for document in documents]
        self.assertTrue(
            any("Cost: 120.00 USD" in content for content in contents),
        )

    def test_list_details_update_delete(self):
        service = Service.objects.create(
            chatbot=self.chatbot,
            name="Cleaning",
            service_type="maintenance",
            cost="30.00",
            created_by=self.user,
        )

        response = self.client.get(
            SERVICE_LIST_URL, {"chatbot": self.chatbot.slug},
        )
        self.assertEqual(response.data["meta"]["count"], 1)

        response = self.client.get(
            SERVICE_DETAILS_URL,
            {"chatbot": self.chatbot.slug, "service": service.pk},
        )
        self.assertEqual(response.data["data"]["name"], "Cleaning")

        response = self.client.patch(
            f"{SERVICE_UPDATE_URL}?chatbot={self.chatbot.slug}&service={service.pk}",
            {"cost": "35.00"},
            format="json",
        )
        service.refresh_from_db()
        self.assertEqual(str(service.cost), "35.00")
        self.assertEqual(service.status, StatusTypes.PENDING)

        vector_service = FakeVectorService()
        with patch(
            "knowledge.api.v1.client.views.KnowledgeVectorService",
            return_value=vector_service,
        ):
            response = self.client.delete(
                f"{SERVICE_DELETE_URL}?chatbot={self.chatbot.slug}&service={service.pk}",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Service.objects.filter(pk=service.pk).exists())


class TrainingLogTests(KnowledgeTestBase):
    def test_logs_listed_per_chatbot_and_space(self):
        KnowledgeTrainingLog.objects.create(
            chatbot=self.chatbot,
            space=KnowledgeSpaceTypes.PRODUCT,
        )
        KnowledgeTrainingLog.objects.create(
            chatbot=self.other_chatbot,
            space=KnowledgeSpaceTypes.SERVICE,
        )

        response = self.client.get(
            LOGS_URL,
            {"chatbot": self.chatbot.slug, "space": KnowledgeSpaceTypes.PRODUCT},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(response.data["data"][0]["space"], "product")

    def test_product_train_endpoint(self):
        self.create_product()
        with fake_training_stack() as vector_service:
            response = self.client.post(
                f"{PRODUCT_TRAIN_URL}?chatbot={self.chatbot.slug}",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        # The API answers with the queued log; the eager run itself is
        # recorded on the database row.
        self.assertEqual(response.data["data"]["stage"], "queued")
        log = KnowledgeTrainingLog.objects.get(pk=response.data["data"]["id"])
        self.assertEqual(log.stage, "completed")
        self.assertEqual(len(vector_service.catalog_replacements), 1)
