from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings

from .models import GeminiReview
from .prompts import DEFAULT_PROMPT_VERSION
from .providers.gemini import (
    GeminiProvider,
    build_mcq_prompt,
    build_written_prompt,
    parse_scoring_response,
)

logger = logging.getLogger(__name__)

provider = GeminiProvider()


def _record_error(
    *,
    target_app_label: str,
    target_model: str,
    target_object_id: str,
    request_ref: str,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    request_payload: dict[str, Any] | None = None,
    error: str = "",
) -> GeminiReview:
    return GeminiReview.objects.create(
        target_app_label=target_app_label,
        target_model=target_model,
        target_object_id=target_object_id,
        prompt_version=prompt_version,
        model_name="",
        request_ref=request_ref,
        request_payload=request_payload or {},
        response_payload={},
        suggested_score=None,
        suggested_feedback=error or "AI review failed.",
        decision="pending",
    )


def log_disabled_review(
    *,
    target_app_label: str,
    target_model: str,
    target_object_id: str,
    request_ref: str,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    request_payload: dict[str, Any] | None = None,
    suggested_feedback: str = "",
) -> GeminiReview:
    return GeminiReview.objects.create(
        target_app_label=target_app_label,
        target_model=target_model,
        target_object_id=target_object_id,
        prompt_version=prompt_version,
        model_name="",
        request_ref=request_ref,
        request_payload=request_payload or {},
        response_payload={},
        suggested_score=None,
        suggested_feedback=suggested_feedback or "AI review is disabled.",
        decision="pending",
    )


def review_entrance_mcq(
    *,
    question_text: str,
    choices: list[dict[str, Any]],
    selected_choice_text: str | None = None,
    rubric: str,
    max_points: Decimal | None = None,
    target_object_id: str,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> GeminiReview:
    request_ref = uuid.uuid4().hex
    request_payload = {
        "question_text": question_text,
        "choices": choices,
        "selected_choice": selected_choice_text,
        "rubric": rubric,
        "max_points": str(max_points) if max_points else None,
        "prompt_version": prompt_version,
    }
    if not provider.enabled:
        return log_disabled_review(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
        )
    prompt = build_mcq_prompt(
        question_text, choices, rubric, selected_choice_text, max_points,
        prompt_version=prompt_version,
    )
    try:
        response = provider.review(prompt, prompt_version=prompt_version)
        parsed = parse_scoring_response(response["raw"])
        return GeminiReview.objects.create(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            prompt_version=prompt_version,
            model_name=response.get("model", settings.GEMINI_MODEL),
            request_ref=request_ref,
            request_payload=request_payload,
            response_payload=response,
            suggested_score=parsed["suggested_score"],
            suggested_feedback=parsed["suggested_feedback"],
            decision=parsed["decision"],
        )
    except Exception:
        return _record_error(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
            error="Gemini call failed; pending manual review.",
        )


def review_entrance_written(
    *,
    question_text: str,
    student_answer: str,
    rubric: str,
    target_object_id: str,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> GeminiReview:
    request_ref = uuid.uuid4().hex
    request_payload = {
        "question_text": question_text,
        "student_answer": student_answer,
        "rubric": rubric,
        "prompt_version": prompt_version,
    }
    if not provider.enabled:
        return log_disabled_review(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
        )
    prompt = build_written_prompt(
        question_text, student_answer, rubric,
        prompt_version=prompt_version, kind="entrance_written",
    )
    try:
        response = provider.review(prompt, prompt_version=prompt_version)
        parsed = parse_scoring_response(response["raw"])
        return GeminiReview.objects.create(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            prompt_version=prompt_version,
            model_name=response.get("model", settings.GEMINI_MODEL),
            request_ref=request_ref,
            request_payload=request_payload,
            response_payload=response,
            suggested_score=parsed["suggested_score"],
            suggested_feedback=parsed["suggested_feedback"],
            decision=parsed["decision"],
        )
    except Exception:
        return _record_error(
            target_app_label="entrance_exams",
            target_model="EntranceAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
            error="Gemini call failed; pending manual review.",
        )


def review_assessment_answer(
    *,
    question_text: str,
    student_answer: str,
    rubric: str,
    target_object_id: str,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> GeminiReview:
    request_ref = uuid.uuid4().hex
    request_payload = {
        "question_text": question_text,
        "student_answer": student_answer,
        "rubric": rubric,
        "prompt_version": prompt_version,
    }
    if not provider.enabled:
        return log_disabled_review(
            target_app_label="assessments",
            target_model="AssessmentAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
        )
    prompt = build_written_prompt(
        question_text, student_answer, rubric,
        prompt_version=prompt_version, kind="assessment_written",
    )
    try:
        response = provider.review(prompt, prompt_version=prompt_version)
        parsed = parse_scoring_response(response["raw"])
        return GeminiReview.objects.create(
            target_app_label="assessments",
            target_model="AssessmentAnswer",
            target_object_id=target_object_id,
            prompt_version=prompt_version,
            model_name=response.get("model", settings.GEMINI_MODEL),
            request_ref=request_ref,
            request_payload=request_payload,
            response_payload=response,
            suggested_score=parsed["suggested_score"],
            suggested_feedback=parsed["suggested_feedback"],
            decision=parsed["decision"],
        )
    except Exception:
        return _record_error(
            target_app_label="assessments",
            target_model="AssessmentAnswer",
            target_object_id=target_object_id,
            request_ref=request_ref,
            prompt_version=prompt_version,
            request_payload=request_payload,
            error="Gemini call failed; pending manual review.",
        )
