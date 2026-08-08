"""Progression services: score aggregation, semester evaluation, promotion, demotion, lock, retake, final exam, completion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import islice

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_RESTRICTED
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
from apps.academics.services import enroll_student_in_diploma
from apps.assessments.constants import (
    ASSESSMENT_TYPE_ADDITIONAL,
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_UNDER_REVIEW,
)
from apps.assessments.models import Assessment, AssessmentAttempt
from apps.attendance.services import get_attendance_summary
from apps.core.permissions import validate_score
from apps.students.models import StudentProfile

from .constants import (
    COMPONENT_TYPE_ATTENDANCE,
    COMPONENT_TYPE_HOMEWORK_ASSESSMENT,
    COMPONENT_TYPE_SEMESTER_EXAM,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_FINAL_EXAM_PENDING,
    DIPLOMA_COMPLETION_GRADUATED,
    DIPLOMA_COMPLETION_REQUIREMENTS_MET,
    PROMOTION_DECISION_AUTO_PROMOTE,
    PROMOTION_DECISION_ACADEMIC_YEAR_PROMOTE,
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
    REJOIN_STATUS_COMPLETED,
    REJOIN_STATUS_PLANNED,
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
    PromotionDecision,
    SemesterGradeComponent,
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

ASSESSMENT_TYPES_FOR_SCORING = [
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ASSESSMENT_TYPE_ADDITIONAL,
    ASSESSMENT_TYPE_HOMEWORK,
]


def get_semester_assessments(semester: Semester):
    return Assessment.objects.filter(
        semester=semester,
        is_active=True,
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
# Automatic promotion
# ---------------------------------------------------------------------------


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
    eval_result = evaluate_semester_progress(student, enrollment, current_semester)

    if eval_result.status == SEMESTER_PROGRESS_PASSED:
        if dry_run:
            result["promoted"] = True
            result["from_semester"] = current_semester.id
            result["to_semester"] = semesters[current_semester_idx + 1].id if current_semester_idx + 1 < len(semesters) else None
            result["reason"] = _("Would promote")
            return result

        next_semester = semesters[current_semester_idx + 1] if current_semester_idx + 1 < len(semesters) else None
        with transaction.atomic():
            eval_result.status = SEMESTER_PROGRESS_PROMOTED
            eval_result.promoted_at = timezone.now()
            eval_result.save()

            PromotionDecision.objects.create(
                student=student,
                diploma_enrollment=enrollment,
                from_semester=current_semester,
                to_semester=next_semester,
                decision_type=PROMOTION_DECISION_AUTO_PROMOTE,
                previous_status=SEMESTER_PROGRESS_PASSED,
                new_status=SEMESTER_PROGRESS_PROMOTED,
                automatic=True,
                reason=_("Auto-promoted based on passing scores."),
                score_snapshot=eval_result.calculated_score,
            )

            if next_semester:
                next_progress, __ = StudentSemesterProgress.objects.get_or_create(
                    diploma_enrollment=enrollment,
                    semester=next_semester,
                    defaults={
                        "student": student,
                        "cohort": enrollment.cohort,
                        "status": SEMESTER_PROGRESS_IN_PROGRESS,
                    },
                )

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
    override_failed: bool = False,
) -> StudentSemesterProgress:
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

    if not override_failed:
        semester_progress = StudentSemesterProgress.objects.filter(
            diploma_enrollment=enrollment,
            semester=target_semester,
        ).first()
        if semester_progress:
            if semester_progress.status in (SEMESTER_PROGRESS_LOCKED, SEMESTER_PROGRESS_FAILED, SEMESTER_PROGRESS_RETAKE_REQUIRED):
                if not reason:
                    raise ValueError(_("Reason is required when overriding failed/locked/retake status."))
            if semester_progress.status == SEMESTER_PROGRESS_LOCKED:
                raise ValueError(_("Student is locked. Unlock before promoting."))

    next_semester = None
    for i, sem in enumerate(semesters):
        if sem.id == expected_semester.id and i + 1 < len(semesters):
            next_semester = semesters[i + 1]
            break

    with transaction.atomic():
        existing = StudentSemesterProgress.objects.filter(
            diploma_enrollment=enrollment,
            semester=target_semester,
        ).first()
        previous_status = existing.status if existing else SEMESTER_PROGRESS_NOT_STARTED

        progress, __ = StudentSemesterProgress.objects.update_or_create(
            diploma_enrollment=enrollment,
            semester=target_semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_PROMOTED,
                "promoted_at": timezone.now(),
                "decided_by": actor,
                "decision_reason": reason,
            },
        )

        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=target_semester,
            to_semester=next_semester,
            decision_type=PROMOTION_DECISION_MANUAL_PROMOTE,
            previous_status=previous_status,
            new_status=SEMESTER_PROGRESS_PROMOTED,
            automatic=False,
            reason=reason or "Manual promotion.",
            decided_by=actor,
        )

        if next_semester:
            next_progress, __ = StudentSemesterProgress.objects.get_or_create(
                diploma_enrollment=enrollment,
                semester=next_semester,
                defaults={
                    "student": student_profile,
                    "cohort": enrollment.cohort,
                    "status": SEMESTER_PROGRESS_IN_PROGRESS,
                },
            )

    return progress


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
    # L6: a mutation must never accept a semester from another diploma.
    if semester.diploma_id != enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))

    progress, __ = StudentSemesterProgress.objects.get_or_create(
        diploma_enrollment=enrollment,
        semester=semester,
        defaults={
            "student": student_profile,
            "cohort": enrollment.cohort,
            "status": SEMESTER_PROGRESS_NOT_STARTED,
        },
    )

    previous_status = progress.status

    with transaction.atomic():
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

    rule = DiplomaProgressionRule.objects.filter(
        diploma=enrollment.cohort.diploma,
        is_active=True,
    ).first()

    if rule and rule.require_final_exam:
        eligibility = FinalExamEligibility.objects.filter(
            diploma_enrollment=enrollment,
        ).first()
        if not eligibility or not eligibility.eligible:
            raise ValueError(_("Student is not eligible for final exam."))
        if eligibility.passed_final_exam is not True:
            completion.status = DIPLOMA_COMPLETION_FINAL_EXAM_PENDING
            completion.save()
            return completion

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

    rule = DiplomaProgressionRule.objects.filter(
        diploma=enrollment.cohort.diploma,
        is_active=True,
    ).first()

    if rule and rule.require_final_exam:
        eligibility = FinalExamEligibility.objects.filter(
            diploma_enrollment=enrollment,
        ).first()
        if not eligibility or not eligibility.eligible:
            raise ValueError(_("Student is not eligible for final exam."))
        if eligibility.passed_final_exam is not True:
            completion.status = DIPLOMA_COMPLETION_FINAL_EXAM_PENDING
            completion.save()
            return completion

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


def sync_attendance_grade_item(diploma_enrollment, semester, actor=None) -> StudentGradeItem | None:
    component = _get_grade_component(diploma_enrollment, semester, COMPONENT_TYPE_ATTENDANCE)
    if component is None:
        return None

    summary = get_attendance_summary(diploma_enrollment, semester)
    attendance_percent = Decimal(str(summary["attendance_percent"]))

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

    assessments = Assessment.objects.filter(
        semester=semester,
        cohort=diploma_enrollment.cohort,
        assessment_type=ASSESSMENT_TYPE_HOMEWORK,
        is_active=True,
    )
    if not assessments:
        return None

    total_earned = Decimal("0.00")
    total_max = Decimal("0.00")

    for assessment in assessments:
        best = (
            AssessmentAttempt.objects.filter(
                assessment=assessment,
                student=diploma_enrollment.student.user,
                status__in=[ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED],
                score_points__isnull=False,
            )
            .order_by("-score_percent")
            .first()
        )
        if best is not None and best.score_points is not None and assessment.total_points:
            total_earned += best.score_points
            total_max += assessment.total_points

    score_percent = (total_earned / total_max * Decimal("100.00")) if total_max > 0 else None

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


def set_custom_activity_grade(component, diploma_enrollment, *, score, max_score, actor, notes="") -> StudentGradeItem:
    if component.component_type != "custom_activity":
        raise ValueError(_("Component is not a custom activity."))
    score_dec = validate_score(score)
    max_score_dec = Decimal(str(max_score))
    if max_score_dec <= 0:
        raise ValueError(_("max_score must be a positive number."))
    if score_dec > max_score_dec:
        raise ValueError(_("Score must be between 0 and max_score."))
    if component.cohort_id != diploma_enrollment.cohort_id:
        raise ValueError(_("Component does not belong to the same cohort as the enrollment."))
    now = timezone.now()
    score_percent = (score_dec / max_score_dec) * Decimal("100")

    item, _created = StudentGradeItem.objects.update_or_create(
        component=component,
        diploma_enrollment=diploma_enrollment,
        defaults={
            "student": diploma_enrollment.student,
            "manual_score": score_dec,
            "max_score": max_score_dec,
            "score_percent": score_percent,
            "graded_by": actor,
            "graded_at": now,
            "notes": notes,
        },
    )
    return item


def sync_all_grade_items(diploma_enrollment, semester, actor=None) -> dict:
    if semester.diploma_id != diploma_enrollment.cohort.diploma_id:
        raise ValueError(_("Semester does not belong to the enrollment's diploma."))
    with transaction.atomic():
        return {
            "semester_exam": sync_semester_exam_grade_item(diploma_enrollment, semester, actor=actor),
            "attendance": sync_attendance_grade_item(diploma_enrollment, semester, actor=actor),
            "homework_assessment": sync_homework_assessment_grade_item(diploma_enrollment, semester, actor=actor),
        }


# ---------------------------------------------------------------------------
# Interview handling
# ---------------------------------------------------------------------------


def request_interview(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    interview_at: str | None = None,
    notes: str = "",
) -> PromotionDecision:
    eval_result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
    if eval_result.status != SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION:
        raise ValueError(_("An interview can only be requested for a pending calculated pass."))
    metadata: dict = {
        "component_summary": {
            "total_percent": str(eval_result.total_percent) if eval_result.total_percent is not None else None,
            "all_required_passed": eval_result.all_required_passed,
        },
    }
    if interview_at:
        metadata["interview_at"] = interview_at
    if notes:
        metadata["notes"] = notes

    decision = PromotionDecision.objects.create(
        student=student_profile,
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type=PROMOTION_DECISION_REQUEST_INTERVIEW,
        automatic=False,
        reason=notes or "Interview requested.",
        decided_by=actor,
        metadata=metadata,
    )
    return decision


def confirm_pass(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    notes: str = "",
    skip_interview: bool = False,
) -> PromotionDecision:
    eval_result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
    if eval_result.status != SEMESTER_PROGRESS_PASSED_PENDING_CONFIRMATION:
        raise ValueError(_("Pass confirmation requires a pending calculated pass."))
    if not skip_interview:
        has_interview_pass = PromotionDecision.objects.filter(
            diploma_enrollment=enrollment,
            from_semester=semester,
            decision_type=PROMOTION_DECISION_INTERVIEW_PASS,
        ).exists()
        if not has_interview_pass:
            raise ValueError(_("Student has not passed the interview. Use skip_interview to bypass."))

    with transaction.atomic():
        decision = PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=semester,
            decision_type=PROMOTION_DECISION_CONFIRM_PASS,
            automatic=False,
            reason=notes or "Pass confirmed.",
            decided_by=actor,
        )
        StudentSemesterProgress.objects.update_or_create(
            diploma_enrollment=enrollment,
            semester=semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_PASSED_CONFIRMED,
                "passed_at": timezone.now(),
                "decided_by": actor,
            },
        )
    return decision


def confirm_interview_pass(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    notes: str = "",
) -> PromotionDecision:
    has_interview_request = PromotionDecision.objects.filter(
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type=PROMOTION_DECISION_REQUEST_INTERVIEW,
    ).exists()
    if not has_interview_request:
        raise ValueError(_("An interview request is required before recording an interview result."))
    decision = PromotionDecision.objects.create(
        student=student_profile,
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type=PROMOTION_DECISION_INTERVIEW_PASS,
        automatic=False,
        reason=notes or "Interview passed.",
        decided_by=actor,
    )
    return decision


def confirm_interview_fail(
    student_profile: StudentProfile,
    enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    actor=None,
    notes: str = "",
) -> PromotionDecision:
    with transaction.atomic():
        decision = PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=enrollment,
            from_semester=semester,
            decision_type=PROMOTION_DECISION_INTERVIEW_FAIL,
            automatic=False,
            reason=notes or "Interview failed.",
            decided_by=actor,
        )
        StudentSemesterProgress.objects.update_or_create(
            diploma_enrollment=enrollment,
            semester=semester,
            defaults={
                "student": student_profile,
                "cohort": enrollment.cohort,
                "status": SEMESTER_PROGRESS_FAILED,
                "failed_at": timezone.now(),
                "decided_by": actor,
            },
        )
    return decision


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_progression_dashboard_data(diploma_id: int | None = None, cohort_id: int | None = None):
    qs = StudentSemesterProgress.objects.select_related(
        "student__user", "semester", "cohort", "diploma_enrollment",
    )
    if diploma_id:
        qs = qs.filter(cohort__diploma_id=diploma_id)
    if cohort_id:
        qs = qs.filter(cohort_id=cohort_id)
    return qs


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
# Semester rejoin (failed-semester transfer)
# ---------------------------------------------------------------------------


def create_rejoin_plan(
    student_profile: StudentProfile,
    from_enrollment: DiplomaEnrollment,
    failed_semester: Semester,
    target_cohort: Cohort,
    *,
    actor,
    notes: str = "",
) -> SemesterRejoinPlan:
    if from_enrollment.student_id != student_profile.id:
        raise ValueError(_("Enrollment does not belong to this student."))
    if target_cohort.diploma_id != from_enrollment.cohort.diploma_id:
        raise ValueError(_("Target cohort must belong to the same diploma as the enrollment."))
    if failed_semester.diploma_id != target_cohort.diploma_id:
        raise ValueError(_("Failed semester must belong to the same diploma as the target cohort."))
    # L8: a rejoin plan requires failed/retake-eligible progress for the
    # semester — never a passing or untouched semester.
    failed_progress = StudentSemesterProgress.objects.filter(
        diploma_enrollment=from_enrollment,
        semester=failed_semester,
    ).first()
    if failed_progress is None or failed_progress.status not in (
        SEMESTER_PROGRESS_FAILED,
        SEMESTER_PROGRESS_RETAKE_REQUIRED,
    ):
        raise ValueError(_("Rejoin requires a failed or retake-required semester progress."))
    if SemesterRejoinPlan.objects.filter(
        from_enrollment=from_enrollment,
        failed_semester=failed_semester,
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    ).exists():
        raise ValueError(_("An active or planned rejoin plan already exists for this enrollment and semester."))

    with transaction.atomic():
        plan = SemesterRejoinPlan.objects.create(
            student=student_profile,
            from_enrollment=from_enrollment,
            failed_semester=failed_semester,
            target_cohort=target_cohort,
            planned_by=actor,
            notes=notes,
        )
        PromotionDecision.objects.create(
            student=student_profile,
            diploma_enrollment=from_enrollment,
            from_semester=failed_semester,
            decision_type=PROMOTION_DECISION_REJOIN_PLANNED,
            reason=notes or _("Rejoin plan created."),
            decided_by=actor,
        )

    return plan


def activate_rejoin_plan(plan, *, actor) -> SemesterRejoinPlan:
    if plan.status != REJOIN_STATUS_PLANNED:
        raise ValueError(_("Only planned rejoin plans can be activated."))

    if plan.from_enrollment.cohort_id == plan.target_cohort_id:
        raise ValueError(_("Source and target cohort are the same. Rejoin requires a different cohort."))

    with transaction.atomic():
        # Deactivate the source enrollment so the student has only one active path.
        source = plan.from_enrollment
        if source.status == ENROLLMENT_STATUS_ACTIVE:
            source.status = ENROLLMENT_STATUS_RESTRICTED
            source.save(update_fields=["status", "updated_at"])

        new_enrollment = enroll_student_in_diploma(plan.student, plan.target_cohort)
        plan.to_enrollment = new_enrollment
        plan.status = REJOIN_STATUS_ACTIVE
        plan.activated_at = timezone.now()
        plan.save(update_fields=["to_enrollment", "status", "activated_at", "updated_at"])

        PromotionDecision.objects.create(
            student=plan.student,
            diploma_enrollment=new_enrollment,
            from_semester=plan.failed_semester,
            decision_type=PROMOTION_DECISION_REJOIN_ACTIVATED,
            reason=_("Rejoin plan activated."),
            decided_by=actor,
        )

        _copy_passed_semester_progress(plan)

    return plan


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


def complete_rejoin_plan(plan, *, actor) -> SemesterRejoinPlan:
    if plan.status != REJOIN_STATUS_ACTIVE:
        raise ValueError(_("Only active rejoin plans can be completed."))

    plan.status = REJOIN_STATUS_COMPLETED
    plan.save(update_fields=["status", "updated_at"])
    return plan


def _copy_passed_semester_progress(plan):
    passed_statuses = [
        SEMESTER_PROGRESS_PASSED,
        SEMESTER_PROGRESS_PROMOTED,
        SEMESTER_PROGRESS_COMPLETED,
        SEMESTER_PROGRESS_PASSED_CONFIRMED,
    ]
    old_progress = StudentSemesterProgress.objects.filter(
        diploma_enrollment=plan.from_enrollment,
        student=plan.student,
        semester__order__lt=plan.failed_semester.order,
        status__in=passed_statuses,
    ).select_related("semester")

    for prog in old_progress:
        StudentSemesterProgress.objects.get_or_create(
            diploma_enrollment=plan.to_enrollment,
            semester=prog.semester,
            defaults={
                "student": plan.student,
                "cohort": plan.target_cohort,
                "status": SEMESTER_PROGRESS_COMPLETED,
                "calculated_score": prog.calculated_score,
                "calculated_at": prog.calculated_at,
                "passed_at": prog.passed_at,
            },
        )
