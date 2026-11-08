from django.urls import path

from chatbot.api.v1.client import views

# chatbot
urlpatterns = [
    path("create/", views.ChatbotCreateView.as_view(), name="chatbot-create"),
    path("details/", views.ChatbotDetailView.as_view(), name="chatbot-detail"),
    path("list/", views.ChatbotListAPIView.as_view(), name="chatbot-list"),
    path("update/", views.ChatbotUpdateView.as_view(), name="chatbot-update"),
    path("delete/", views.ChatbotDeleteView.as_view(), name="chatbot-delete"),
]
