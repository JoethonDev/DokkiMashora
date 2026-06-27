from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from ..prompts import DEFAULT_PROMPT_VERSION, get_prompt

try:
    from google import genai
except ImportError:
    genai = None

logger = logging.getLogger(__name__)

GEMINI_DISABLED_REVIEW = object()


class GeminiProvider:
    def __init__(self) -> None:
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.timeout = settings.GEMINI_TIMEOUT_SECONDS
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            if genai is None:
                raise ImportError("google.genai is not available")
            self._client = genai.Client(
                api_key=self.api_key,
                http_options={"timeout": self.timeout * 1000},
            )
        return self._client

    def review(
        self,
        prompt: str,
        system_instruction: str | None = None,
        *,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(_("Gemini is not configured (GEMINI_API_KEY is empty)."))
        client = self._get_client()
        try:
            kwargs = {
                "model": self.model,
                "contents": prompt,
            }
            if system_instruction:
                kwargs["system_instruction"] = system_instruction
            response = client.models.generate_content(**kwargs)
            return {
                "raw": response.text,
                "model": self.model,
                "finish_reason": getattr(response.candidates[0], "finish_reason", None) if response.candidates else None,
                "prompt_version": prompt_version,
            }
        except Exception as exc:
            logger.error("Gemini API call failed: %s", exc)
            raise


def parse_scoring_response(response_text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "suggested_score": None,
        "suggested_feedback": "",
        "decision": "pending",
    }
    if not response_text:
        return result
    try:
        data = json.loads(response_text)
        if isinstance(data, dict):
            score = data.get("score")
            if score is not None:
                result["suggested_score"] = Decimal(str(score))
            result["suggested_feedback"] = data.get("feedback", "")
            result["decision"] = data.get("decision", "pending")
    except (json.JSONDecodeError, ValueError, TypeError):
        result["suggested_feedback"] = response_text[:500]
    return result


def build_mcq_prompt(
    question_text: str,
    choices: list[dict],
    rubric: str,
    selected_choice_text: str | None = None,
    max_points: Decimal | None = None,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    lines = [
        get_prompt(prompt_version, "entrance_mcq"),
        "",
        f"Question: {question_text}",
    ]
    if rubric:
        lines.append(f"Rubric: {rubric}")
    if max_points is not None:
        lines.append(f"Question max points: {max_points}")
    if choices:
        lines.append("Choices:")
        for c in choices:
            lines.append(f"- {c['text']} (weight: {c['weight']})")
    if selected_choice_text:
        lines.append(f"\nStudent selected choice: {selected_choice_text}")
    return "\n".join(lines)


def build_written_prompt(
    question_text: str,
    student_answer: str,
    rubric: str,
    *,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    kind: str = "entrance_written",
) -> str:
    lines = [
        get_prompt(prompt_version, kind),
        "",
        f"Question: {question_text}",
    ]
    if rubric:
        lines.append(f"Rubric: {rubric}")
    lines.append(f"\nStudent answer: {student_answer}")
    return "\n".join(lines)
