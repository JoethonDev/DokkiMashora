"""Health-check endpoint used by the deployment workflow."""

from __future__ import annotations

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET


@csrf_exempt
@require_GET
def healthz(request: HttpRequest) -> HttpResponse:
    """Return a tiny JSON response when the app and database are healthy."""
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - defensive
        db_ok = False

    payload = {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
    }
    return JsonResponse(payload, status=200 if db_ok else 503)
