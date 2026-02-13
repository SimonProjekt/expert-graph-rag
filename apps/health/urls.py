from django.urls import path

from apps.health.views import healthz

urlpatterns = [
    path("health", healthz, name="health"),
    path("healthz", healthz, name="healthz"),
]
