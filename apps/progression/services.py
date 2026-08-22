"""Progression services: score aggregation, semester evaluation, promotion, demotion, lock, retake, final exam, completion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import islice

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_RESTRICTED
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    LEGACY_STATUS_ACTIVE,
)
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.attendance.services import (
    get_attendance_evaluation_percent,
    get_attendance_summaries,
    get_attendance_summary,
)
from apps.students.models import StudentProfile

from .constants import (
    COMPONENT_TYPE_ATTENDANCE,
    COMPONENT_TYPE_HOMEWORK_ASSESSMENT,
    COMPONENT_TYPE_SEMESTER_EXAM,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
    DIPLOMA_COMPLETION_REQUIREMENTS_MET,
    EVALUATION_CONFIG_STATUS_ACTIVE,
    FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING,
    FINAL_PROGRESSION_STATUS_PENDING,
    FINAL_PROGRESSION_STATUS_RESOLVED,
    INTERVIEW_DECISION_FAIL,
    INTERVIEW_DECISION_PASS,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_REQUESTED,
    PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
    PROMOTION_DECISION_AUTO_PROMOTE,
    PROMOTION_DECISION_COMPLETE,
    PROMOTION_DECISION_CONFIRM_PASS,
    PROMOTION_DECISION_DEMOTE,
    PROMOTION_DECISION_GRADUATE,
    PROMOTION_DECISION_INTERVIEW_FAIL,
    PROMOTION_DECISION_INTERVIEW_PASS,
    PROMOTION_DECISION_LOCK,
    PROMOTION_DECISION_MANUAL_PROMOTE,
    PROMOTION_DECISION_OVERRIDE_FAIL,
    PROMOTION_DECISION_OVERRIDE_PASS,
    PROMOTION_DECISION_REJOIN_ACTIVATED,
    PROMOTION_DECISION_REJOIN_CANCELLED,
    PROMOTION_DECISION_REJOIN_PLANNED,
    PROMOTION_DECISION_REQUEST_INTERVIEW,
    PROMOTION_DECISION_RETAKE_ASSIGNED,
    PROMOTION_DECISION_RETAKE_CLEARED,
    PROMOTION_DECISION_UNLOCK,
    REJOIN_STATUS_ACTIVE,
    REJOIN_STATUS_CANCELLED,
    REJOIN_STATUS_PLANNED,
    SEMESTER_EVALUATION_FAIL,
    SEMESTER_EVALUATION_PASS,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_DEMOTED,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION,
    SEMESTER_PROGRESS_PROMOTED,
    SEMESTER_PROGRESS_RETAKE_REQUIRED,
    SEMESTER_PROGRESS_UNDER_REVIEW,
)
from .models import (
    DiplomaProgressionRule,
    FinalExamEligibility,
    FinalProgressionOutcome,
    PromotionDecision,
    SemesterEvaluation,
    SemesterEvaluationConfig,
    SemesterGradeComponent,
    SemesterInterview,
    SemesterProgressionRule,
    SemesterRejoinPlan,
    StudentDiplomaCompletion,
    StudentGradeItem,
    StudentSemesterProgress,
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SemesterScoreResult:
    score_percent: Decimal | None
    missing_required: list
    under_review_assessments: list
    failed_assessments: list
    passed: bool
    under_review: bool


@dataclass
class ComponentEvaluationResult:
    total_percent: Decimal | None
    component_results: list
    all_required_passed: bool
    total_passed: bool
    under_review: bool
    status: str


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------

# Locked active vocabulary for semester scoring (P2-01): Diploma assessments
# are Homework and Semester Exam only. Quiz (course scope) and deprecated
# types never contribute to diploma semester scores.
ASSESSMENT_TYPES_FOR_SCORING = [
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
]


def get_semester_assessments(semester: Semester):
    return Assessment.objects.filter(
        semester=semester,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
        assessment_type__in=ASSESSMENT_TYPES_FOR_SCORING,
    )


def aggregate_semester_scores(student_profile: StudentProfile, semester: Semester, *, pass_percent: Decimal | None = None) -> SemesterScoreResult:
    assessments = list(get_semester_assessments(semester))
    if not assessments:
        return SemesterScoreResult(
            score_percent=Decimal("0.00"),
            missing_required=[],
            under_review_assessments=[],
            failed_assessments=[],
            passed=False,
            under_review=False,
        )
    pass_pct = pass_percent if pass_percent is not None else Decimal("50.00")
    total_points = Decimal("0.00")
    earned_points = Decimal("0.00")
    missing_required = []
    under_review_list = []
    failed_list = []

    for assessment in assessments:
        attempts = AssessmentAttempt.objects.filter(
            assessment=assessment,
            student=student_profile.user,
        ).exclude(
            status__in=[ATTEMPT_STATUS_CANCELLED],
        ).order_by("-created_at")
        best = None
        for attempt in attempts:
            if attempt.status == ATTEMPT_STATUS_RELEASED and attempt.score_percent is not None:
                if best is None or (attempt.score_percent or Decimal("0")) > (best.score_percent or Decimal("0")):
                    best = attempt
            elif attempt.status == ATTEMPT_STATUS_GRADED and attempt.score_percent is not None:
                if best is None or (attempt.score_percent or Decimal("0")) > (best.score_percent or Decimal("0")):
                    best = attempt
            elif attempt.status == ATTEMPT_STATUS_UNDER_REVIEW:
                if assessment not in under_review_list:
                    under_review_list.append(assessment)
        if best and best.score_points is not None and assessment.total_points:
            earned_points += best.score_points
            total_points += assessment.total_points
            if best.is_pass is False:
                failed_list.append(assessment)
        else:
            missing_required.append(assessment)

    score_pct = (earned_points / total_points * Decimal("100.00")) if total_points > 0 else Decimal("0.00")
    under_review = len(under_review_list) > 0
    passed = score_pct >= pass_pct and not missing_required and not under_review
    return SemesterScoreResult(
        score_percent=score_pct,
        missing_required=missing_required,
        under_review_assessments=under_review_list,
        failed_assessments=failed_list,
        passed=passed,
        under_review=under_review,
    )


# ---------------------------------------------------------------------------
# Semester evaluation
# ---------------------------------------------------------------------------


from apps.audit.constants import ACTION_RULE_REEVALUATION, ACTION_SEMESTER_FINALIZED
from apps.audit.services import record_audit_event

REEVALUATION_CHUNK_SIZE = 100


def save_progression_rule(rule, actor):
    """Persist a progression rule and re-evaluate affected semesters atomically.

    S2-19/S2-27: rule save and the bounded re-evaluation are one transaction
    with the real actor (never ``None``) so a failure rolls back the rule too.
    ``rule`` is a ``SemesterProgressionRule`` or ``DiplomaProgressionRule``;
    a diploma-level rule re-evaluates every semester that has no active own rule.
    """
    if actor is None:
        raise ValueError(_("An actor is required to save a progression rule."))
    with transaction.atomic():
        rule.full_clean()
        rule.save()
        if isinstance(rule, SemesterProgressionRule):
            reevaluate_semester_for_rule_change(rule.semester, actor)
        elif isinstance(rule, DiplomaProgressionRule):
            for semester in Semester.objects.filter(diploma=rule.diploma, is_active=True):
                has_own_rule = SemesterProgressionRule.objects.filter(
                    semester=semester, is_active=True,
                ).exists()
                if not has_own_rule:
                    reevaluate_semester_for_rule_change(semester, actor)
    return rule


def reevaluate_semester_for_rule_change(semester, actor=None):
    """Re-evaluate non-finalized progress rows for a semester in bounded chunks.

    Rows are updated with one ``bulk_update`` per chunk (S2-27); the immutable
    per-row audit events are written as required, inside the caller's
    transaction.
    """
    non_finalized = StudentSemesterProgress.objects.filter(
        semester=semester,
        status__in=[
            SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION,
            SEMESTER_PROGRESS_FAILED,
            SEMESTER_PROGRESS_UNDER_REVIEW,
            SEMESTER_PROGRESS_IN_PROGRESS,
        ],
        diploma_enrollment__cohort__diploma=semester.diploma,
    ).select_related("diploma_enrollment", "student", "diploma_enrollment__cohort")

    stream = non_finalized.iterator(chunk_size=REEVALUATION_CHUNK_SIZE)
    while True:
        chunk = list(islice(stream, REEVALUATION_CHUNK_SIZE))
        if not chunk:
            break
        for progress in chunk:
            enrollment = progress.diploma_enrollment
            result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
            progress.status = result.status
            progress.calculated_score = result.total_percent
            progress.calculated_at = timezone.now()
            record_audit_event(
                actor=actor,
                action=ACTION_RULE_REEVALUATION,
                target_type="enrollment",
                target_id=str(enrollment.id),
                metadata={
                    "semester_id": semester.id,
                    "status": result.status,
                    "total_percent": str(result.total_percent),
                    "reason": "rule_change",
                },
            )
        StudentSemesterProgress.objects.bulk_update(
            chunk, ["status", "calculated_score", "calculated_at"],
        )


def evaluate_semester_progress(
    student_profile: StudentProfile,
    diploma_enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    is_override: bool = False,
) -> StudentSemesterProgress:
    # DEPRECATED — use evaluate_semester_components + finalize_semester instead
    if not is_override:
        if diploma_enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            raise ValueError(_("Enrollment is not active."))
        if semester.diploma_id != diploma_enrollment.cohort.diploma_id:
            raise ValueError(_("Semester does not belong to the diploma."))

    existing = StudentSemesterProgress.objects.filter(
        diploma_enrollment=diploma_enrollment,
        semester=semester,
    ).first()
    if not is_override and existing and existing.status == SEMESTER_PROGRESS_LOCKED:
        raise ValueError(_("Student progress is locked."))

    progress, __ = StudentSemesterProgress.objects.get_or_create(
        diploma_enrollment=diploma_enrollment,
        semester=semester,
        defaults={
            "student": student_profile,
            "cohort": diploma_enrollment.cohort,
            "status": SEMESTER_PROGRESS_NOT_STARTED,
        },
    )

    rule = SemesterProgressionRule.objects.filter(
        semester=semester,
        is_active=True,
    ).select_related("diploma_rule").first()
    pass_percent = None
    if rule:
        pass_percent = rule.pass_percent
    else:
        diploma_rule = DiplomaProgressionRule.objects.filter(
            diploma=diploma_enrollment.cohort.diploma,
            is_active=True,
        ).first()
        if diploma_rule:
            pass_percent = diploma_rule.semester_pass_percent

    result = aggregate_semester_scores(student_profile, semester, pass_percent=pass_percent)
    now = timezone.now()

    progress.calculated_score = result.score_percent
    progress.calculated_at = now

    if result.under_review:
        progress.status = SEMESTER_PROGRESS_UNDER_REVIEW
    elif result.passed:
        progress.status = SEMESTER_PROGRESS_PASSED
        progress.passed_at = now
    else:
        progress.status = SEMESTER_PROGRESS_FAILED
        progress.failed_at = now

    progress.save()
    return progress


def evaluate_semester_components(
    diploma_enrollment: DiplomaEnrollment,
    cohort: Cohort,
    semester: Semester,
) -> ComponentEvaluationResult:
    # S2-16/L6: never evaluate a semester from another diploma.
    if semester.diploma_id != cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))
    components = list(
        SemesterGradeComponent.objects.filter(
            cohort=cohort,
            semester=semester,
            active=True,
        ).order_by("order")
    )
    if not components:
        raise ValueError(_("No grade component setup for this cohort/semester."))

    total_weight = sum(c.weight_percent for c in components)
    if total_weight != Decimal("100"):
        raise ValueError(_("Active component weights must sum to 100."))

    semester_rule = SemesterProgressionRule.objects.filter(
        semester=semester, is_active=True,
    ).select_related("diploma_rule").first()
    if semester_rule:
        pass_threshold = semester_rule.pass_percent
    else:
        rule = DiplomaProgressionRule.objects.filter(
            diploma=diploma_enrollment.cohort.diploma,
            is_active=True,
        ).first()
        pass_threshold = rule.semester_pass_percent if rule else Decimal("50.00")

    component_results = []
    under_review = False
    all_required_passed = True

    grade_items_by_component = {
        gi.component_id: gi
        for gi in StudentGradeItem.objects.filter(
            component__in=components,
            diploma_enrollment=diploma_enrollment,
        )
    }

    for component in components:
        result = {
            "component_id": component.id,
            "name": component.name,
            "component_type": component.component_type,
            "weight_percent": component.weight_percent,
            "required_min_percent": component.required_min_percent,
            "required_to_pass": component.required_to_pass,
            "score_percent": None,
            "passed_minimum": False,
            "status": "pending",
        }

        grade_item = grade_items_by_component.get(component.id)

        if grade_item is None:
            result["status"] = "missing"
            if component.required_to_pass:
                under_review = True
        elif grade_item.score_percent is None:
            result["status"] = "pending"
            if component.required_to_pass:
                under_review = True
        else:
            result["score_percent"] = grade_item.score_percent
            if grade_item.score_percent >= component.required_min_percent:
                result["passed_minimum"] = True
                result["status"] = "passed"
            else:
                result["status"] = "failed"
                if component.required_to_pass:
                    all_required_passed = False

        component_results.append(result)

    weighted_sum = sum(
        component.weight_percent * result["score_percent"]
        for component, result in zip(components, component_results, strict=False)
        if result["score_percent"] is not None
    )
    total_percent = weighted_sum / Decimal("100")

    total_passed = total_percent >= pass_threshold

    if under_review:
        status = SEMESTER_PROGRESS_UNDER_REVIEW
    elif not all_required_passed or not total_passed:
        status = SEMESTER_PROGRESS_FAILED
    else:
        status = SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION

    return ComponentEvaluationResult(
        total_percent=total_percent,
        component_results=component_results,
        all_required_passed=all_required_passed,
        total_passed=total_passed,
        under_review=under_review,
        status=status,
    )


def get_semester_component_totals(
    enrollments,
    cohort: Cohort,
    semester: Semester,
) -> dict[int, Decimal]:
    """Weighted totals for a whole cohort/semester in a constant query count.

    Batch path for the CSV export: the active component setup and the grade
    items are loaded once and the weighted total (``evaluate_semester_components``
    semantics) is computed per enrollment from the shared inputs instead of
    re-querying per enrollment. Raises the same cohort-level ``ValueError`` for
    a missing or non-100% component setup.
    """
    components = list(
        SemesterGradeComponent.objects.filter(
            cohort=cohort,
            semester=semester,
            active=True,
        ).order_by("order")
    )
    if not components:
        raise ValueError(_("No grade component setup for this cohort/semester."))
    total_weight = sum(component.weight_percent for component in components)
    if total_weight != Decimal("100"):
        raise ValueError(_("Active component weights must sum to 100."))

    items_by_enrollment: dict[int, dict[int, StudentGradeItem]] = {}
    for grade_item in StudentGradeItem.objects.filter(
        diploma_enrollment__in=enrollments,
        component__in=components,
    ):
        items_by_enrollment.setdefault(grade_item.diploma_enrollment_id, {})[
            grade_item.component_id
        ] = grade_item

    totals: dict[int, Decimal] = {}
    for enrollment in enrollments:
        items = items_by_enrollment.get(enrollment.id, {})
        weighted_sum = Decimal("0.00")
        for component in components:
            grade_item = items.get(component.id)
            if grade_item is not None and grade_item.score_percent is not None:
                weighted_sum += component.weight_percent * grade_item.score_percent
        totals[enrollment.id] = weighted_sum / Decimal("100")
    return totals


# ---------------------------------------------------------------------------
# Finalization (one-step)
# ---------------------------------------------------------------------------


def finalize_semester(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    actor=None,
) -> StudentSemesterProgress:
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    sync_all_grade_items(enrollment, semester, actor=actor)
    result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
    progress, _created = StudentSemesterProgress.objects.update_or_create(
        diploma_enrollment=enrollment,
        semester=semester,
        defaults={
            "student": enrollment.student,
            "cohort": enrollment.cohort,
            "status": result.status,
            "calculated_score": result.total_percent,
            "calculated_at": timezone.now(),
        },
    )
    record_audit_event(
        actor=actor,
        action=ACTION_SEMESTER_FINALIZED,
        target_type="enrollment",
        target_id=str(enrollment.id),
        metadata={
            "semester_id": semester.id,
            "status": result.status,
            "total_percent": str(result.total_percent),
        },
    )
    return progress


# ---------------------------------------------------------------------------
# Exact Semester Evaluation (P2-06): one pure calculator + immutable finalize
# ---------------------------------------------------------------------------


def _attendance_evaluation_percent(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    required_sessions: int | None = None,
    summary: dict | None = None,
) -> Decimal | None:
    """Attendance percent for the evaluation.

    With ``required_sessions`` > 0 the percent is recorded attendances out of
    the required number (e.g. 7 of 10 ⇒ 70%), capped at 100. Otherwise it
    falls back to attended / ended-closed sessions. Returns ``None`` when no
    countable session exists, so the component is not applicable. Accepts a
    precomputed ``summary`` so cohort loops can use the batch attendance path.
    """
    return get_attendance_evaluation_percent(
        enrollment, semester, required_sessions, summary=summary,
    )


def _homework_best_scores(
    enrollment: DiplomaEnrollment, semester: Semester,
) -> tuple[Decimal, Decimal] | None:
    """Aggregate each homework assessment's best graded attempt (set-wise).

    Shared by the grade-item sync and the Semester Evaluation calculator so
    both always collect and display the same Homework component value.
    """
    assessments = list(
        Assessment.objects.filter(
            semester=semester,
            cohort=enrollment.cohort,
            assessment_type=ASSESSMENT_TYPE_HOMEWORK,
            is_active=True,
        )
    )
    if not assessments:
        return None
    best_by_assessment: dict[int, AssessmentAttempt] = {}
    for attempt in AssessmentAttempt.objects.filter(
        assessment__in=assessments,
        student=enrollment.student.user,
        status__in=[ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED],
        score_points__isnull=False,
    ).order_by("assessment_id", "-score_percent"):
        best_by_assessment.setdefault(attempt.assessment_id, attempt)
    total_earned = Decimal("0.00")
    total_max = Decimal("0.00")
    for assessment in assessments:
        best = best_by_assessment.get(assessment.id)
        if best is not None and best.score_points is not None and assessment.total_points:
            total_earned += best.score_points
            total_max += assessment.total_points
    return total_earned, total_max


def _homework_evaluation_percent(enrollment: DiplomaEnrollment, semester: Semester) -> Decimal | None:
    aggregate = _homework_best_scores(enrollment, semester)
    if aggregate is None or aggregate[1] <= 0:
        return None
    return (aggregate[0] / aggregate[1] * Decimal("100")).quantize(Decimal("0.01"))


def _semester_exam_evaluation_percent(enrollment: DiplomaEnrollment, semester: Semester) -> Decimal | None:
    assessments = Assessment.objects.filter(
        semester=semester,
        cohort=enrollment.cohort,
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
    )
    best = (
        AssessmentAttempt.objects.filter(
            assessment__in=assessments,
            student=enrollment.student.user,
            status__in=[ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED],
            score_percent__isnull=False,
        )
        .order_by("-score_percent")
        .first()
    )
    return best.score_percent if best is not None else None


def evaluate_semester_evaluation(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    attendance_summary: dict | None = None,
) -> dict:
    """Pure calculator shared by preview and finalization (P2-06).

    PASS requires every applicable component minimum AND the weighted overall
    minimum. A component with no configured source (e.g. no Homework
    assessments were created for the cohort/semester) is NOT applicable: it
    produces no blocked reason and its weight is excluded from the overall
    average — admins are only accountable for what they created. Returns a
    domain presentation projection with explicit missing inputs; never writes.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    config = SemesterEvaluationConfig.objects.filter(
        cohort=enrollment.cohort, semester=semester,
    ).first()
    if config is None:
        return {
            "config": None, "blocked": True,
            "blocked_reasons": [_("No evaluation configuration for this cohort/semester.")],
            "parts": [], "overall_percent": None, "overall_min": None, "passed": False,
        }
    if config.status != EVALUATION_CONFIG_STATUS_ACTIVE:
        return {
            "config": config, "blocked": True,
            "blocked_reasons": [_("Evaluation configuration is incomplete and requires remediation.")],
            "parts": [], "overall_percent": None, "overall_min": None, "passed": False,
        }
    homework_applicable = Assessment.objects.filter(
        semester=semester,
        cohort=enrollment.cohort,
        assessment_type=ASSESSMENT_TYPE_HOMEWORK,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
    ).exists()
    exam_applicable = Assessment.objects.filter(
        semester=semester,
        cohort=enrollment.cohort,
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
    ).exists()
    attendance_percent = _attendance_evaluation_percent(
        enrollment, semester, config.attendance_required_sessions,
        summary=attendance_summary,
    )
    parts = [
        {
            "key": "attendance",
            "label": _("Attendance"),
            "percent": attendance_percent,
            "weight": config.attendance_weight_percent,
            "min": config.attendance_min_percent,
            "applicable": attendance_percent is not None,
        },
        {
            "key": "homework",
            "label": _("Homework"),
            "percent": _homework_evaluation_percent(enrollment, semester),
            "weight": config.homework_weight_percent,
            "min": config.homework_min_percent,
            "applicable": homework_applicable,
        },
        {
            "key": "semester_exam",
            "label": _("Semester Exam"),
            "percent": _semester_exam_evaluation_percent(enrollment, semester),
            "weight": config.semester_exam_weight_percent,
            "min": config.semester_exam_min_percent,
            "applicable": exam_applicable,
        },
    ]
    for part in parts:
        part["passed"] = (
            part["applicable"]
            and part["percent"] is not None
            and part["percent"] >= part["min"]
        )
    blocked_reasons = []
    for part in parts:
        if not part["applicable"]:
            continue
        if part["percent"] is None:
            blocked_reasons.append(_("%(label)s has no graded result yet.") % {"label": part["label"]})
        elif part["percent"] < part["min"]:
            blocked_reasons.append(
                _("%(label)s is below its minimum of %(min)s%%.") % {"label": part["label"], "min": part["min"]}
            )
    graded_parts = [part for part in parts if part["applicable"] and part["percent"] is not None]
    overall = None
    weight_total = sum(part["weight"] for part in graded_parts)
    if graded_parts and weight_total > 0:
        overall = (
            sum(part["percent"] * part["weight"] for part in graded_parts) / weight_total
        ).quantize(Decimal("0.01"))
    passed = (
        overall is not None
        and not blocked_reasons
        and overall >= config.overall_min_percent
    )
    return {
        "config": config,
        "blocked": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
        "parts": parts,
        "overall_percent": overall,
        "overall_min": config.overall_min_percent,
        "passed": passed,
    }


