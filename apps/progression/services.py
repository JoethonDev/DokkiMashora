"""Progression services: score aggregation, semester evaluation, promotion, demotion, lock, retake, final exam, completion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q, Subquery, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_RESTRICTED
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_EXPIRED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    LEGACY_STATUS_ACTIVE,
)
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.attendance.constants import SESSION_STATUS_CANCELLED
from apps.attendance.models import AttendanceSession
from apps.attendance.services import (
    get_attendance_evaluation_percent,
    get_attendance_summaries,
    get_attendance_summary,
)
from apps.audit.constants import (
    ACTION_PROGRESSION_CONFIG_UPDATED,
    ACTION_PROGRESSION_INTERVIEW_COMPLETED,
    ACTION_PROGRESSION_INTERVIEW_REQUESTED,
    ACTION_PROGRESSION_REJOIN_ACTIVATED,
    ACTION_PROGRESSION_REJOIN_CANCELLED,
    ACTION_PROGRESSION_REJOIN_CREATED,
    ACTION_PROMOTION,
    ACTION_RULE_REEVALUATION,
    ACTION_SEMESTER_FINALIZED,
    ACTION_SEMESTER_REEVALUATED,
    SECTION_PROGRESSION,
)
from apps.audit.services import record_audit_event
from apps.core.scoring import compute_percent
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
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
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

logger = logging.getLogger(__name__)

# Promotion decisions that represent a real semester advancement. Re-evaluation
# supersedes these plus the linked fail/interview decisions instead of deleting
# history; active checks must ignore superseded rows.
PROMOTION_DECISION_PROMOTE_TYPES = (
    PROMOTION_DECISION_AUTO_PROMOTE,
    PROMOTION_DECISION_MANUAL_PROMOTE,
    PROMOTION_DECISION_OVERRIDE_PASS,
)
PROMOTION_DECISION_SUPERSEDED_TYPES = (
    *PROMOTION_DECISION_PROMOTE_TYPES,
    PROMOTION_DECISION_DEMOTE,
    PROMOTION_DECISION_OVERRIDE_FAIL,
    PROMOTION_DECISION_REQUEST_INTERVIEW,
    PROMOTION_DECISION_INTERVIEW_PASS,
    PROMOTION_DECISION_INTERVIEW_FAIL,
    PROMOTION_DECISION_CONFIRM_PASS,
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


@dataclass(frozen=True)
class EffectiveProgressionPolicy:
    diploma_id: int
    semester_id: int
    semester_pass_percent: Decimal
    require_all_required_assessments: bool
    allow_missing_optional_assessments: bool
    semester_pass_percent_source: str
    require_all_required_assessments_source: str
    allow_missing_optional_assessments_source: str


PROGRESSION_POLICY_SOURCE_SEMESTER_OVERRIDE = "semester_override"
PROGRESSION_POLICY_SOURCE_DIPLOMA_DEFAULT = "diploma_default"
PROGRESSION_POLICY_SOURCE_CODE_DEFAULT = "code_default"
DEFAULT_SEMESTER_PASS_PERCENT = Decimal("50.00")
DEFAULT_REQUIRE_ALL_REQUIRED_ASSESSMENTS = True
DEFAULT_ALLOW_MISSING_OPTIONAL_ASSESSMENTS = False
_UNSET_EVALUATION_CONFIG = object()


def _progression_audit_target_label(
    *,
    diploma_title: str = "",
    cohort_name: str = "",
    semester_title: str = "",
    student=None,
) -> str:
    """Build the canonical Diploma · Cohort · Semester · Student audit label."""
    parts = [diploma_title, cohort_name, semester_title]
    if student is not None:
        parts.append(str(getattr(student, "full_name", "") or ""))
    return " · ".join(str(part) for part in parts if part)


def _student_audit_subject(student) -> dict[str, str]:
    """Immutable student snapshot for audit rows (never an email/national ID)."""
    return {
        "subject_type": "student",
        "subject_id": str(student.pk),
        "subject_label": str(getattr(student, "full_name", "") or student.pk),
    }


def _progression_rule_audit_snapshot(rule) -> dict:
    """JSON-safe field snapshot for a diploma or semester progression rule."""
    if isinstance(rule, SemesterProgressionRule):
        fields = (
            "semester_id",
            "pass_percent",
            "require_all_required_assessments",
            "allow_missing_optional_assessments",
            "is_active",
        )
    else:
        fields = (
            "diploma_id",
            "semester_pass_percent",
            "require_all_required_assessments",
            "allow_missing_optional_assessments",
            "final_exam_pass_percent",
            "require_all_semesters_passed",
            "require_final_exam",
            "auto_promote_enabled",
            "allow_admin_override",
            "allow_retake",
            "max_retake_count",
            "is_active",
        )
    snapshot = {}
    for field in fields:
        value = getattr(rule, field)
        snapshot[field] = str(value) if isinstance(value, Decimal) else value
    return snapshot


def resolve_progression_policy(diploma: Diploma | int, semester: Semester) -> EffectiveProgressionPolicy:
    """Resolve semester overrides over diploma defaults and documented code defaults."""
    diploma_id = getattr(diploma, "pk", diploma)
    semester_id = getattr(semester, "pk", None)
    if diploma_id is None or semester_id is None or semester.diploma_id != diploma_id:
        raise ValueError(_("Semester does not belong to the specified diploma."))

    active_semester_rules = list(
        SemesterProgressionRule.objects.filter(semester_id=semester_id, is_active=True)
        .select_related("diploma_rule")
        .order_by("pk")
    )
    if any(rule.diploma_rule.diploma_id != diploma_id for rule in active_semester_rules):
        raise ValueError(_("Semester progression rule belongs to a different diploma."))

    diploma_rule = DiplomaProgressionRule.objects.filter(
        diploma_id=diploma_id,
        is_active=True,
    ).order_by("pk").first()
    if active_semester_rules and (
        diploma_rule is None
        or any(rule.diploma_rule_id != diploma_rule.pk for rule in active_semester_rules)
    ):
        raise ValueError(_("Active semester overrides must be attached to the active diploma progression rule."))
    semester_rule = next(
        (
            rule for rule in active_semester_rules
            if diploma_rule is not None and rule.diploma_rule_id == diploma_rule.pk
        ),
        None,
    )

    def resolve_value(field_name, override_value, code_default):
        if override_value is not None:
            return override_value, PROGRESSION_POLICY_SOURCE_SEMESTER_OVERRIDE
        if diploma_rule is not None:
            return getattr(diploma_rule, field_name), PROGRESSION_POLICY_SOURCE_DIPLOMA_DEFAULT
        return code_default, PROGRESSION_POLICY_SOURCE_CODE_DEFAULT

    pass_percent, pass_percent_source = resolve_value(
        "semester_pass_percent",
        semester_rule.pass_percent if semester_rule else None,
        DEFAULT_SEMESTER_PASS_PERCENT,
    )
    require_all, require_all_source = resolve_value(
        "require_all_required_assessments",
        semester_rule.require_all_required_assessments if semester_rule else None,
        DEFAULT_REQUIRE_ALL_REQUIRED_ASSESSMENTS,
    )
    allow_missing, allow_missing_source = resolve_value(
        "allow_missing_optional_assessments",
        semester_rule.allow_missing_optional_assessments if semester_rule else None,
        DEFAULT_ALLOW_MISSING_OPTIONAL_ASSESSMENTS,
    )
    return EffectiveProgressionPolicy(
        diploma_id=diploma_id,
        semester_id=semester_id,
        semester_pass_percent=pass_percent,
        require_all_required_assessments=require_all,
        allow_missing_optional_assessments=allow_missing,
        semester_pass_percent_source=pass_percent_source,
        require_all_required_assessments_source=require_all_source,
        allow_missing_optional_assessments_source=allow_missing_source,
    )


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


def get_semester_assessments(semester: Semester, cohort: Cohort):
    if cohort.diploma_id != semester.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))
    return Assessment.objects.filter(
        semester=semester,
        cohort=cohort,
        is_active=True,
        legacy_status=LEGACY_STATUS_ACTIVE,
        assessment_type__in=ASSESSMENT_TYPES_FOR_SCORING,
    )


def aggregate_semester_scores(
    student_profile: StudentProfile,
    semester: Semester,
    *,
    cohort: Cohort | None = None,
    progression_policy: EffectiveProgressionPolicy | None = None,
) -> SemesterScoreResult:
    if cohort is None:
        raise ValueError(_("A cohort is required to calculate semester assessment results."))
    if cohort.diploma_id != semester.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))
    assessments = list(get_semester_assessments(semester, cohort))
    if not assessments:
        return SemesterScoreResult(
            score_percent=Decimal("0.00"),
            missing_required=[],
            under_review_assessments=[],
            failed_assessments=[],
            passed=False,
            under_review=False,
        )
    progression_policy = progression_policy or resolve_progression_policy(semester.diploma_id, semester)
    if progression_policy.diploma_id != semester.diploma_id or progression_policy.semester_id != semester.pk:
        raise ValueError(_("Progression policy does not match the semester."))
    pass_pct = progression_policy.semester_pass_percent
    total_points = Decimal("0.00")
    earned_points = Decimal("0.00")
    missing_required = []
    under_review_list = []
    failed_list = []

    for assessment in assessments:
        latest_attempt = AssessmentAttempt.objects.filter(
            assessment=assessment,
            student=student_profile.user,
        ).exclude(
            status__in=[ATTEMPT_STATUS_CANCELLED],
        ).order_by("-attempt_number", "-created_at", "-pk").first()

        if latest_attempt and latest_attempt.status in (
            ATTEMPT_STATUS_IN_PROGRESS,
            ATTEMPT_STATUS_SUBMITTED,
            ATTEMPT_STATUS_UNDER_REVIEW,
        ):
            under_review_list.append(assessment)
        elif (
            latest_attempt
            and latest_attempt.status in (ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED)
            and latest_attempt.score_points is not None
            and latest_attempt.score_percent is not None
            and assessment.total_points > 0
        ):
            earned_points += latest_attempt.score_points
            total_points += assessment.total_points
            if latest_attempt.is_pass is False:
                failed_list.append(assessment)
        else:
            missing_required.append(assessment)

    score_pct = compute_percent(earned_points, total_points) or Decimal("0.00")
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

REEVALUATION_CHUNK_SIZE = 100


def save_progression_rule(rule, actor):
    """Persist a progression rule and re-evaluate affected semesters atomically.

    S2-19/S2-27: rule save and the bounded re-evaluation are one transaction
    with the real actor (never ``None``) so a failure rolls back the rule too.
    ``rule`` is a ``SemesterProgressionRule`` or ``DiplomaProgressionRule``.
    A diploma-level update re-evaluates active semesters whose pass threshold
    is inherited; explicit semester thresholds remain unaffected.
    """
    if actor is None:
        raise ValueError(_("An actor is required to save a progression rule."))
    with transaction.atomic():
        persisted = (
            type(rule).objects.filter(pk=rule.pk).first() if rule.pk else None
        )
        before = _progression_rule_audit_snapshot(persisted) if persisted else None
        rule.full_clean()
        rule.save()
        if isinstance(rule, SemesterProgressionRule):
            target_type = "semester_progression_rule"
            diploma_title = rule.semester.diploma.title
            target_label = _progression_audit_target_label(
                diploma_title=diploma_title,
                semester_title=rule.semester.title,
            )
            config_metadata = {
                "diploma_id": rule.semester.diploma_id,
                "diploma_title": diploma_title,
                "semester_id": rule.semester_id,
                "semester_title": rule.semester.title,
            }
            progression_policy = resolve_progression_policy(rule.semester.diploma_id, rule.semester)
            reevaluate_semester_for_rule_change(
                rule.semester,
                actor,
                progression_policy=progression_policy,
            )
        elif isinstance(rule, DiplomaProgressionRule):
            target_type = "diploma_progression_rule"
            target_label = _progression_audit_target_label(
                diploma_title=rule.diploma.title,
            )
            config_metadata = {
                "diploma_id": rule.diploma_id,
                "diploma_title": rule.diploma.title,
            }
            if rule.is_active:
                semesters = Semester.objects.filter(
                    diploma_id=rule.diploma_id,
                    is_active=True,
                ).order_by("order", "pk")
                for semester in semesters:
                    progression_policy = resolve_progression_policy(rule.diploma_id, semester)
                    if progression_policy.semester_pass_percent_source != PROGRESSION_POLICY_SOURCE_SEMESTER_OVERRIDE:
                        reevaluate_semester_for_rule_change(
                            semester,
                            actor,
                            progression_policy=progression_policy,
                        )
        record_audit_event(
            actor=actor,
            action=ACTION_PROGRESSION_CONFIG_UPDATED,
            target_type=target_type,
            target_id=str(rule.pk),
            section=SECTION_PROGRESSION,
            target_label=target_label,
            metadata={
                **config_metadata,
                "before": before,
                "after": _progression_rule_audit_snapshot(rule),
            },
        )
    return rule


def reevaluate_semester_for_rule_change(semester, actor=None, *, progression_policy=None):
    """Re-evaluate non-finalized progress rows for a semester in bounded chunks.

    Rows are updated with one ``bulk_update`` per chunk (S2-27); the immutable
    per-row audit events are written as required, inside the caller's
    transaction.
    """
    progression_policy = progression_policy or resolve_progression_policy(semester.diploma_id, semester)
    if progression_policy.diploma_id != semester.diploma_id or progression_policy.semester_id != semester.pk:
        raise ValueError(_("Progression policy does not match the semester."))

    non_finalized = StudentSemesterProgress.objects.filter(
        semester=semester,
        status__in=[
            SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION,
            SEMESTER_PROGRESS_FAILED,
            SEMESTER_PROGRESS_UNDER_REVIEW,
            SEMESTER_PROGRESS_IN_PROGRESS,
        ],
        diploma_enrollment__cohort__diploma_id=semester.diploma_id,
    ).select_related("diploma_enrollment", "student", "diploma_enrollment__cohort")

    diploma_title = semester.diploma.title
    stream = non_finalized.iterator(chunk_size=REEVALUATION_CHUNK_SIZE)
    while True:
        chunk = list(islice(stream, REEVALUATION_CHUNK_SIZE))
        if not chunk:
            break
        for progress in chunk:
            enrollment = progress.diploma_enrollment
            previous_status = progress.status
            result = evaluate_semester_components(
                enrollment,
                enrollment.cohort,
                semester,
                progression_policy=progression_policy,
            )
            progress.status = result.status
            progress.calculated_score = result.total_percent
            progress.calculated_at = timezone.now()
            record_audit_event(
                actor=actor,
                action=ACTION_RULE_REEVALUATION,
                target_type="enrollment",
                target_id=str(enrollment.id),
                section=SECTION_PROGRESSION,
                target_label=_progression_audit_target_label(
                    diploma_title=diploma_title,
                    cohort_name=enrollment.cohort.name,
                    semester_title=semester.title,
                    student=enrollment.student,
                ),
                metadata={
                    "diploma_id": semester.diploma_id,
                    "diploma_title": diploma_title,
                    "cohort_id": enrollment.cohort_id,
                    "cohort_name": enrollment.cohort.name,
                    "semester_id": semester.id,
                    "semester_title": semester.title,
                    "status_before": previous_status,
                    "status": result.status,
                    "total_percent": str(result.total_percent),
                    "reason": "rule_change",
                },
                **_student_audit_subject(enrollment.student),
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
    if semester.diploma_id != diploma_enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the diploma."))
    if not is_override:
        if diploma_enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            raise ValueError(_("Enrollment is not active."))

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

    result = aggregate_semester_scores(
        student_profile,
        semester,
        cohort=diploma_enrollment.cohort,
    )
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
    *,
    progression_policy: EffectiveProgressionPolicy | None = None,
) -> ComponentEvaluationResult:
    if diploma_enrollment.cohort_id != cohort.pk:
        raise ValueError(_("Enrollment does not belong to the specified cohort."))
    if semester.diploma_id != cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))
    progression_policy = progression_policy or resolve_progression_policy(cohort.diploma_id, semester)
    if progression_policy.diploma_id != cohort.diploma_id or progression_policy.semester_id != semester.pk:
        raise ValueError(_("Progression policy does not match the semester."))
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

    pass_threshold = progression_policy.semester_pass_percent

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
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=semester.diploma.title,
            cohort_name=enrollment.cohort.name,
            semester_title=semester.title,
            student=enrollment.student,
        ),
        metadata={
            "diploma_id": semester.diploma_id,
            "diploma_title": semester.diploma.title,
            "cohort_id": enrollment.cohort_id,
            "cohort_name": enrollment.cohort.name,
            "semester_id": semester.id,
            "semester_title": semester.title,
            "status": result.status,
            "total_percent": str(result.total_percent),
        },
        **_student_audit_subject(enrollment.student),
    )
    return progress


# ---------------------------------------------------------------------------
# Exact Semester Evaluation (P2-06): one pure calculator + immutable finalize
# ---------------------------------------------------------------------------


ASSESSMENT_PENDING_ATTEMPT_STATUSES = (
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
)
ASSESSMENT_MISSING_ATTEMPT_STATUSES = (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_EXPIRED,
)
ASSESSMENT_SCORED_ATTEMPT_STATUSES = (
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
)


def _load_semester_assessment_sources(
    cohort: Cohort,
    semester: Semester,
    student_ids: list[int],
) -> dict:
    """Load one scoped assessment catalog and the latest attempt per student/source."""
    if cohort.diploma_id != semester.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))
    assessments_by_type = {assessment_type: [] for assessment_type in ASSESSMENT_TYPES_FOR_SCORING}
    assessments = list(
        Assessment.objects.filter(
            semester=semester,
            cohort=cohort,
            assessment_type__in=ASSESSMENT_TYPES_FOR_SCORING,
            is_active=True,
            legacy_status=LEGACY_STATUS_ACTIVE,
        ).order_by("assessment_type", "pk")
    )
    for assessment in assessments:
        assessments_by_type[assessment.assessment_type].append(assessment)

    latest_attempts_by_student: dict[int, dict[int, AssessmentAttempt]] = {}
    assessment_ids = [assessment.pk for assessment in assessments]
    if assessment_ids and student_ids:
        # Rank attempts in PostgreSQL so memory scales with student/assessment
        # pairs, not with the full historical retake count.
        attempts = AssessmentAttempt.objects.filter(
            assessment_id__in=assessment_ids,
            student_id__in=student_ids,
        ).exclude(
            status=ATTEMPT_STATUS_CANCELLED,
        ).order_by().only(
            "id",
            "assessment_id",
            "student_id",
            "attempt_number",
            "created_at",
            "status",
            "exam_review_status",
            "score_points",
            "score_percent",
        ).annotate(
            latest_rank=Window(
                expression=RowNumber(),
                partition_by=[F("assessment_id"), F("student_id")],
                order_by=[
                    F("attempt_number").desc(),
                    F("created_at").desc(),
                    F("pk").desc(),
                ],
            ),
        ).filter(latest_rank=1).order_by()
        for attempt in attempts:
            latest_attempts_by_student.setdefault(attempt.student_id, {}).setdefault(
                attempt.assessment_id, attempt,
            )

    return {
        "cohort_id": cohort.pk,
        "semester_id": semester.pk,
        "assessments_by_type": assessments_by_type,
        "latest_attempts_by_student": latest_attempts_by_student,
    }


def _semester_assessment_component_inputs(
    enrollment: DiplomaEnrollment,
    assessment_type: str,
    assessment_sources: dict,
    progression_policy: EffectiveProgressionPolicy,
) -> dict:
    """Classify every active source as included, omitted, or blocking for this student."""
    if (
        progression_policy.diploma_id != enrollment.cohort.diploma_id
        or progression_policy.semester_id != assessment_sources["semester_id"]
        or enrollment.cohort_id != assessment_sources["cohort_id"]
    ):
        raise ValueError(_("Assessment sources or progression policy do not match the enrollment."))

    assessments = assessment_sources["assessments_by_type"].get(assessment_type, [])
    attempts_by_assessment = assessment_sources["latest_attempts_by_student"].get(
        enrollment.student.user_id,
        {},
    )
    scored_results = []
    blocked_reasons = []
    snapshot = []
    earned_points = Decimal("0.00")
    possible_points = Decimal("0.00")
    score_percentages = []

    for assessment in assessments:
        attempt = attempts_by_assessment.get(assessment.pk)
        record = {
            "assessment_id": assessment.pk,
            "assessment_title": assessment.title,
            "assessment_type": assessment.assessment_type,
            "required": assessment.is_required_for_semester_evaluation,
            "attempt_id": attempt.pk if attempt else None,
            "attempt_status": attempt.status if attempt else None,
            "exam_review_status": (
                attempt.exam_review_status
                if attempt and assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
                else None
            ),
            "possible_points": str(assessment.total_points),
            "score_points": str(attempt.score_points)
            if attempt and attempt.score_points is not None else None,
            "score_percent": str(attempt.score_percent)
            if attempt and attempt.score_percent is not None else None,
        }

        if attempt is None or attempt.status in ASSESSMENT_MISSING_ATTEMPT_STATUSES:
            allowed_to_omit = (
                not progression_policy.require_all_required_assessments
                if assessment.is_required_for_semester_evaluation
                else progression_policy.allow_missing_optional_assessments
            )
            if allowed_to_omit:
                record["outcome"] = "omitted"
            elif assessment.is_required_for_semester_evaluation:
                record["outcome"] = "blocked_missing"
                blocked_reasons.append(
                    _("Required assessment ‘%(title)s’ is missing a submitted result.")
                    % {"title": assessment.title}
                )
            else:
                record["outcome"] = "blocked_missing"
                blocked_reasons.append(
                    _("Optional assessment ‘%(title)s’ is missing; the effective policy does not allow skipping it.")
                    % {"title": assessment.title}
                )
        elif attempt.status in ASSESSMENT_PENDING_ATTEMPT_STATUSES:
            record["outcome"] = "pending"
            blocked_reasons.append(
                _("Assessment ‘%(title)s’ is awaiting completion or grading.")
                % {"title": assessment.title}
            )
        elif attempt.status in ASSESSMENT_SCORED_ATTEMPT_STATUSES:
            if assessment_type == ASSESSMENT_TYPE_HOMEWORK:
                if attempt.score_points is None:
                    record["outcome"] = "incomplete_grade"
                    blocked_reasons.append(
                        _("Assessment ‘%(title)s’ has no complete score.")
                        % {"title": assessment.title}
                    )
                elif assessment.total_points <= 0:
                    record["outcome"] = "incomplete_grade"
                    blocked_reasons.append(
                        _("Assessment ‘%(title)s’ has no possible points and cannot be evaluated.")
                        % {"title": assessment.title}
                    )
                else:
                    record["outcome"] = "included"
                    earned_points += attempt.score_points
                    possible_points += assessment.total_points
                    scored_results.append((assessment, attempt))
            elif attempt.score_percent is None:
                record["outcome"] = "incomplete_grade"
                blocked_reasons.append(
                    _("Assessment ‘%(title)s’ has no complete score.")
                    % {"title": assessment.title}
                )
            else:
                record["outcome"] = "included"
                score_percentages.append(attempt.score_percent)
                scored_results.append((assessment, attempt))
        else:
            record["outcome"] = "incomplete_grade"
            blocked_reasons.append(
                _("Assessment ‘%(title)s’ has an unsupported attempt state.")
                % {"title": assessment.title}
            )

        snapshot.append(record)

    return {
        "configured": bool(assessments),
        "applicable": any(record["outcome"] != "omitted" for record in snapshot),
        "earned_points": earned_points,
        "possible_points": possible_points,
        "score_percentages": score_percentages,
        "scored_results": scored_results,
        "blocked_reasons": blocked_reasons,
        "snapshot": snapshot,
    }


def _homework_assessment_points(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    progression_policy: EffectiveProgressionPolicy | None = None,
    assessment_sources: dict | None = None,
) -> tuple[Decimal, Decimal] | None:
    """Aggregate the latest scored attempt per included Homework assessment."""
    progression_policy = progression_policy or resolve_progression_policy(
        enrollment.cohort.diploma_id,
        semester,
    )
    assessment_sources = assessment_sources or _load_semester_assessment_sources(
        enrollment.cohort,
        semester,
        [enrollment.student.user_id],
    )
    inputs = _semester_assessment_component_inputs(
        enrollment,
        ASSESSMENT_TYPE_HOMEWORK,
        assessment_sources,
        progression_policy,
    )
    if not inputs["configured"]:
        return None
    return inputs["earned_points"], inputs["possible_points"]


def _homework_evaluation_percent(assessment_inputs: dict) -> Decimal | None:
    if not assessment_inputs["applicable"]:
        return None
    return compute_percent(
        assessment_inputs["earned_points"],
        assessment_inputs["possible_points"],
    )


def _semester_exam_evaluation_percent(assessment_inputs: dict) -> Decimal | None:
    if not assessment_inputs["applicable"] or not assessment_inputs["score_percentages"]:
        return None
    # Preserve the existing component aggregation across distinct Assessment
    # records. Each record has already selected only its latest non-cancelled
    # attempt, so this never falls back to a student's earlier retake score.
    return max(assessment_inputs["score_percentages"])


def _has_attendance_source(cohort: Cohort, semester: Semester) -> bool:
    """Whether this cohort/semester has any non-cancelled period sessions."""
    return AttendanceSession.objects.filter(
        cohort=cohort,
        semester=semester,
        period__isnull=False,
    ).exclude(status=SESSION_STATUS_CANCELLED).exists()


def evaluate_semester_evaluation(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    attendance_summary: dict | None = None,
    attendance_source_exists: bool | None = None,
    *,
    progression_policy: EffectiveProgressionPolicy | None = None,
    assessment_sources: dict | None = None,
    evaluation_config=_UNSET_EVALUATION_CONFIG,
) -> dict:
    """Pure calculator shared by preview and finalization (P2-06).

    PASS requires every applicable component minimum AND the weighted overall
    minimum. A component with no configured source is not applicable. Within
    configured diploma assessment components, the effective progression policy
    controls whether missing required/optional assessments block or are omitted;
    pending work always blocks. SemesterEvaluationConfig remains authoritative
    for component weights and minimums. Returns a domain projection; never writes.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    config = (
        SemesterEvaluationConfig.objects.filter(
            cohort=enrollment.cohort, semester=semester,
        ).first()
        if evaluation_config is _UNSET_EVALUATION_CONFIG
        else evaluation_config
    )
    if config is not None and (
        config.cohort_id != enrollment.cohort_id or config.semester_id != semester.pk
    ):
        raise ValueError(_("Evaluation configuration does not match the enrollment and semester."))
    if config is None:
        return {
            "config": None, "blocked": True,
            "blocked_reasons": [_("No evaluation configuration for this cohort/semester.")],
            "failure_reasons": [],
            "parts": [], "overall_percent": None, "overall_min": None, "passed": False,
        }
    if config.status != EVALUATION_CONFIG_STATUS_ACTIVE:
        return {
            "config": config, "blocked": True,
            "blocked_reasons": [_("Evaluation configuration is incomplete and requires remediation.")],
            "failure_reasons": [],
            "parts": [], "overall_percent": None, "overall_min": None, "passed": False,
        }
    progression_policy = progression_policy or resolve_progression_policy(
        enrollment.cohort.diploma_id,
        semester,
    )
    if (
        progression_policy.diploma_id != enrollment.cohort.diploma_id
        or progression_policy.semester_id != semester.pk
    ):
        raise ValueError(_("Progression policy does not match the enrollment and semester."))
    assessment_sources = assessment_sources or _load_semester_assessment_sources(
        enrollment.cohort,
        semester,
        [enrollment.student.user_id],
    )
    homework_inputs = _semester_assessment_component_inputs(
        enrollment,
        ASSESSMENT_TYPE_HOMEWORK,
        assessment_sources,
        progression_policy,
    )
    exam_inputs = _semester_assessment_component_inputs(
        enrollment,
        ASSESSMENT_TYPE_SEMESTER_EXAM,
        assessment_sources,
        progression_policy,
    )
    if attendance_source_exists is None:
        attendance_source_exists = _has_attendance_source(enrollment.cohort, semester)
    attendance_percent = get_attendance_evaluation_percent(
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
            "applicable": attendance_source_exists,
        },
        {
            "key": "homework",
            "label": _("Homework"),
            "percent": _homework_evaluation_percent(homework_inputs),
            "weight": config.homework_weight_percent,
            "min": config.homework_min_percent,
            "applicable": homework_inputs["applicable"],
        },
        {
            "key": "semester_exam",
            "label": _("Semester Exam"),
            "percent": _semester_exam_evaluation_percent(exam_inputs),
            "weight": config.semester_exam_weight_percent,
            "min": config.semester_exam_min_percent,
            "applicable": exam_inputs["applicable"],
        },
    ]
    for part in parts:
        part["passed"] = (
            part["applicable"]
            and part["percent"] is not None
            and part["percent"] >= part["min"]
        )
    blocked_reasons = [
        *homework_inputs["blocked_reasons"],
        *exam_inputs["blocked_reasons"],
    ]
    failure_reasons = []
    for part in parts:
        if not part["applicable"]:
            continue
        if part["percent"] is None:
            if part["key"] == "attendance":
                blocked_reasons.append(_("%(label)s has no graded result yet.") % {"label": part["label"]})
        elif part["percent"] < part["min"]:
            failure_reasons.append(
                _("%(label)s is below its minimum of %(min)s%%.") % {"label": part["label"], "min": part["min"]}
            )
    graded_parts = [part for part in parts if part["applicable"] and part["percent"] is not None]
    overall = None
    weight_total = sum(part["weight"] for part in graded_parts)
    if graded_parts and weight_total > 0:
        overall = (
            sum(part["percent"] * part["weight"] for part in graded_parts) / weight_total
        ).quantize(Decimal("0.01"))
    if not graded_parts:
        blocked_reasons.append(_("No applicable attendance or assessment results are available to finalize."))
    passed = (
        overall is not None
        and not blocked_reasons
        and not failure_reasons
        and overall >= config.overall_min_percent
    )
    return {
        "config": config,
        "blocked": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
        "failure_reasons": failure_reasons,
        "parts": parts,
        "overall_percent": overall,
        "overall_min": config.overall_min_percent,
        "passed": passed,
        "progression_policy_snapshot": {
            "require_all_required_assessments": progression_policy.require_all_required_assessments,
            "require_all_required_assessments_source": progression_policy.require_all_required_assessments_source,
            "allow_missing_optional_assessments": progression_policy.allow_missing_optional_assessments,
            "allow_missing_optional_assessments_source": progression_policy.allow_missing_optional_assessments_source,
        },
        "assessment_inputs": {
            "homework": homework_inputs["snapshot"],
            "semester_exam": exam_inputs["snapshot"],
        },
    }


