from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings

v1_client_urls = [
    path("accounts/", include("accounts.api.v1.urls")),
    path("base/", include("base.api.v1.client.urls")),
    path("chatbots/", include("chatbot.api.v1.client.urls")),
    path("chat/", include("chat.api.v1.client.urls")),
    path("knowledge/", include("knowledge.api.v1.client.urls")),
    path("notifications/", include("notification.api.v1.client.urls")),
]

v1_admin_urls = [
    path("base/", include("base.api.v1.admin.urls")),
    path("chatbots/", include("chatbot.api.v1.admin.urls")),
    path("analytics/", include("analytics.api.v1.admin.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(v1_client_urls)),
    path("api/v1/admin/", include(v1_admin_urls)),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