def finalize_semester_evaluation(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    attendance_summary: dict | None = None,
) -> SemesterEvaluation:
    """Finalize the exact Semester Evaluation once, immutably (P2-06).

    Idempotent: an existing evaluation for the enrollment+semester is returned
    and never rewritten. The slow grade-item provenance sync runs outside the
    evaluation lock; the locked step recalculates from live sources and creates
    one immutable snapshot.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    # Provenance sync outside the evaluation lock.
    sync_all_grade_items(enrollment, semester, actor=actor, attendance_summary=attendance_summary)
    with transaction.atomic():
        existing = SemesterEvaluation.objects.select_for_update().filter(
            diploma_enrollment=enrollment, semester=semester,
        ).first()
        if existing is not None:
            return existing
        calc = evaluate_semester_evaluation(
            enrollment, semester, attendance_summary=attendance_summary,
        )
        if calc["blocked"] or calc["overall_percent"] is None:
            reasons = "; ".join(calc["blocked_reasons"]) or _("missing inputs")
            raise ValueError(_("Semester evaluation cannot be finalized: %(reasons)s.") % {"reasons": reasons})
        config = calc["config"]
        evaluation = SemesterEvaluation.objects.create(
            diploma_enrollment=enrollment,
            student=enrollment.student,
            cohort=enrollment.cohort,
            semester=semester,
            result=SEMESTER_EVALUATION_PASS if calc["passed"] else SEMESTER_EVALUATION_FAIL,
            overall_percent=calc["overall_percent"],
            attendance_percent=calc["parts"][0]["percent"],
            homework_percent=calc["parts"][1]["percent"],
            semester_exam_percent=calc["parts"][2]["percent"],
            config_snapshot={
                "attendance_weight_percent": str(config.attendance_weight_percent),
                "homework_weight_percent": str(config.homework_weight_percent),
                "semester_exam_weight_percent": str(config.semester_exam_weight_percent),
                "attendance_min_percent": str(config.attendance_min_percent),
                "homework_min_percent": str(config.homework_min_percent),
                "semester_exam_min_percent": str(config.semester_exam_min_percent),
                "overall_min_percent": str(config.overall_min_percent),
            },
            source_snapshot={
                "attendance_percent": str(calc["parts"][0]["percent"]),
                "homework_percent": str(calc["parts"][1]["percent"]),
                "semester_exam_percent": str(calc["parts"][2]["percent"]),
            },
            finalized_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_SEMESTER_FINALIZED,
            target_type="enrollment",
            target_id=str(enrollment.id),
            metadata={
                "semester_id": semester.id,
                "evaluation_id": evaluation.id,
                "result": evaluation.result,
                "overall_percent": str(evaluation.overall_percent),
            },
        )
    return evaluation


# ---------------------------------------------------------------------------
# Final progression & Interview (P2-07) — canonical state machine
# ---------------------------------------------------------------------------


def get_semester_progression_rows(cohort: Cohort, semester: Semester, enrollments):
    """Set-wise projection of one cohort/semester progression table (P4-01).

    Loads the immutable evaluations, final outcomes, interviews, and one
    batch attendance summary in a few queries and returns one row per
    enrollment with the canonical calculated result, final decision,
    Interview state, and a live preview for not-yet-finalized enrollments.
    No per-enrollment/component loops.
    """
    enrollment_ids = [enrollment.id for enrollment in enrollments]
    evaluations = {
        evaluation.diploma_enrollment_id: evaluation
        for evaluation in SemesterEvaluation.objects.filter(
            diploma_enrollment_id__in=enrollment_ids, semester=semester,
        )
    }
    evaluation_ids = [evaluation.id for evaluation in evaluations.values()]
    outcome_rows = (
        FinalProgressionOutcome.objects.filter(evaluation_id__in=evaluation_ids)
        .select_related("interview")
    )
    outcomes = {outcome.evaluation_id: outcome for outcome in outcome_rows}

    # The evaluation config is cohort-wide: when it is absent or incomplete the
    # preview is the same blocked projection for every not-yet-finalized
    # enrollment, so it is computed once and reused instead of once per row.
    config = SemesterEvaluationConfig.objects.filter(
        cohort=cohort, semester=semester,
    ).first()
    config_blocked = config is None or config.status != EVALUATION_CONFIG_STATUS_ACTIVE
    blocked_preview = None
    if config_blocked and enrollments:
        try:
            blocked_preview = evaluate_semester_evaluation(enrollments[0], semester)
        except ValueError:
            blocked_preview = None
    needs_previews = not config_blocked and any(
        enrollment.id not in evaluations for enrollment in enrollments
    )
    attendance_summaries = (
        get_attendance_summaries(enrollments, semester) if needs_previews else {}
    )
    rows = []
    for enrollment in enrollments:
        evaluation = evaluations.get(enrollment.id)
        outcome = outcomes.get(evaluation.id) if evaluation else None
        interview = outcome.interview if outcome and hasattr(outcome, "interview") else None
        row = {
            "enrollment": enrollment,
            "student": enrollment.student,
            "evaluation": evaluation,
            "outcome": outcome,
            "interview": interview,
            "preview": None,
            "components": None,
        }
        if evaluation is not None:
            row["components"] = {
                "attendance": evaluation.attendance_percent,
                "homework": evaluation.homework_percent,
                "semester_exam": evaluation.semester_exam_percent,
            }
        elif config_blocked:
            row["preview"] = blocked_preview
            if blocked_preview is not None:
                row["components"] = {
                    part["key"]: part["percent"] for part in blocked_preview.get("parts", [])
                }
        else:
            try:
                preview = evaluate_semester_evaluation(
                    enrollment, semester,
                    attendance_summary=attendance_summaries.get(enrollment.id),
                )
                row["preview"] = preview
                if preview is not None:
                    row["components"] = {
                        part["key"]: part["percent"] for part in preview.get("parts", [])
                    }
            except ValueError:
                row["preview"] = None
        rows.append(row)
    return rows


def _get_or_create_progression_outcome(evaluation: SemesterEvaluation) -> FinalProgressionOutcome:
    outcome, _created = FinalProgressionOutcome.objects.select_for_update().get_or_create(
        evaluation=evaluation,
        defaults={
            "diploma_enrollment": evaluation.diploma_enrollment,
            "student": evaluation.student,
            "semester": evaluation.semester,
            "calculated_result": evaluation.result,
            "status": FINAL_PROGRESSION_STATUS_PENDING,
        },
    )
    return outcome


@transaction.atomic
def resolve_final_progression(
    evaluation: SemesterEvaluation,
    *,
    actor=None,
    final_result: str,
    reason: str = "",
) -> FinalProgressionOutcome:
    """Resolve the final Continue/Promote or Do Not Continue decision.

    Locked on the outcome; idempotent for the same result, immutable once
    resolved differently. Blocked while an interview is pending. An
    exceptional decision (different from the calculated result) requires an
    explicit reason. Never mutates the evaluation snapshot.
    """
    outcome = _get_or_create_progression_outcome(evaluation)
    if outcome.status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
        raise ValueError(_("Final decision is blocked until the interview is completed."))
    if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
        if outcome.final_result == final_result:
            return outcome
        raise ValueError(_("This progression outcome is already resolved."))
    if final_result not in (SEMESTER_EVALUATION_PASS, SEMESTER_EVALUATION_FAIL):
        raise ValueError(_("Invalid final progression result."))
    is_exceptional = final_result != outcome.calculated_result
    if is_exceptional and not reason.strip():
        raise ValueError(_("An explicit reason is required for an exceptional final decision."))
    now = timezone.now()
    outcome.final_result = final_result
    outcome.is_exceptional = is_exceptional
    outcome.reason = reason.strip()
    outcome.decided_by = actor
    outcome.decided_at = now
    outcome.status = FINAL_PROGRESSION_STATUS_RESOLVED
    outcome.save(update_fields=[
        "final_result", "is_exceptional", "reason", "decided_by", "decided_at", "status", "updated_at",
    ])
    decision_type = PROMOTION_DECISION_CONFIRM_PASS if final_result == SEMESTER_EVALUATION_PASS else PROMOTION_DECISION_DEMOTE
    PromotionDecision.objects.create(
        student=evaluation.student,
        diploma_enrollment=evaluation.diploma_enrollment,
        from_semester=evaluation.semester,
        decision_type=decision_type,
        automatic=False,
        reason=reason.strip() or ("Final pass." if final_result == SEMESTER_EVALUATION_PASS else "Final fail."),
        score_snapshot=evaluation.overall_percent,
        decided_by=actor,
        decided_at=now,
        metadata={
            "evaluation_id": evaluation.id,
            "calculated_result": outcome.calculated_result,
            "final_result": final_result,
            "is_exceptional": is_exceptional,
            "overall_percent": str(evaluation.overall_percent),
        },
    )
    record_audit_event(
        actor=actor,
        action=ACTION_SEMESTER_FINALIZED,
        target_type="enrollment",
        target_id=str(evaluation.diploma_enrollment_id),
        metadata={
            "semester_id": evaluation.semester_id,
            "outcome_id": outcome.id,
            "calculated_result": outcome.calculated_result,
            "final_result": final_result,
            "is_exceptional": is_exceptional,
            "reason": reason.strip(),
        },
    )
    return outcome


@transaction.atomic
def request_final_interview(
    evaluation: SemesterEvaluation,
    *,
    actor=None,
    notes: str = "",
) -> SemesterInterview:
    """Request the post-evaluation interview (PASS or FAIL both allowed).

    Sets the outcome to ``interview_pending`` which blocks all final
    resolution until the interview is completed. Idempotent when already
    requested; a completed interview cannot be re-requested.
    """
    outcome = _get_or_create_progression_outcome(evaluation)
    if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
        raise ValueError(_("Progression is already resolved; an interview cannot be requested."))
    interview = SemesterInterview.objects.select_for_update().filter(outcome=outcome).first()
    if interview is not None:
        if interview.status == INTERVIEW_STATUS_REQUESTED:
            return interview
        raise ValueError(_("An interview for this semester has already been completed."))
    interview = SemesterInterview.objects.create(
        outcome=outcome,
        status=INTERVIEW_STATUS_REQUESTED,
        details=notes,
        requested_by=actor,
    )
    outcome.status = FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING
    outcome.save(update_fields=["status", "updated_at"])
    PromotionDecision.objects.create(
        student=evaluation.student,
        diploma_enrollment=evaluation.diploma_enrollment,
        from_semester=evaluation.semester,
        decision_type=PROMOTION_DECISION_REQUEST_INTERVIEW,
        automatic=False,
        reason=notes or "Interview requested.",
        decided_by=actor,
        metadata={"evaluation_id": evaluation.id, "interview_id": interview.id},
    )
    return interview


@transaction.atomic
def complete_final_interview(
    evaluation: SemesterEvaluation,
    *,
    actor=None,
    decision: str,
    details: str = "",
) -> SemesterInterview:
    """Complete the pending interview; resolution becomes allowed.

    Records the structured interview outcome. The final Continue/Promote or
    Do Not Continue decision is still a separate explicit Admin action.
    """
    outcome = _get_or_create_progression_outcome(evaluation)
    interview = SemesterInterview.objects.select_for_update().filter(outcome=outcome).first()
    if interview is None:
        raise ValueError(_("No interview has been requested for this semester."))
    if interview.status != INTERVIEW_STATUS_REQUESTED:
        raise ValueError(_("This interview is already completed."))
    if decision not in (INTERVIEW_DECISION_PASS, INTERVIEW_DECISION_FAIL):
        raise ValueError(_("Invalid interview outcome."))
    now = timezone.now()
    interview.outcome_result = decision
    interview.details = details or interview.details
    interview.status = INTERVIEW_STATUS_COMPLETED
    interview.completed_by = actor
    interview.completed_at = now
    interview.save(update_fields=["outcome_result", "details", "status", "completed_by", "completed_at", "updated_at"])
    outcome.status = FINAL_PROGRESSION_STATUS_PENDING
    outcome.save(update_fields=["status", "updated_at"])
    decision_type = (
        PROMOTION_DECISION_INTERVIEW_PASS
        if decision == INTERVIEW_DECISION_PASS
        else PROMOTION_DECISION_INTERVIEW_FAIL
    )
    PromotionDecision.objects.create(
        student=evaluation.student,
        diploma_enrollment=evaluation.diploma_enrollment,
        from_semester=evaluation.semester,
        decision_type=decision_type,
        automatic=False,
        reason=details or "Interview completed.",
        decided_by=actor,
        metadata={"evaluation_id": evaluation.id, "interview_id": interview.id, "interview_outcome": decision},
    )
    return interview


# Promotion decision types treated as the promotion transition (AUTO/MANUAL).
# Used to detect duplicate/conflicting promotion decisions in the canonical
# promotion service. CONFIRM_PASS / DEMOTE belong to the final-decision
# workflow (resolve_final_progression) and are intentionally excluded here.
PROMOTION_DECISION_PROMOTE_TYPES = (
    PROMOTION_DECISION_AUTO_PROMOTE,
    PROMOTION_DECISION_MANUAL_PROMOTE,
)


@transaction.atomic
def promote_semester(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    reason: str = "",
    automatic: bool = False,
) -> PromotionDecision:
    """Canonical promotion state machine (BUG-019/BUG-020).

    Manual, automatic, bulk, and individual promotion entry points all
    converge here. The pass decision is the canonical synchronized evaluation:
    the finalized, immutable ``SemesterEvaluation`` created by
    ``finalize_semester_evaluation`` from the exact two-level rule
    (component minimums AND weighted overall). Promotion is only eligible for
    a calculated PASS. The service validates the current promotion state (no
    duplicate/conflicting decisions, no pending interview, not locked) and
    creates exactly one ``PromotionDecision`` row before advancing the
    student's semester progress.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    evaluation = SemesterEvaluation.objects.select_for_update().filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        raise ValueError(_("Finalize the semester evaluation before promotion."))
    if evaluation.result != SEMESTER_EVALUATION_PASS:
        raise ValueError(_("Cannot promote: the calculated semester evaluation is FAIL."))

    outcome = FinalProgressionOutcome.objects.select_for_update().filter(
        evaluation=evaluation,
    ).first()
    if outcome is not None:
        if outcome.status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
            raise ValueError(_("Promotion is blocked until the interview is completed."))
        if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
            raise ValueError(_("This progression outcome is already resolved."))

    if PromotionDecision.objects.filter(
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type__in=PROMOTION_DECISION_PROMOTE_TYPES,
    ).exists():
        raise ValueError(_("A promotion decision already exists for this semester."))

    progress = StudentSemesterProgress.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if progress is not None and progress.status == SEMESTER_PROGRESS_LOCKED:
        raise ValueError(_("Student progress is locked."))

    now = timezone.now()
    previous_status = progress.status if progress is not None else SEMESTER_PROGRESS_NOT_STARTED
    next_semester = (
        Semester.objects.filter(
            diploma=enrollment.cohort.diploma, order=semester.order + 1,
        ).first()
    )
    decision_type = (
        PROMOTION_DECISION_AUTO_PROMOTE if automatic else PROMOTION_DECISION_MANUAL_PROMOTE
    )
    decision = PromotionDecision.objects.create(
        student=enrollment.student,
        diploma_enrollment=enrollment,
        from_semester=semester,
        to_semester=next_semester,
        decision_type=decision_type,
        previous_status=previous_status,
        new_status=SEMESTER_PROGRESS_PROMOTED,
        automatic=automatic,
        reason=reason or (
            _("Auto-promoted based on the calculated semester evaluation.")
            if automatic
            else "Manual promotion."
        ),
        score_snapshot=evaluation.overall_percent,
        decided_by=actor,
        decided_at=now,
        metadata={
            "evaluation_id": evaluation.id,
            "calculated_result": evaluation.result,
            "overall_percent": str(evaluation.overall_percent),
        },
    )
    # The promotion is also the authoritative final Continue/Promote decision:
    # resolve the one-to-one FinalProgressionOutcome so the progression rows
    # keep showing the same final decision for every promotion entry point.
    resolved_outcome = _get_or_create_progression_outcome(evaluation)
    if resolved_outcome.status != FINAL_PROGRESSION_STATUS_RESOLVED:
        resolved_outcome.final_result = SEMESTER_EVALUATION_PASS
        resolved_outcome.is_exceptional = False
        resolved_outcome.reason = reason.strip()
        resolved_outcome.decided_by = actor
        resolved_outcome.decided_at = now
        resolved_outcome.status = FINAL_PROGRESSION_STATUS_RESOLVED
        resolved_outcome.save(update_fields=[
            "final_result", "is_exceptional", "reason",
            "decided_by", "decided_at", "status", "updated_at",
        ])
    StudentSemesterProgress.objects.update_or_create(
        diploma_enrollment=enrollment,
        semester=semester,
        defaults={
            "student": enrollment.student,
            "cohort": enrollment.cohort,
            "status": SEMESTER_PROGRESS_PROMOTED,
            "promoted_at": now,
            "decided_by": actor,
            "decision_reason": reason,
        },
    )
    if next_semester is not None:
        StudentSemesterProgress.objects.get_or_create(
            diploma_enrollment=enrollment,
            semester=next_semester,
            defaults={
                "student": enrollment.student,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_IN_PROGRESS,
            },
        )
    return decision