def _semester_evaluation_config_snapshot(calc: dict) -> dict:
    config = calc["config"]
    return {
        "attendance_weight_percent": str(config.attendance_weight_percent),
        "homework_weight_percent": str(config.homework_weight_percent),
        "semester_exam_weight_percent": str(config.semester_exam_weight_percent),
        "attendance_min_percent": str(config.attendance_min_percent),
        "homework_min_percent": str(config.homework_min_percent),
        "semester_exam_min_percent": str(config.semester_exam_min_percent),
        "overall_min_percent": str(config.overall_min_percent),
        "assessment_missing_policy": calc["progression_policy_snapshot"],
    }


def _semester_evaluation_source_snapshot(calc: dict) -> dict:
    def score_snapshot(value):
        return str(value) if value is not None else None

    return {
        "attendance_percent": score_snapshot(calc["parts"][0]["percent"]),
        "homework_percent": score_snapshot(calc["parts"][1]["percent"]),
        "semester_exam_percent": score_snapshot(calc["parts"][2]["percent"]),
        "component_applicability": {
            part["key"]: part["applicable"] for part in calc["parts"]
        },
        "assessment_inputs": calc["assessment_inputs"],
    }


def finalize_semester_evaluation(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    attendance_summary: dict | None = None,
) -> SemesterEvaluation:
    """Finalize the exact Semester Evaluation once (P2-06).

    Idempotent: an existing evaluation for the enrollment+semester is returned
    and never rewritten by this operation. The slow grade-item provenance sync
    runs outside the evaluation lock; the locked step recalculates from live
    sources and creates the snapshot.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    existing = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if existing is not None:
        return existing
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise ValueError(_("Enrollment is not active."))
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
            reasons = "; ".join(str(r) for r in calc["blocked_reasons"]) or str(_("missing inputs"))
            tmpl = str(_("Semester evaluation cannot be finalized: %(reasons)s."))
            try:
                msg = tmpl % {"reasons": reasons, "error": reasons}
            except Exception:
                msg = f"Semester evaluation cannot be finalized: {reasons}"
            raise ValueError(msg)
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
            config_snapshot=_semester_evaluation_config_snapshot(calc),
            source_snapshot=_semester_evaluation_source_snapshot(calc),
            finalized_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_SEMESTER_FINALIZED,
            target_type="enrollment",
            target_id=str(enrollment.id),
            section=SECTION_PROGRESSION,
            target_label=_progression_audit_target_label(
                diploma_title=semester.diploma.title,
                cohort_name=enrollment.cohort.name,
                semester_title=semester.title,
                student=enrollment.student,
            ),
            metadata={
                "diploma_id": semester.diploma_id,
                "diploma_title": semester.diploma.title,
                "cohort_id": enrollment.cohort_id,
                "cohort_name": enrollment.cohort.name,
                "semester_id": semester.id,
                "semester_title": semester.title,
                "evaluation_id": evaluation.id,
                "calculated_result": evaluation.result,
                "overall_percent": str(evaluation.overall_percent),
                "attendance_percent": str(evaluation.attendance_percent),
                "homework_percent": str(evaluation.homework_percent),
                "semester_exam_percent": str(evaluation.semester_exam_percent),
            },
            **_student_audit_subject(enrollment.student),
        )
    return evaluation


@transaction.atomic
def reevaluate_semester_evaluation(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
) -> SemesterEvaluation:
    """Replace an existing evaluation with a fresh, audited calculation.

    This is an explicit Admin correction path. Any linked final progression
    outcome, promotion/fail decision, and interview is superseded (never
    deleted) so the old decision cannot block a new decision on the recalculated
    result.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise ValueError(_("Enrollment is not active."))

    evaluation = SemesterEvaluation.objects.select_for_update().filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        raise ValueError(_("Finalize the semester evaluation before re-evaluating it."))

    outcome = FinalProgressionOutcome.objects.select_for_update().filter(
        evaluation=evaluation,
    ).first()

    sync_all_grade_items(enrollment, semester, actor=actor)
    calc = evaluate_semester_evaluation(enrollment, semester)
    if calc["blocked"] or calc["overall_percent"] is None:
        reasons = "; ".join(str(r) for r in calc["blocked_reasons"]) or str(_("missing inputs"))
        raise ValueError(_("Semester evaluation cannot be re-evaluated: %(reasons)s.") % {"reasons": reasons})

    active_interviews = []
    if outcome is not None:
        active_interviews = list(
            SemesterInterview.objects.select_for_update().filter(
                outcome=outcome,
                superseded_at__isnull=True,
            )
        )
    active_decisions = list(
        PromotionDecision.objects.select_for_update().filter(
            diploma_enrollment=enrollment,
            from_semester=semester,
            superseded_at__isnull=True,
            decision_type__in=PROMOTION_DECISION_SUPERSEDED_TYPES,
        )
    )
    superseded_at = timezone.now()
    superseded_decision_ids = [decision.pk for decision in active_decisions]
    superseded_interview_ids = [interview.pk for interview in active_interviews]
    if active_decisions:
        PromotionDecision.objects.filter(pk__in=superseded_decision_ids).update(
            superseded_at=superseded_at,
            superseded_by=actor,
            updated_at=superseded_at,
        )
    if active_interviews:
        SemesterInterview.objects.filter(pk__in=superseded_interview_ids).update(
            superseded_at=superseded_at,
            superseded_by=actor,
            updated_at=superseded_at,
        )

    previous = {
        "result": evaluation.result,
        "overall_percent": str(evaluation.overall_percent),
        "attendance_percent": str(evaluation.attendance_percent),
        "homework_percent": str(evaluation.homework_percent),
        "semester_exam_percent": str(evaluation.semester_exam_percent),
        "config_snapshot": evaluation.config_snapshot,
        "source_snapshot": evaluation.source_snapshot,
    }
    evaluation.result = SEMESTER_EVALUATION_PASS if calc["passed"] else SEMESTER_EVALUATION_FAIL
    evaluation.overall_percent = calc["overall_percent"]
    evaluation.attendance_percent = calc["parts"][0]["percent"]
    evaluation.homework_percent = calc["parts"][1]["percent"]
    evaluation.semester_exam_percent = calc["parts"][2]["percent"]
    evaluation.config_snapshot = _semester_evaluation_config_snapshot(calc)
    evaluation.source_snapshot = _semester_evaluation_source_snapshot(calc)
    evaluation.finalized_by = actor
    evaluation.save()

    if outcome:
        outcome.calculated_result = evaluation.result
        outcome.status = FINAL_PROGRESSION_STATUS_PENDING
        outcome.final_result = ""
        outcome.is_exceptional = None
        outcome.reason = ""
        outcome.decided_by = None
        outcome.decided_at = None
        outcome.save(update_fields=[
            "calculated_result", "status", "final_result", "is_exceptional",
            "reason", "decided_by", "decided_at", "updated_at",
        ])

    record_audit_event(
        actor=actor,
        action=ACTION_SEMESTER_REEVALUATED,
        target_type="enrollment",
        target_id=str(enrollment.id),
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=semester.diploma.title,
            cohort_name=enrollment.cohort.name,
            semester_title=semester.title,
            student=enrollment.student,
        ),
        metadata={
            "diploma_id": semester.diploma_id,
            "diploma_title": semester.diploma.title,
            "cohort_id": enrollment.cohort_id,
            "cohort_name": enrollment.cohort.name,
            "semester_id": semester.id,
            "semester_title": semester.title,
            "evaluation_id": evaluation.id,
            "before": previous,
            "after": {
                "result": evaluation.result,
                "overall_percent": str(evaluation.overall_percent),
                "attendance_percent": str(evaluation.attendance_percent),
                "homework_percent": str(evaluation.homework_percent),
                "semester_exam_percent": str(evaluation.semester_exam_percent),
                "config_snapshot": evaluation.config_snapshot,
                "source_snapshot": evaluation.source_snapshot,
            },
            "superseded_promotion_decision_ids": superseded_decision_ids,
            "superseded_interview_ids": superseded_interview_ids,
        },
        **_student_audit_subject(enrollment.student),
    )
    return evaluation


