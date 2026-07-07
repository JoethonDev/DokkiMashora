"""Progression services: score aggregation, semester evaluation, promotion, demotion, lock, retake, final exam, completion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
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
from apps.students.models import StudentProfile

from .constants import (
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_FINAL_EXAM_PENDING,
    DIPLOMA_COMPLETION_GRADUATED,
    DIPLOMA_COMPLETION_REQUIREMENTS_MET,
    PROMOTION_DECISION_AUTO_PROMOTE,
    PROMOTION_DECISION_COMPLETE,
    PROMOTION_DECISION_DEMOTE,
    PROMOTION_DECISION_GRADUATE,
    PROMOTION_DECISION_LOCK,
    PROMOTION_DECISION_MANUAL_PROMOTE,
    PROMOTION_DECISION_OVERRIDE_FAIL,
    PROMOTION_DECISION_OVERRIDE_PASS,
    PROMOTION_DECISION_RETAKE_ASSIGNED,
    PROMOTION_DECISION_RETAKE_CLEARED,
    PROMOTION_DECISION_UNLOCK,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_DEMOTED,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_NOT_STARTED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PROMOTED,
    SEMESTER_PROGRESS_RETAKE_REQUIRED,
    SEMESTER_PROGRESS_UNDER_REVIEW,
)
from .models import (
    DiplomaProgressionRule,
    FinalExamEligibility,
    PromotionDecision,
    SemesterProgressionRule,
    StudentDiplomaCompletion,
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
    ).prefetch_related("attempts")


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


def evaluate_semester_progress(
    student_profile: StudentProfile,
    diploma_enrollment: DiplomaEnrollment,
    semester: Semester,
    *,
    is_override: bool = False,
) -> StudentSemesterProgress:
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


def get_student_progress_detail(student_profile: StudentProfile):
    semesters = StudentSemesterProgress.objects.filter(
        student=student_profile,
    ).select_related("semester", "diploma_enrollment__cohort__diploma").order_by("semester__order")

    completions = StudentDiplomaCompletion.objects.filter(student=student_profile)
    eligibilities = FinalExamEligibility.objects.filter(student=student_profile)
    decisions = PromotionDecision.objects.filter(student=student_profile).order_by("-decided_at")[:20]

    return {
        "semesters": semesters,
        "completions": completions,
        "eligibilities": eligibilities,
        "decisions": decisions,
    }
