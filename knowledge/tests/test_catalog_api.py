import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot
from knowledge.models import KnowledgeTrainingLog, Product, Service
from knowledge.utils.choices import KnowledgeSpaceTypes, StatusTypes

from .fakes import FakeVectorService, fake_training_stack

User = get_user_model()

PRODUCT_TEMPLATE_URL = reverse("product-template")
PRODUCT_IMPORT_URL = reverse("product-import")
PRODUCT_LIST_URL = reverse("product-list")
PRODUCT_DISABLE_URL = reverse("product-disable")
PRODUCT_UPDATE_URL = reverse("product-update")

SERVICE_TEMPLATE_URL = reverse("service-template")
SERVICE_IMPORT_URL = reverse("service-import")
SERVICE_CREATE_URL = reverse("service-create")
SERVICE_BULK_CREATE_URL = reverse("service-bulk-create")
SERVICE_LIST_URL = reverse("service-list")
SERVICE_UPDATE_URL = reverse("service-update")
SERVICE_DELETE_URL = reverse("service-delete")

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

    def csv_file(self, rows, filename="catalog.csv"):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        buffer.seek(0)
        return SimpleUploadedFile(
            filename,
            buffer.getvalue().encode(),
            content_type="text/csv",
        )


class ProductAPITests(KnowledgeTestBase):
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

    def test_foreign_chatbot_scoping(self):
        response = self.client.get(
            PRODUCT_LIST_URL, {"chatbot": self.other_chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_retrains_product(self):
        product = self.create_product(status=StatusTypes.READY)

        with fake_training_stack() as vector_service:
            response = self.client.patch(
                f"{PRODUCT_UPDATE_URL}?chatbot={self.chatbot.slug}&product={product.pk}",
                {"price": "24.99"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product.refresh_from_db()
        self.assertEqual(str(product.price), "24.99")
        # The update queued training and the eager run retrained the item.
        self.assertEqual(product.status, StatusTypes.READY)
        space, item_ids, _ = vector_service.catalog_replacements[0]
        self.assertEqual(space, KnowledgeSpaceTypes.PRODUCT)
        self.assertEqual(item_ids, [product.pk])
        self.assertIsNotNone(response.data["data"]["training_log"])

    def test_update_rejects_duplicate_sku(self):
        self.create_product(sku="WM-001")
        other = self.create_product(name="Clone target", sku="OTHER-1")

        response = self.client.patch(
            f"{PRODUCT_UPDATE_URL}?chatbot={self.chatbot.slug}&product={other.pk}",
            {"sku": "wm-001"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_disable_multiple_products_removes_vectors(self):
        first = self.create_product()
        second = self.create_product(name="USB Hub", sku="UH-100")
        vector_service = FakeVectorService()

        with patch(
            "knowledge.api.v1.client.views.KnowledgeVectorService",
            return_value=vector_service,
        ):
            response = self.client.post(
                PRODUCT_DISABLE_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "products": [str(first.pk), str(second.pk)],
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["disabled_count"], 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_enabled)
        self.assertFalse(second.is_enabled)
        self.assertEqual(
            sorted(vector_service.removed_catalog_items),
            sorted(
                [
                    (KnowledgeSpaceTypes.PRODUCT, first.pk),
                    (KnowledgeSpaceTypes.PRODUCT, second.pk),
                ]
            ),
        )

    def test_disable_rejects_unknown_products(self):
        self.create_product()
        missing_id = "00000000-0000-0000-0000-000000000000"

        response = self.client.post(
            PRODUCT_DISABLE_URL,
            {"chatbot": self.chatbot.slug, "products": [missing_id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProductTemplateAndImportTests(KnowledgeTestBase):
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

    def test_import_csv_creates_products_and_trains(self):
        csv_content = self.csv_file(
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
                {"name": "", "sku": "BAD", "category": "", "description": "", "price": "", "currency": "", "product_url": ""},
                {"name": "No Price", "sku": "", "category": "Misc", "description": "", "price": "", "currency": "", "product_url": ""},
            ]
        )

        with fake_training_stack() as vector_service:
            response = self.client.post(
                PRODUCT_IMPORT_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": csv_content,
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

    def test_import_rejects_unsupported_type(self):
        response = self.client.post(
            PRODUCT_IMPORT_URL,
            {
                "chatbot": self.chatbot.slug,
                "file": SimpleUploadedFile(
                    "products.exe", b"binary", content_type="application/octet-stream",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_import_skips_duplicate_skus(self):
        self.create_product(sku="WM-001")
        csv_content = self.csv_file(
            [
                {
                    "name": "Clone",
                    "sku": "WM-001",
                    "category": "",
                    "description": "",
                    "price": "1.00",
                    "currency": "",
                    "product_url": "",
                }
            ],
        )
        with fake_training_stack():
            response = self.client.post(
                PRODUCT_IMPORT_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": csv_content,
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["created_count"], 0)
        self.assertIn("WM-001", response.data["data"]["errors"][0])


class ServiceAPITests(KnowledgeTestBase):
    def test_download_service_template(self):
        response = self.client.get(SERVICE_TEMPLATE_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(
            list(rows[0].keys()),
            ["name", "service_type", "description", "cost", "currency"],
        )
        self.assertGreaterEqual(len(rows), 1)

    def test_import_csv_creates_services_and_trains(self):
        csv_content = self.csv_file(
            [
                {
                    "name": "Laptop Repair",
                    "service_type": "repair",
                    "description": "Screen and battery replacement.",
                    "cost": "75.00",
                    "currency": "usd",
                },
                {
                    "name": "",
                    "service_type": "",
                    "description": "",
                    "cost": "",
                    "currency": "",
                },
            ],
            filename="services.csv",
        )

        with fake_training_stack() as vector_service:
            response = self.client.post(
                SERVICE_IMPORT_URL,
                {
                    "chatbot": self.chatbot.slug,
                    "file": csv_content,
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(data["created_count"], 1)
        self.assertEqual(len(data["errors"]), 1)

        service = Service.objects.get(chatbot=self.chatbot, name="Laptop Repair")
        self.assertEqual(service.status, StatusTypes.READY)

        space, item_ids, documents = vector_service.catalog_replacements[0]
        self.assertEqual(space, KnowledgeSpaceTypes.SERVICE)
        self.assertEqual(item_ids, [service.pk])
        contents = [document.content for document in documents]
        self.assertTrue(
            any("Cost: 75.00 USD" in content for content in contents),
        )

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

    def test_list_update_and_delete(self):
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

        with fake_training_stack() as vector_service:
            response = self.client.patch(
                f"{SERVICE_UPDATE_URL}?chatbot={self.chatbot.slug}&service={service.pk}",
                {"cost": "35.00"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service.refresh_from_db()
        self.assertEqual(str(service.cost), "35.00")
        # The update queued training and the eager run retrained the item.
        self.assertEqual(service.status, StatusTypes.READY)
        self.assertEqual(len(vector_service.catalog_replacements), 1)

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
        self.assertEqual(
            vector_service.removed_catalog_items,
            [(KnowledgeSpaceTypes.SERVICE, service.pk)],
        )


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
