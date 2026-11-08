from django.urls import path

from base.socket.consumers.chat import ChatSessionConsumer
from base.socket.consumers.live import LiveChatConsumer

websocket_urlpatterns = [
    path(
        "ws/chat/sessions/<uuid:session_id>/",
        ChatSessionConsumer.as_asgi(),
    ),
    path(
        "ws/chat/sessions/<uuid:session_id>/live/",
        LiveChatConsumer.as_asgi(),
    ),
]
