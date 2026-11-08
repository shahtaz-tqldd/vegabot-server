from django.urls import path

from chatbot.api.v1.admin import views

# chatbot admin
urlpatterns = [
    path("list/", views.ChatbotAdminListAPIView.as_view(), name="admin-chatbot-list"),
    path("update/", views.ChatbotAdminUpdateView.as_view(), name="admin-chatbot-update"),
    path("delete/", views.ChatbotAdminDeleteView.as_view(), name="admin-chatbot-delete"),
]
