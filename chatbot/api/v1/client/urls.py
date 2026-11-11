from django.urls import path

from chatbot.api.v1.client import views, voice_views

# chatbot
urlpatterns = [
    path("create/", views.ChatbotCreateView.as_view(), name="chatbot-create"),
    path("details/", views.ChatbotDetailView.as_view(), name="chatbot-detail"),
    path("list/", views.ChatbotListAPIView.as_view(), name="chatbot-list"),
    path("update/", views.ChatbotUpdateView.as_view(), name="chatbot-update"),
    path("delete/", views.ChatbotDeleteView.as_view(), name="chatbot-delete"),
    # voices
    path(
        "voices/options/",
        voice_views.VoiceOptionsView.as_view(),
        name="chatbot-voice-options",
    ),
    path(
        "voices/preview/",
        voice_views.VoicePreviewView.as_view(),
        name="chatbot-voice-preview",
    ),
    path(
        "voices/list/",
        voice_views.ChatbotVoicesView.as_view(),
        name="chatbot-voice-list",
    ),
    path(
        "voices/select/",
        voice_views.VoiceSelectView.as_view(),
        name="chatbot-voice-select",
    ),
    path(
        "voices/custom/create/",
        voice_views.CustomVoiceCreateView.as_view(),
        name="chatbot-custom-voice-create",
    ),
    path(
        "voices/custom/delete/",
        voice_views.CustomVoiceDeleteView.as_view(),
        name="chatbot-custom-voice-delete",
    ),
]
