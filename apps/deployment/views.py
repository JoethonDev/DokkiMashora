from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .health import health_check


@csrf_exempt
@require_GET
def health_view(request: HttpRequest) -> HttpResponse:
    payload = health_check()
    return JsonResponse(payload, status=200 if payload["status"] == "ok" else 503)