def auto_promote_students(
    cohort: Cohort | None = None,
    diploma: Diploma | None = None,
    *,
    dry_run: bool = False,
) -> list[dict]:
    results = []
    rules_qs = DiplomaProgressionRule.objects.filter(is_active=True)
    if diploma:
        rules_qs = rules_qs.filter(diploma=diploma)
    rules = list(rules_qs.select_related("diploma"))

    for rule in rules:
        if not rule.auto_promote_enabled:
            continue
        enrollments = DiplomaEnrollment.objects.filter(
            cohort__diploma=rule.diploma,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).select_related("student", "cohort")
        if cohort:
            enrollments = enrollments.filter(cohort=cohort)

        for enrollment in enrollments:
            result = _try_auto_promote(rule, enrollment, dry_run=dry_run)
            results.append(result)

    return results


def _try_auto_promote(
    rule: DiplomaProgressionRule,
    enrollment: DiplomaEnrollment,
    *,
    dry_run: bool = False,
) -> dict:
    """Per-enrollment automatic promotion (BUG-019): consumes the canonical
    synchronized evaluation result (finalized ``SemesterEvaluation``) and
    delegates the promotion transition to the canonical ``promote_semester``.
    """
    student = enrollment.student
    semesters = list(Semester.objects.filter(diploma=rule.diploma).order_by("order"))
    result = {
        "student_id": student.id,
        "enrollment_id": enrollment.id,
        "promoted": False,
        "from_semester": None,
        "to_semester": None,
        "reason": "",
    }

    current_progress = StudentSemesterProgress.objects.filter(
        diploma_enrollment=enrollment,
    ).select_related("semester").order_by("semester__order").last()

    if current_progress and current_progress.status == SEMESTER_PROGRESS_LOCKED:
        result["reason"] = _("Locked")
        return result

    current_semester_idx = 0
    if current_progress:
        for i, sem in enumerate(semesters):
            if sem.id == current_progress.semester_id:
                current_semester_idx = i
                break
        current_semester_idx += 1

    if current_semester_idx >= len(semesters):
        result["reason"] = _("All semesters completed")
        return result

    current_semester = semesters[current_semester_idx]
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=current_semester,
    ).first()
    if evaluation is None or evaluation.result != SEMESTER_EVALUATION_PASS:
        result["reason"] = _("No passing evaluation to promote.")
        return result

    if dry_run:
        result["promoted"] = True
        result["from_semester"] = current_semester.id
        result["to_semester"] = semesters[current_semester_idx + 1].id if current_semester_idx + 1 < len(semesters) else None
        result["reason"] = _("Would promote")
        return result

    try:
        promote_semester(
            enrollment, current_semester,
            actor=None, reason=_("Auto-promoted based on the calculated semester evaluation."),
            automatic=True,
        )
    except ValueError as exc:
        result["reason"] = str(exc)
        return result

    next_semester = semesters[current_semester_idx + 1] if current_semester_idx + 1 < len(semesters) else None
    result["promoted"] = True
    result["from_semester"] = current_semester.id
    result["to_semester"] = next_semester.id if next_semester else None
    result["reason"] = _("Promoted")
    return result