def refresh_finalized_semester_evaluations(
    cohort: Cohort,
    semester: Semester,
    *,
    actor=None,
) -> dict:
    """Refresh every finalized evaluation after a config change.

    A settings edit changes the authoritative weights/minimums, so every
    finalized evaluation for the cohort/semester must be recalculated. The
    operation is intentionally per-enrollment: one student's blocker or
    interview must not roll back the other students' refreshed snapshots.
    Active enrollments without a finalized evaluation are skipped; their live
    preview already uses the current settings.
    """
    if semester.diploma_id != cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the cohort's diploma."))

    enrollments = list(
        DiplomaEnrollment.objects.filter(
            cohort=cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).select_related("student", "cohort__diploma").order_by("student_id")
    )
    evaluation_ids = set(
        SemesterEvaluation.objects.filter(
            diploma_enrollment__in=enrollments,
            semester=semester,
        ).values_list("diploma_enrollment_id", flat=True)
    )

    refreshed = 0
    skipped = 0
    failures = []
    for enrollment in enrollments:
        if enrollment.id not in evaluation_ids:
            skipped += 1
            continue
        try:
            reevaluate_semester_evaluation(enrollment, semester, actor=actor)
        except Exception as exc:  # noqa: BLE001 - one student must not block the batch
            logger.exception(
                "Automatic evaluation refresh failed for enrollment %s",
                enrollment.id,
            )
            failures.append(f"{enrollment.student}: {exc}")
        else:
            refreshed += 1

    return {"refreshed": refreshed, "skipped": skipped, "failures": failures}


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
    outcome_rows = FinalProgressionOutcome.objects.filter(evaluation_id__in=evaluation_ids)
    outcomes = {outcome.evaluation_id: outcome for outcome in outcome_rows}
    interviews = {
        interview.outcome_id: interview
        for interview in SemesterInterview.objects.filter(
            outcome_id__in=[outcome.id for outcome in outcomes.values()],
            superseded_at__isnull=True,
        )
    }
    promotion_decisions_by_enrollment = {}
    for decision in PromotionDecision.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
        from_semester=semester,
        superseded_at__isnull=True,
    ).values("diploma_enrollment_id", "decision_type"):
        promotion_decisions_by_enrollment.setdefault(
            decision["diploma_enrollment_id"], set(),
        ).add(decision["decision_type"])
    progress_status_by_enrollment = dict(
        StudentSemesterProgress.objects.filter(
            diploma_enrollment_id__in=enrollment_ids,
            semester=semester,
        ).values_list("diploma_enrollment_id", "status")
    )

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
            blocked_preview = evaluate_semester_evaluation(
                enrollments[0], semester, evaluation_config=config,
            )
        except ValueError:
            blocked_preview = None
    preview_enrollments = [
        enrollment for enrollment in enrollments
        if enrollment.id not in evaluations
    ] if not config_blocked else []
    needs_previews = bool(preview_enrollments)
    preview_policy = None
    assessment_sources = None
    if needs_previews:
        preview_policy = resolve_progression_policy(cohort.diploma_id, semester)
        assessment_sources = _load_semester_assessment_sources(
            cohort,
            semester,
            [enrollment.student.user_id for enrollment in preview_enrollments],
        )
    attendance_source_exists = (
        _has_attendance_source(cohort, semester)
        if needs_previews else None
    )
    attendance_summaries = (
        get_attendance_summaries(preview_enrollments, semester) if needs_previews else {}
    )
    rows = []
    for enrollment in enrollments:
        evaluation = evaluations.get(enrollment.id)
        outcome = outcomes.get(evaluation.id) if evaluation else None
        interview = interviews.get(outcome.id) if outcome else None
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
                    attendance_source_exists=attendance_source_exists,
                    progression_policy=preview_policy,
                    assessment_sources=assessment_sources,
                    evaluation_config=config,
                )
                row["preview"] = preview
                if preview is not None:
                    row["components"] = {
                        part["key"]: part["percent"] for part in preview.get("parts", [])
                    }
            except ValueError:
                row["preview"] = None

        available_actions = ["sync"]
        if evaluation is None:
            if row["preview"] is not None and not row["preview"].get("blocked"):
                available_actions.append("finalize")
        else:
            available_actions.append("reevaluate")
            outcome_status = outcome.status if outcome is not None else FINAL_PROGRESSION_STATUS_PENDING
            interview_status = interview.status if interview is not None else None
            if interview_status == INTERVIEW_STATUS_REQUESTED:
                if outcome_status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
                    available_actions.append("complete_interview")
            elif outcome_status != FINAL_PROGRESSION_STATUS_RESOLVED:
                if interview is None:
                    available_actions.append("request_interview")
                if evaluation.result == SEMESTER_EVALUATION_PASS:
                    available_actions.append("final_pass")
                    if interview_status == INTERVIEW_STATUS_COMPLETED:
                        available_actions.append("exceptional_fail")
                elif evaluation.result == SEMESTER_EVALUATION_FAIL:
                    available_actions.extend(["final_fail", "exceptional_promote"])
            elif (
                outcome.final_result == SEMESTER_EVALUATION_PASS
                and PROMOTION_DECISION_CONFIRM_PASS in promotion_decisions_by_enrollment.get(enrollment.id, set())
                and not promotion_decisions_by_enrollment.get(enrollment.id, set()).intersection(
                    PROMOTION_DECISION_PROMOTE_TYPES,
                )
                and progress_status_by_enrollment.get(enrollment.id) in (
                    None,
                    SEMESTER_PROGRESS_NOT_STARTED,
                    SEMESTER_PROGRESS_IN_PROGRESS,
                    SEMESTER_PROGRESS_PASSED,
                    SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION,
                    SEMESTER_PROGRESS_PASSED_CONFIRMED,
                )
            ):
                # Repair rows created by the old Final Pass path, which closed
                # the outcome but did not advance the authoritative progress.
                available_actions.append("recover_final_pass")
        row["available_actions"] = available_actions
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


