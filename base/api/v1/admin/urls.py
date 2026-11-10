from django.urls import path
from base.api.v1.admin import views

urlpatterns = [
    path("update/", views.VegabotConfigUpdateAPIView.as_view(), name="vegabot-config-update"),
]