# ---------------------------------------------------------------------------
# Manual promotion
# ---------------------------------------------------------------------------


def manual_promote(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    target_semester: Semester,
    *,
    actor=None,
    reason: str = "",
) -> StudentSemesterProgress:
    # BUG-020: the manual promotion entry point converges on the canonical
    # promotion state machine (promote_semester). The canonical pass decision
    # is the finalized SemesterEvaluation two-level rule; an exceptional
    # decision (calculated FAIL → promote) belongs to the interview/final
    # decision workflow (resolve_final_progression), not this transition.
    semesters = list(Semester.objects.filter(diploma=enrollment.cohort.diploma).order_by("order"))
    if not semesters:
        raise ValueError(_("Diploma has no semesters."))

    if target_semester.id not in [s.id for s in semesters]:
        raise ValueError(_("Target semester does not belong to the diploma."))

    # Determine the student's current highest progress semester
    latest_progress = (
        StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
        .select_related("semester")
        .order_by("-semester__order")
        .first()
    )

    if latest_progress is None:
        # No progress yet — must promote from the first semester
        expected_semester = semesters[0]
    else:
        expected_semester = latest_progress.semester

    if target_semester.id != expected_semester.id:
        raise ValueError(
            _("Can only promote from the current semester: {semester_title}").format(
                semester_title=expected_semester.title,
            )
        )

    promote_semester(
        enrollment, target_semester,
        actor=actor, reason=reason, automatic=False,
    )
    return StudentSemesterProgress.objects.get(
        diploma_enrollment=enrollment, semester=target_semester,
    )


