from django.urls import path

from base.socket.consumers.chat import ChatSessionConsumer
from base.socket.consumers.live import LiveChatConsumer
from base.socket.consumers.notification import NotificationConsumer

websocket_urlpatterns = [
    path(
        "ws/notifications/",
        NotificationConsumer.as_asgi(),
    ),
    path(
        "ws/chat/sessions/<uuid:session_id>/",
        ChatSessionConsumer.as_asgi(),
    ),
    path(
        "ws/chat/sessions/<uuid:session_id>/live/",
        LiveChatConsumer.as_asgi(),
    ),
]
