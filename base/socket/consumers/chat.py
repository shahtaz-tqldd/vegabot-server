from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from base.socket.events import NOTIFICATION_CREATED
from base.socket.services.groups import (
    chat_session_group,
    chatbot_dashboard_group,
)
from chatbot.models import Chatbot
from chat.services.messages import create_user_message
from chat.services.sessions import session_for_user


class ChatSessionConsumer(AsyncJsonWebsocketConsumer):
    """Realtime transport for one chat session.

    The frontend connects with a JWT (header or ``?token=``) and receives
    every new message in the session plus the notifications of the
    chatbots it owns (for example training completion). Clients may also
    send ``message.send`` events instead of using the REST API.
    """

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return

        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        self.session = await database_sync_to_async(session_for_user)(
            user, session_id,
        )
        if self.session is None:
            await self.close(code=4403)
            return

        owned_chatbot_ids = await database_sync_to_async(
            self.get_owned_chatbot_ids,
        )(user)
        self.groups = {
            chat_session_group(self.session.pk),
            *(chatbot_dashboard_group(chatbot_id) for chatbot_id in owned_chatbot_ids),
        }
        await self.accept()
        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.send_json({
            "type": "connection.ready",
            "data": {"session_id": str(self.session.pk)},
        })

    @staticmethod
    def get_owned_chatbot_ids(user):
        return list(
            Chatbot.objects.filter(
                is_deleted=False,
                created_by=user,
            ).values_list("pk", flat=True),
        )

    async def disconnect(self, close_code):
        for group in getattr(self, "groups", ()):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error("Expected a JSON object.")
            return
        event_type = content.get("type")

        if event_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if event_type == "message.send":
            await self.handle_message_send(content)
            return
        await self.send_error("Unsupported event type.")

    async def handle_message_send(self, content):
        user = self.scope["user"]
        if self.session.created_by_id != user.pk:
            await self.send_error("Only the session owner can send messages here.")
            return
        try:
            message = await database_sync_to_async(create_user_message)(
                self.session, content.get("content"),
            )
        except ValueError as exc:
            await self.send_error(str(exc))
            return
        # Delivery to the session group happens via the ChatMessage
        # post-save signal; acknowledge the accepted message here.
        await self.send_json({
            "type": "message.accepted",
            "session_id": str(self.session.pk),
            "message_id": str(message.pk),
        })

    async def chat_message_event(self, event):
        await self.send_json(event["event"])

    async def notification_created(self, event):
        await self.send_json({
            "type": NOTIFICATION_CREATED,
            "data": event["notification"],
        })

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})
