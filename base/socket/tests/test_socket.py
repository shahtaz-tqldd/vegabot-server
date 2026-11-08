import sys
import types

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

# ``channels.testing``'s package init imports daphne, which this project
# does not use (ASGI is served with uvicorn). Stub it so the websocket
# test communicator can be imported.
_daphne_testing = types.ModuleType("daphne.testing")
_daphne_testing.DaphneProcess = object
sys.modules.setdefault("daphne", types.ModuleType("daphne"))
sys.modules.setdefault("daphne.testing", _daphne_testing)

from channels.testing.websocket import WebsocketCommunicator  # noqa: E402
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from base.socket.consumers.chat import ChatSessionConsumer  # noqa: F401
from base.socket.consumers.live import LiveChatConsumer  # noqa: F401
from base.socket.events import LIVE_NOT_AVAILABLE, NOTIFICATION_CREATED
from base.socket.middleware import JWTAuthMiddlewareStack
from base.socket.routing import websocket_urlpatterns
from base.socket.services.broadcaster import broadcast, publish_chat_message
from base.socket.services.groups import (
    chat_session_group,
    chatbot_dashboard_group,
)
from chat.models import ChatSession
from chatbot.models import Chatbot

User = get_user_model()


def socket_app():
    """The production websocket stack: routing + JWT auth."""
    from channels.routing import URLRouter

    return JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))

IN_MEMORY_LAYER = override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)


def token_for(user):
    return str(AccessToken.for_user(user))


class SocketTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            email="socket@example.com",
            password="StrongPass123!",
            name="Socket User",
        )
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Socket Bot",
            business_type="ecommerce",
            created_by=self.user,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            created_by=self.user,
        )


@IN_MEMORY_LAYER
class ChatSessionConsumerTests(SocketTestMixin, TransactionTestCase):
    def _connect(self, token=None):
        url = f"/ws/chat/sessions/{self.session.pk}/?token={token or token_for(self.user)}"
        return WebsocketCommunicator(socket_app(), url)

    def test_connect_delivers_session_messages(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from()
            self.assertEqual(ready["type"], "connection.ready")

            await get_channel_layer().group_send(
                chat_session_group(self.session.pk),
                {
                    "type": "chat.message.event",
                    "event": {
                        "type": "chat.message.created",
                        "message": {"id": "m1", "content": "hello"},
                    },
                },
            )
            event = await communicator.receive_json_from()
            self.assertEqual(event["type"], "chat.message.created")
            self.assertEqual(event["message"]["content"], "hello")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_unauthenticated_connection_rejected(self):
        async def scenario():
            communicator = self._connect(token="not-a-token")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(scenario)()

    def test_foreign_session_rejected(self):
        other = User.objects.create_user(
            email="intruder@example.com",
            password="StrongPass123!",
            name="Intruder",
        )

        async def scenario():
            communicator = self._connect(token=token_for(other))
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(scenario)()

    def test_message_send_acknowledges_and_broadcasts(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()  # connection.ready

            await communicator.send_json_to(
                {"type": "message.send", "content": "hello over socket"},
            )
            accepted = await communicator.receive_json_from()
            self.assertEqual(accepted["type"], "message.accepted")

            broadcast = await communicator.receive_json_from()
            self.assertEqual(broadcast["type"], "chat.message.created")
            self.assertEqual(broadcast["message"]["content"], "hello over socket")
            self.assertEqual(
                broadcast["message"]["sender_type"],
                "user",
            )
            await communicator.disconnect()

        async_to_sync(scenario)()
        self.assertTrue(
            ChatSession.objects.get(pk=self.session.pk)
            .messages.filter(content="hello over socket")
            .exists(),
        )

    def test_blank_message_send_rejected(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()

            await communicator.send_json_to({"type": "message.send", "content": "  "})
            error = await communicator.receive_json_from()
            self.assertEqual(error["type"], "error")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_training_notification_reaches_chatbot_owner_socket(self):
        # The owner connects through any chat session socket; the training
        # notification broadcast on their chatbot's group is delivered.
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()  # connection.ready

            broadcast(
                [chatbot_dashboard_group(self.chatbot.pk)],
                {
                    "type": "notification.created",
                    "group": chatbot_dashboard_group(self.chatbot.pk),
                    "notification": {
                        "notification_type": "training_complete",
                        "title": "Knowledge training complete",
                        "message": "Trained 3 product items.",
                    },
                },
            )
            event = await communicator.receive_json_from()
            self.assertEqual(event["type"], NOTIFICATION_CREATED)
            self.assertEqual(
                event["data"]["notification_type"],
                "training_complete",
            )
            await communicator.disconnect()

        async_to_sync(scenario)()


@IN_MEMORY_LAYER
class LiveChatConsumerTests(SocketTestMixin, TransactionTestCase):
    def _connect(self):
        return WebsocketCommunicator(
            socket_app(),
            f"/ws/chat/sessions/{self.session.pk}/live/?token={token_for(self.user)}",
        )

    def test_live_start_reports_error_when_agent_cannot_start(self):
        # The real agent module exists; without ADK_DB_URL the session
        # cannot start and the consumer surfaces a live.error event.
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.receive_json_from()  # connection.ready

            await communicator.send_json_to({"type": "live.start"})
            event = await communicator.receive_json_from()
            self.assertEqual(event["type"], "live.error")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_audio_without_live_start_errors(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()

            await communicator.send_to(bytes_data=b"audio-chunk")
            event = await communicator.receive_json_from()
            self.assertEqual(event["type"], "error")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_text_message_send_works_on_live_socket(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()

            await communicator.send_json_to(
                {"type": "message.send", "content": "text alongside voice"},
            )
            accepted = await communicator.receive_json_from()
            self.assertEqual(accepted["type"], "message.accepted")
            await communicator.disconnect()

        async_to_sync(scenario)()


class BroadcasterTests(APITestCase):
    @override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    )
    def test_publish_chat_message_reaches_session_group_member(self):
        async def scenario():
            layer = get_channel_layer()
            channel_name = await layer.new_channel()
            await layer.group_add(
                chat_session_group(self.session.pk),
                channel_name,
            )

            publish_chat_message({"content": "hi"}, self.session.pk)

            message = await layer.receive(channel_name)
            self.assertEqual(message["type"], "chat.message.event")
            self.assertEqual(message["event"]["message"]["content"], "hi")

        self.session = ChatSession.objects.create(
            chatbot=Chatbot.objects.create(
                chatbot_name="Broadcaster Bot",
                business_type="ecommerce",
                created_by=User.objects.create_user(
                    email="broadcaster@example.com",
                    password="StrongPass123!",
                ),
            ),
            created_by=User.objects.get(email="broadcaster@example.com"),
        )
        async_to_sync(scenario)()