# Promotion decisions represent actual semester advancement, not only the
# final outcome label. Keep the exceptional override in the same uniqueness
# guard so a second override cannot create a duplicate transition.
# (PROMOTION_DECISION_PROMOTE_TYPES is defined near the top because
# re-evaluation also needs it to supersede old decisions.)


@transaction.atomic
def resolve_final_progression(
    evaluation: SemesterEvaluation,
    *,
    actor=None,
    final_result: str,
    reason: str = "",
) -> FinalProgressionOutcome:
    """Resolve the final Continue/Promote or Do Not Continue decision.

    Locks in evaluation-then-outcome order; idempotent for the same result and
    immutable once resolved differently. A final PASS delegates to the
    canonical atomic promotion transition. Exceptional decisions require a
    reason, and an exceptional Do Not Continue requires a completed interview.
    Never mutates the evaluation snapshot.
    """
    if final_result not in (SEMESTER_EVALUATION_PASS, SEMESTER_EVALUATION_FAIL):
        raise ValueError(_("Invalid final progression result."))
    evaluation = SemesterEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    outcome = _get_or_create_progression_outcome(evaluation)
    if outcome.status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
        raise ValueError(_("Final decision is blocked until the interview is completed."))
    if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
        if outcome.final_result == final_result:
            if final_result == SEMESTER_EVALUATION_PASS:
                has_promotion = PromotionDecision.objects.filter(
                    diploma_enrollment_id=evaluation.diploma_enrollment_id,
                    from_semester_id=evaluation.semester_id,
                    decision_type__in=PROMOTION_DECISION_PROMOTE_TYPES,
                    superseded_at__isnull=True,
                ).exists()
                has_legacy_confirmation = PromotionDecision.objects.filter(
                    diploma_enrollment_id=evaluation.diploma_enrollment_id,
                    from_semester_id=evaluation.semester_id,
                    decision_type=PROMOTION_DECISION_CONFIRM_PASS,
                    superseded_at__isnull=True,
                ).exists()
                if not has_promotion and has_legacy_confirmation:
                    promote_semester(
                        evaluation.diploma_enrollment,
                        evaluation.semester,
                        actor=actor,
                        reason=reason.strip() or outcome.reason,
                        exceptional=(
                            bool(outcome.is_exceptional)
                            or outcome.calculated_result != SEMESTER_EVALUATION_PASS
                        ),
                        _recover_legacy_final_pass=True,
                    )
                    outcome.refresh_from_db()
                elif not has_promotion:
                    raise ValueError(_("The resolved pass is missing its promotion record."))
            return outcome
        raise ValueError(_("This progression outcome is already resolved."))
    is_exceptional = final_result != outcome.calculated_result
    if is_exceptional and not reason.strip():
        raise ValueError(_("An explicit reason is required for an exceptional final decision."))

    if final_result == SEMESTER_EVALUATION_PASS:
        promote_semester(
            evaluation.diploma_enrollment,
            evaluation.semester,
            actor=actor,
            reason=reason,
            exceptional=is_exceptional,
        )
        outcome.refresh_from_db()
    else:
        if is_exceptional:
            interview = SemesterInterview.objects.select_for_update().filter(
                outcome=outcome,
                superseded_at__isnull=True,
            ).first()
            if interview is None or interview.status != INTERVIEW_STATUS_COMPLETED:
                raise ValueError(_("Complete the interview before recording an exceptional Do Not Continue decision."))
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
        PromotionDecision.objects.create(
            student=evaluation.student,
            diploma_enrollment=evaluation.diploma_enrollment,
            from_semester=evaluation.semester,
            decision_type=PROMOTION_DECISION_DEMOTE,
            automatic=False,
            reason=reason.strip() or _("Final fail."),
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
        StudentSemesterProgress.objects.update_or_create(
            diploma_enrollment=evaluation.diploma_enrollment,
            semester=evaluation.semester,
            defaults={
                "student": evaluation.student,
                "cohort": evaluation.diploma_enrollment.cohort,
                "status": SEMESTER_PROGRESS_FAILED,
                "failed_at": now,
                "decided_by": actor,
                "decision_reason": reason.strip() or _("Final fail."),
            },
        )
    enrollment = evaluation.diploma_enrollment
    student = enrollment.student
    semester = evaluation.semester
    diploma_title = semester.diploma.title
    cohort_name = enrollment.cohort.name
    record_audit_event(
        actor=actor,
        action=ACTION_SEMESTER_FINALIZED,
        target_type="enrollment",
        target_id=str(enrollment.id),
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=diploma_title,
            cohort_name=cohort_name,
            semester_title=semester.title,
            student=student,
        ),
        metadata={
            "diploma_id": semester.diploma_id,
            "diploma_title": diploma_title,
            "cohort_id": enrollment.cohort_id,
            "cohort_name": cohort_name,
            "semester_id": semester.id,
            "semester_title": semester.title,
            "outcome_id": outcome.id,
            "calculated_result": outcome.calculated_result,
            "final_result": final_result,
            "is_exceptional": is_exceptional,
            "overall_percent": str(evaluation.overall_percent),
            "reason": reason.strip(),
        },
        **_student_audit_subject(student),
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
    # Serialize interview requests with final decisions and explicit
    # re-evaluation. All progression transitions acquire the evaluation before
    # its outcome, so concurrent requests cannot overwrite a completed result.
    evaluation = SemesterEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    outcome = _get_or_create_progression_outcome(evaluation)
    if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
        raise ValueError(_("Progression is already resolved; an interview cannot be requested."))
    interview = SemesterInterview.objects.select_for_update().filter(
        outcome=outcome,
        superseded_at__isnull=True,
    ).first()
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
        reason=notes or _("Interview requested."),
        decided_by=actor,
        metadata={"evaluation_id": evaluation.id, "interview_id": interview.id},
    )
    enrollment = evaluation.diploma_enrollment
    record_audit_event(
        actor=actor,
        action=ACTION_PROGRESSION_INTERVIEW_REQUESTED,
        target_type="semester_interview",
        target_id=str(interview.id),
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=evaluation.semester.diploma.title,
            cohort_name=enrollment.cohort.name,
            semester_title=evaluation.semester.title,
            student=evaluation.student,
        ),
        metadata={
            "diploma_id": evaluation.semester.diploma_id,
            "cohort_id": enrollment.cohort_id,
            "semester_id": evaluation.semester_id,
            "semester_title": evaluation.semester.title,
            "evaluation_id": evaluation.id,
            "outcome_id": outcome.id,
            "interview_id": interview.id,
            "status": interview.status,
            "calculated_result": outcome.calculated_result,
            "reason": notes,
        },
        **_student_audit_subject(evaluation.student),
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
    # Keep the canonical evaluation -> outcome -> interview lock order shared
    # with finalization, promotion, and re-evaluation.
    evaluation = SemesterEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    outcome = _get_or_create_progression_outcome(evaluation)
    if outcome.status != FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
        raise ValueError(_("This progression is not waiting for an Interview."))
    interview = SemesterInterview.objects.select_for_update().filter(
        outcome=outcome,
        superseded_at__isnull=True,
    ).first()
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
        reason=details or _("Interview completed."),
        decided_by=actor,
        metadata={"evaluation_id": evaluation.id, "interview_id": interview.id, "interview_outcome": decision},
    )
    enrollment = evaluation.diploma_enrollment
    record_audit_event(
        actor=actor,
        action=ACTION_PROGRESSION_INTERVIEW_COMPLETED,
        target_type="semester_interview",
        target_id=str(interview.id),
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=evaluation.semester.diploma.title,
            cohort_name=enrollment.cohort.name,
            semester_title=evaluation.semester.title,
            student=evaluation.student,
        ),
        metadata={
            "diploma_id": evaluation.semester.diploma_id,
            "cohort_id": enrollment.cohort_id,
            "semester_id": evaluation.semester_id,
            "semester_title": evaluation.semester.title,
            "evaluation_id": evaluation.id,
            "outcome_id": outcome.id,
            "interview_id": interview.id,
            "decision": decision,
            "status_before": INTERVIEW_STATUS_REQUESTED,
            "status": interview.status,
            "reason": details,
        },
        **_student_audit_subject(evaluation.student),
    )
    return interview


