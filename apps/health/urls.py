from django.urls import path

from apps.health.views import healthz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
]
