from django.urls import include, path
from chat.api.v1.client import views

chat_sessions = [
    path("create/", views.ChatSessionCreateView.as_view(), name="chat-session-create"),
    path("list/", views.ChatSessionListView.as_view(), name="chat-session-list"),
    path("details/", views.ChatSessionDetailView.as_view(), name="chat-session-detail"),
    path("delete/", views.ChatSessionDeleteView.as_view(), name="chat-session-delete"),
    path(
        "cart-items/",
        views.SessionCartItemListView.as_view(),
        name="chat-session-cart-items",
    ),
    path("orders/", views.SessionOrderListView.as_view(), name="chat-session-orders"),
    path(
        "escalations/",
        views.SessionEscalationListView.as_view(),
        name="chat-session-escalations",
    ),
]

chat_messages = [
    path("list/", views.ChatMessageListView.as_view(), name="chat-message-list"),
    path("send/", views.ChatMessageCreateView.as_view(), name="chat-message-send"),
]

chat_analytics = [
    path("stats/", views.SessionStatsAPIView.as_view(), name="session-stats"),
]

chat_escalations = [
    path("list/", views.ChatEscalationListView.as_view(), name="chat-escalation-list"),
    path(
        "resolve/",
        views.ChatEscalationResolveView.as_view(),
        name="chat-escalation-resolve",
    ),
]

urlpatterns = [
    path("sessions/", include(chat_sessions)),
    path("messages/", include(chat_messages)),
    path("analytics/", include(chat_analytics)),
    path("escalations/", include(chat_escalations)),
]