@transaction.atomic
def promote_semester(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    reason: str = "",
    automatic: bool = False,
    exceptional: bool = False,
    _recover_legacy_final_pass: bool = False,
) -> PromotionDecision:
    """Canonical promotion state machine (BUG-019/BUG-020).

    Manual, automatic, bulk, and individual promotion entry points all
    converge here. The pass decision is the canonical synchronized evaluation:
    the finalized ``SemesterEvaluation`` created by
    ``finalize_semester_evaluation`` from the exact two-level rule
    (component minimums AND weighted overall). Ordinary promotion requires a
    calculated PASS. A calculated FAIL or an explicit exceptional decision
    may use the reason-required exceptional path. The service
    validates the current promotion state (no duplicate/conflicting
    decisions, no pending interview, not locked) and creates exactly one
    ``PromotionDecision`` row before advancing the student's semester
    progress.
    """
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise ValueError(_("Enrollment is not active."))
    if exceptional and automatic:
        raise ValueError(_("Exceptional promotion cannot be automatic."))
    if exceptional and not reason.strip():
        raise ValueError(_("A reason is required for exceptional promotion."))
    evaluation = SemesterEvaluation.objects.select_for_update().filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        raise ValueError(_("Finalize the semester evaluation before promotion."))
    if not exceptional and evaluation.result != SEMESTER_EVALUATION_PASS:
        raise ValueError(_("Cannot promote: the calculated semester evaluation is FAIL."))

    outcome = FinalProgressionOutcome.objects.select_for_update().filter(
        evaluation=evaluation,
    ).first()
    legacy_final_pass_recovery = False
    if outcome is not None:
        if outcome.status == FINAL_PROGRESSION_STATUS_INTERVIEW_PENDING:
            raise ValueError(_("Promotion is blocked until the interview is completed."))
        if outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED:
            has_legacy_confirmation = PromotionDecision.objects.filter(
                diploma_enrollment=enrollment,
                from_semester=semester,
                decision_type=PROMOTION_DECISION_CONFIRM_PASS,
                superseded_at__isnull=True,
            ).exists()
            if (
                _recover_legacy_final_pass
                and outcome.final_result == SEMESTER_EVALUATION_PASS
                and has_legacy_confirmation
                and not PromotionDecision.objects.filter(
                    diploma_enrollment=enrollment,
                    from_semester=semester,
                    decision_type__in=PROMOTION_DECISION_PROMOTE_TYPES,
                    superseded_at__isnull=True,
                ).exists()
            ):
                legacy_final_pass_recovery = True
            else:
                raise ValueError(_("This progression outcome is already resolved."))

    if PromotionDecision.objects.filter(
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type__in=PROMOTION_DECISION_PROMOTE_TYPES,
        superseded_at__isnull=True,
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
    if exceptional:
        decision_type = PROMOTION_DECISION_OVERRIDE_PASS
    elif automatic:
        decision_type = PROMOTION_DECISION_AUTO_PROMOTE
    else:
        decision_type = PROMOTION_DECISION_MANUAL_PROMOTE
    decision = PromotionDecision.objects.create(
        student=enrollment.student,
        diploma_enrollment=enrollment,
        from_semester=semester,
        to_semester=next_semester,
        decision_type=decision_type,
        previous_status=previous_status,
        new_status=SEMESTER_PROGRESS_PROMOTED,
        automatic=automatic,
        reason=reason.strip() or (outcome.reason if legacy_final_pass_recovery and outcome else "") or (
            _("Auto-promoted based on the calculated semester evaluation.")
            if automatic
            else _("Manual promotion.")
        ),
        score_snapshot=evaluation.overall_percent,
        decided_by=actor,
        decided_at=now,
        metadata={
            "evaluation_id": evaluation.id,
            "calculated_result": evaluation.result,
            "exceptional": exceptional,
            "overall_percent": str(evaluation.overall_percent),
            "legacy_final_pass_recovered": legacy_final_pass_recovery,
        },
    )
    # The promotion is also the authoritative final Continue/Promote decision:
    # resolve the one-to-one FinalProgressionOutcome so the progression rows
    # keep showing the same final decision for every promotion entry point.
    resolved_outcome = _get_or_create_progression_outcome(evaluation)
    if resolved_outcome.status != FINAL_PROGRESSION_STATUS_RESOLVED:
        resolved_outcome.final_result = SEMESTER_EVALUATION_PASS
        resolved_outcome.is_exceptional = exceptional
        resolved_outcome.reason = reason.strip()
        resolved_outcome.decided_by = actor
        resolved_outcome.decided_at = now
        resolved_outcome.status = FINAL_PROGRESSION_STATUS_RESOLVED
        resolved_outcome.save(update_fields=[
            "final_result", "is_exceptional", "reason",
            "decided_by", "decided_at", "status", "updated_at",
        ])
    record_audit_event(
        actor=actor,
        action=ACTION_PROMOTION,
        target_type="enrollment",
        target_id=str(enrollment.id),
        section=SECTION_PROGRESSION,
        target_label=_progression_audit_target_label(
            diploma_title=semester.diploma.title,
            cohort_name=enrollment.cohort.name,
            semester_title=semester.title,
            student=enrollment.student,
        ),
        metadata={
            "diploma_id": semester.diploma_id,
            "cohort_id": enrollment.cohort_id,
            "semester_id": semester.id,
            "semester_title": semester.title,
            "next_semester_id": next_semester.id if next_semester else None,
            "next_semester_title": next_semester.title if next_semester else "",
            "evaluation_id": evaluation.id,
            "calculated_result": evaluation.result,
            "final_result": SEMESTER_EVALUATION_PASS,
            "decision_type": decision.decision_type,
            "automatic": automatic,
            "exceptional": exceptional,
            "legacy_final_pass_recovered": legacy_final_pass_recovery,
            "status_before": previous_status,
            "status": SEMESTER_PROGRESS_PROMOTED,
            "reason": reason.strip(),
        },
        **_student_audit_subject(enrollment.student),
    )
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


def exceptional_promote_semester(
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    reason: str = "",
) -> PromotionDecision:
    """Promote through the explicit reason-required exceptional path."""
    return promote_semester(
        enrollment,
        semester,
        actor=actor,
        reason=reason,
        exceptional=True,
    )


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


def _schedule_certificate_issuance(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    *,
    actor,
    source: str,
) -> None:
    """Issue the completion certificate after the completion transaction commits.

    Automatic issuance is best-effort: a rendering/storage error leaves a
    retryable failed certificate row and never reverses the graduation.
    """

    def _issue():
        # Lazy import: certificates.services imports progression models.
        from apps.certificates.services import issue_certificate

        try:
            issue_certificate(
                student_profile,
                enrollment,
                actor=actor,
                source=source,
            )
        except ValueError as exc:
            logger.info(
                "Certificate auto-issuance skipped for enrollment %s: %s",
                enrollment.pk,
                exc,
            )
        except Exception:
            logger.exception(
                "Certificate auto-issuance failed for enrollment %s",
                enrollment.pk,
            )

    transaction.on_commit(_issue, robust=True)


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
            reason=notes or _("Manual completion."),
            decided_by=actor,
        )

    _schedule_certificate_issuance(
        student_profile,
        enrollment,
        actor=actor,
        source="auto_completion",
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
            reason=notes or _("Manual graduation."),
            decided_by=actor,
        )

    _schedule_certificate_issuance(
        student_profile,
        enrollment,
        actor=actor,
        source="auto_graduation",
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


def sync_semester_exam_grade_item(
    diploma_enrollment,
    semester,
    actor=None,
    *,
    progression_policy: EffectiveProgressionPolicy | None = None,
    assessment_sources: dict | None = None,
) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_SEMESTER_EXAM)
    if component is None:
        return None

    progression_policy = progression_policy or resolve_progression_policy(
        diploma_enrollment.cohort.diploma_id,
        semester,
    )
    assessment_sources = assessment_sources or _load_semester_assessment_sources(
        diploma_enrollment.cohort,
        semester,
        [diploma_enrollment.student.user_id],
    )
    exam_inputs = _semester_assessment_component_inputs(
        diploma_enrollment,
        ASSESSMENT_TYPE_SEMESTER_EXAM,
        assessment_sources,
        progression_policy,
    )
    # The latest non-cancelled attempt per Assessment is selected in
    # _load_semester_assessment_sources; choose the same representative source
    # as the Semester Exam component's existing cross-assessment max rule.
    component_source = max(
        exam_inputs["scored_results"],
        key=lambda result: result[1].score_percent,
        default=None,
    )
    if component_source is None:
        return None
    selected_assessment, selected_attempt = component_source

    item, _created = StudentGradeItem.objects.update_or_create(
        component=component,
        diploma_enrollment=diploma_enrollment,
        defaults={
            "student": diploma_enrollment.student,
            "source_assessment_attempt": selected_attempt,
            "max_score": selected_assessment.total_points,
            "score_percent": selected_attempt.score_percent,
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
        # must not be recorded as a 0% grade item. If the previously generated
        # attendance item became stale after its session window was edited,
        # clear only its derived score while preserving the row and any manual
        # score/notes/visibility metadata.
        StudentGradeItem.objects.filter(
            component=component,
            diploma_enrollment=diploma_enrollment,
            manual_score__isnull=True,
            source_attendance_summary__has_key="total_sessions",
        ).update(
            score_percent=None,
            source_attendance_summary=summary,
            updated_at=timezone.now(),
        )
        return None
    config = SemesterEvaluationConfig.objects.filter(
        cohort=diploma_enrollment.cohort, semester=semester,
    ).first()
    required = config.attendance_required_sessions if config else None
    attendance_percent = get_attendance_evaluation_percent(
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


def sync_homework_assessment_grade_item(
    diploma_enrollment,
    semester,
    actor=None,
    *,
    progression_policy: EffectiveProgressionPolicy | None = None,
    assessment_sources: dict | None = None,
) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_HOMEWORK_ASSESSMENT)
    if component is None:
        return None

    aggregate = _homework_assessment_points(
        diploma_enrollment,
        semester,
        progression_policy=progression_policy,
        assessment_sources=assessment_sources,
    )
    if aggregate is None:
        return None
    total_earned, total_max = aggregate
    if total_max <= 0:
        return None
    score_percent = compute_percent(total_earned, total_max)

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
    if diploma_enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise ValueError(_("Enrollment is not active."))
    with transaction.atomic():
        progression_policy = resolve_progression_policy(
            diploma_enrollment.cohort.diploma_id,
            semester,
        )
        assessment_sources = _load_semester_assessment_sources(
            diploma_enrollment.cohort,
            semester,
            [diploma_enrollment.student.user_id],
        )
        return {
            "semester_exam": sync_semester_exam_grade_item(
                diploma_enrollment,
                semester,
                actor=actor,
                progression_policy=progression_policy,
                assessment_sources=assessment_sources,
            ),
            "attendance": sync_attendance_grade_item(
                diploma_enrollment, semester, actor=actor, summary=attendance_summary,
            ),
            "homework_assessment": sync_homework_assessment_grade_item(
                diploma_enrollment,
                semester,
                actor=actor,
                progression_policy=progression_policy,
                assessment_sources=assessment_sources,
            ),
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


def get_rejoin_eligibility_rows(
    *, offset=0, limit=25, diploma_id=None, cohort_id=None, semester_id=None,
):
    """Paginated Rejoin Eligibility projection (P4-02).

    Only immediately-next/same-semester rows remain actionable until the
    target semester is finalized.
    """
    rows = []
    current_failed_outcome = FinalProgressionOutcome.objects.filter(
        diploma_enrollment_id=OuterRef("from_enrollment_id"),
        semester_id=OuterRef("failed_semester_id"),
        status=FINAL_PROGRESSION_STATUS_RESOLVED,
        final_result=SEMESTER_EVALUATION_FAIL,
    )
    plans_qs = SemesterRejoinPlan.objects.filter(
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    ).annotate(
        source_failed=Exists(current_failed_outcome),
    ).filter(
        source_failed=True,
        status=REJOIN_STATUS_PLANNED,
    ).select_related("student__user", "from_enrollment__cohort", "failed_semester", "target_cohort")
    if diploma_id is not None:
        plans_qs = plans_qs.filter(
            from_enrollment__cohort__diploma_id=diploma_id,
            failed_semester__diploma_id=diploma_id,
        )
    if cohort_id is not None:
        plans_qs = plans_qs.filter(from_enrollment__cohort_id=cohort_id)
    if semester_id is not None:
        plans_qs = plans_qs.filter(failed_semester_id=semester_id)
    plans_qs = plans_qs.order_by("-created_at", "-id")
    plan_count = plans_qs.count()

    active_plan_for_outcome = SemesterRejoinPlan.objects.filter(
        from_enrollment_id=OuterRef("diploma_enrollment_id"),
        failed_semester_id=OuterRef("semester_id"),
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    )
    active_replacement_for_outcome = DiplomaEnrollment.objects.filter(
        student_id=OuterRef("student_id"),
        cohort__diploma_id=OuterRef("evaluation__diploma_enrollment__cohort__diploma_id"),
        status=ENROLLMENT_STATUS_ACTIVE,
    ).exclude(id=OuterRef("diploma_enrollment_id"))
    resolved_failed = (
        FinalProgressionOutcome.objects.filter(
            status=FINAL_PROGRESSION_STATUS_RESOLVED,
            final_result=SEMESTER_EVALUATION_FAIL,
        )
        .annotate(has_plan=Exists(active_plan_for_outcome))
        .annotate(has_active_replacement=Exists(active_replacement_for_outcome))
        .filter(has_plan=False, has_active_replacement=False)
        .select_related(
            "evaluation__diploma_enrollment__cohort",
            "evaluation__semester",
            "student__user",
        )
        .order_by("-created_at", "-id")
    )
    if diploma_id is not None:
        resolved_failed = resolved_failed.filter(
            evaluation__diploma_enrollment__cohort__diploma_id=diploma_id,
            evaluation__semester__diploma_id=diploma_id,
        )
    if cohort_id is not None:
        resolved_failed = resolved_failed.filter(
            evaluation__diploma_enrollment__cohort_id=cohort_id,
        )
    if semester_id is not None:
        resolved_failed = resolved_failed.filter(evaluation__semester_id=semester_id)
    failed_count = resolved_failed.count()

    plan_start = min(offset, plan_count)
    plan_stop = min(plan_count, offset + limit)
    page_plans = list(plans_qs[plan_start:plan_stop])
    remaining = limit - len(page_plans)
    failed_start = max(0, offset - plan_count)
    page_failed = list(resolved_failed[failed_start:failed_start + remaining])

    source_cohort_ids = {
        plan.from_enrollment.cohort_id for plan in page_plans
    } | {
        outcome.evaluation.diploma_enrollment.cohort_id for outcome in page_failed
    }
    next_cohort_id_by_source = {}
    if source_cohort_ids:
        immediate_next_cohort_id = Subquery(
            Cohort.objects.filter(
                diploma_id=OuterRef("diploma_id"),
                is_active=True,
            ).filter(
                Q(start_date__gt=OuterRef("start_date"))
                | Q(start_date=OuterRef("start_date"), id__gt=OuterRef("id"))
            ).order_by("start_date", "id").values("id")[:1]
        )
        next_cohort_id_by_source = dict(
            Cohort.objects.filter(pk__in=source_cohort_ids)
            .annotate(immediate_next_id=immediate_next_cohort_id)
            .values_list("pk", "immediate_next_id")
        )

    target_cohort_ids = {
        cohort_id for cohort_id in next_cohort_id_by_source.values() if cohort_id is not None
    } | {plan.target_cohort_id for plan in page_plans}
    semester_ids = {plan.failed_semester_id for plan in page_plans} | {
        outcome.evaluation.semester_id for outcome in page_failed
    }
    student_ids = {plan.student_id for plan in page_plans} | {
        outcome.student_id for outcome in page_failed
    }

    finalized_target_semesters = set()
    active_target_enrollments = set()
    if target_cohort_ids and semester_ids:
        finalized_target_semesters = set(
            SemesterEvaluation.objects.filter(
                diploma_enrollment__cohort_id__in=target_cohort_ids,
                semester_id__in=semester_ids,
            ).values_list("diploma_enrollment__cohort_id", "semester_id").distinct()
        )
    if target_cohort_ids and student_ids:
        active_target_enrollments = set(
            DiplomaEnrollment.objects.filter(
                cohort_id__in=target_cohort_ids,
                student_id__in=student_ids,
                status=ENROLLMENT_STATUS_ACTIVE,
            ).values_list("cohort_id", "student_id").distinct()
        )

    next_cohort_ids = {
        cohort_id for cohort_id in next_cohort_id_by_source.values() if cohort_id is not None
    }
    next_cohorts_by_id = Cohort.objects.in_bulk(next_cohort_ids) if next_cohort_ids else {}

    for plan in page_plans:
        rows.append({
            "plan": plan,
            "student": plan.student,
            "from_enrollment_id": plan.from_enrollment_id,
            "failed_semester_id": plan.failed_semester_id,
            "previous_cohort": plan.from_enrollment.cohort,
            "failed_semester": plan.failed_semester,
            "next_cohort": plan.target_cohort,
                "eligible": (
                    plan.status == REJOIN_STATUS_PLANNED
                    and plan.from_enrollment.status == ENROLLMENT_STATUS_ACTIVE
                    and not plan.from_enrollment.is_access_expired
                    and next_cohort_id_by_source.get(plan.from_enrollment.cohort_id) == plan.target_cohort_id
                and (plan.target_cohort_id, plan.failed_semester_id) not in finalized_target_semesters
                and (plan.target_cohort_id, plan.student_id) not in active_target_enrollments
            ),
        })
    for outcome in page_failed:
        enrollment = outcome.evaluation.diploma_enrollment
        next_cohort_id = next_cohort_id_by_source.get(enrollment.cohort_id)
        next_cohort = next_cohorts_by_id.get(next_cohort_id)
        rows.append({
            "plan": None,
            "student": outcome.student,
            "from_enrollment_id": enrollment.id,
            "failed_semester_id": outcome.evaluation.semester_id,
            "previous_cohort": enrollment.cohort,
            "failed_semester": outcome.evaluation.semester,
            "next_cohort": next_cohort,
                "eligible": (
                    enrollment.status == ENROLLMENT_STATUS_ACTIVE
                    and not enrollment.is_access_expired
                    and next_cohort is not None
                and (next_cohort_id, outcome.evaluation.semester_id) not in finalized_target_semesters
                and (next_cohort_id, outcome.student_id) not in active_target_enrollments
            ),
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
            if locked_enrollment.status != ENROLLMENT_STATUS_ACTIVE or locked_enrollment.is_access_expired:
                raise ValueError(_("Only active, non-expired enrollments may use Direct Rejoin."))
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
            record_audit_event(
                actor=actor,
                action=ACTION_PROGRESSION_REJOIN_CREATED,
                target_type="semester_rejoin_plan",
                target_id=str(plan.id),
                section=SECTION_PROGRESSION,
                target_label=(
                    f"{locked_student.full_name} · {failed_semester.diploma.title} · "
                    f"{failed_semester.title} · {locked_enrollment.cohort.name} → "
                    f"{target_cohort.name}"
                ),
                metadata={
                    "diploma_id": failed_semester.diploma_id,
                    "diploma_title": failed_semester.diploma.title,
                    "source_enrollment_id": locked_enrollment.id,
                    "source_cohort_id": locked_enrollment.cohort_id,
                    "source_cohort_name": locked_enrollment.cohort.name,
                    "target_cohort_id": target_cohort.id,
                    "target_cohort_name": target_cohort.name,
                    "semester_id": failed_semester.id,
                    "semester_title": failed_semester.title,
                    "status": plan.status,
                    "notes": notes,
                },
                **_student_audit_subject(locked_student),
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
    with transaction.atomic():
        plan_ref = SemesterRejoinPlan.objects.select_for_update().get(pk=plan.pk)
        if plan_ref.status != REJOIN_STATUS_PLANNED:
            raise ValueError(_("Only planned rejoin plans can be activated."))

        # Serialize every active-enrollment transition for this student before
        # validating and creating the replacement. A stale plan is historical
        # data, not authority to move a student into an arbitrary cohort.
        locked_student = StudentProfile.objects.select_for_update().get(
            pk=plan_ref.student_id,
        )
        source = DiplomaEnrollment.objects.select_for_update().select_related(
            "cohort",
        ).get(pk=plan_ref.from_enrollment_id)
        target_cohort = Cohort.objects.select_for_update().get(
            pk=plan_ref.target_cohort_id,
        )
        failed_semester = Semester.objects.select_for_update().get(
            pk=plan_ref.failed_semester_id,
        )

        if source.student_id != locked_student.pk:
            raise ValueError(_("The source enrollment no longer belongs to this student."))
        if plan_ref.to_enrollment_id is not None:
            raise ValueError(_("A planned rejoin cannot already have a replacement enrollment."))
        if source.status != ENROLLMENT_STATUS_ACTIVE or source.is_access_expired:
            raise ValueError(_("The source enrollment is no longer active or has expired."))
        if (
            source.cohort.diploma_id != failed_semester.diploma_id
            or target_cohort.diploma_id != source.cohort.diploma_id
        ):
            raise ValueError(_("The source, failed semester, and target cohort must belong to the same diploma."))

        immediate_next_cohort = get_immediate_next_cohort(source.cohort)
        if immediate_next_cohort is None or immediate_next_cohort.pk != target_cohort.pk:
            raise ValueError(_("The plan target is no longer the immediately next active cohort."))

        # Match the evaluation -> outcome lock order used by finalization and
        # re-evaluation so a concurrent correction cannot leave a stale fail
        # eligible for activation.
        source_evaluation = SemesterEvaluation.objects.select_for_update().filter(
            diploma_enrollment=source,
            semester=failed_semester,
        ).first()
        source_outcome = (
            FinalProgressionOutcome.objects.select_for_update().filter(
                evaluation=source_evaluation,
            ).first()
            if source_evaluation is not None else None
        )
        if (
            source_outcome is None
            or source_outcome.status != FINAL_PROGRESSION_STATUS_RESOLVED
            or source_outcome.final_result != SEMESTER_EVALUATION_FAIL
        ):
            raise ValueError(_("The source failed outcome is no longer authoritative."))

        if _target_semester_finalized(target_cohort, failed_semester):
            raise ValueError(_("The target cohort has finalized this semester; Direct Rejoin has expired."))
        if DiplomaEnrollment.objects.filter(
            student=locked_student,
            cohort=target_cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists():
            raise ValueError(_("The student already has an active enrollment in the target cohort."))

        if SemesterRejoinPlan.objects.filter(
            student=locked_student,
            status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
        ).exclude(pk=plan_ref.pk).exists():
            raise ValueError(_("Another active rejoin plan already exists for this student."))

        source.status = ENROLLMENT_STATUS_RESTRICTED
        source.save(update_fields=["status", "updated_at"])

        # Fresh enrollment starting at the failed semester; no progress copy.
        new_enrollment = DiplomaEnrollment.objects.create(
            student=locked_student,
            cohort=target_cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
            entry_semester=failed_semester,
        )
        StudentSemesterProgress.objects.create(
            student=locked_student,
            diploma_enrollment=new_enrollment,
            cohort=target_cohort,
            semester=failed_semester,
            status=SEMESTER_PROGRESS_NOT_STARTED,
        )
        plan_ref.to_enrollment = new_enrollment
        plan_ref.status = REJOIN_STATUS_ACTIVE
        plan_ref.activated_at = timezone.now()
        plan_ref.save(update_fields=["to_enrollment", "status", "activated_at", "updated_at"])

        PromotionDecision.objects.create(
            student=locked_student,
            diploma_enrollment=new_enrollment,
            from_semester=failed_semester,
            decision_type=PROMOTION_DECISION_REJOIN_ACTIVATED,
            reason=_("Rejoin plan activated at the failed semester."),
            decided_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_PROGRESSION_REJOIN_ACTIVATED,
            target_type="semester_rejoin_plan",
            target_id=str(plan_ref.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{locked_student.full_name} · {failed_semester.diploma.title} · "
                f"{failed_semester.title} · {source.cohort.name} → "
                f"{target_cohort.name}"
            ),
            metadata={
                "diploma_id": failed_semester.diploma_id,
                "diploma_title": failed_semester.diploma.title,
                "source_enrollment_id": source.id,
                "source_enrollment_status_before": ENROLLMENT_STATUS_ACTIVE,
                "source_enrollment_status": source.status,
                "source_cohort_id": source.cohort_id,
                "source_cohort_name": source.cohort.name,
                "target_cohort_id": target_cohort.id,
                "target_cohort_name": target_cohort.name,
                "new_enrollment_id": new_enrollment.id,
                "semester_id": failed_semester.id,
                "semester_title": failed_semester.title,
                "status_before": REJOIN_STATUS_PLANNED,
                "status": plan_ref.status,
            },
            **_student_audit_subject(locked_student),
        )

    return plan_ref


def cancel_rejoin_plan(plan, *, actor, reason: str = "") -> SemesterRejoinPlan:
    with transaction.atomic():
        plan_ref = SemesterRejoinPlan.objects.select_for_update().get(pk=plan.pk)
        source = DiplomaEnrollment.objects.select_for_update().get(pk=plan_ref.from_enrollment_id)
        target = (
            DiplomaEnrollment.objects.select_for_update().get(pk=plan_ref.to_enrollment_id)
            if plan_ref.to_enrollment_id else None
        )
        previous_status = plan_ref.status
        source_status_before = source.status
        target_enrollment_id = target.id if target is not None else None
        target_cohort = target.cohort if target is not None else None
        recoverable_cancel = (
            plan_ref.status == REJOIN_STATUS_CANCELLED
            and source.status == ENROLLMENT_STATUS_RESTRICTED
            and target is not None
            and target.status == ENROLLMENT_STATUS_ACTIVE
        )
        if plan_ref.status not in {REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE} and not recoverable_cancel:
            raise ValueError(_("Only planned or active rejoin plans can be cancelled."))

        if plan_ref.status == REJOIN_STATUS_ACTIVE or recoverable_cancel:
            if target is None:
                raise ValueError(_("The active rejoin has no replacement enrollment."))
            target.delete()
            source.status = ENROLLMENT_STATUS_ACTIVE
            source.save(update_fields=["status", "updated_at"])

        if plan_ref.status != REJOIN_STATUS_CANCELLED:
            plan_ref.status = REJOIN_STATUS_CANCELLED
            plan_ref.save(update_fields=["status", "updated_at"])

        if not recoverable_cancel:
            PromotionDecision.objects.create(
                student=plan_ref.student,
                diploma_enrollment=source,
                from_semester=plan_ref.failed_semester,
                decision_type=PROMOTION_DECISION_REJOIN_CANCELLED,
                reason=reason or _("Rejoin plan cancelled."),
                decided_by=actor,
            )
        student = plan_ref.student
        semester = plan_ref.failed_semester
        source_cohort = source.cohort
        record_audit_event(
            actor=actor,
            action=ACTION_PROGRESSION_REJOIN_CANCELLED,
            target_type="semester_rejoin_plan",
            target_id=str(plan_ref.id),
            section=SECTION_PROGRESSION,
            target_label=" · ".join(
                part for part in (
                    student.full_name,
                    semester.diploma.title,
                    semester.title,
                    f"{source_cohort.name} → {target_cohort.name}" if target_cohort else source_cohort.name,
                ) if part
            ),
            metadata={
                "diploma_id": semester.diploma_id,
                "diploma_title": semester.diploma.title,
                "source_enrollment_id": source.id,
                "source_enrollment_status_before": source_status_before,
                "source_enrollment_status": source.status,
                "source_cohort_id": source_cohort.id,
                "source_cohort_name": source_cohort.name,
                "target_enrollment_id": target_enrollment_id,
                "target_enrollment_deleted": target_enrollment_id is not None,
                "target_cohort_id": target_cohort.id if target_cohort else None,
                "target_cohort_name": target_cohort.name if target_cohort else "",
                "semester_id": semester.id,
                "semester_title": semester.title,
                "status_before": previous_status,
                "status": plan_ref.status,
                "previous_enrollment_restored": recoverable_cancel,
                "reason": reason,
            },
            **_student_audit_subject(student),
        )

    return plan_ref
