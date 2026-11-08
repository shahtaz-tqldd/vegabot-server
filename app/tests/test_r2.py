from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from app.services.r2 import (
    R2Storage,
    delete_image,
    extract_key,
    upload_file,
    upload_image,
)
from base.signals import delete_public_asset_with_record


@override_settings(
    R2_BUCKET_NAME="dison-chatbot",
    R2_PUBLIC_URL="https://assets.example.com",
    R2_IMAGES_PREFIX="images",
)
class R2StorageTests(SimpleTestCase):
    def setUp(self):
        self.client = Mock()
        self.storage = R2Storage(client=self.client)

    def test_public_image_upload_returns_encoded_r2_url(self):
        image = SimpleUploadedFile(
            "logo.png",
            (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
                b"\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff"
                b"\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
            content_type="image/png",
        )

        result = upload_image(
            image,
            folder="images/workspaces",
            public_id="workspace logo",
            storage=self.storage,
        )

        self.assertEqual(result["key"], "images/workspaces/workspace-logo.webp")
        self.assertEqual(
            result["url"],
            "https://assets.example.com/images/workspaces/workspace-logo.webp",
        )
        extra_args = self.client.upload_fileobj.call_args.kwargs["ExtraArgs"]
        self.assertEqual(extra_args["ContentType"], "image/webp")
        self.assertIn("immutable", extra_args["CacheControl"])
        self.assertNotIn("ACL", extra_args)

    def test_public_file_upload_keeps_content_and_returns_r2_url(self):
        document = SimpleUploadedFile(
            "report.pdf",
            b"%PDF-1.4 fake body",
            content_type="application/pdf",
        )

        result = upload_file(
            document,
            folder="files/chat",
            public_id="attachment-1",
            storage=self.storage,
        )

        self.assertEqual(result["key"], "files/chat/attachment-1.pdf")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertEqual(
            result["url"],
            "https://assets.example.com/files/chat/attachment-1.pdf",
        )
        self.client.upload_fileobj.assert_called_once_with(
            document,
            "dison-chatbot",
            "files/chat/attachment-1.pdf",
            ExtraArgs={"ContentType": "application/pdf"},
        )

    def test_delete_accepts_only_urls_from_the_configured_public_domain(self):
        self.assertEqual(
            extract_key(
                "https://assets.example.com/images/users/avatar%20one.png?x=1"
            ),
            "images/users/avatar one.png",
        )
        self.assertIsNone(extract_key("https://images.example.net/avatar.png"))

        result = delete_image(
            image_url="https://assets.example.com/images/users/avatar.png",
            storage=self.storage,
        )

        self.assertEqual(result["result"], "deleted")
        self.client.delete_object.assert_called_once_with(
            Bucket="dison-chatbot",
            Key="images/users/avatar.png",
        )

    @patch("base.signals.schedule_delete_image")
    def test_record_delete_signal_schedules_its_public_asset(self, schedule_delete):
        sender = SimpleNamespace(
            _meta=SimpleNamespace(label_lower="chatbot.chatbot")
        )
        instance = SimpleNamespace(
            logo="https://assets.example.com/chatbots/logo.webp"
        )

        delete_public_asset_with_record(sender, instance)

        schedule_delete.assert_called_once_with(image_url=instance.logo)
