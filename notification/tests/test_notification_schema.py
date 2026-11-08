from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework import status
from rest_framework.test import APITestCase

from base.socket.services.groups import chatbot_dashboard_group
from chatbot.models import Chatbot
from notification.models import Notification
from notification.services import create_chatbot_notification
from notification.utils.choices import NotificationType

User = get_user_model()

NOTIFICATION_LIST_URL = reverse("notification-list")


class NotificationServiceTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Notify Bot",
            business_type="ecommerce",
            created_by=self.owner,
        )

    def test_training_notification_stored_on_the_chatbot(self):
        notification = create_chatbot_notification(
            chatbot=self.chatbot,
            title="Knowledge training complete",
            message="Trained 3 product items.",
            metadata={"space": "product"},
        )

        self.assertEqual(
            notification.notification_type,
            NotificationType.TRAINING_COMPLETE,
        )
        stored = Notification.objects.get(pk=notification.pk)
        self.assertEqual(stored.chatbot, self.chatbot)
        self.assertEqual(stored.metadata["space"], "product")

    @override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    def test_training_notification_broadcasts_to_chatbot_group(self):
        notification = create_chatbot_notification(
            chatbot=self.chatbot,
            title="Knowledge training complete",
            message="done",
        )

        async def scenario():
            layer = get_channel_layer()
            channel_name = await layer.new_channel()
            await layer.group_add(
                chatbot_dashboard_group(self.chatbot.pk),
                channel_name,
            )
            from notification.services import emit_notification

            emit_notification(notification)
            return await layer.receive(channel_name)

        event = async_to_sync(scenario)()
        self.assertEqual(event["type"], "notification.created")
        self.assertEqual(
            event["notification"]["notification_type"],
            "training_complete",
        )
        self.assertEqual(event["group"], chatbot_dashboard_group(self.chatbot.pk))


class NotificationAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.other = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Notify Bot",
            business_type="ecommerce",
            created_by=self.owner,
        )
        self.foreign_chatbot = Chatbot.objects.create(
            chatbot_name="Foreign Bot",
            business_type="ecommerce",
            created_by=self.other,
        )
        create_chatbot_notification(
            chatbot=self.chatbot,
            title="Knowledge training complete",
            message="Trained 12 chunks.",
        )
        create_chatbot_notification(
            chatbot=self.foreign_chatbot,
            title="Knowledge training complete",
            message="Theirs.",
        )
        self.client.force_authenticate(self.owner)

    def test_list_shows_only_own_chatbot_notifications(self):
        response = self.client.get(NOTIFICATION_LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(
            response.data["data"][0]["chatbot_id"],
            str(self.chatbot.pk),
        )
        self.assertEqual(response.data["meta"]["unread_count"], 1)

    def test_filter_by_chatbot_id(self):
        response = self.client.get(
            NOTIFICATION_LIST_URL, {"chatbot_id": self.foreign_chatbot.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 0)

    def test_mark_read_and_unread_count_drops(self):
        notification = Notification.objects.get(chatbot=self.chatbot)
        response = self.client.patch(
            reverse(
                "notification-read",
                kwargs={"notification_id": notification.pk},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        list_response = self.client.get(NOTIFICATION_LIST_URL)
        self.assertEqual(list_response.data["meta"]["unread_count"], 0)

    def test_read_all(self):
        response = self.client.patch(reverse("notification-read-all"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["marked_read_count"], 1)
