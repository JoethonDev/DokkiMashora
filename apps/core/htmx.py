"""HTMX request detection utility."""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse


def is_htmx(request: HttpRequest) -> bool:
    # Boosted shell navigation needs the full page so the client can select the
    # replaceable content boundary; ordinary HTMX interactions still receive
    # their fragments.
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-Boosted") != "true"
    )


def hx_toast(
    response: HttpResponse,
    message: str,
    type: str = "success",
    extra_events: dict | None = None,
) -> HttpResponse:
    """Set HX-Trigger header for toast notifications.

    Merges extra_events without overwriting the toast event.
    Supports both hyphen and camelCase keys for compatibility.
    """
    payload: dict = {"show-toast": {"message": str(message), "type": type}}
    # Alias for spec compatibility (showToast camelCase)
    payload["showToast"] = payload["show-toast"]
    # Merge existing HX-Trigger if already set (preserve without overwriting toast)
    existing_raw = response.headers.get("HX-Trigger") if hasattr(response, "headers") else None
    if not existing_raw:
        try:
            existing_raw = response["HX-Trigger"]
        except KeyError:
            existing_raw = None
    if existing_raw:
        try:
            existing = json.loads(existing_raw) if isinstance(existing_raw, str) else {}
            for k, v in existing.items():
                if k not in payload:
                    payload[k] = v
        except (ValueError, TypeError):
            pass
    if extra_events:
        for k, v in extra_events.items():
            if k not in payload:
                payload[k] = v
    response["HX-Trigger"] = json.dumps(payload)
    return response
