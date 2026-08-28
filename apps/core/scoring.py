"""Canonical pure scoring helpers (DUP-003).

Only tiny pure arithmetic is shared between Assessment and Entrance Exam
workflows; all orchestration, state machines, and persistence remain separate.

Rule: round-first-then-evaluate — percent is quantized to 0.01 before any
pass comparison, exactly as required for both diploma and entrance grading.
"""

from __future__ import annotations

from decimal import Decimal

_SCORE_QUANT = Decimal("0.01")
_HUNDRED = Decimal("100")


def compute_percent(earned: Decimal, possible: Decimal) -> Decimal | None:
    """Return quantized percent (earned/possible*100) or None when no possible."""
    if possible is None:
        return None
    try:
        p = Decimal(possible)
    except Exception:
        return None
    if p <= 0:
        return None
    e = Decimal(earned) if earned is not None else Decimal("0")
    return (e / p * _HUNDRED).quantize(_SCORE_QUANT)


def is_passing(percent: Decimal | None, passing_score: Decimal | None) -> bool | None:
    """Compare already-rounded percent to passing threshold.

    Returns None when percent is None (no applicable result), otherwise
    True/False. Callers that need False for None can coerce.
    """
    if percent is None:
        return None
    if passing_score is None:
        return None
    return percent >= Decimal(passing_score)


def calculate_score(
    earned: Decimal,
    possible: Decimal,
    passing_score: Decimal | None,
) -> tuple[Decimal | None, bool | None]:
    """Quantize-first then evaluate (DUP-003 canonical).

    Returns (percent, is_pass) where percent is quantized to 0.01 and
    is_pass is evaluated from the rounded percent. When possible <=0,
    percent is None and is_pass is None.
    """
    percent = compute_percent(earned, possible)
    passing = is_passing(percent, passing_score) if passing_score is not None else None
    return percent, passing


def mcq_score(
    points: Decimal,
    is_correct: bool,
    penalty_percent: Decimal | None,
) -> Decimal:
    """Canonical MCQ penalty (DUP-003).

    Correct -> full points. Incorrect with penalty -> points - (points*penalty/100)
    clamped to 0. No penalty or zero penalty -> 0 for incorrect.
    """
    pts = Decimal(points) if points is not None else Decimal("0")
    if is_correct:
        return pts
    if penalty_percent is None:
        return Decimal("0.00")
    try:
        pen = Decimal(penalty_percent)
    except Exception:
        return Decimal("0.00")
    if pen > 0:
        # Quantize penalty result to 2 decimals to match stored scores
        score = pts - (pts * pen / _HUNDRED)
        if score < 0:
            score = Decimal("0.00")
        # Keep two decimals for consistency with other scoring paths
        return score.quantize(_SCORE_QUANT) if score != 0 else Decimal("0.00")
    return Decimal("0.00")
