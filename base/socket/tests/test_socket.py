import sys
import types
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
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
from base.socket.consumers.notification import NotificationConsumer  # noqa: F401
from base.socket.events import NOTIFICATION_CREATED
from agent.helpers.lock import acquire_session_lock, session_lock_holder
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
        cache.clear()
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

    def test_message_send_streams_text_reply(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()  # connection.ready

            await communicator.send_json_to(
                {"type": "message.send", "content": "hello over socket"},
            )
            accepted = await communicator.receive_json_from()
            self.assertEqual(accepted["type"], "message.accepted")
            self.assertEqual(accepted["mode"], "text")

            # The user echo (group broadcast) and the streamed reply
            # (direct) interleave; compare as a set.
            events = [await communicator.receive_json_from() for _ in range(3)]
            await communicator.disconnect()
            return events

        with patch(
            "base.socket.consumers.chat.AgentClient", FakeAgentClient,
        ), patch("base.socket.consumers.chat.build_session_service", return_value=None):
            events = async_to_sync(scenario)()

        self.assertEqual(
            sorted(event["type"] for event in events),
            ["agent.response.completed", "agent.response.delta", "chat.message.created"],
        )
        echo = next(e for e in events if e["type"] == "chat.message.created")
        self.assertEqual(echo["message"]["content"], "hello over socket")
        self.assertEqual(echo["message"]["sender_type"], "user")
        self.assertEqual(FakeAgentClient.answered, ["hello over socket"])
        self.assertIsNone(session_lock_holder(self.session.pk))

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
class NotificationConsumerTests(SocketTestMixin, TransactionTestCase):
    def _connect(self, token=None):
        url = f"/ws/notifications/?token={token or token_for(self.user)}"
        return WebsocketCommunicator(socket_app(), url)

    def test_connect_delivers_owned_chatbot_notifications(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            ready = await communicator.receive_json_from()
            self.assertEqual(ready["type"], "connection.ready")

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

    def test_ping_pong(self):
        async def scenario():
            communicator = self._connect()
            connected, _ = await communicator.connect()
            await communicator.receive_json_from()  # connection.ready

            await communicator.send_json_to({"type": "ping"})
            event = await communicator.receive_json_from()
            self.assertEqual(event["type"], "pong")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_unauthenticated_connection_rejected(self):
        async def scenario():
            communicator = self._connect(token="not-a-token")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        async_to_sync(scenario)()


class FakeAgentClient:
    """Stands in for agent.client.AgentClient: one delta, then done."""

    answered = []

    @classmethod
    def for_session(cls, session_id, **kwargs):
        return cls()

    async def generate_reply(self, message, *, emit=None):
        FakeAgentClient.answered.append(message.content)
        await emit({"type": "agent.response.delta", "data": {"delta": "Hi"}})
        await emit({"type": "agent.response.completed", "data": {"message": None}})


class FakeVoiceSession:
    instances = []
    fail_start = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.audio = []
        self.texts = []
        self.closed = False
        FakeVoiceSession.instances.append(self)

    async def start(self):
        if FakeVoiceSession.fail_start:
            raise RuntimeError("no live model")

    async def send_audio(self, chunk):
        self.audio.append(chunk)

    async def send_text(self, text):
        self.texts.append(text)

    async def close(self):
        self.closed = True


@IN_MEMORY_LAYER
class ChatModeTests(SocketTestMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        FakeVoiceSession.instances = []
        FakeVoiceSession.fail_start = False
        patchers = [
            patch("base.socket.consumers.chat.VoiceSession", FakeVoiceSession),
            patch("base.socket.consumers.chat.build_session_service", return_value=None),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _connect(self):
        return WebsocketCommunicator(
            socket_app(),
            f"/ws/chat/sessions/{self.session.pk}/?token={token_for(self.user)}",
        )

    async def _ready(self):
        communicator = self._connect()
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        ready = await communicator.receive_json_from()
        self.assertEqual(ready["data"]["mode"], "text")
        return communicator

    def test_audio_mode_routes_audio_and_text_to_the_voice_session(self):
        async def scenario():
            communicator = await self._ready()
            await communicator.send_json_to({"type": "mode.set", "mode": "audio"})
            changed = await communicator.receive_json_from()
            self.assertEqual(changed, {"type": "mode.changed", "data": {"mode": "audio"}})

            await communicator.send_to(bytes_data=b"pcm-chunk")
            await communicator.send_json_to(
                {"type": "message.send", "content": "typed while talking"},
            )
            accepted = await communicator.receive_json_from()
            self.assertEqual(accepted["type"], "message.accepted")
            self.assertEqual(accepted["mode"], "audio")
            await communicator.receive_json_from()  # chat.message.created echo

            await communicator.send_json_to({"type": "mode.set", "mode": "text"})
            changed = await communicator.receive_json_from()
            self.assertEqual(changed["data"]["mode"], "text")
            await communicator.disconnect()

        async_to_sync(scenario)()

        voice = FakeVoiceSession.instances[0]
        self.assertEqual(voice.audio, [b"pcm-chunk"])
        self.assertEqual(voice.texts, ["typed while talking"])
        self.assertTrue(voice.closed)
        self.assertEqual(
            self.session.messages.get().mode, "audio",
        )

    def test_failed_voice_start_stays_in_text_mode(self):
        FakeVoiceSession.fail_start = True

        async def scenario():
            communicator = await self._ready()
            await communicator.send_json_to({"type": "mode.set", "mode": "audio"})
            error = await communicator.receive_json_from()
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["code"], "voice_unavailable")

            await communicator.send_to(bytes_data=b"pcm-chunk")
            error = await communicator.receive_json_from()
            self.assertEqual(error["message"], "Switch to audio mode before sending audio.")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_invalid_mode_rejected(self):
        async def scenario():
            communicator = await self._ready()
            await communicator.send_json_to({"type": "mode.set", "mode": "video"})
            error = await communicator.receive_json_from()
            self.assertEqual(error["type"], "error")
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_busy_session_rejects_text_turns(self):
        acquire_session_lock(self.session.pk, "voice:elsewhere", 60)

        async def scenario():
            communicator = await self._ready()
            await communicator.send_json_to({"type": "message.send", "content": "hi"})
            error = await communicator.receive_json_from()
            self.assertEqual(error["code"], "busy")
            await communicator.disconnect()

        async_to_sync(scenario)()
        self.assertFalse(self.session.messages.exists())


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
