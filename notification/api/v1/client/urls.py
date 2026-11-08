from django.urls import path

from notification.api.v1.client import views


urlpatterns = [
    path("", views.NotificationListAPIView.as_view(), name="notification-list"),
    path("read-all/", views.NotificationReadAllAPIView.as_view(), name="notification-read-all"),
    path("<uuid:notification_id>/read/", views.NotificationReadAPIView.as_view(), name="notification-read"),
]