def promote_academic_year(
    student_profile,
    enrollment,
    from_semester,
    to_semester,
    *,
    actor=None,
    reason="",
):
    if not reason:
        raise ValueError(_("Reason is required for academic-year promotion."))
    if from_semester.diploma_id != enrollment.cohort.diploma_id or to_semester.diploma_id != from_semester.diploma_id:
        raise ValueError(_("Semesters must belong to the enrollment diploma."))
    # L7: only the next ordered semester of the SAME diploma may be the target,
    # mirroring manual_promote's forward-ordering rule.
    if to_semester.order != from_semester.order + 1:
        raise ValueError(_("Promotion must target the next semester in order."))
    with transaction.atomic():
        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=from_semester,
            to_semester=to_semester,
            decision_type=PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
            previous_status=SEMESTER_PROGRESS_PASSED,
            new_status=SEMESTER_PROGRESS_PROMOTED,
            automatic=False,
            reason=reason,
            decided_by=actor,
        )
        return StudentSemesterProgress.objects.get_or_create(
            diploma_enrollment=enrollment,
            semester=to_semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_IN_PROGRESS,
            },
        )[0]


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------


def demote_student(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    target_semester: Semester,
    *,
    actor=None,
    reason: str = "",
) -> StudentSemesterProgress:
    if not reason:
        raise ValueError(_("Reason is required for demotion."))

    semesters = list(Semester.objects.filter(diploma=enrollment.cohort.diploma).order_by("order"))
    if not semesters:
        raise ValueError(_("Diploma has no semesters."))

    if target_semester.id not in [s.id for s in semesters]:
        raise ValueError(_("Target semester does not belong to the diploma."))

    if target_semester.id == semesters[0].id:
        raise ValueError(_("Cannot demote below first semester."))

    # Must demote from the student's current highest-progress semester
    latest_progress = (
        StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
        .select_related("semester")
        .order_by("-semester__order")
        .first()
    )
    if latest_progress is None:
        raise ValueError(_("Student has no progress to demote from."))
    if target_semester.id != latest_progress.semester.id:
        raise ValueError(
            _("Can only demote from the current semester: {semester_title}").format(
                semester_title=latest_progress.semester.title,
            )
        )

    target_idx = next(i for i, s in enumerate(semesters) if s.id == target_semester.id)
    prev_semester = semesters[target_idx - 1]

    with transaction.atomic():
        existing = StudentSemesterProgress.objects.filter(
            diploma_enrollment=enrollment,
            semester=target_semester,
        ).first()
        previous_status = existing.status if existing else SEMESTER_PROGRESS_NOT_STARTED

        StudentSemesterProgress.objects.update_or_create(
            diploma_enrollment=enrollment,
            semester=target_semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_DEMOTED,
                "demoted_at": timezone.now(),
                "decided_by": actor,
                "decision_reason": reason,
            },
        )

        prev_progress, __ = StudentSemesterProgress.objects.get_or_create(
            diploma_enrollment=enrollment,
            semester=prev_semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_IN_PROGRESS,
            },
        )

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=target_semester,
            to_semester=prev_semester,
            decision_type=PROMOTION_DECISION_DEMOTE,
            previous_status=previous_status,
            new_status=SEMESTER_PROGRESS_DEMOTED,
            automatic=False,
            reason=reason,
            decided_by=actor,
        )

    return prev_progress


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------


