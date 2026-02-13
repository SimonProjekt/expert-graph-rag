from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.health.services import HealthCheckService


@require_GET
def healthz(_request):
    report = HealthCheckService().check()
    status_code = 200 if report["status"] == "ok" else 503
    return JsonResponse(report, status=status_code)
