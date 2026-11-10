from django.urls import path, include
from base.api.v1.client import views


file_apis = [
    path("", views.RetrieveFileAPIView.as_view(), name="file-retrieve"),
    path("upload/", views.UploadFileAPIView.as_view(), name="file-upload"),
    path("delete/", views.DeleteFileAPIView.as_view(), name="file-delete"),
]

urlpatterns = [
    path("", views.VegabotConfigAPIView.as_view(), name="Vegabot-config"),
    path("file/", include(file_apis)),
]