def override_student_progress(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    new_status: str,
    *,
    actor=None,
    reason: str = "",
) -> StudentSemesterProgress:
    if not reason:
        raise ValueError(_("Reason is required for override."))
    if new_status not in {SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_FAILED}:
        raise ValueError(_("Legacy overrides are limited to passed or failed."))
    # L6: a mutation must never accept a semester from another diploma.
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))

    with transaction.atomic():
        locked_enrollment = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment.pk)
        progress, __ = StudentSemesterProgress.objects.select_for_update().get_or_create(
            diploma_enrollment=locked_enrollment,
            semester=semester,
            defaults={
                "student": student_profile,
                "cohort": locked_enrollment.cohort,
                "status": SEMESTER_PROGRESS_NOT_STARTED,
            },
        )
        previous_status = progress.status
        progress.status = new_status
        progress.decided_by = actor
        progress.decision_reason = reason
        progress.save()

        decision_type = PROMOTION_DECISION_OVERRIDE_PASS if new_status in (
            SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_PROMOTED,
        ) else PROMOTION_DECISION_OVERRIDE_FAIL

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=semester,
            to_semester=None,
            decision_type=decision_type,
            previous_status=previous_status,
            new_status=new_status,
            automatic=False,
            reason=reason,
            score_snapshot=progress.calculated_score,
            decided_by=actor,
        )

    return progress


# ---------------------------------------------------------------------------
# Lock and unlock
# ---------------------------------------------------------------------------


def lock_student(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester | None = None,
    *,
    actor=None,
    reason: str = "",
) -> list[StudentSemesterProgress]:
    if not reason:
        raise ValueError(_("Reason is required for lock."))

    progress_qs = StudentSemesterProgress.objects.filter(
        diploma_enrollment=enrollment,
    )
    if semester:
        progress_qs = progress_qs.filter(semester=semester)

    results = []
    with transaction.atomic():
        for prog in progress_qs:
            previous_status = prog.status
            prog.status = SEMESTER_PROGRESS_LOCKED
            prog.locked_at = timezone.now()
            prog.lock_reason = reason
            prog.decided_by = actor
            prog.decision_reason = reason
            prog.save()

            PromotionDecision.objects.create(
                student=student_profile,
                diploma_enrollment=enrollment,
                from_semester=prog.semester,
                decision_type=PROMOTION_DECISION_LOCK,
                previous_status=previous_status,
                new_status=SEMESTER_PROGRESS_LOCKED,
                automatic=False,
                reason=reason,
                decided_by=actor,
            )
            results.append(prog)

    return results


def unlock_student(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester | None = None,
    *,
    actor=None,
    reason: str = "",
) -> list[StudentSemesterProgress]:
    if not reason:
        raise ValueError(_("Reason is required for unlock."))

    progress_qs = StudentSemesterProgress.objects.filter(
        diploma_enrollment=enrollment,
        status=SEMESTER_PROGRESS_LOCKED,
    )
    if semester:
        progress_qs = progress_qs.filter(semester=semester)

    results = []
    with transaction.atomic():
        for prog in progress_qs:
            previous_status = prog.status
            prog.status = SEMESTER_PROGRESS_IN_PROGRESS
            prog.locked_at = None
            prog.lock_reason = ""
            prog.decided_by = actor
            prog.decision_reason = reason
            prog.save()

            PromotionDecision.objects.create(
                student=student_profile,
                diploma_enrollment=enrollment,
                from_semester=prog.semester,
                decision_type=PROMOTION_DECISION_UNLOCK,
                previous_status=previous_status,
                new_status=SEMESTER_PROGRESS_IN_PROGRESS,
                automatic=False,
                reason=reason,
                decided_by=actor,
            )
            results.append(prog)

    return results


# ---------------------------------------------------------------------------
# Retake handling
# ---------------------------------------------------------------------------


def assign_retake(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    reason: str = "",
) -> StudentSemesterProgress:
    if not reason:
        raise ValueError(_("Reason is required for assigning retake."))
    # L6: a mutation must never accept a semester from another diploma.
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))

    rule = DiplomaProgressionRule.objects.filter(
        diploma=enrollment.cohort.diploma,
        is_active=True,
    ).first()

    existing = StudentSemesterProgress.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if rule and rule.max_retake_count > 0 and existing and existing.retake_count >= rule.max_retake_count:
        raise ValueError(_("Maximum retake count ({max_retake}) reached.").format(max_retake=rule.max_retake_count))

    progress, __ = StudentSemesterProgress.objects.get_or_create(
        diploma_enrollment=enrollment,
        semester=semester,
        defaults={
            "student": student_profile,
            "cohort": enrollment.cohort,
            "status": SEMESTER_PROGRESS_RETAKE_REQUIRED,
        },
    )

    with transaction.atomic():
        previous_status = progress.status
        progress.status = SEMESTER_PROGRESS_RETAKE_REQUIRED
        progress.retake_required = True
        progress.retake_count += 1
        progress.decided_by = actor
        progress.decision_reason = reason
        progress.save()

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=semester,
            decision_type=PROMOTION_DECISION_RETAKE_ASSIGNED,
            previous_status=previous_status,
            new_status=SEMESTER_PROGRESS_RETAKE_REQUIRED,
            automatic=False,
            reason=reason,
            decided_by=actor,
        )

    return progress


def clear_retake(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    reason: str = "",
) -> StudentSemesterProgress:
    # L6: a mutation must never accept a semester from another diploma.
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    progress = StudentSemesterProgress.objects.get(
        diploma_enrollment=enrollment,
        semester=semester,
    )

    with transaction.atomic():
        previous_status = progress.status
        progress.retake_required = False
        progress.status = SEMESTER_PROGRESS_IN_PROGRESS
        progress.decided_by = actor
        progress.decision_reason = reason
        progress.save()

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=semester,
            decision_type=PROMOTION_DECISION_RETAKE_CLEARED,
            previous_status=previous_status,
            new_status=SEMESTER_PROGRESS_IN_PROGRESS,
            automatic=False,
            reason=reason,
            decided_by=actor,
        )

    return progress


# ---------------------------------------------------------------------------
# Final exam eligibility
# ---------------------------------------------------------------------------


