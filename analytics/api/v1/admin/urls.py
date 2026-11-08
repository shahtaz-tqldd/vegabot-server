from django.urls import path

from analytics.api.v1.admin import views

urlpatterns = [
    path(
        "ai-usage/",
        views.AIUsageStatsAPIView.as_view(),
        name="analytics-ai-usage",
    ),
    path(
        "growth/",
        views.UserGrowthAPIView.as_view(),
        name="analytics-growth",
    ),
]
