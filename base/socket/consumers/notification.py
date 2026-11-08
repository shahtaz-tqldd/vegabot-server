from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from base.socket.events import NOTIFICATION_CREATED
from base.socket.services.groups import (
    chatbot_dashboard_group,
    global_notification_group,
    user_notification_group,
)
from chatbot.models import Chatbot


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    """Realtime notification stream for the signed-in user.

    The frontend connects with a JWT (header or ``?token=``) and
    receives global notifications, notifications addressed to the user,
    and the notifications of every chatbot they own (for example
    training completion).
    """

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return

        owned_chatbot_ids = await database_sync_to_async(
            self.get_chatbot_ids,
        )(user)
        self.groups = [
            global_notification_group(),
            user_notification_group(user.pk),
            *(chatbot_dashboard_group(pk) for pk in owned_chatbot_ids),
        ]
        await self.accept()
        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.send_json({"type": "connection.ready"})

    @staticmethod
    def get_chatbot_ids(user):
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
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})
            return
        await self.send_error("Unsupported event type.")

    async def notification_created(self, event):
        await self.send_json({
            "type": NOTIFICATION_CREATED,
            "data": event["notification"],
        })

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})