def check_final_exam_eligibility(student_profile: StudentProfile, enrollment: DiplomaEnrollment) -> FinalExamEligibility:
    rule = DiplomaProgressionRule.objects.filter(
        diploma=enrollment.cohort.diploma,
        is_active=True,
    ).first()

    eligibility, created = FinalExamEligibility.objects.get_or_create(
        diploma_enrollment=enrollment,
        defaults={"student": student_profile},
    )

    semesters = list(Semester.objects.filter(diploma=enrollment.cohort.diploma).order_by("order"))
    progress_records = {
        p.semester_id: p
        for p in StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
    }

    blocked_reasons = []

    if not rule:
        blocked_reasons.append(str(_("No active progression rule.")))
    elif rule.require_final_exam:
        all_passed = all(
            progress_records.get(s.id) and progress_records[s.id].status in (
                SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_PROMOTED, SEMESTER_PROGRESS_COMPLETED,
            )
            for s in semesters
        )
        if not all_passed:
            blocked_reasons.append(str(_("Not all semesters have been passed.")))

    for prog in progress_records.values():
        if prog.status == SEMESTER_PROGRESS_LOCKED:
            blocked_reasons.append(str(_("Student progression is locked.")))
            break

    for prog in progress_records.values():
        if prog.retake_required:
            blocked_reasons.append(str(_("Student has unresolved retake requirements.")))
            break

    now = timezone.now()
    if blocked_reasons:
        eligibility.eligible = False
        eligibility.blocked_reason = "; ".join(blocked_reasons)
    else:
        eligibility.eligible = True
        eligibility.eligible_at = now
        eligibility.blocked_reason = ""

    eligibility.save()
    return eligibility


# ---------------------------------------------------------------------------
# Completion and graduation
# ---------------------------------------------------------------------------


def mark_requirements_met(student_profile: StudentProfile, enrollment: DiplomaEnrollment) -> StudentDiplomaCompletion:
    semesters = list(Semester.objects.filter(diploma=enrollment.cohort.diploma).order_by("order"))
    progress_records = {
        p.semester_id: p
        for p in StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment)
    }

    all_completed = all(
        progress_records.get(s.id) and progress_records[s.id].status in (
            SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_PROMOTED, SEMESTER_PROGRESS_COMPLETED,
        )
        for s in semesters
    )
    if not all_completed:
        raise ValueError(_("Not all semesters have been passed."))

    has_lock = any(
        p.status == SEMESTER_PROGRESS_LOCKED for p in progress_records.values()
    )
    if has_lock:
        raise ValueError(_("Student progression is locked."))

    has_retake = any(p.retake_required for p in progress_records.values())
    if has_retake:
        raise ValueError(_("Student has unresolved retake requirements."))

    completion, __ = StudentDiplomaCompletion.objects.get_or_create(
        diploma_enrollment=enrollment,
        defaults={"student": student_profile},
    )
    completion.status = DIPLOMA_COMPLETION_REQUIREMENTS_MET
    completion.save()
    return completion


def mark_completed(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor=None,
    notes: str = "",
) -> StudentDiplomaCompletion:
    completion = mark_requirements_met(student_profile, enrollment)

    with transaction.atomic():
        previous_status = completion.status
        completion.status = DIPLOMA_COMPLETION_COMPLETED
        completion.completed_at = timezone.now()
        completion.completed_by = actor
        completion.graduation_notes = notes
        completion.save()

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            decision_type=PROMOTION_DECISION_COMPLETE,
            previous_status=previous_status,
            new_status=DIPLOMA_COMPLETION_COMPLETED,
            automatic=False,
            reason=notes or "Manual completion.",
            decided_by=actor,
        )

    return completion


def mark_graduated(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor=None,
    notes: str = "",
) -> StudentDiplomaCompletion:
    completion = mark_requirements_met(student_profile, enrollment)

    with transaction.atomic():
        previous_status = completion.status
        completion.status = DIPLOMA_COMPLETION_GRADUATED
        completion.completed_at = timezone.now()
        completion.graduated_at = timezone.now()
        completion.completed_by = actor
        completion.graduation_notes = notes
        completion.save()

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            decision_type=PROMOTION_DECISION_GRADUATE,
            previous_status=previous_status,
            new_status=DIPLOMA_COMPLETION_GRADUATED,
            automatic=False,
            reason=notes or "Manual graduation.",
            decided_by=actor,
        )

    return completion


# ---------------------------------------------------------------------------
# Grade item sync
# ---------------------------------------------------------------------------


def _get_grade_component(diploma_enrollment, semester, component_type) -> SemesterGradeComponent | None:
    return SemesterGradeComponent.objects.filter(
        cohort=diploma_enrollment.cohort,
        semester=semester,
        component_type=component_type,
        active=True,
    ).first()


def sync_semester_exam_grade_item(diploma_enrollment, semester, actor=None) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_SEMESTER_EXAM)
    if component is None:
        return None

    assessments = Assessment.objects.filter(
        semester=semester,
        cohort=diploma_enrollment.cohort,
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        is_active=True,
    )
    best = (
        AssessmentAttempt.objects.filter(
            assessment__in=assessments,
            student=diploma_enrollment.student.user,
            status__in=[ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED],
            score_percent__isnull=False,
        )
        .order_by("-score_percent")
        .first()
    )
    if best is None:
        return None

    item, _created = StudentGradeItem.objects.update_or_create(
        component=component,
        diploma_enrollment=diploma_enrollment,
        defaults={
            "student": diploma_enrollment.student,
            "source_assessment_attempt": best,
            "max_score": best.assessment.total_points,
            "score_percent": best.score_percent,
        },
    )
    return item


def sync_attendance_grade_item(
    diploma_enrollment, semester, actor=None, summary: dict | None = None,
) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_ATTENDANCE)
    if component is None:
        return None

    if summary is None:
        summary = get_attendance_summary(diploma_enrollment, semester)
    if not summary["total_sessions"]:
        # No ended/closed session exists: attendance is not applicable and
        # must not be recorded as a 0% grade item.
        return None
    config = SemesterEvaluationConfig.objects.filter(
        cohort=diploma_enrollment.cohort, semester=semester,
    ).first()
    required = config.attendance_required_sessions if config else None
    attendance_percent = _attendance_evaluation_percent(
        diploma_enrollment, semester, required, summary=summary,
    )

    item, _created = StudentGradeItem.objects.update_or_create(
        component=component,
        diploma_enrollment=diploma_enrollment,
        defaults={
            "student": diploma_enrollment.student,
            "max_score": Decimal("100"),
            "score_percent": attendance_percent,
            "source_attendance_summary": summary,
        },
    )
    return item


def sync_homework_assessment_grade_item(diploma_enrollment, semester, actor=None) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_HOMEWORK_ASSESSMENT)
    if component is None:
        return None

    aggregate = _homework_best_scores(diploma_enrollment, semester)
    if aggregate is None:
        return None
    total_earned, total_max = aggregate
    score_percent = None
    if total_max > 0:
        score_percent = (total_earned / total_max * Decimal("100")).quantize(Decimal("0.01"))

    item, _created = StudentGradeItem.objects.update_or_create(
        component=component,
        diploma_enrollment=diploma_enrollment,
        defaults={
            "student": diploma_enrollment.student,
            "max_score": total_max,
            "score_percent": score_percent,
        },
    )
    return item


