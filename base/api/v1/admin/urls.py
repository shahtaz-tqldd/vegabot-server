from django.urls import path
from base.api.v1.admin import views

urlpatterns = [
    path("update/", views.DisonConfigUpdateAPIView.as_view(), name="dison-config-update"),
]
