from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone

from .models import GeminiReview


def log_gemini_review(
    *,
    target_app_label: str,
    target_model: str,
    target_object_id: str,
    prompt_version: str,
    model_name: str,
    request_payload: dict | None = None,
    response_payload: dict | None = None,
    suggested_score: Decimal | None = None,
    suggested_feedback: str = "",
    decision: str = "pending",
    decided_by=None,
    decided_at=None,
    request_ref: str | None = None,
) -> GeminiReview:
    return GeminiReview.objects.create(
        target_app_label=target_app_label,
        target_model=target_model,
        target_object_id=target_object_id,
        prompt_version=prompt_version,
        model_name=model_name,
        request_ref=request_ref or uuid.uuid4().hex,
        request_payload=request_payload or {},
        response_payload=response_payload or {},
        suggested_score=suggested_score,
        suggested_feedback=suggested_feedback,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at or (timezone.now() if decided_by else None),
    )