def sync_all_grade_items(
    diploma_enrollment, semester, actor=None, attendance_summary: dict | None = None,
) -> dict:
    if semester.diploma_id != diploma_enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    with transaction.atomic():
        return {
            "semester_exam": sync_semester_exam_grade_item(diploma_enrollment, semester, actor=actor),
            "attendance": sync_attendance_grade_item(
                diploma_enrollment, semester, actor=actor, summary=attendance_summary,
            ),
            "homework_assessment": sync_homework_assessment_grade_item(diploma_enrollment, semester, actor=actor),
        }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_student_progress_detail(student_profile: StudentProfile, cohort_ids: list[int] | None = None):
    base_filter = {"student": student_profile}
    if cohort_ids is not None:
        base_filter["diploma_enrollment__cohort_id__in"] = cohort_ids
    semesters = StudentSemesterProgress.objects.filter(
        **base_filter,
    ).select_related("semester", "diploma_enrollment__cohort__diploma").order_by("semester__order")

    base_filter2 = {"student": student_profile}
    if cohort_ids is not None:
        base_filter2["diploma_enrollment__cohort_id__in"] = cohort_ids
    completions = StudentDiplomaCompletion.objects.filter(**base_filter2)
    eligibilities = FinalExamEligibility.objects.filter(**base_filter2)
    decisions = PromotionDecision.objects.filter(student=student_profile).order_by("-decided_at")[:20]

    return {
        "semesters": semesters,
        "completions": completions,
        "eligibilities": eligibilities,
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Semester rejoin (Direct Rejoin, P4-02)
# ---------------------------------------------------------------------------


def get_immediate_next_cohort(cohort: Cohort) -> Cohort | None:
    """Deterministically return the immediately next active cohort (by start date, id)."""
    return (
        Cohort.objects.filter(diploma=cohort.diploma, is_active=True)
        .filter(
            Q(start_date__gt=cohort.start_date)
            | Q(start_date=cohort.start_date, id__gt=cohort.id)
        )
        .order_by("start_date", "id")
        .first()
    )


def _source_failed_outcome_resolved(enrollment: DiplomaEnrollment, semester: Semester) -> bool:
    """True when the source enrollment has a finalized FAILED outcome for the semester."""
    outcome = (
        FinalProgressionOutcome.objects.filter(
            evaluation__diploma_enrollment=enrollment,
            evaluation__semester=semester,
        ).first()
    )
    return bool(
        outcome is not None
        and outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED
        and outcome.final_result == SEMESTER_EVALUATION_FAIL
    )


def _target_semester_finalized(target_cohort: Cohort, semester: Semester) -> bool:
    """Authoritative cutoff (P4-02): Cohort B / Semester N completed/finalized.

    Uses the immutable SemesterEvaluation persistence: once any active
    enrollment in the target cohort has a finalized evaluation for the same
    semester, the target semester is considered finalized and Direct Rejoin
    expires. Cohort dates and is_active are never the cutoff.
    """
    return SemesterEvaluation.objects.filter(
        diploma_enrollment__cohort=target_cohort,
        semester=semester,
    ).exists()


def get_rejoin_eligibility_rows(*, offset=0, limit=25):
    """Paginated Rejoin Eligibility projection (P4-02).

    Only immediately-next/same-semester rows remain actionable until the
    target semester is finalized.
    """
    rows = []
    plans_qs = SemesterRejoinPlan.objects.filter(
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    ).select_related("student__user", "from_enrollment__cohort", "failed_semester", "target_cohort")
    plan_count = plans_qs.count()

    active_plan_for_outcome = SemesterRejoinPlan.objects.filter(
        from_enrollment_id=OuterRef("diploma_enrollment_id"),
        failed_semester_id=OuterRef("semester_id"),
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    )
    resolved_failed = (
        FinalProgressionOutcome.objects.filter(
            status=FINAL_PROGRESSION_STATUS_RESOLVED,
            final_result=SEMESTER_EVALUATION_FAIL,
        )
        .annotate(has_plan=Exists(active_plan_for_outcome))
        .filter(has_plan=False)
        .select_related(
            "evaluation__diploma_enrollment__cohort",
            "evaluation__semester",
            "student__user",
        )
        .order_by("-created_at")
    )
    failed_count = resolved_failed.count()

    plan_start = min(offset, plan_count)
    plan_stop = min(plan_count, offset + limit)
    for plan in plans_qs[plan_start:plan_stop]:
        rows.append({
            "plan": plan,
            "student": plan.student,
            "from_enrollment_id": plan.from_enrollment_id,
            "failed_semester_id": plan.failed_semester_id,
            "previous_cohort": plan.from_enrollment.cohort,
            "failed_semester": plan.failed_semester,
            "next_cohort": plan.target_cohort,
            "eligible": plan.status == REJOIN_STATUS_PLANNED
            and not _target_semester_finalized(plan.target_cohort, plan.failed_semester),
        })
    remaining = limit - len(rows)
    failed_start = max(0, offset - plan_count)
    for outcome in resolved_failed[failed_start:failed_start + remaining]:
        enrollment = outcome.evaluation.diploma_enrollment
        next_cohort = get_immediate_next_cohort(enrollment.cohort)
        rows.append({
            "plan": None,
            "student": outcome.student,
            "from_enrollment_id": enrollment.id,
            "failed_semester_id": outcome.evaluation.semester_id,
            "previous_cohort": enrollment.cohort,
            "failed_semester": outcome.evaluation.semester,
            "next_cohort": next_cohort,
            "eligible": next_cohort is not None
            and not _target_semester_finalized(next_cohort, outcome.evaluation.semester)
            and not DiplomaEnrollment.objects.filter(
                student=outcome.student, cohort=next_cohort, status=ENROLLMENT_STATUS_ACTIVE,
            ).exists(),
        })
    return rows, plan_count + failed_count


def create_rejoin_plan(
    student_profile: StudentProfile,
    from_enrollment: DiplomaEnrollment,
    failed_semester: Semester,
    *,
    actor,
    notes: str = "",
) -> SemesterRejoinPlan:
    """Create a Direct Rejoin plan for the immediately next cohort (P4-02).

    Exact eligibility: finalized FAILED outcome for Cohort A / Semester N;
    target is the immediately next cohort; same Semester N; target semester not
    yet finalized; no active/planned plan; no existing active target
    enrollment. Arbitrary cohort selection is rejected.
    """
    try:
        with transaction.atomic():
            locked_student = StudentProfile.objects.select_for_update().get(pk=student_profile.pk)
            locked_enrollment = DiplomaEnrollment.objects.select_for_update().select_related("cohort").get(
                pk=from_enrollment.pk,
            )
            if locked_enrollment.student_id != locked_student.id:
                raise ValueError(_("Enrollment does not belong to this student."))
            if failed_semester.diploma_id != locked_enrollment.cohort.diploma_id:
                raise ValueError(_("Failed semester must belong to the same diploma as the enrollment."))
            if not _source_failed_outcome_resolved(locked_enrollment, failed_semester):
                raise ValueError(_("Direct Rejoin requires a finalized failed outcome for the source semester."))
            target_cohort = get_immediate_next_cohort(locked_enrollment.cohort)
            if target_cohort is None:
                raise ValueError(_("There is no immediately next cohort for Direct Rejoin."))
            if _target_semester_finalized(target_cohort, failed_semester):
                raise ValueError(_("The target cohort has already finalized this semester; Direct Rejoin has expired."))
            if SemesterRejoinPlan.objects.filter(
                student=locked_student,
                status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
            ).exists():
                raise ValueError(_("A rejoin plan already exists for this student."))
            if DiplomaEnrollment.objects.filter(
                student=locked_student, cohort=target_cohort, status=ENROLLMENT_STATUS_ACTIVE,
            ).exists():
                raise ValueError(_("The student already has an active enrollment in the target cohort."))

            plan = SemesterRejoinPlan.objects.create(
                student=locked_student,
                from_enrollment=locked_enrollment,
                failed_semester=failed_semester,
                target_cohort=target_cohort,
                planned_by=actor,
                notes=notes,
            )
            PromotionDecision.objects.create(
                student=locked_student,
                diploma_enrollment=locked_enrollment,
                from_semester=failed_semester,
                decision_type=PROMOTION_DECISION_REJOIN_PLANNED,
                reason=notes or _("Rejoin plan created."),
                decided_by=actor,
            )
    except IntegrityError as exc:
        raise ValueError(_("A rejoin plan already exists for this student.")) from exc

    return plan


def activate_rejoin_plan(plan, *, actor) -> SemesterRejoinPlan:
    """Activate a planned Direct Rejoin (P4-02).

    Creates a fresh target enrollment starting at the failed semester, never
    copies progress/grades/attempts/attendance, revalidates the cutoff and
    invariants at commit, and preserves the source enrollment as immutable
    history.
    """
    if plan.status != REJOIN_STATUS_PLANNED:
        raise ValueError(_("Only planned rejoin plans can be activated."))
    if plan.from_enrollment.cohort_id == plan.target_cohort_id:
        raise ValueError(_("Source and target cohort are the same. Rejoin requires a different cohort."))

    with transaction.atomic():
        plan_ref = SemesterRejoinPlan.objects.select_for_update().get(pk=plan.pk)
        if plan_ref.status != REJOIN_STATUS_PLANNED:
            raise ValueError(_("This rejoin plan is no longer planned."))
        if not _source_failed_outcome_resolved(plan_ref.from_enrollment, plan_ref.failed_semester):
            raise ValueError(_("The source failed outcome is no longer authoritative."))
        if _target_semester_finalized(plan_ref.target_cohort, plan_ref.failed_semester):
            raise ValueError(_("The target cohort has finalized this semester; Direct Rejoin has expired."))
        if DiplomaEnrollment.objects.filter(
            student=plan_ref.student, cohort=plan_ref.target_cohort, status=ENROLLMENT_STATUS_ACTIVE,
        ).exists():
            raise ValueError(_("The student already has an active enrollment in the target cohort."))

        source = DiplomaEnrollment.objects.select_for_update().get(pk=plan_ref.from_enrollment_id)
        if source.status == ENROLLMENT_STATUS_ACTIVE:
            source.status = ENROLLMENT_STATUS_RESTRICTED
            source.save(update_fields=["status", "updated_at"])

        # Fresh enrollment starting at the failed semester; no progress copy.
        new_enrollment = DiplomaEnrollment.objects.create(
            student=plan_ref.student,
            cohort=plan_ref.target_cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
            entry_semester=plan_ref.failed_semester,
        )
        plan_ref.to_enrollment = new_enrollment
        plan_ref.status = REJOIN_STATUS_ACTIVE
        plan_ref.activated_at = timezone.now()
        plan_ref.save(update_fields=["to_enrollment", "status", "activated_at", "updated_at"])

        PromotionDecision.objects.create(
            student=plan_ref.student,
            diploma_enrollment=new_enrollment,
            from_semester=plan_ref.failed_semester,
            decision_type=PROMOTION_DECISION_REJOIN_ACTIVATED,
            reason=_("Rejoin plan activated at the failed semester."),
            decided_by=actor,
        )

    return plan_ref


def cancel_rejoin_plan(plan, *, actor, reason: str = "") -> SemesterRejoinPlan:
    with transaction.atomic():
        plan.status = REJOIN_STATUS_CANCELLED
        plan.save(update_fields=["status", "updated_at"])

        PromotionDecision.objects.create(
            student=plan.student,
            diploma_enrollment=plan.to_enrollment or plan.from_enrollment,
            from_semester=plan.failed_semester,
            decision_type=PROMOTION_DECISION_REJOIN_CANCELLED,
            reason=reason or _("Rejoin plan cancelled."),
            decided_by=actor,
        )

    return plan
