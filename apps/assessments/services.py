"""Business logic services for assessments, scoring, attempts, and grading."""

from __future__ import annotations

import random
import re
import secrets
import string
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Q, Subquery, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import CourseEnrollment, DiplomaEnrollment, StudentClusterMembership
from apps.academics.services import get_student_diploma_access, get_visible_semester_ids_by_enrollment
from apps.applications.constants import DELIVERY_MODE_ONLINE as APPLICATION_DELIVERY_MODE_ONLINE
from apps.applications.constants import PROGRAM_APP_STATUS_REJECTED, PROGRAM_TYPE_DIPLOMA
from apps.applications.models import ProgramApplication
from apps.audit.constants import (
    ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED,
    ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
    ACTION_EXAM_ACCESS_OVERRIDE_USED,
    ACTION_EXAM_DELETED,
    ACTION_GRADE_CHANGED,
    ACTION_GRADE_HIDDEN,
    ACTION_GRADE_RELEASED,
    ACTION_STUDENT_ACCESS_CHANGE,
)
from apps.audit.services import record_audit_event
from apps.core.permissions import assessment_staff_scope_filter, is_admin, is_moderator, validate_score
from apps.core.scoring import compute_percent, is_passing, mcq_score
from apps.storage.services import record_deferred_storage_deletion, storage_service
from apps.students.models import StudentProfile

from . import write_behind as write_behind_protocol
from .constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_EXPIRED,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    EXAM_OVERRIDE_ACTIVE,
    EXAM_OVERRIDE_CANCELLED,
    EXAM_OVERRIDE_USED,
    EXAM_REVIEW_STATUS_FAILED,
    EXAM_REVIEW_STATUS_NOT_REVIEWED,
    EXAM_REVIEW_STATUS_PASSED,
    HOMEWORK_STATUS_GRADED,
    HOMEWORK_STATUS_RELEASED,
    HOMEWORK_STATUS_SUBMITTED,
    HOMEWORK_STATUS_UNDER_REVIEW,
    HOMEWORK_STATUS_UPLOADED,
    LEGACY_STATUS_ACTIVE,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    RELEASE_MODE_AUTOMATIC,
    REVIEWER_ACCESS_BOTH,
    REVIEWER_ACCESS_OFFLINE,
    REVIEWER_ACCESS_ONLINE,
    REVIEWER_CLAIM_FINALIZED,
    REVIEWER_CLAIM_IN_PROGRESS,
    REVIEWER_SCOPE_COHORT,
    REVIEWER_SCOPE_EXAM,
    REVIEWER_SCOPE_SEMESTER,
    SNAPSHOT_QUESTION_SOURCE_BANK,
    SNAPSHOT_QUESTION_SOURCE_MANUAL,
    SUBMISSION_STATUS_FAILED,
    SUBMISSION_STATUS_PENDING,
    SUBMISSION_STATUS_PROCESSED,
    SUBMISSION_STATUS_PROCESSING,
    SUBMISSION_STATUS_UNDER_REVIEW,
)
from .models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentAttemptChoice,
    AssessmentAttemptQuestion,
    AssessmentAttemptSnapshotAnswer,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSubmission,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkAttachment,
    HomeworkSubmission,
    OfflineSectionGrade,
    ReviewerExamClaim,
    SemesterExamAccessOverride,
    SemesterExamCode,
)

if TYPE_CHECKING:
    pass

HOMEWORK_ALLOWED_EXTENSIONS = [".pdf", ".docx"]


def sync_course_quiz_total_points(assessment):
    """Persist the total raw points defined by a course quiz's questions."""
    if not assessment.pk or assessment.assessment_type != ASSESSMENT_TYPE_QUIZ or not assessment.course_version_id:
        return assessment.total_points
    total = assessment.questions.aggregate(total=Sum("points"))["total"] or Decimal("0.00")
    if assessment.total_points != total:
        assessment.total_points = total
        assessment.save(update_fields=["total_points", "updated_at"])
    return total


def delete_assessment_record(*, assessment, actor):
    """Delete an unused active assessment while preserving academic history.

    Legacy assessments and assessments with student attempts or homework
    submissions are immutable history. They must be deactivated or archived,
    not hard-deleted. A clean active assessment is authored configuration and
    may be removed together with its questions, sections, and operational rows.
    """
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    if assessment.attempts.exists() or assessment.homework_submissions.exists():
        raise ValueError(
            _("This assessment cannot be deleted because student attempt or submission history exists. Deactivate it instead.")
        )
    with transaction.atomic():
        record_audit_event(
            actor=actor,
            action=ACTION_EXAM_DELETED,
            target_type="assessment",
            target_id=str(assessment.id),
            metadata={
                "title": assessment.title,
                "assessment_type": assessment.assessment_type,
            },
        )
        assessment.delete()


@dataclass
class AssessmentAnswerData:
    question_id: int
    selected_choice_id: int | None = None
    written_text: str = ""


# ---------------------------------------------------------------------------
# Access & enrollment checks
# ---------------------------------------------------------------------------


def ensure_active_assessment(assessment: Assessment) -> None:
    """Raise ValueError when the assessment is quarantined legacy data.

    Legacy/archived/review-required rows are preserved read-only and must not
    enter active student, grading, release, or scoring workflows (P2-01).
    """
    if getattr(assessment, "legacy_status", LEGACY_STATUS_ACTIVE) != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and cannot be used."))
    invalid_question = assessment.questions.annotate(
        choice_count=Count("choices"),
        correct_count=Count("choices", filter=Q(choices__is_correct=True)),
    ).filter(
        Q(
            question_type=QUESTION_TYPE_MCQ,
            choice_count__lt=2,
        )
        | (Q(question_type=QUESTION_TYPE_MCQ) & ~Q(correct_count=1))
        | Q(
            question_type=QUESTION_TYPE_WRITTEN,
            choice_count__gt=0,
        )
    ).exists()
    if invalid_question:
        raise ValueError(_("This assessment has an invalid question configuration."))


def has_active_enrollment(student, assessment: Assessment) -> bool:
    """Check student has an active, unexpired enrollment for the assessment's scope (E5).

    Mirrors the canonical academic predicate: the exact enrollment must be
    active and its access window must not have expired.
    """
    now = timezone.now()
    cv = assessment.course_version
    if cv:
        return CourseEnrollment.objects.filter(
            student__user=student,
            course_version=cv,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).exists()
    # Diploma-scoped assessment (no course_version, has cohort+semester)
    if assessment.cohort and assessment.semester:
        return DiplomaEnrollment.objects.filter(
            student__user=student,
            cohort=assessment.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).exists()
    return True


def check_delivery_mode(student, assessment: Assessment) -> None:
    """Raise ValueError if the authoritative application is offline."""
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
        return
    if assessment.cohort_id is None:
        return
    application = ProgramApplication.objects.filter(
        student__user=student,
        program_type=PROGRAM_TYPE_DIPLOMA,
        cohort_id=assessment.cohort_id,
    ).exclude(
        status=PROGRAM_APP_STATUS_REJECTED,
    ).order_by("-created_at", "-id").first()
    if application and application.delivery_mode == APPLICATION_DELIVERY_MODE_ONLINE:
        return
    if application and application.delivery_mode == DELIVERY_MODE_OFFLINE:
        raise ValueError(_("This exam is offline-only and cannot be taken online."))


def check_assessment_access(
    student,
    assessment: Assessment,
    *,
    existing_attempt: bool = False,
    allow_late_submission: bool = False,
) -> None:
    """Raise ValueError if the student cannot access the assessment."""
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    if getattr(assessment, "legacy_status", LEGACY_STATUS_ACTIVE) != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and cannot be accessed."))
    enrollment = None
    if assessment.cohort_id and assessment.semester_id:
        enrollment = DiplomaEnrollment.objects.select_related("cohort__diploma").filter(
            student__user=student,
            cohort_id=assessment.cohort_id,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
        ).first()
        if not enrollment or assessment.semester_id not in {
            semester.id for semester in get_student_diploma_access(enrollment)["visible_semesters"]
        }:
            raise ValueError(_("This semester is not available yet."))
    elif not has_active_enrollment(student, assessment):
        raise ValueError(_("You do not have an active enrollment for this course."))

    check_delivery_mode(student, assessment)

    override = get_active_assessment_access_override(
        student,
        assessment,
        now,
        enrollment=enrollment,
    )
    if override and override.open_at <= now <= override.close_at:
        if existing_attempt or override.used_count < override.max_attempts:
            return
    if assessment.open_at and now < assessment.open_at:
        raise ValueError(_("Assessment has not opened yet."))
    if assessment.close_at and now > assessment.close_at and not allow_late_submission:
        raise ValueError(_("Assessment has already closed."))
    if not existing_attempt:
        attempt_count = AssessmentAttempt.objects.filter(
            assessment=assessment, student=student,
        ).exclude(
            status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
        ).count()
        if attempt_count >= assessment.max_attempts:
            raise ValueError(_("You have reached the maximum number of attempts."))


def check_attempt_submission_access(
    attempt: AssessmentAttempt,
    *,
    allow_late_submission: bool = False,
) -> None:
    """Revalidate ownership, enrollment, scope, schedule, and timer for a live attempt."""
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Attempt is not in progress."))
    assessment = attempt.assessment
    check_assessment_access(
        attempt.student,
        assessment,
        existing_attempt=True,
        allow_late_submission=allow_late_submission,
    )
    now = timezone.now()
    if attempt.started_at and assessment.duration_minutes:
        deadline = attempt.started_at + timezone.timedelta(minutes=assessment.duration_minutes)
        if now > deadline:
            raise ValueError(_("Assessment duration has expired."))


# ---------------------------------------------------------------------------
# Attempt lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_assessment(student, assessment: Assessment) -> AssessmentAttempt:
    override = get_active_assessment_access_override(student, assessment)
    now = timezone.now()
    exceptional_access_start = bool(
        override
        and override.status == EXAM_OVERRIDE_ACTIVE
        and override.open_at <= now <= override.close_at
        and override.used_count < override.max_attempts
    )

    with transaction.atomic():
        # Serialize concurrent starts for the same student on one row so the
        # attempt-number derivation (count()+1) is deterministic under races
        # (RV-006). The caller passes the User; lock the owning profile.
        StudentProfile.objects.select_for_update().get(user_id=student.pk)

        if exceptional_access_start:
            # Exceptional access is a fresh assessment run, but the original
            # attempt is immutable history. Preserve it and let the new row receive
            # the next attempt number (normally attempt 2).
            if AssessmentAttempt.objects.filter(
                assessment=assessment,
                student=student,
                status=ATTEMPT_STATUS_IN_PROGRESS,
            ).exists():
                raise ValueError(_("Finish the current attempt before starting exceptional access."))
            existing = None
        else:
            snapshot_exists = AssessmentAttemptQuestion.objects.filter(
                attempt_id=OuterRef("pk"),
            )
            existing = AssessmentAttempt.objects.filter(
                assessment=assessment,
                student=student,
                status=ATTEMPT_STATUS_IN_PROGRESS,
            ).annotate(has_snapshot=Exists(snapshot_exists)).first()
        if existing:
            check_attempt_submission_access(
                existing,
                allow_late_submission=assessment.allow_late_submission,
            )
            if _is_diploma_random(assessment) and not existing.has_snapshot:
                generate_diploma_assessment_snapshot(existing)
            return existing
        snapshot_exists = AssessmentAttemptQuestion.objects.filter(
            attempt_id=OuterRef("pk"),
        )
        assigned = AssessmentAttempt.objects.select_for_update().filter(
            assessment=assessment,
            student=student,
            status=ATTEMPT_STATUS_ASSIGNED,
        ).annotate(has_snapshot=Exists(snapshot_exists)).order_by("attempt_number").first()
        check_assessment_access(student, assessment, existing_attempt=bool(assigned))
        if assigned:
            assigned.status = ATTEMPT_STATUS_IN_PROGRESS
            assigned.started_at = now
            assigned.save(update_fields=["status", "started_at", "updated_at"])
            if _is_diploma_random(assessment) and not assigned.has_snapshot:
                generate_diploma_assessment_snapshot(assigned)
            return assigned
        override = get_active_assessment_access_override(student, assessment)
        if override:
            attempt_number = AssessmentAttempt.objects.filter(
                assessment=assessment, student=student,
            ).count() + 1
            if override.used_count >= override.max_attempts:
                raise ValueError(_("You have reached the maximum number of attempts."))
        else:
            attempt_number = AssessmentAttempt.objects.filter(
                assessment=assessment, student=student,
            ).count() + 1
            if attempt_number > assessment.max_attempts:
                raise ValueError(_("You have reached the maximum number of attempts."))
        attempt = AssessmentAttempt.objects.create(
            assessment=assessment,
            student=student,
            attempt_number=attempt_number,
            status=ATTEMPT_STATUS_IN_PROGRESS,
            started_at=timezone.now(),
        )
        if override:
            attempt.is_exceptional = True
            attempt.save(update_fields=["is_exceptional"])
            consume_assessment_access_override(override, attempt)
        if _is_diploma_random(assessment):
            generate_diploma_assessment_snapshot(attempt)
        return attempt


def prepare_diploma_assessment_attempts(assessment: Assessment, limit: int = 50) -> tuple[int, int]:
    """Prepare online enrolled students, including exceptional online access."""
    now = timezone.now()
    latest_delivery_mode = ProgramApplication.objects.filter(
        student_id=OuterRef("pk"),
        program_type=PROGRAM_TYPE_DIPLOMA,
        cohort_id=assessment.cohort_id,
    ).exclude(
        status=PROGRAM_APP_STATUS_REJECTED,
    ).order_by("-created_at", "-id").values("delivery_mode")[:1]
    students = StudentProfile.objects.filter(
        diploma_enrollments__cohort_id=assessment.cohort_id,
        diploma_enrollments__status=ENROLLMENT_STATUS_ACTIVE,
    ).filter(
        Q(diploma_enrollments__expires_at__isnull=True) | Q(diploma_enrollments__expires_at__gt=now),
    ).exclude(
        user__assessment_attempts__assessment=assessment,
        user__assessment_attempts__status__in=[ATTEMPT_STATUS_ASSIGNED, ATTEMPT_STATUS_IN_PROGRESS],
    ).annotate(
        application_delivery_mode=Subquery(latest_delivery_mode),
    ).filter(
        Q(application_delivery_mode=APPLICATION_DELIVERY_MODE_ONLINE)
        | Q(application_delivery_mode__isnull=True),
    ).select_related("user").order_by("pk")[:limit]
    created = 0
    skipped = 0
    for profile in students:
        with transaction.atomic():
            attempts = AssessmentAttempt.objects.select_for_update().filter(
                assessment=assessment, student=profile.user,
            )
            if attempts.filter(status__in=[ATTEMPT_STATUS_ASSIGNED, ATTEMPT_STATUS_IN_PROGRESS]).exists():
                skipped += 1
                continue
            attempt_number = attempts.exclude(
                status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
            ).count() + 1
            if attempt_number > assessment.max_attempts:
                skipped += 1
                continue
            attempt = AssessmentAttempt.objects.create(
                assessment=assessment,
                student=profile.user,
                attempt_number=attempt_number,
                status=ATTEMPT_STATUS_ASSIGNED,
            )
            generate_diploma_assessment_snapshot(attempt)
            created += 1
    return created, skipped


def _is_diploma_random(assessment: Assessment) -> bool:
    """True if this is an online Diploma Semester Exam."""
    return (
        assessment.course_version_id is None
        and bool(assessment.semester_id and assessment.cohort_id)
        and assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
    )


def generate_diploma_assessment_snapshot(attempt: AssessmentAttempt) -> None:
    """Generate per-student immutable snapshot questions for a diploma random assessment.

    Idempotent: if snapshot questions already exist, returns early.
    Raises ValueError if any section's lecture has insufficient active MCQs.
    """
    if attempt.snapshot_questions.exists():
        return

    assessment = attempt.assessment
    sections = list(assessment.lecture_sections.order_by("order").select_related("lecture"))
    if not sections:
        raise ValueError(_("Assessment has no lecture sections configured."))

    with transaction.atomic():
        attempt_ref = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt_ref.snapshot_questions.exists():
            return

        order = 0

        for section in sections:
            # ponytail: manual questions copied as-is, no random selection needed
            for mq in section.manual_questions.order_by("order"):
                order += 1
                AssessmentAttemptQuestion.objects.create(
                    attempt=attempt_ref,
                    section=section,
                    source_lecture=section.lecture,
                    source_manual_question=mq,
                    source_type=SNAPSHOT_QUESTION_SOURCE_MANUAL,
                    text_snapshot=mq.text,
                    question_type=mq.question_type,
                    points=mq.points,
                    order=order,
                    required=mq.required,
                    rubric_snapshot=mq.rubric,
                )

            bank_qs = list(DiplomaLectureQuestion.objects.filter(
                lecture=section.lecture, active=True, question_type=QUESTION_TYPE_MCQ,
            ))
            if len(bank_qs) < section.mcq_count:
                raise ValueError(
                    _("Section '%(lecture)s' needs %(req)d MCQs, %(avail)d available.")
                    % {"lecture": section.lecture.title, "req": section.mcq_count, "avail": len(bank_qs)}
                )

            for q in random.sample(bank_qs, section.mcq_count):
                order += 1
                sq = AssessmentAttemptQuestion.objects.create(
                    attempt=attempt_ref,
                    section=section,
                    source_lecture=section.lecture,
                    source_bank_question=q,
                    source_type=SNAPSHOT_QUESTION_SOURCE_BANK,
                    text_snapshot=q.text,
                    question_type=q.question_type,
                    points=q.points,
                    order=order,
                    required=True,
                )
                for c in DiplomaLectureChoice.objects.filter(question=q).order_by("order"):
                    AssessmentAttemptChoice.objects.create(
                        attempt_question=sq,
                        text_snapshot=c.text,
                        is_correct_snapshot=c.is_correct,
                        order=c.order,
                    )

        _ensure_snapshot_answers(attempt_ref)

    def _post_snapshot_cache():
        _cache_snapshot_questions(attempt_ref)
        # Build prepared gzip chunks (render once, gzip once)
        try:
            from apps.assessments.prepared_take import build_prepared_take

            build_prepared_take(attempt_ref)
        except Exception:
            # Prepared cache is rebuildable; correctness remains via DB fallback
            pass

    transaction.on_commit(_post_snapshot_cache)


def _ensure_snapshot_answers(attempt: AssessmentAttempt) -> None:
    """Create empty snapshot answer for each question that lacks one."""
    existing = set(
        AssessmentAttemptSnapshotAnswer.objects.filter(
            attempt_question__attempt=attempt,
        ).values_list("attempt_question_id", flat=True)
    )
    to_create = []
    for sq in attempt.snapshot_questions.only("pk").all():
        if sq.pk not in existing:
            to_create.append(AssessmentAttemptSnapshotAnswer(attempt_question=sq))
    if to_create:
        AssessmentAttemptSnapshotAnswer.objects.bulk_create(to_create)


def _snapshot_questions_cache_key(attempt_id: int) -> str:
    return f"assessment-attempt:{attempt_id}:snapshot-questions:v1"


def _cache_snapshot_questions(attempt: AssessmentAttempt) -> None:
    """Cache the immutable question/choice metadata used by autosave."""
    try:
        questions = attempt.snapshot_questions.prefetch_related("choices").order_by("order")
        mapping = {
            question.order: {
                "id": question.pk,
                "type": question.question_type,
                "choice_ids": [choice.pk for choice in question.choices.all()],
            }
            for question in questions
        }
        timeout = max(300, ((attempt.assessment.duration_minutes or 60) * 60) + 300)
        cache.set(_snapshot_questions_cache_key(attempt.pk), mapping, timeout=timeout)
    except Exception:
        # Redis is an optimization only; the database remains authoritative.
        return


def _get_snapshot_questions(attempt: AssessmentAttempt) -> list[AssessmentAttemptQuestion]:
    """Load snapshot questions using cached ids, falling back to the database."""
    try:
        cached = cache.get(_snapshot_questions_cache_key(attempt.pk))
    except Exception:
        cached = None
    if not cached:
        return list(attempt.snapshot_questions.order_by("order"))

    question_ids = [item["id"] for item in cached.values()]
    questions = AssessmentAttemptQuestion.objects.filter(
        attempt=attempt, pk__in=question_ids,
    ).order_by("order")
    return list(questions) or list(attempt.snapshot_questions.order_by("order"))


def _get_snapshot_question_metadata(attempt: AssessmentAttempt) -> list[dict]:
    """Load autosave validation metadata without re-reading immutable rows."""
    try:
        cached = cache.get(_snapshot_questions_cache_key(attempt.pk))
    except Exception:
        cached = None
    if cached:
        return [
            {
                "order": int(order),
                "question_type": item["type"],
                "choice_ids": set(item.get("choice_ids", [])),
            }
            for order, item in cached.items()
        ]

    questions = attempt.snapshot_questions.prefetch_related("choices").order_by("order")
    return [
        {
            "order": question.order,
            "question_type": question.question_type,
            "choice_ids": {choice.pk for choice in question.choices.all()},
        }
        for question in questions
    ]


def submit_assessment(attempt: AssessmentAttempt, answers: list[AssessmentAnswerData]) -> AssessmentAttempt:
    if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
        raise ValueError(_("Assessment can only be submitted from in_progress status."))
    assessment = attempt.assessment
    ensure_active_assessment(assessment)
    check_attempt_submission_access(
        attempt,
        allow_late_submission=assessment.allow_late_submission,
    )
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    questions = list(assessment.questions.select_related("assessment").prefetch_related("choices"))
    if not questions:
        # An empty question set must never be finalized as a zero-point grade (E8).
        raise ValueError(_("This assessment has no questions available."))
    has_ungraded = False
    total_score = Decimal("0.00")
    total_possible = sum(q.points for q in questions)
    answers_by_question = {answer_data.question_id: answer_data for answer_data in answers}

    with transaction.atomic():
        # Lock the attempt so concurrent submits serialize; re-check status
        # after acquiring the lock (RV-006, mirrors submit_diploma_assessment).
        attempt = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Assessment can only be submitted from in_progress status."))
        for question in questions:
            answer_data = answers_by_question.get(
                question.id,
                AssessmentAnswerData(question_id=question.id),
            )
            if question.question_type == QUESTION_TYPE_MCQ:
                if answer_data.written_text:
                    raise ValueError(_("MCQ answer must not carry written text."))
                choice = _resolve_choice(question, answer_data.selected_choice_id)
                score = _score_mcq_answer(choice, question, assessment)
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt, question=question,
                    defaults={
                        "selected_choice": choice,
                        "written_text": "",
                        "score": score,
                        "feedback": "",
                    },
                )
                total_score += score
            elif question.question_type == QUESTION_TYPE_WRITTEN:
                if answer_data.selected_choice_id is not None:
                    raise ValueError(_("Written answer must not carry a selected choice."))
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt, question=question,
                    defaults={
                        "selected_choice": None,
                        "written_text": answer_data.written_text,
                        "score": None,
                        "feedback": "",
                    },
                )
                has_ungraded = True
        status = ATTEMPT_STATUS_UNDER_REVIEW if has_ungraded else ATTEMPT_STATUS_GRADED
        attempt.status = status
        attempt.submitted_at = now
        attempt.score_points = total_score
        attempt.score_percent = compute_percent(total_score, total_possible)
        if not has_ungraded:
            attempt.graded_at = now
            attempt.is_pass = is_passing(attempt.score_percent, assessment.passing_score)
            if attempt.is_pass is None:
                attempt.is_pass = False
        attempt.save(
            update_fields=[
                "status", "submitted_at", "score_points", "score_percent",
                "graded_at", "is_pass",
            ],
        )
        # P2-04: automatic release fires only after the authoritative grade
        # transaction commits.
        if not has_ungraded and assessment.release_mode == RELEASE_MODE_AUTOMATIC:
            transaction.on_commit(lambda aid=attempt.pk: _maybe_auto_release_attempt(aid))
    return attempt


def submit_diploma_assessment(attempt: AssessmentAttempt, answer_data: dict) -> AssessmentAttempt:
    """Submit a diploma random assessment attempt.

    answer_data maps ``q_{order}`` -> selected_choice_id (MCQ) or written_text (written).
    """
    if attempt.status not in (ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_SUBMITTED):
        raise ValueError(_("Attempt is not in progress."))

    assessment = attempt.assessment
    ensure_active_assessment(assessment)
    if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
        check_attempt_submission_access(
            attempt,
            allow_late_submission=assessment.allow_late_submission,
        )
    now = timezone.now()

    with transaction.atomic():
        attempt_ref = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt_ref.status not in (ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_SUBMITTED):
            return attempt_ref

        if write_behind_protocol.enabled() and attempt_ref.status == ATTEMPT_STATUS_IN_PROGRESS:
            write_behind_protocol.drain(
                attempt_ref.pk,
                lambda values: diploma_autosave_attempt(
                    attempt_ref, values, write_behind=False,
                ),
            )

        # Load the snapshot question set with prefetched choices and the
        # existing answers once (P3-01): no per-question queries.
        snapshot_questions = list(
            attempt_ref.snapshot_questions.prefetch_related("choices").order_by("order")
        )
        if not snapshot_questions:
            # An empty question set must never be finalized as a zero-point grade (E8).
            raise ValueError(_("This assessment has no questions available."))

        existing_answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt_ref,
            )
        }
        choices_by_key = {
            (sq.pk, choice.pk): choice
            for sq in snapshot_questions
            for choice in sq.choices.all()
        }
        from apps.assessments.prepared_take import get_valid_choices

        cached_valid_choices = get_valid_choices(attempt_ref.pk) or {}
        cached_valid_by_order = cached_valid_choices.get("by_order", {})

        has_written = False
        total_earned = Decimal("0")
        total_possible = Decimal("0")
        new_answers = []
        answers_to_save = []

        for sq in snapshot_questions:
            answer = existing_answers.get(sq.pk)
            if answer is None:
                answer = AssessmentAttemptSnapshotAnswer(attempt_question=sq)
                new_answers.append(answer)
            else:
                answers_to_save.append(answer)
            order_key = f"q_{sq.order}"

            if sq.question_type == QUESTION_TYPE_MCQ:
                raw = answer_data.get(order_key, "")
                if raw:
                    try:
                        allowed_ids = cached_valid_by_order.get(str(sq.order), [])
                        if allowed_ids and int(raw) not in allowed_ids:
                            raise ValueError
                        choice = choices_by_key[(sq.pk, int(raw))]
                    except (KeyError, ValueError):
                        raise ValueError(_("Invalid choice for question %(order)d.") % {"order": sq.order}) from None
                    answer.selected_choice = choice
                    answer.score = mcq_score(sq.points, bool(choice.is_correct_snapshot), assessment.penalty_percent)
                    total_earned += answer.score
                else:
                    answer.selected_choice = None
                    answer.score = Decimal("0")
                total_possible += sq.points

            elif sq.question_type == QUESTION_TYPE_WRITTEN:
                text = answer_data.get(order_key, "")
                answer.written_text = text
                answer.score = None
                if text.strip():
                    has_written = True
                total_possible += sq.points

        if new_answers:
            AssessmentAttemptSnapshotAnswer.objects.bulk_create(new_answers)
        for answer in answers_to_save:
            answer.save()

        attempt_ref.score_points = total_earned
        attempt_ref.score_percent = compute_percent(total_earned, total_possible)
        is_pass = is_passing(attempt_ref.score_percent, assessment.passing_score)
        attempt_ref.is_pass = is_pass if is_pass is not None else False
        if has_written:
            attempt_ref.status = ATTEMPT_STATUS_UNDER_REVIEW
        else:
            attempt_ref.status = ATTEMPT_STATUS_GRADED
            attempt_ref.graded_at = now
        if attempt_ref.submitted_at is None:
            attempt_ref.submitted_at = now
        attempt_ref.save()

    return attempt_ref


_SUBMISSION_ID_RE = re.compile(r"\A[0-9a-fA-F-]{16,64}\Z")


def valid_diploma_submission_id(value: str | None) -> bool:
    return bool(value and _SUBMISSION_ID_RE.fullmatch(value))


def accept_diploma_submission(
    attempt: AssessmentAttempt,
    submission_id: str,
    answer_data: dict[str, str],
) -> AssessmentSubmission:
    """Atomically close an online attempt and persist its final payload.

    Celery is deliberately not part of this transaction.  The database row
    remains recoverable if the post-commit wake-up cannot be delivered.
    """
    if not valid_diploma_submission_id(submission_id):
        raise ValueError(_("Invalid submission ID."))

    with transaction.atomic():
        attempt_ref = AssessmentAttempt.objects.select_for_update().select_related(
            "assessment", "student",
        ).get(pk=attempt.pk)
        existing_submission = AssessmentSubmission.objects.select_for_update().filter(
            Q(attempt=attempt_ref) | Q(submission_id=submission_id),
        ).first()
        if existing_submission and existing_submission.attempt_id != attempt_ref.pk:
            raise ValueError(_("This submission ID belongs to another attempt."))
        if existing_submission:
            if existing_submission.submission_id != submission_id:
                raise ValueError(_("This attempt already has a different submission."))
            return existing_submission

        if attempt_ref.status != ATTEMPT_STATUS_IN_PROGRESS:
            raise ValueError(_("Attempt is not in progress."))
        ensure_active_assessment(attempt_ref.assessment)
        check_attempt_submission_access(
            attempt_ref,
            allow_late_submission=attempt_ref.assessment.allow_late_submission,
        )

        now = timezone.now()
        submission = AssessmentSubmission.objects.create(
            submission_id=submission_id,
            attempt=attempt_ref,
            final_payload={"answers": answer_data},
            accepted_at=now,
            processing_status=SUBMISSION_STATUS_PENDING,
        )
        attempt_ref.status = ATTEMPT_STATUS_SUBMITTED
        attempt_ref.submitted_at = now
        attempt_ref.save(update_fields=["status", "submitted_at", "updated_at"])
        transaction.on_commit(
            lambda sid=submission.submission_id: _dispatch_diploma_submission(sid),
        )
        transaction.on_commit(
            lambda aid=attempt_ref.pk: write_behind_protocol.close(aid),
        )
        transaction.on_commit(
            lambda aid=attempt_ref.pk: _delete_prepared_diploma_take(aid),
        )
    return submission


def _dispatch_diploma_submission(submission_id: str) -> None:
    from .tasks import process_diploma_submission

    try:
        delay = max(0.0, float(getattr(settings, "ASSESSMENT_SUBMISSION_GRADING_DELAY", 0.0)))
        if delay:
            process_diploma_submission.apply_async(args=[submission_id], countdown=delay)
        else:
            process_diploma_submission.delay(submission_id)
    except Exception:
        # The durable pending row is recovered by the periodic sweep.
        pass


def _delete_prepared_diploma_take(attempt_id: int) -> None:
    from apps.assessments.prepared_take import delete_prepared_take

    try:
        delete_prepared_take(attempt_id)
    except Exception:
        # Prepared Take data is rebuildable and deletion is not part of Submit.
        pass


def process_durable_diploma_submission(submission_id: str) -> AssessmentSubmission:
    """Claim and score one durable submission idempotently."""
    with transaction.atomic():
        submission = AssessmentSubmission.objects.select_for_update().select_related(
            "attempt__assessment",
        ).get(submission_id=submission_id)
        if submission.processing_status in (
            SUBMISSION_STATUS_PROCESSED,
            SUBMISSION_STATUS_UNDER_REVIEW,
        ):
            return submission
        submission.processing_status = SUBMISSION_STATUS_PROCESSING
        submission.processing_started_at = timezone.now()
        submission.retry_count = F("retry_count") + 1
        submission.save(
            update_fields=[
                "processing_status", "processing_started_at", "retry_count", "updated_at",
            ],
        )

    try:
        attempt = AssessmentAttempt.objects.select_related("assessment").get(
            pk=submission.attempt_id,
        )
        if attempt.status not in (ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_UNDER_REVIEW):
            attempt = submit_diploma_assessment(
                attempt,
                submission.final_payload["answers"],
            )
        final_status = (
            SUBMISSION_STATUS_UNDER_REVIEW
            if attempt.status == ATTEMPT_STATUS_UNDER_REVIEW
            else SUBMISSION_STATUS_PROCESSED
        )
    except Exception as exc:
        AssessmentSubmission.objects.filter(pk=submission.pk).update(
            processing_status=SUBMISSION_STATUS_FAILED,
            failed_at=timezone.now(),
            last_error_code=exc.__class__.__name__[:80],
            last_error_detail=str(exc)[:2000],
        )
        raise

    AssessmentSubmission.objects.filter(pk=submission.pk).update(
        processing_status=final_status,
        processed_at=timezone.now(),
        failed_at=None,
        last_error_code="",
        last_error_detail="",
    )
    submission.processing_status = final_status
    return submission


def diploma_autosave_attempt(
    attempt: AssessmentAttempt,
    answer_data: dict,
    *,
    write_behind: bool | None = None,
) -> None:
    """Save in-progress snapshot answers without scoring or status change."""
    use_write_behind = (
        write_behind is None and write_behind_protocol.enabled()
    ) if write_behind is None else write_behind
    attempt_query = AssessmentAttempt.objects.select_related("assessment", "student")
    if not use_write_behind:
        attempt_query = attempt_query.select_for_update()
    with transaction.atomic():
        attempt_ref = attempt_query.get(pk=attempt.pk)
        check_attempt_submission_access(
            attempt_ref,
            allow_late_submission=attempt_ref.assessment.allow_late_submission,
        )
        if use_write_behind:
            question_metadata = _get_snapshot_question_metadata(attempt_ref)
            normalized = {}
            for question in question_metadata:
                order_key = f"q_{question['order']}"
                if order_key not in answer_data:
                    continue
                raw = answer_data[order_key]
                if question["question_type"] == QUESTION_TYPE_MCQ:
                    try:
                        selected_choice_id = int(raw) if raw else None
                    except (ValueError, TypeError):
                        selected_choice_id = None
                    if selected_choice_id not in question["choice_ids"]:
                        selected_choice_id = None
                    normalized[order_key] = selected_choice_id
                elif question["question_type"] == QUESTION_TYPE_WRITTEN:
                    normalized[order_key] = raw
            if normalized:
                generation = write_behind_protocol.enqueue(attempt_ref.pk, normalized)
                if not generation:
                    raise ValueError(_("Assessment editing is closed."))
                from .tasks import flush_snapshot_batch

                if write_behind_protocol.claim_schedule(attempt_ref.pk):
                    try:
                        flush_snapshot_batch.apply_async(
                            args=[attempt_ref.pk, generation], countdown=1,
                        )
                    except Exception:
                        write_behind_protocol.release_schedule(attempt_ref.pk)
                        raise
                return
        snapshot_questions = _get_snapshot_questions(attempt_ref)
        question_ids = [question.pk for question in snapshot_questions]
        valid_choice_ids_by_question = {}
        for question_id, choice_id in AssessmentAttemptChoice.objects.filter(
            attempt_question_id__in=question_ids,
        ).values_list("attempt_question_id", "pk"):
            valid_choice_ids_by_question.setdefault(question_id, set()).add(choice_id)
        existing = {
            answer.attempt_question_id: answer
            for answer in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question_id__in=question_ids,
            )
        }
        new_answers = []
        to_save = []
        now = timezone.now()
        for question in snapshot_questions:
            order_key = f"q_{question.order}"
            if order_key not in answer_data:
                continue
            answer = existing.get(question.pk)
            if answer is None:
                answer = AssessmentAttemptSnapshotAnswer(attempt_question=question)
                new_answers.append(answer)
            else:
                to_save.append(answer)
            raw = answer_data[order_key]
            if question.question_type == QUESTION_TYPE_MCQ:
                try:
                    selected_choice_id = int(raw) if raw else None
                except (ValueError, TypeError):
                    selected_choice_id = None
                answer.selected_choice_id = (
                    selected_choice_id
                    if selected_choice_id in valid_choice_ids_by_question.get(question.pk, set())
                    else None
                )
            elif question.question_type == QUESTION_TYPE_WRITTEN:
                answer.written_text = raw
            if answer.pk:
                answer.updated_at = now
        if new_answers:
            AssessmentAttemptSnapshotAnswer.objects.bulk_create(new_answers)
        if to_save:
            AssessmentAttemptSnapshotAnswer.objects.bulk_update(
                to_save,
                ["selected_choice", "written_text", "updated_at"],
            )


def diploma_autosave_attempt_batch(values_by_attempt: dict[int, dict[str, object]]) -> None:
    """Persist Redis deltas for several attempts with one bulk update."""
    if not values_by_attempt:
        return
    with transaction.atomic():
        attempts = list(
            AssessmentAttempt.objects.select_for_update().select_related("assessment", "student").filter(
                pk__in=values_by_attempt, status=ATTEMPT_STATUS_IN_PROGRESS,
            ).order_by("pk")
        )
        questions = list(AssessmentAttemptQuestion.objects.filter(attempt_id__in=[a.pk for a in attempts]))
        question_by_key = {(q.attempt_id, f"q_{q.order}"): q for q in questions}
        question_ids = [q.pk for q in questions]
        valid_choices = {}
        for question_id, choice_id in AssessmentAttemptChoice.objects.filter(
            attempt_question_id__in=question_ids,
        ).values_list("attempt_question_id", "pk"):
            valid_choices.setdefault(question_id, set()).add(choice_id)
        answers = {
            answer.attempt_question_id: answer
            for answer in AssessmentAttemptSnapshotAnswer.objects.filter(attempt_question_id__in=question_ids)
        }
        new_answers = []
        changed_answers = []
        now = timezone.now()
        for attempt in attempts:
            check_attempt_submission_access(
                attempt, allow_late_submission=attempt.assessment.allow_late_submission,
            )
            for key, raw in values_by_attempt[attempt.pk].items():
                question = question_by_key.get((attempt.pk, key))
                if question is None:
                    continue
                answer = answers.get(question.pk)
                if answer is None:
                    answer = AssessmentAttemptSnapshotAnswer(attempt_question=question)
                    new_answers.append(answer)
                if question.question_type == QUESTION_TYPE_MCQ:
                    answer.selected_choice_id = raw if raw in valid_choices.get(question.pk, set()) else None
                else:
                    answer.written_text = raw
                answer.updated_at = now
                if answer.pk:
                    changed_answers.append(answer)
        if new_answers:
            AssessmentAttemptSnapshotAnswer.objects.bulk_create(new_answers)
        if changed_answers:
            AssessmentAttemptSnapshotAnswer.objects.bulk_update(
                changed_answers, ["selected_choice", "written_text", "updated_at"],
            )


# ponytail: V154 — save in-progress answers without scoring or status change.
def autosave_attempt(attempt: AssessmentAttempt, answers: list[AssessmentAnswerData]) -> None:
    check_attempt_submission_access(
        attempt,
        allow_late_submission=attempt.assessment.allow_late_submission,
    )
    answers_by_question = {a.question_id: a for a in answers}
    questions = list(attempt.assessment.questions.prefetch_related("choices"))
    with transaction.atomic():
        for question in questions:
            answer_data = answers_by_question.get(question.id)
            if not answer_data:
                continue
            if question.question_type == QUESTION_TYPE_MCQ:
                selected_choice_id = answer_data.selected_choice_id
                if selected_choice_id is not None and selected_choice_id not in {
                    choice.id for choice in question.choices.all()
                }:
                    selected_choice_id = None
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "selected_choice_id": selected_choice_id,
                        "written_text": "",
                    },
                )
            elif question.question_type == QUESTION_TYPE_WRITTEN:
                AssessmentAnswer.objects.update_or_create(
                    attempt=attempt,
                    question=question,
                    defaults={
                        "selected_choice": None,
                        "written_text": answer_data.written_text,
                    },
                )


def _resolve_choice(question: AssessmentQuestion, choice_id: int | None) -> AssessmentChoice | None:
    if choice_id is None:
        return None
    return AssessmentChoice.objects.filter(id=choice_id, question=question).first()


def _score_mcq_answer(
    choice: AssessmentChoice | None,
    question: AssessmentQuestion,
    assessment: Assessment,
) -> Decimal:
    if choice is None:
        return Decimal("0.00")
    return mcq_score(question.points, bool(choice.is_correct), assessment.penalty_percent)


# ---------------------------------------------------------------------------
# Homework submission (aggregate lifecycle, P2-03)
# ---------------------------------------------------------------------------


def _validate_homework_flow(student, assessment: Assessment) -> None:
    """Shared guard for homework draft/upload/submit operations."""
    if assessment.assessment_type != ASSESSMENT_TYPE_HOMEWORK:
        raise ValueError(_("This assessment does not accept homework uploads."))
    ensure_active_assessment(assessment)
    now = timezone.now()
    if not assessment.is_active:
        raise ValueError(_("Assessment is not active."))
    if assessment.open_at and now < assessment.open_at:
        raise ValueError(_("Assessment has not opened yet."))
    if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
        raise ValueError(_("Assessment has already closed."))
    if not has_active_enrollment(student, assessment):
        raise ValueError(_("You do not have an active enrollment for this course."))


def get_student_homework_submission(student, assessment: Assessment) -> HomeworkSubmission | None:
    """Return the student's homework submission for an assessment, or None."""
    return (
        HomeworkSubmission.objects.filter(assessment=assessment, student=student)
        .select_related("attempt", "assessment")
        .prefetch_related("attachments")
        .first()
    )


def add_homework_attachment(
    student,
    assessment: Assessment,
    file_data: bytes,
    original_filename: str,
    content_type: str,
) -> HomeworkAttachment:
    """Upload one file into the student's draft homework submission.

    The object upload happens before the short locked attach step; an orphaned
    object is deleted if the attach step rejects it. Never submits on upload.
    """
    _validate_homework_flow(student, assessment)
    now = timezone.now()
    max_size = settings.HOMEWORK_MAX_UPLOAD_MB * 1024 * 1024
    result = storage_service.upload_file(
        data=file_data,
        key_prefix=f"homework/{assessment.id}",
        content_type=content_type,
        max_size=max_size,
        allowed_types=HOMEWORK_ALLOWED_EXTENSIONS,
        original_filename=original_filename,
    )
    try:
        with transaction.atomic():
            # Lock the durable parent that all first-upload contenders share.
            # The caller passes the User; lock the owning StudentProfile row so
            # concurrent uploads from the same student serialize on one row.
            StudentProfile.objects.select_for_update().get(user_id=student.pk)
            submission, _created = HomeworkSubmission.objects.select_for_update().get_or_create(
                assessment=assessment,
                student=student,
                defaults={"status": HOMEWORK_STATUS_UPLOADED},
            )
            if submission.status != HOMEWORK_STATUS_UPLOADED:
                raise ValueError(_("This homework has already been submitted."))
            if assessment.max_upload_files is not None and (
                submission.attachments.count() >= assessment.max_upload_files
            ):
                raise ValueError(_("Maximum number of files reached for this assessment."))
            attachment = HomeworkAttachment.objects.create(
                submission=submission,
                assessment=assessment,
                student=student,
                original_filename=original_filename,
                storage_key=result.key,
                content_type=content_type,
                file_size=result.byte_size,
                checksum=result.checksum,
                status=HOMEWORK_STATUS_UPLOADED,
                submitted_at=now,
            )
    except Exception:
        # Reconcile the orphaned object uploaded before the rejected attach.
        try:
            if not storage_service.delete_file(result.key):
                record_deferred_storage_deletion(key=result.key, error="Homework attach rejected")
        except Exception as cleanup_exc:
            record_deferred_storage_deletion(key=result.key, error=str(cleanup_exc))
        raise
    return attachment


def remove_homework_attachment(student, assessment: Assessment, attachment_id: int) -> None:
    """Remove one file from the student's draft before submission."""
    attachment = (
        HomeworkAttachment.objects.select_related("submission")
        .filter(id=attachment_id, student=student, assessment=assessment)
        .first()
    )
    if attachment is None or attachment.submission_id is None:
        raise ValueError(_("Attachment not found."))
    storage_key = attachment.storage_key
    with transaction.atomic():
        submission = HomeworkSubmission.objects.select_for_update().get(pk=attachment.submission_id)
        if submission.status != HOMEWORK_STATUS_UPLOADED:
            raise ValueError(_("Homework attachments can only be removed before submission."))
        attachment.delete()
    try:
        if not storage_service.delete_file(storage_key):
            record_deferred_storage_deletion(key=storage_key, error="Homework attachment removed")
    except Exception as exc:
        record_deferred_storage_deletion(key=storage_key, error=str(exc))


def submit_homework_submission(student, assessment: Assessment) -> HomeworkSubmission:
    """Explicitly submit the student's draft homework (one logical attempt).

    Idempotent in effect: a second submit is rejected and never creates a
    second attempt. Requires at least one uploaded attachment.
    """
    _validate_homework_flow(student, assessment)
    now = timezone.now()
    with transaction.atomic():
        submission = HomeworkSubmission.objects.select_for_update().filter(
            assessment=assessment, student=student,
        ).select_related("assessment").first()
        if submission is None:
            raise ValueError(_("You have not added any homework files yet."))
        if submission.status != HOMEWORK_STATUS_UPLOADED:
            raise ValueError(_("This homework has already been submitted."))
        if not submission.attachments.exists():
            raise ValueError(_("Add at least one homework file before submitting."))
        attempt = AssessmentAttempt.objects.create(
            assessment=assessment,
            student=student,
            attempt_number=1,
            status=ATTEMPT_STATUS_SUBMITTED,
            submitted_at=now,
        )
        submission.attempt = attempt
        submission.status = HOMEWORK_STATUS_SUBMITTED
        submission.submitted_at = now
        submission.save(update_fields=["attempt", "status", "submitted_at", "updated_at"])
    return submission


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def grade_written_answer(
    answer_id: int,
    score: Decimal,
    feedback: str,
    grader,
    *,
    override_reason: str = "",
    recalculate: bool = True,
) -> AssessmentAnswer:
    answer = AssessmentAnswer.objects.select_related("attempt__assessment").get(id=answer_id)
    if score < 0:
        raise ValueError(_("Score cannot be negative."))
    if answer.question and score > answer.question.points:
        raise ValueError(
            _("Score ({score}) cannot exceed question points ({max_points}).").format(
                score=score, max_points=answer.question.points,
            )
        )
    is_override = answer.score is not None and answer.score != score
    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    if is_override:
        if not override_reason:
            raise ValueError(_("An override reason is required when changing a previously-graded answer."))
        answer.score_override_reason = override_reason
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by", "score_override_reason"])
    if recalculate:
        _update_attempt_grade(answer.attempt)
    return answer


def grade_diploma_snapshot_answer(
    answer_id: int,
    score: Decimal,
    feedback: str,
    grader,
    *,
    override_reason: str = "",
    recalculate: bool = True,
) -> AssessmentAttemptSnapshotAnswer:
    answer = AssessmentAttemptSnapshotAnswer.objects.select_related(
        "attempt_question__attempt__assessment",
    ).get(pk=answer_id)

    if score < 0:
        raise ValueError(_("Score cannot be negative."))
    sq = answer.attempt_question
    if score > sq.points:
        raise ValueError(
            _("Score (%(score)s) cannot exceed question points (%(max_points)s).")
            % {"score": score, "max_points": sq.points}
        )
    is_override = answer.score is not None and answer.score != score
    if is_override and not override_reason:
        raise ValueError(_("An override reason is required when changing a previously-graded answer."))

    answer.score = score
    answer.feedback = feedback
    answer.graded_at = timezone.now()
    answer.graded_by = grader
    if is_override:
        answer.score_override_reason = override_reason
    answer.save(update_fields=["score", "feedback", "graded_at", "graded_by", "score_override_reason"])
    if recalculate:
        _update_diploma_attempt_grade(answer.attempt_question.attempt)
    return answer


def _update_diploma_attempt_grade(attempt: AssessmentAttempt) -> None:
    """Recompute a diploma snapshot attempt aggregate under an attempt lock.

    The attempt row is locked so concurrent graders serialize and the final
    aggregate is never computed from a partial answer set (RV-014).
    """
    with transaction.atomic():
        locked = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        snapshot_questions = list(locked.snapshot_questions.all())
        if not snapshot_questions:
            return
        answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=locked,
            )
        }
        total = Decimal("0")
        all_graded = True
        for sq in snapshot_questions:
            answer = answers.get(sq.pk)
            if answer and answer.score is not None:
                total += answer.score
            else:
                all_graded = False
        locked.score_points = total
        total_possible = sum(sq.points for sq in snapshot_questions)
        assessment = locked.assessment
        locked.score_percent = compute_percent(total, total_possible)
        if all_graded:
            locked.status = ATTEMPT_STATUS_GRADED
            locked.graded_at = timezone.now()
            locked.is_pass = is_passing(locked.score_percent, assessment.passing_score)
            locked.save(update_fields=["score_points", "score_percent", "status", "graded_at", "is_pass"])
        else:
            locked.save(update_fields=["score_points", "score_percent"])


def grade_homework(submission_id: int, score: Decimal, feedback: str, grader) -> HomeworkSubmission:
    """Grade the aggregate homework submission (one score/feedback for all files)."""
    with transaction.atomic():
        submission = HomeworkSubmission.objects.select_for_update().select_related("assessment", "attempt").get(id=submission_id)
        score = validate_score(score, max_score=submission.assessment.total_points)
        if submission.legacy_status != LEGACY_STATUS_ACTIVE:
            raise ValueError(_("This homework submission is preserved legacy data and is read-only."))
        if submission.status in (HOMEWORK_STATUS_UPLOADED, HOMEWORK_STATUS_RELEASED):
            raise ValueError(_("This homework submission cannot be graded in its current state."))
        attempt = submission.attempt
        if attempt is None:
            raise ValueError(_("This homework submission has no linked assessment attempt."))
        attempt = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        now = timezone.now()
        submission.score = score
        submission.feedback = feedback
        submission.reviewed_at = now
        submission.reviewed_by = grader
        submission.status = HOMEWORK_STATUS_GRADED
        submission.save(update_fields=["score", "feedback", "reviewed_at", "reviewed_by", "status", "updated_at"])

        if submission.assessment.total_points <= 0:
            raise ValueError(_("Assessment total points must be positive."))
        attempt.score_points = score
        attempt.score_percent = compute_percent(score, submission.assessment.total_points)
        attempt.graded_at = now
        attempt.graded_by = grader
        attempt.is_pass = is_passing(attempt.score_percent, submission.assessment.passing_score)
        if attempt.is_pass is None:
            attempt.is_pass = False
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.save(update_fields=[
            "score_points", "score_percent", "graded_at", "graded_by", "is_pass", "status",
        ])
        # P2-04: automatic release only after the aggregate grade commits.
        if submission.assessment.release_mode == RELEASE_MODE_AUTOMATIC:
            transaction.on_commit(lambda aid=attempt.pk: _maybe_auto_release_attempt(aid))
    return submission


def _update_attempt_grade(attempt: AssessmentAttempt) -> None:
    """Recompute the attempt aggregate from its answers under an attempt lock.

    The attempt row is locked so concurrent graders serialize and the final
    aggregate is never computed from a partial answer set (RV-014).
    """
    with transaction.atomic():
        locked = AssessmentAttempt.objects.select_for_update().get(pk=attempt.pk)
        total = Decimal("0.00")
        all_graded = True
        for answer in locked.answers.select_related("question").all():
            if answer.score is not None:
                total += answer.score
            else:
                all_graded = False
        locked.score_points = total
        assessment = locked.assessment
        total_possible = sum(q.points for q in assessment.questions.all())
        locked.score_percent = compute_percent(total, total_possible)
        if all_graded:
            locked.status = ATTEMPT_STATUS_GRADED
            locked.graded_at = timezone.now()
            locked.is_pass = is_passing(locked.score_percent, assessment.passing_score)
            locked.save(
                update_fields=[
                    "score_points", "score_percent", "status",
                    "graded_at", "is_pass",
                ],
            )
        else:
            locked.save(update_fields=["score_points", "score_percent"])


# ---------------------------------------------------------------------------
# Grade release (P2-04)
# ---------------------------------------------------------------------------


@transaction.atomic
def release_attempt_grades(
    attempt_id: int,
    *,
    actor=None,
    reason: str = "",
    source: str = "manual",
) -> AssessmentAttempt:
    """Idempotently release a graded attempt.

    Locked transition: graded -> released with ``visible_to_student=True``.
    Duplicate release requests are harmless. Manual Diploma release requires
    an Admin/Super Admin actor; automatic release (``source="automatic"``) is
    invoked only after the final grade transaction commits.
    """
    attempt = AssessmentAttempt.objects.select_for_update().select_related("assessment").get(pk=attempt_id)
    assessment = attempt.assessment
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    if source == "manual" and assessment.course_version_id is None and not is_admin(actor):
        raise ValueError(_("Only Administrators can release Diploma grades."))
    if attempt.status == ATTEMPT_STATUS_RELEASED and attempt.visible_to_student:
        return attempt
    if attempt.status != ATTEMPT_STATUS_GRADED:
        raise ValueError(_("Only graded attempts can be released."))
    before = {"status": attempt.status, "visible_to_student": attempt.visible_to_student}
    attempt.status = ATTEMPT_STATUS_RELEASED
    attempt.visible_to_student = True
    attempt.save(update_fields=["status", "visible_to_student", "updated_at"])
    # Sync the linked aggregate homework submission so its lifecycle matches.
    HomeworkSubmission.objects.filter(attempt=attempt).update(status=HOMEWORK_STATUS_RELEASED, updated_at=timezone.now())
    record_audit_event(
        actor=actor,
        action=ACTION_GRADE_RELEASED,
        target_type="assessment_attempt",
        target_id=str(attempt.pk),
        metadata={
            "assessment_id": assessment.pk,
            "student_id": attempt.student_id,
            "source": source,
            "reason": reason,
            "before": before,
            "after": {"status": attempt.status, "visible_to_student": attempt.visible_to_student},
        },
    )
    return attempt


@transaction.atomic
def hide_attempt_grades(attempt_id: int, *, actor=None, reason: str = "") -> AssessmentAttempt:
    """Idempotently hide (un-release) a released attempt."""
    attempt = AssessmentAttempt.objects.select_for_update().select_related("assessment").get(pk=attempt_id)
    if attempt.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    if attempt.status != ATTEMPT_STATUS_RELEASED or not attempt.visible_to_student:
        raise ValueError(_("Only released attempts can be hidden."))
    before = {"status": attempt.status, "visible_to_student": attempt.visible_to_student}
    attempt.status = ATTEMPT_STATUS_GRADED
    attempt.visible_to_student = False
    attempt.save(update_fields=["status", "visible_to_student", "updated_at"])
    # Sync the linked aggregate homework submission back to graded.
    HomeworkSubmission.objects.filter(attempt=attempt).update(status=HOMEWORK_STATUS_GRADED, updated_at=timezone.now())
    record_audit_event(
        actor=actor,
        action=ACTION_GRADE_HIDDEN,
        target_type="assessment_attempt",
        target_id=str(attempt.pk),
        metadata={
            "assessment_id": attempt.assessment_id,
            "student_id": attempt.student_id,
            "reason": reason,
            "before": before,
            "after": {"status": attempt.status, "visible_to_student": attempt.visible_to_student},
        },
    )
    return attempt


def _maybe_auto_release_attempt(attempt_id: int) -> None:
    """Release an attempt after commit when the assessment is automatic (P2-04).

    Called via ``transaction.on_commit`` so it never runs inside the grading
    transaction and never releases unless the authoritative grade committed.
    """
    attempt = AssessmentAttempt.objects.filter(pk=attempt_id).select_related("assessment").first()
    if attempt is None:
        return
    if attempt.assessment.release_mode != RELEASE_MODE_AUTOMATIC:
        return
    try:
        release_attempt_grades(attempt_id, source="automatic", reason="automatic release")
    except ValueError:
        # Not graded/ready yet; the manual/automatic trigger will run later.
        return


def release_all_graded(assessment: Assessment, *, actor) -> int:
    """Release every graded attempt of an assessment in bounded batches."""
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    ids = list(
        AssessmentAttempt.objects.filter(
            assessment=assessment, status=ATTEMPT_STATUS_GRADED,
        ).values_list("id", flat=True)
    )
    count = 0
    for attempt_id in ids:
        try:
            release_attempt_grades(attempt_id, actor=actor, source="manual", reason="bulk release")
            count += 1
        except ValueError:
            continue
    return count


def hide_all_released(assessment: Assessment, *, actor) -> int:
    """Hide every released attempt of an assessment in bounded batches."""
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    ids = list(
        AssessmentAttempt.objects.filter(
            assessment=assessment, status=ATTEMPT_STATUS_RELEASED,
        ).values_list("id", flat=True)
    )
    count = 0
    for attempt_id in ids:
        try:
            hide_attempt_grades(attempt_id, actor=actor, reason="bulk hide")
            count += 1
        except ValueError:
            continue
    return count


# ---------------------------------------------------------------------------
# Admin Semester Exam stage review (P3-03)
# ---------------------------------------------------------------------------


def _semester_exam_attempt_details(attempt: AssessmentAttempt) -> dict:
    """Return grading completeness and computed percent for a semester exam attempt.

    Online (diploma snapshot): all written/MCQ snapshot answers must have a
    score. Offline: every lecture section must have an entered grade for the
    exam code. Computed totals are set-wise.
    """
    is_online = attempt.snapshot_questions.exists()
    if is_online:
        snapshot_questions = list(attempt.snapshot_questions.prefetch_related("choices"))
        answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt,
            )
        }
        graded_count = 0
        total_points = Decimal("0")
        earned = Decimal("0")
        for sq in snapshot_questions:
            answer = answers.get(sq.pk)
            total_points += sq.points
            if answer is not None and answer.score is not None:
                graded_count += 1
                earned += answer.score
        complete = bool(snapshot_questions) and graded_count == len(snapshot_questions)
        percent = compute_percent(earned, total_points)
        return {"is_online": True, "complete": complete, "percent": percent}

    code = SemesterExamCode.objects.filter(
        assessment=attempt.assessment,
        diploma_enrollment__student__user=attempt.student,
        active=True,
    ).first()
    sections = list(attempt.assessment.lecture_sections.order_by("order"))
    if not sections or code is None:
        return {"is_online": False, "complete": False, "percent": None}
    grades = {
        g.section_id: g
        for g in OfflineSectionGrade.objects.filter(exam_code=code, section__in=sections)
    }
    total_max = sum(section.max_grade for section in sections)
    earned = sum(grades[s.id].score for s in sections if s.id in grades)
    complete = len(grades) == len(sections)
    percent = compute_percent(earned, total_max)
    return {"is_online": False, "complete": complete, "percent": percent}


def get_semester_exam_review_queue(user=None):
    """Paginated Admin queue: graded semester exam attempts awaiting Admin review."""
    qs = AssessmentAttempt.objects.filter(
        assessment__assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        assessment__course_version__isnull=True,
        assessment__legacy_status=LEGACY_STATUS_ACTIVE,
        status__in=[ATTEMPT_STATUS_GRADED, ATTEMPT_STATUS_RELEASED],
        exam_review_status=EXAM_REVIEW_STATUS_NOT_REVIEWED,
    ).select_related("assessment", "student")
    if user is not None and not is_admin(user):
        qs = qs.none()
    return qs


@transaction.atomic
def admin_review_semester_exam(
    attempt_id: int,
    decision: str,
    *,
    actor=None,
    note: str = "",
) -> AssessmentAttempt:
    """Make the explicit Admin Semester Exam stage decision.

    Requires every configured part graded, permits Fail only below the exam
    passing threshold, preserves all grades, locks the attempt, audits
    before/after, and hands off to release only after commit when the
    assessment release mode is automatic. Idempotent for the same decision.
    """
    attempt = AssessmentAttempt.objects.select_for_update().select_related("assessment").get(pk=attempt_id)
    assessment = attempt.assessment
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM or assessment.course_version_id is not None:
        raise ValueError(_("Exam-stage review applies only to diploma-scoped Semester Exams."))
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("This assessment is preserved legacy data and is read-only."))
    details = _semester_exam_attempt_details(attempt)
    if not details["complete"]:
        raise ValueError(_("All exam parts must be graded before the stage decision."))
    percent = details["percent"]
    if percent is None:
        raise ValueError(_("The computed exam grade is unavailable."))
    if attempt.exam_review_status != EXAM_REVIEW_STATUS_NOT_REVIEWED:
        if (attempt.exam_review_status == EXAM_REVIEW_STATUS_PASSED and decision == "pass") or (
            attempt.exam_review_status == EXAM_REVIEW_STATUS_FAILED and decision == "fail"
        ):
            return attempt
        raise ValueError(_("This attempt has already received an exam-stage decision."))
    if decision not in ("pass", "fail"):
        raise ValueError(_("Invalid exam review decision."))
    if decision == "pass" and percent < assessment.passing_score:
        raise ValueError(_("An exam below the passing threshold cannot be marked as passed."))
    if decision == "fail" and percent >= assessment.passing_score:
        raise ValueError(_("An exam at or above the passing threshold cannot be failed."))
    before = {
        "exam_review_status": attempt.exam_review_status,
        "score_percent": str(percent),
        "is_pass": attempt.is_pass,
    }
    now = timezone.now()
    attempt.exam_review_status = (
        EXAM_REVIEW_STATUS_PASSED if decision == "pass" else EXAM_REVIEW_STATUS_FAILED
    )
    attempt.exam_reviewed_by = actor
    attempt.exam_reviewed_at = now
    attempt.exam_review_note = note.strip()
    attempt.save(update_fields=[
        "exam_review_status", "exam_reviewed_by", "exam_reviewed_at", "exam_review_note", "updated_at",
    ])
    record_audit_event(
        actor=actor,
        action=ACTION_GRADE_CHANGED,
        target_type="assessment_attempt",
        target_id=str(attempt.pk),
        metadata={
            "assessment_id": assessment.pk,
            "student_id": attempt.student_id,
            "stage": "semester_exam_review",
            "decision": decision,
            "computed_percent": str(percent),
            "before": before,
            "after": {
                "exam_review_status": attempt.exam_review_status,
                "exam_reviewed_at": now.isoformat(),
            },
            "note": note.strip(),
        },
    )
    if assessment.release_mode == RELEASE_MODE_AUTOMATIC:
        transaction.on_commit(lambda aid=attempt.pk: _maybe_auto_release_attempt(aid))
    return attempt


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_student_result_projection(attempt: AssessmentAttempt) -> dict:
    """Project an attempt for student display without any correctness data.

    Correctness flags, answer keys, rubrics, and model answers are never
    included before or after release (P2-04). Scores/feedback are omitted
    until the attempt is released (``visible_to_student``).
    """
    show_scores = bool(attempt.visible_to_student)
    is_diploma = bool(
        attempt.assessment.course_version_id is None
        and attempt.assessment.semester_id and attempt.assessment.cohort_id
    )
    questions = []
    if is_diploma:
        answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt,
            ).select_related("selected_choice")
        }
        for sq in attempt.snapshot_questions.order_by("order"):
            answer = answers.get(sq.pk)
            selected = answer.selected_choice if answer and answer.selected_choice_id else None
            questions.append({
                "order": sq.order,
                "text": sq.text_snapshot,
                "question_type": sq.question_type,
                "selected_text": selected.text_snapshot if selected else "",
                "written_text": answer.written_text if answer else "",
                "score": (answer.score if show_scores and answer and answer.score is not None else None),
                "feedback": (answer.feedback if show_scores and answer else ""),
            })
    else:
        answers = {
            a.question_id: a
            for a in attempt.answers.select_related("question", "selected_choice")
        }
        for question in attempt.assessment.questions.order_by("order"):
            answer = answers.get(question.pk)
            selected = answer.selected_choice if answer and answer.selected_choice_id else None
            questions.append({
                "order": question.order,
                "text": question.text,
                "question_type": question.question_type,
                "selected_text": selected.text if selected else "",
                "written_text": answer.written_text if answer else "",
                "score": (answer.score if show_scores and answer and answer.score is not None else None),
                "feedback": (answer.feedback if show_scores and answer else ""),
            })
    return {
        "status": attempt.status,
        "visible_to_student": show_scores,
        "score_points": attempt.score_points if show_scores else None,
        "score_percent": attempt.score_percent if show_scores else None,
        "is_pass": attempt.is_pass if show_scores else None,
        "submitted_at": attempt.submitted_at,
        "graded_at": attempt.graded_at,
        # The student's own questions/answers are reviewable read-only before
        # release; scores, feedback, correctness flags, and answer keys are
        # never projected until the attempt is released (P2-04).
        "questions": questions,
    }


def get_assessment_readiness(student, assessment: Assessment, latest_attempt=None) -> dict:
    """Project one assessment into a student-facing state (E6).

    Returns ``state`` (ready / continue / scheduled / closed / max-attempts /
    awaiting-release / completed), a translated ``state_label``, a
    ``primary_action`` dict (``{label, url, method}``) that only exists when
    the student can act right now, and the legacy flat ``action_url`` /
    ``action_label`` pair. The list page and the instructions page must both
    use it so they stay equivalent. ``primary_action`` is the shared
    presentation key (P1-02); templates never derive it.
    """
    now = timezone.now()
    if not assessment.is_active or assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        return {"state": "closed", "state_label": _("Closed"), "action_url": None, "action_label": None, "primary_action": None}
    if not has_active_enrollment(student, assessment):
        return {"state": "closed", "state_label": _("No active enrollment"), "action_url": None, "action_label": None, "primary_action": None}

    override = get_active_assessment_access_override(student, assessment, now)
    exceptional_window = bool(
        override and override.open_at <= now <= override.close_at
    )
    exceptional_attempt_available = bool(
        exceptional_window and override.used_count < override.max_attempts
    )

    attempt = latest_attempt
    if attempt is not None:
        if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
            return {
                "state": "continue", "state_label": _("In progress"),
                "action_url": reverse("assessments:take", args=[assessment.id]),
                "action_label": _("Continue"),
                "primary_action": {
                    "label": _("Continue"),
                    "url": reverse("assessments:take", args=[assessment.id]),
                    "method": "GET",
                },
            }
        if exceptional_attempt_available:
            return {
                "state": "ready", "state_label": _("Ready"),
                "action_url": reverse("assessments:instructions", args=[assessment.id]),
                "action_label": _("Open"),
                "primary_action": {
                    "label": _("Open"),
                    "url": reverse("assessments:instructions", args=[assessment.id]),
                    "method": "GET",
                },
            }
        if attempt.status in (ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW) or (
            attempt.status == ATTEMPT_STATUS_GRADED and not attempt.visible_to_student
        ):
            return {
                "state": "awaiting-release", "state_label": _("Awaiting release"),
                "action_url": None, "action_label": None, "primary_action": None,
            }
        if attempt.status == ATTEMPT_STATUS_RELEASED:
            return {
                "state": "completed", "state_label": _("Completed"),
                "action_url": reverse("assessments:results", args=[assessment.id]),
                "action_label": _("View result"),
                "primary_action": {
                    "label": _("View result"),
                    "url": reverse("assessments:results", args=[assessment.id]),
                    "method": "GET",
                },
            }

    attempt_count = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).exclude(
        status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED],
    ).count()
    if exceptional_attempt_available:
        return {
            "state": "ready", "state_label": _("Ready"),
            "action_url": reverse("assessments:instructions", args=[assessment.id]),
            "action_label": _("Open"),
            "primary_action": {
                "label": _("Open"),
                "url": reverse("assessments:instructions", args=[assessment.id]),
                "method": "GET",
            },
        }
    if not exceptional_window and assessment.open_at and now < assessment.open_at:
        return {"state": "scheduled", "state_label": _("Scheduled"), "action_url": None, "action_label": None, "primary_action": None}
    if not exceptional_window and assessment.close_at and now > assessment.close_at:
        return {"state": "closed", "state_label": _("Closed"), "action_url": None, "action_label": None, "primary_action": None}
    max_attempts = override.max_attempts if exceptional_window else assessment.max_attempts
    if attempt_count >= max_attempts:
        return {
            "state": "max-attempts", "state_label": _("Maximum attempts reached"),
            "action_url": None, "action_label": None, "primary_action": None,
        }
    return {
        "state": "ready", "state_label": _("Ready"),
        "action_url": reverse("assessments:instructions", args=[assessment.id]),
        "action_label": _("Open"),
        "primary_action": {
            "label": _("Open"),
            "url": reverse("assessments:instructions", args=[assessment.id]),
            "method": "GET",
        },
    }


def get_student_assessments(student):
    """Return assessments visible to the student (active unexpired enrollment + open or active override)."""
    now = timezone.now()
    active_enrollment_q = (
        Q(course_version__enrollments__student__user=student, course_version__enrollments__status=ENROLLMENT_STATUS_ACTIVE)
        & (Q(course_version__enrollments__expires_at__isnull=True) | Q(course_version__enrollments__expires_at__gt=now))
    )
    enrolled_cohort_ids = DiplomaEnrollment.objects.filter(
        student__user=student, status=ENROLLMENT_STATUS_ACTIVE,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now),
    ).values_list("cohort_id", flat=True)
    base_qs = Assessment.objects.filter(
        is_active=True, legacy_status=LEGACY_STATUS_ACTIVE,
    ).filter(
        active_enrollment_q
        | Q(cohort_id__in=list(enrolled_cohort_ids), course_version__isnull=True)
    )
    open_assessments = list(
        base_qs.filter(
            Q(open_at__isnull=True) | Q(open_at__lte=now),
            Q(close_at__isnull=True) | Q(close_at__gte=now),
        )
        .select_related("course_version__course")
        .prefetch_related("attempts")
        .distinct()
    )
    # Filter out diploma assessments whose semester isn't visible yet
    diploma_assessments = [
        a for a in open_assessments
        if a.course_version_id is None and a.cohort_id and a.semester_id
    ]
    if diploma_assessments:
        enrollments = {
            e.cohort_id: e
            for e in DiplomaEnrollment.objects.filter(
                student__user=student, status=ENROLLMENT_STATUS_ACTIVE,
                cohort_id__in={a.cohort_id for a in diploma_assessments},
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            ).select_related("cohort__diploma")
        }
        visible_by_enrollment = get_visible_semester_ids_by_enrollment(enrollments.values())
        visible_by_cohort = {
            cohort_id: visible_by_enrollment.get(enrollment.id, set())
            for cohort_id, enrollment in enrollments.items()
        }
        open_assessments = [
            a for a in open_assessments
            if not (a.course_version_id is None and a.cohort_id and a.semester_id)
            or (a.cohort_id in visible_by_cohort and a.semester_id in visible_by_cohort[a.cohort_id])
        ]
    override_ids = set(SemesterExamAccessOverride.objects.filter(
        (
            Q(
                course_enrollment__student__user=student,
                course_enrollment__status=ENROLLMENT_STATUS_ACTIVE,
            )
            & (Q(course_enrollment__expires_at__isnull=True) | Q(course_enrollment__expires_at__gt=now))
        )
        | (
            Q(
                diploma_enrollment__student__user=student,
                diploma_enrollment__status=ENROLLMENT_STATUS_ACTIVE,
            )
            & (Q(diploma_enrollment__expires_at__isnull=True) | Q(diploma_enrollment__expires_at__gt=now))
        ),
        status=EXAM_OVERRIDE_ACTIVE,
        open_at__lte=now,
        close_at__gte=now,
    ).values_list("assessment_id", flat=True))
    override_ids.update(
        AssessmentAttempt.objects.filter(
            student=student,
            status=ATTEMPT_STATUS_IN_PROGRESS,
            assessment__is_active=True,
            assessment__legacy_status=LEGACY_STATUS_ACTIVE,
        ).values_list("assessment_id", flat=True)
    )
    if override_ids:
        extra = list(
            Assessment.objects.filter(
                id__in=override_ids,
                is_active=True,
                legacy_status=LEGACY_STATUS_ACTIVE,
            )
            .select_related("course_version__course")
            .prefetch_related("attempts")
        )
        existing_ids = {a.id for a in open_assessments}
        for a in extra:
            if a.id not in existing_ids:
                open_assessments.append(a)
    return open_assessments


def get_student_attempts(student):
    return AssessmentAttempt.objects.filter(student=student).select_related(
        "assessment",
    ).order_by("-created_at")


def get_pending_grading(user=None):
    qs = AssessmentAttempt.objects.filter(
        status=ATTEMPT_STATUS_UNDER_REVIEW,
        assessment__legacy_status=LEGACY_STATUS_ACTIVE,
    ).select_related("assessment", "assessment__cohort", "student")
    if user is not None:
        qs = qs.filter(assessment_staff_scope_filter(user)).distinct()
    return qs


def get_homework_pending_grading(user=None):
    qs = HomeworkSubmission.objects.filter(
        status__in=[HOMEWORK_STATUS_SUBMITTED, HOMEWORK_STATUS_UNDER_REVIEW],
        assessment__legacy_status=LEGACY_STATUS_ACTIVE,
    ).select_related("assessment", "assessment__cohort", "student", "attempt").prefetch_related("attachments")
    if user is not None:
        qs = qs.filter(assessment_staff_scope_filter(user)).distinct()
        # Moderators grade Diploma Homework only (P1-01): exclude course-scoped
        # homework from their queue regardless of assignment shape.
        if is_moderator(user) and not is_admin(user):
            qs = qs.filter(assessment__course_version__isnull=True)
    return qs


def get_grading_queue(user):
    """Return attempts pending review, scoped to user's permissions."""
    if is_admin(user):
        return get_pending_grading()
    if user.is_authenticated:
        return get_pending_grading(user)
    return AssessmentAttempt.objects.none()


# ---------------------------------------------------------------------------
# Diploma section validation
# ---------------------------------------------------------------------------


def get_available_bank_mcq_count(lecture):
    """Return count of active MCQ questions in the lecture's bank."""
    return DiplomaLectureQuestion.objects.filter(
        lecture=lecture, active=True, question_type=QUESTION_TYPE_MCQ,
    ).count()


def validate_sections_ready(assessment):
    """Raise ValueError if assessment has sections that cannot generate required MCQ count
    or if total max_grade doesn't match assessment.total_points."""
    for section in assessment.lecture_sections.all():
        available = get_available_bank_mcq_count(section.lecture)
        if section.mcq_count > available:
            raise ValueError(
                _("Section '%(lecture)s' requires %(required)d MCQs but only %(available)d available.")
                % {"lecture": section.lecture.title, "required": section.mcq_count, "available": available}
            )
    total_max = sum(section.max_grade for section in assessment.lecture_sections.all())
    if total_max != assessment.total_points:
        raise ValueError(
            _("Total section max grade (%(total)s) does not match assessment total points (%(expected)s).")
            % {"total": total_max, "expected": assessment.total_points}
        )


# ---------------------------------------------------------------------------
# Exam code generation and offline grade entry (Phase 7)
# ---------------------------------------------------------------------------


def generate_exam_codes(assessment: Assessment, actor) -> int:
    if assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM:
        raise ValueError(_("Exam codes are only for semester exams."))
    if not assessment.cohort:
        raise ValueError(_("Assessment must have a cohort."))

    enrollments = DiplomaEnrollment.objects.filter(
        cohort=assessment.cohort,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("student__user")

    existing_codes = set(
        SemesterExamCode.objects.filter(assessment=assessment).values_list("diploma_enrollment_id", flat=True)
    )
    existing_numbers = set(
        SemesterExamCode.objects.filter(assessment=assessment).values_list("code_number", flat=True)
    )
    application_modes = {}
    for row in ProgramApplication.objects.filter(
        program_type=PROGRAM_TYPE_DIPLOMA,
        cohort_id=assessment.cohort_id,
    ).exclude(status=PROGRAM_APP_STATUS_REJECTED).order_by("-created_at", "-id").values(
        "student_id", "delivery_mode",
    ):
        application_modes.setdefault(row["student_id"], row["delivery_mode"])

    new_codes = []
    for de in enrollments:
        if de.pk in existing_codes:
            continue
        for _attempt in range(1000):
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if code not in existing_numbers:
                existing_numbers.add(code)
                break
        else:
            raise ValueError(_("Could not generate a unique exam code after 1000 attempts."))
        new_codes.append(SemesterExamCode(
            assessment=assessment, diploma_enrollment=de,
            code_number=code,
            delivery_mode=application_modes.get(
                de.student_id,
                APPLICATION_DELIVERY_MODE_ONLINE,
            ),
            created_by=actor, active=True,
        ))

    if new_codes:
        SemesterExamCode.objects.bulk_create(new_codes)
    return len(new_codes)


@transaction.atomic
def change_diploma_enrollment_delivery_mode(
    enrollment_id: int,
    mode: str,
    actor,
    *,
    source_code_id: int | None = None,
) -> SemesterExamCode | None:
    """Change a Diploma application's mode and safe code projections.

    Printed codes and codes with academic activity are immutable historical
    evidence. The application remains authoritative; mutable, unused codes
    for the same enrollment are synchronized as operational projections.
    """
    if mode not in {DELIVERY_MODE_ONLINE, DELIVERY_MODE_OFFLINE}:
        raise ValueError(_("Invalid delivery mode."))

    enrollment = DiplomaEnrollment.objects.select_for_update().get(pk=enrollment_id)
    source_code = None
    if source_code_id is not None:
        source_code = SemesterExamCode.objects.select_for_update().select_related(
            "assessment",
        ).get(pk=source_code_id, diploma_enrollment_id=enrollment.id)
        if source_code.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
            raise ValueError(_("Legacy semester exam data is read-only."))

    application = ProgramApplication.objects.select_for_update().filter(
        student_id=enrollment.student_id,
        cohort_id=enrollment.cohort_id,
        program_type=PROGRAM_TYPE_DIPLOMA,
    ).exclude(status=PROGRAM_APP_STATUS_REJECTED).order_by(
        "-created_at", "-id",
    ).first()
    if application is None:
        raise ValueError(_("No active Diploma application was found for this student."))

    related_codes = list(
        SemesterExamCode.objects.select_for_update().filter(
            diploma_enrollment_id=enrollment.id,
        )
    )
    related_assessment_ids = [item.assessment_id for item in related_codes]
    attempted_assessment_ids = set(
        AssessmentAttempt.objects.filter(
            student__student_profile__id=enrollment.student_id,
            assessment_id__in=related_assessment_ids,
        ).values_list("assessment_id", flat=True)
    )
    graded_code_ids = set(
        OfflineSectionGrade.objects.filter(
            exam_code_id__in=[item.pk for item in related_codes],
        ).values_list("exam_code_id", flat=True)
    )
    mutable_codes = [
        item for item in related_codes
        if not item.printed_at
        and item.assessment_id not in attempted_assessment_ids
        and item.pk not in graded_code_ids
    ]
    mutable_code_ids = {item.pk for item in mutable_codes}
    if source_code is not None and source_code.pk not in mutable_code_ids:
        raise ValueError(_("This exam code is historical and cannot change delivery mode."))

    code_mode_changed = any(item.delivery_mode != mode for item in mutable_codes)
    if application.delivery_mode == mode and not code_mode_changed:
        return source_code

    old_application_mode = application.delivery_mode
    application.delivery_mode = mode
    now = timezone.now()
    application.updated_at = now
    application.save(update_fields=["delivery_mode", "updated_at"])
    for item in mutable_codes:
        item.delivery_mode = mode
        item.updated_at = now
    SemesterExamCode.objects.bulk_update(mutable_codes, ["delivery_mode", "updated_at"])
    if source_code is not None:
        source_code.delivery_mode = mode
        source_code.updated_at = now

    record_audit_event(
        actor=actor,
        action=ACTION_STUDENT_ACCESS_CHANGE,
        target_type="program_application",
        target_id=str(application.pk),
        metadata={
            "student_id": enrollment.student_id,
            "cohort_id": enrollment.cohort_id,
            "assessment_id": source_code.assessment_id if source_code else None,
            "exam_code_id": source_code.pk if source_code else None,
            "old_delivery_mode": old_application_mode,
            "new_delivery_mode": mode,
            "updated_exam_code_ids": [item.pk for item in mutable_codes],
        },
    )
    return source_code


def change_exam_code_delivery_mode(code_id: int, mode: str, actor) -> SemesterExamCode:
    """Change mode from a roster code while preserving application authority."""
    code = SemesterExamCode.objects.only("diploma_enrollment_id").get(pk=code_id)
    changed_code = change_diploma_enrollment_delivery_mode(
        code.diploma_enrollment_id,
        mode,
        actor,
        source_code_id=code_id,
    )
    return changed_code or SemesterExamCode.objects.get(pk=code_id)


def _offline_code_grading_complete(exam_code: SemesterExamCode) -> bool:
    """True when every configured section of the exam has a grade for this code."""
    section_ids = set(
        AssessmentLectureSection.objects.filter(
            assessment_id=exam_code.assessment_id,
        ).values_list("id", flat=True)
    )
    if not section_ids:
        return False
    graded_ids = set(
        OfflineSectionGrade.objects.filter(
            exam_code_id=exam_code.id,
            section_id__in=section_ids,
        ).values_list("section_id", flat=True)
    )
    return graded_ids >= section_ids


def enter_offline_section_grade(
    exam_code_id: int,
    section_id: int,
    score: Decimal,
    grader,
    *,
    assessment_id: int | None = None,
) -> OfflineSectionGrade:
    exam_code = SemesterExamCode.objects.select_related("assessment").get(pk=exam_code_id)
    if exam_code.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("Legacy semester exam data is read-only."))
    if assessment_id is not None and exam_code.assessment_id != assessment_id:
        raise ValueError(_("Exam code does not belong to this assessment."))
    if not exam_code.active or exam_code.delivery_mode != DELIVERY_MODE_OFFLINE:
        raise ValueError(_("Offline section grades require an active offline exam code."))
    section = AssessmentLectureSection.objects.get(pk=section_id)
    if section.assessment_id != exam_code.assessment_id:
        raise ValueError(_("Section does not belong to the exam code's assessment."))
    validate_score(score, max_score=section.max_grade)
    grade, created = OfflineSectionGrade.objects.update_or_create(
        exam_code_id=exam_code_id, section_id=section_id,
        defaults={"score": score, "graded_by": grader},
    )
    # Once the last section is graded, persist the offline attempt record so
    # Admin review and the Semester Evaluation see the computed exam grade.
    if _offline_code_grading_complete(exam_code):
        finalize_offline_attempt(exam_code.id)
    return grade


def finalize_offline_attempt(exam_code_id: int) -> AssessmentAttempt:
    exam_code = SemesterExamCode.objects.select_related("assessment", "diploma_enrollment__student__user").get(pk=exam_code_id)
    assessment = exam_code.assessment
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        raise ValueError(_("Legacy semester exam data is read-only."))
    with transaction.atomic():
        total_score = Decimal("0")
        total_max = Decimal("0")
        all_graded = True
        for section in assessment.lecture_sections.all():
            total_max += section.max_grade
            grade = OfflineSectionGrade.objects.filter(exam_code=exam_code, section=section).first()
            if grade:
                total_score += grade.score
            else:
                all_graded = False
        if not all_graded or total_max <= 0:
            raise ValueError(_("All exam sections must be graded before the offline attempt can be recorded."))
        attempt, _created = AssessmentAttempt.objects.get_or_create(
            assessment=assessment, student=exam_code.diploma_enrollment.student.user,
            attempt_number=1,
            defaults={"status": ATTEMPT_STATUS_GRADED, "submitted_at": timezone.now()},
        )
        attempt.score_points = total_score
        attempt.score_percent = compute_percent(total_score, total_max)
        attempt.is_pass = is_passing(attempt.score_percent, assessment.passing_score)
        if attempt.is_pass is None:
            attempt.is_pass = False
        attempt.status = ATTEMPT_STATUS_GRADED
        attempt.submitted_at = timezone.now()
        attempt.visible_to_student = False
        attempt.save()
    return attempt


# ---------------------------------------------------------------------------
# Reviewer services (Phase 8) — anonymous Semester Exam grading (P3-02)
# ---------------------------------------------------------------------------


def _reviewer_assignment_scope(assignment):
    """Return active online Semester Exam attempts within an assignment scope."""
    if assignment.access_mode not in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH):
        return AssessmentAttempt.objects.none()
    qs = AssessmentAttempt.objects.filter(
        assessment__cohort_id=assignment.cohort_id,
        assessment__assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        assessment__legacy_status=LEGACY_STATUS_ACTIVE,
        assessment__exam_codes__diploma_enrollment__student__user_id=F("student_id"),
        assessment__exam_codes__active=True,
        assessment__exam_codes__delivery_mode=DELIVERY_MODE_ONLINE,
        status__in=[ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW, ATTEMPT_STATUS_GRADED],
        snapshot_questions__question_type=QUESTION_TYPE_WRITTEN,
        snapshot_questions__answers__score__isnull=True,
    )
    if assignment.scope == REVIEWER_SCOPE_EXAM and assignment.assessment_id:
        qs = qs.filter(assessment_id=assignment.assessment_id)
    elif assignment.scope == REVIEWER_SCOPE_SEMESTER and assignment.semester_id:
        qs = qs.filter(assessment__semester_id=assignment.semester_id)
    elif assignment.scope != REVIEWER_SCOPE_COHORT:
        return AssessmentAttempt.objects.none()
    if assignment.section_id:
        qs = qs.filter(snapshot_questions__section=assignment.section)
    if assignment.semester_id:
        qs = qs.filter(assessment__semester_id=assignment.semester_id)
    newer_attempt = AssessmentAttempt.objects.filter(
        assessment_id=OuterRef("assessment_id"),
        student_id=OuterRef("student_id"),
        attempt_number__gt=OuterRef("attempt_number"),
    ).exclude(status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED])
    qs = qs.annotate(has_newer_attempt=Exists(newer_attempt)).filter(has_newer_attempt=False)
    if assignment.cluster_id:
        qs = qs.filter(
            student__student_profile__cluster_memberships__cluster_id=assignment.cluster_id,
        )
    return qs.distinct()


def get_active_reviewer_claim(assignment):
    return ReviewerExamClaim.objects.filter(
        assignment=assignment,
        claimed_by=assignment.reviewer,
        status=REVIEWER_CLAIM_IN_PROGRESS,
    ).select_related("attempt", "section").first()


@transaction.atomic
def claim_next_reviewer_submission(assignment) -> ReviewerExamClaim:
    """Exclusively claim one anonymous submission for an online reviewer.

    The assignment lock prevents one reviewer from opening a second pending
    submission. ``skip_locked`` plus the active claim constraint prevents two
    reviewers assigned to the same section from receiving the same attempt.
    """
    locked_assignment = assignment.__class__.objects.select_for_update().get(pk=assignment.pk)
    active_claim = ReviewerExamClaim.objects.filter(
        assignment=locked_assignment,
        status=REVIEWER_CLAIM_IN_PROGRESS,
    ).first()
    if active_claim:
        return active_claim

    candidate_ids = list(
        _reviewer_assignment_scope(locked_assignment)
        .order_by("submitted_at", "id")
        .values_list("id", flat=True)[:25]
    )
    # PostgreSQL forbids FOR UPDATE with DISTINCT, so lock the scoped
    # candidates by primary key instead; skip_locked skips rows another
    # reviewer transaction is currently claiming.
    locked_by_id = {
        attempt.pk: attempt
        for attempt in AssessmentAttempt.objects.filter(pk__in=candidate_ids)
        .select_for_update(skip_locked=True)
    }
    candidates = [locked_by_id[pk] for pk in candidate_ids if pk in locked_by_id]
    candidate = None
    section_id = None
    for possible in candidates:
        section_ids = list(
            AssessmentAttemptQuestion.objects.filter(
                attempt=possible,
                question_type=QUESTION_TYPE_WRITTEN,
                answers__score__isnull=True,
            ).values_list("section_id", flat=True).distinct()
        )
        claimed_section_ids = set(
            ReviewerExamClaim.objects.filter(
                attempt=possible, section_id__in=section_ids,
            ).values_list("section_id", flat=True)
        )
        available_section_ids = [sid for sid in section_ids if sid not in claimed_section_ids]
        if available_section_ids:
            candidate = possible
            section_id = available_section_ids[0]
            break
    if candidate is None or section_id is None:
        raise ValueError(_("No submitted online Semester Exam is waiting for this assignment."))
    return ReviewerExamClaim.objects.create(
        assignment=locked_assignment,
        attempt=candidate,
        section_id=section_id,
        claimed_by=locked_assignment.reviewer,
    )


def reviewer_claim_items(claim):
    """Return anonymous primitives for every written answer in the claim's section.

    The claim reserves one anonymous attempt; the Reviewer grades ALL of its
    written questions in the claimed section before finalizing. Never includes
    student identity, enrollment, or attempt objects.
    """
    code_number = SemesterExamCode.objects.filter(
        assessment_id=claim.attempt.assessment_id,
        diploma_enrollment__student__user_id=claim.attempt.student_id,
        active=True,
        delivery_mode=DELIVERY_MODE_ONLINE,
    ).values("code_number")[:1]
    return (
        AssessmentAttemptSnapshotAnswer.objects.filter(
            attempt_question__attempt_id=claim.attempt_id,
            attempt_question__question_type=QUESTION_TYPE_WRITTEN,
            attempt_question__section_id=claim.section_id,
        )
        .annotate(
            answer_id=F("pk"),
            code_number=Subquery(code_number),
            question_text=F("attempt_question__text_snapshot"),
            answer_text=F("written_text"),
            points=F("attempt_question__points"),
        )
        .values("answer_id", "code_number", "question_text", "answer_text", "points", "score")
        .order_by("answer_id")
    )


@transaction.atomic
def finalize_reviewer_claim(claim, reviewer) -> ReviewerExamClaim:
    """Finalize a claimed section only after every written answer is scored."""
    locked_claim = ReviewerExamClaim.objects.select_for_update().get(pk=claim.pk)
    if locked_claim.claimed_by_id != reviewer.id or locked_claim.status != REVIEWER_CLAIM_IN_PROGRESS:
        raise ValueError(_("This reviewer submission is no longer pending for you."))
    if AssessmentAttemptSnapshotAnswer.objects.filter(
        attempt_question__attempt_id=locked_claim.attempt_id,
        attempt_question__section_id=locked_claim.section_id,
        attempt_question__question_type=QUESTION_TYPE_WRITTEN,
        score__isnull=True,
    ).exists():
        raise ValueError(_("Grade every written answer before finalizing this submission."))
    locked_claim.status = REVIEWER_CLAIM_FINALIZED
    locked_claim.finalized_by = reviewer
    locked_claim.finalized_at = timezone.now()
    locked_claim.save(update_fields=["status", "finalized_by", "finalized_at", "updated_at"])
    record_audit_event(
        actor=reviewer,
        action=ACTION_GRADE_CHANGED,
        target_type="reviewer_exam_claim",
        target_id=str(locked_claim.pk),
        metadata={"attempt_id": locked_claim.attempt_id, "section_id": locked_claim.section_id, "stage": "finalized"},
    )
    return locked_claim


def _reviewer_assignment_assessments(assignment):
    """Return active Diploma Semester Exams covered by an assignment."""
    assessments = Assessment.objects.filter(
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        legacy_status=LEGACY_STATUS_ACTIVE,
        cohort_id=assignment.cohort_id,
    )
    if assignment.scope == REVIEWER_SCOPE_EXAM and assignment.assessment_id:
        assessments = assessments.filter(pk=assignment.assessment_id)
    elif assignment.scope == REVIEWER_SCOPE_SEMESTER and assignment.semester_id:
        assessments = assessments.filter(semester_id=assignment.semester_id)
    elif assignment.scope != REVIEWER_SCOPE_COHORT:
        return assessments.none()
    if assignment.semester_id:
        assessments = assessments.filter(semester_id=assignment.semester_id)
    if assignment.section_id:
        assessments = assessments.filter(pk=assignment.section.assessment_id)
    return assessments


def get_reviewer_ungraded_count(assignment) -> int:
    """Return ungraded offline code/section pairs within an assignment scope."""
    if assignment.access_mode not in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH):
        return 0
    assessments = _reviewer_assignment_assessments(assignment)
    sections = AssessmentLectureSection.objects.filter(assessment__in=assessments)
    if assignment.section_id:
        sections = sections.filter(pk=assignment.section_id)
    codes = SemesterExamCode.objects.filter(
        assessment__in=assessments,
        active=True,
        delivery_mode=DELIVERY_MODE_OFFLINE,
        diploma_enrollment__cohort=assignment.cohort,
    )
    if assignment.cluster_id:
        codes = codes.filter(
            diploma_enrollment__student__cluster_memberships__cluster_id=assignment.cluster_id,
        )
    total = codes.annotate(
        section_count=Count("assessment__lecture_sections", filter=Q(assessment__lecture_sections__in=sections), distinct=True),
    ).aggregate(total=Sum("section_count"))["total"] or 0
    graded_qs = OfflineSectionGrade.objects.filter(
        section__in=sections,
        exam_code__assessment__in=assessments,
        exam_code__delivery_mode=DELIVERY_MODE_OFFLINE,
        exam_code__diploma_enrollment__cohort=assignment.cohort,
    )
    if assignment.cluster_id:
        graded_qs = graded_qs.filter(
            exam_code__diploma_enrollment__student__cluster_memberships__cluster_id=assignment.cluster_id,
        )
    graded = graded_qs.values("exam_code_id", "section_id").distinct().count()
    return max(0, total - graded)


def _reviewer_scope_maps(assignments):
    """Resolve scoped assessments/sections for a batch of ReviewerAssignments.

    Set-based counterpart of ``_reviewer_assignment_assessments`` evaluated
    once across every supplied assignment. Returns primitive id mappings only:
    ``{assignment_id: {assessment_id}}``, ``{assignment_id: {section_id}}``,
    and ``{assessment_id: {section_id}}``. Never exposes student identity,
    enrollment, or attempt objects.
    """
    assignments = list(assignments)
    if not assignments:
        return {}, {}, {}
    cohort_ids = {assignment.cohort_id for assignment in assignments}
    assessment_rows = list(
        Assessment.objects.filter(
            assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
            legacy_status=LEGACY_STATUS_ACTIVE,
            cohort_id__in=cohort_ids,
        ).values("id", "cohort_id", "semester_id")
    )
    assessment_ids_by_cohort = {}
    assessment_ids_by_cohort_semester = {}
    for row in assessment_rows:
        assessment_ids_by_cohort.setdefault(row["cohort_id"], set()).add(row["id"])
        assessment_ids_by_cohort_semester.setdefault((row["cohort_id"], row["semester_id"]), set()).add(row["id"])

    candidate_ids = {}
    for assignment in assignments:
        ids = set(assessment_ids_by_cohort.get(assignment.cohort_id, ()))
        if assignment.semester_id:
            ids &= set(assessment_ids_by_cohort_semester.get((assignment.cohort_id, assignment.semester_id), ()))
        if assignment.scope == REVIEWER_SCOPE_EXAM and assignment.assessment_id:
            ids &= {assignment.assessment_id}
        elif assignment.scope == REVIEWER_SCOPE_SEMESTER and not assignment.semester_id:
            ids = set()
        elif assignment.scope != REVIEWER_SCOPE_COHORT:
            ids = set()
        if assignment.section_id:
            ids &= {assignment.section.assessment_id}
        candidate_ids[assignment.id] = ids

    all_assessment_ids = set().union(*candidate_ids.values()) if candidate_ids else set()
    section_rows = list(
        AssessmentLectureSection.objects.filter(assessment_id__in=all_assessment_ids).values("id", "assessment_id")
    )
    sections_by_assessment = {}
    for row in section_rows:
        sections_by_assessment.setdefault(row["assessment_id"], set()).add(row["id"])

    section_ids = {}
    for assignment in assignments:
        ids = set()
        for assessment_id in candidate_ids[assignment.id]:
            ids |= sections_by_assessment.get(assessment_id, set())
        if assignment.section_id:
            ids &= {assignment.section_id}
        section_ids[assignment.id] = ids
    return candidate_ids, section_ids, sections_by_assessment


def annotate_reviewer_ungraded_counts(assignments) -> dict[int, int]:
    """Return offline availability counts for the supplied assignments.

    Set-based: scoped assessments/sections, their offline exam codes, and the
    already-graded code/section pairs are loaded once and grouped in Python.
    The query count is bounded by the batch, not by the number of assignments.
    """
    assignments = list(assignments)
    if not assignments:
        return {}
    counts = {assignment.id: 0 for assignment in assignments}
    candidate_ids, section_ids, sections_by_assessment = _reviewer_scope_maps(assignments)
    all_assessment_ids = set().union(*(candidate_ids.values()))
    if not all_assessment_ids:
        return counts
    cohort_ids = {assignment.cohort_id for assignment in assignments}

    code_rows = list(
        SemesterExamCode.objects.filter(
            assessment_id__in=all_assessment_ids,
            delivery_mode=DELIVERY_MODE_OFFLINE,
            diploma_enrollment__cohort_id__in=cohort_ids,
        ).values("id", "assessment_id", "active", "diploma_enrollment__cohort_id")
    )
    code_active = {row["id"]: row["active"] for row in code_rows}
    code_assessment = {row["id"]: row["assessment_id"] for row in code_rows}
    codes_by_assessment_cohort = {}
    for row in code_rows:
        codes_by_assessment_cohort.setdefault((row["assessment_id"], row["diploma_enrollment__cohort_id"]), set()).add(
            row["id"]
        )

    cluster_ids = {assignment.cluster_id for assignment in assignments if assignment.cluster_id}
    code_ids_by_cluster = {}
    if cluster_ids:
        cluster_q = Q()
        for cluster_id in cluster_ids:
            cluster_q |= Q(diploma_enrollment__student__cluster_memberships__cluster_id=cluster_id)
        for code_id, cluster_id in (
            SemesterExamCode.objects.filter(
                cluster_q,
                assessment_id__in=all_assessment_ids,
                delivery_mode=DELIVERY_MODE_OFFLINE,
                diploma_enrollment__cohort_id__in=cohort_ids,
            )
            .values_list("id", "diploma_enrollment__student__cluster_memberships__cluster_id")
            .distinct()
        ):
            code_ids_by_cluster.setdefault(cluster_id, set()).add(code_id)

    all_section_ids = set().union(*(section_ids.values()))
    graded_by_code = {}
    for code_id, section_id in (
        OfflineSectionGrade.objects.filter(
            section_id__in=all_section_ids,
            exam_code__assessment_id__in=all_assessment_ids,
            exam_code__delivery_mode=DELIVERY_MODE_OFFLINE,
            exam_code__diploma_enrollment__cohort_id__in=cohort_ids,
        ).values_list("exam_code_id", "section_id")
    ):
        graded_by_code.setdefault(code_id, set()).add(section_id)

    for assignment in assignments:
        if assignment.access_mode not in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH):
            continue
        ids = candidate_ids[assignment.id]
        if not ids:
            continue
        grade_codes = set()
        for assessment_id in ids:
            grade_codes |= codes_by_assessment_cohort.get((assessment_id, assignment.cohort_id), set())
        if assignment.cluster_id:
            grade_codes &= code_ids_by_cluster.get(assignment.cluster_id, set())
        active_by_assessment = {}
        for code_id in grade_codes:
            if code_active[code_id]:
                assessment_id = code_assessment[code_id]
                active_by_assessment[assessment_id] = active_by_assessment.get(assessment_id, 0) + 1
        scoped_sections = section_ids[assignment.id]
        total = sum(
            code_count * len(sections_by_assessment.get(assessment_id, set()) & scoped_sections)
            for assessment_id, code_count in active_by_assessment.items()
        )
        graded = sum(
            len(graded_by_code.get(code_id, set()) & scoped_sections)
            for code_id in grade_codes
        )
        counts[assignment.id] = max(0, total - graded)
    return counts


def annotate_reviewer_online_ungraded_counts(assignments) -> dict[int, int]:
    """Count available anonymous online submissions per assignment.

    Set-based: candidate attempts, newest-attempt state, unscored written
    section coverage, cluster membership, and claim state are loaded once and
    grouped in Python. The query count is bounded by the batch, not by the
    number of assignments. Only primitive attempt ids flow through the
    groupings; student identity is never exposed.
    """
    assignments = list(assignments)
    if not assignments:
        return {}
    counts = {assignment.id: 0 for assignment in assignments}
    online_assignment_ids = {
        assignment.id
        for assignment in assignments
        if assignment.access_mode in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH)
    }
    if not online_assignment_ids:
        return counts
    candidate_ids, _, _ = _reviewer_scope_maps(assignments)
    all_assessment_ids = set().union(*(candidate_ids.values()))
    if not all_assessment_ids:
        return counts

    attempt_rows = list(
        AssessmentAttempt.objects.filter(
            assessment_id__in=all_assessment_ids,
            status__in=[ATTEMPT_STATUS_SUBMITTED, ATTEMPT_STATUS_UNDER_REVIEW, ATTEMPT_STATUS_GRADED],
            assessment__exam_codes__active=True,
            assessment__exam_codes__delivery_mode=DELIVERY_MODE_ONLINE,
            assessment__exam_codes__diploma_enrollment__student__user_id=F("student_id"),
            snapshot_questions__question_type=QUESTION_TYPE_WRITTEN,
            snapshot_questions__answers__score__isnull=True,
        )
        .values("id", "assessment_id", "student_id", "attempt_number")
        .distinct()
    )
    latest_number_by_student = {}
    for row in (
        AssessmentAttempt.objects.filter(assessment_id__in=all_assessment_ids)
        .exclude(status__in=[ATTEMPT_STATUS_CANCELLED, ATTEMPT_STATUS_EXPIRED])
        .values("assessment_id", "student_id")
        .annotate(latest_number=Max("attempt_number"))
    ):
        latest_number_by_student[(row["assessment_id"], row["student_id"])] = row["latest_number"]
    attempts_by_assessment = {}
    for row in attempt_rows:
        if row["attempt_number"] != latest_number_by_student.get((row["assessment_id"], row["student_id"])):
            continue
        attempts_by_assessment.setdefault(row["assessment_id"], []).append(row["id"])
    candidate_attempt_ids = set().union(*(attempts_by_assessment.values()))

    unscored_sections = {}
    for attempt_id, section_id in (
        AssessmentAttemptQuestion.objects.filter(
            attempt_id__in=candidate_attempt_ids,
            question_type=QUESTION_TYPE_WRITTEN,
            answers__score__isnull=True,
        )
        .values_list("attempt_id", "section_id")
        .distinct()
    ):
        unscored_sections.setdefault(attempt_id, set()).add(section_id)

    cluster_ids = {assignment.cluster_id for assignment in assignments if assignment.cluster_id}
    clusters_by_attempt = {}
    if cluster_ids:
        cluster_q = Q()
        for cluster_id in cluster_ids:
            cluster_q |= Q(student__student_profile__cluster_memberships__cluster_id=cluster_id)
        for attempt_id, cluster_id in (
            AssessmentAttempt.objects.filter(cluster_q, assessment_id__in=all_assessment_ids)
            .values_list("id", "student__student_profile__cluster_memberships__cluster_id")
            .distinct()
        ):
            clusters_by_attempt.setdefault(attempt_id, set()).add(cluster_id)

    claimed_attempt_ids = set(
        ReviewerExamClaim.objects.filter(attempt_id__in=candidate_attempt_ids)
        .values_list("attempt_id", flat=True)
        .distinct()
    )

    for assignment in assignments:
        if assignment.id not in online_assignment_ids:
            continue
        count = 0
        for assessment_id in candidate_ids[assignment.id]:
            for attempt_id in attempts_by_assessment.get(assessment_id, ()):
                if assignment.section_id and assignment.section_id not in unscored_sections.get(attempt_id, ()):
                    continue
                if assignment.cluster_id and assignment.cluster_id not in clusters_by_attempt.get(attempt_id, ()):
                    continue
                if attempt_id in claimed_attempt_ids:
                    continue
                count += 1
        counts[assignment.id] = count
    return counts


def reviewer_grade_section(exam_code_id: int, section_id: int, score: Decimal, grader, assignment) -> OfflineSectionGrade:
    """Enter or update an offline section grade as a reviewer.

    Hardened (P3-02): locks the code, requires offline delivery and the exact
    assigned section, validates against the section maximum, and audits.
    """
    with transaction.atomic():
        code = SemesterExamCode.objects.select_for_update().select_related(
            "assessment", "diploma_enrollment",
        ).get(pk=exam_code_id)
        if not assignment.active or assignment.reviewer_id != grader.id:
            raise ValueError(_("This Reviewer assignment is not active for grading."))
        if assignment.access_mode not in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH):
            raise ValueError(_("This assignment does not allow offline grading."))
        if code.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
            raise ValueError(_("Legacy semester exam data is read-only."))
        if code.delivery_mode != DELIVERY_MODE_OFFLINE:
            raise ValueError(_("Offline section grades require an offline exam code."))
        section = AssessmentLectureSection.objects.select_for_update().get(pk=section_id)
        if not can_reviewer_grade_code(assignment, code) or not can_reviewer_grade_section(assignment, section):
            raise ValueError(_("This exam code or section is outside your Reviewer assignment."))
        if section.assessment_id != code.assessment_id:
            raise ValueError(_("Section does not belong to the exam code's assessment."))
        score = validate_score(score, max_score=section.max_grade)
        existing_grade = OfflineSectionGrade.objects.filter(
            exam_code_id=exam_code_id, section_id=section_id,
        ).first()
        if existing_grade:
            raise ValueError(_("This exam section has already been graded."))
        grade, created = OfflineSectionGrade.objects.update_or_create(
            exam_code_id=exam_code_id, section_id=section_id,
            defaults={"score": score, "graded_by": grader},
        )
        # Once the last assigned section is graded, persist the offline
        # attempt record so Admin review and Semester Evaluation see it.
        if _offline_code_grading_complete(code):
            finalize_offline_attempt(code.id)
        record_audit_event(
            actor=grader,
            action=ACTION_GRADE_CHANGED,
            target_type="offline_section_grade",
            target_id=str(grade.pk),
            metadata={"exam_code_id": exam_code_id, "section_id": section_id, "score": str(score)},
        )
    return grade


def reviewer_grade_written_answer(answer_id: int, score: Decimal, grader, assignment) -> AssessmentAttemptSnapshotAnswer:
    """Grade one anonymous written snapshot answer within a ReviewerAssignment.

    Locks the answer, re-validates the assignment scope (cohort/semester/
    section/assessment, written-only), saves the score, recomputes the attempt
    grade set-wise, and audits. Reviewer can never grade MCQ.
    """
    with transaction.atomic():
        answer = (
            AssessmentAttemptSnapshotAnswer.objects.select_for_update()
            .select_related("attempt_question__section", "attempt_question__attempt__assessment")
            .get(pk=answer_id)
        )
        sq = answer.attempt_question
        assessment = sq.attempt.assessment
        if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
            raise ValueError(_("Legacy semester exam data is read-only."))
        if sq.question_type != QUESTION_TYPE_WRITTEN:
            raise ValueError(_("Only written answers can be graded by a Reviewer."))
        if not assignment.active or assignment.reviewer_id != grader.id:
            raise ValueError(_("This Reviewer assignment is not active for online grading."))
        if assignment.access_mode not in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH):
            raise ValueError(_("This assignment does not allow online grading."))
        if assignment.section_id and sq.section_id != assignment.section_id:
            raise ValueError(_("Answer is outside your assigned section."))
        claim = ReviewerExamClaim.objects.filter(
            assignment=assignment,
            attempt=sq.attempt,
            claimed_by=grader,
            status=REVIEWER_CLAIM_IN_PROGRESS,
        ).first()
        if not claim:
            raise ValueError(_("Request and keep the anonymous submission before grading it."))
        if answer.score is not None:
            raise ValueError(_("This answer has already been graded."))
        if not _reviewer_assignment_assessments(assignment).filter(pk=assessment.pk).exists():
            raise ValueError(_("Answer is outside your assigned assessment."))
        if assignment.cohort_id != assessment.cohort_id or assignment.semester_id != assessment.semester_id:
            raise ValueError(_("Answer is outside your assigned cohort/semester."))
        score = validate_score(score, max_score=sq.points)
        answer.score = score
        answer.graded_by = grader
        answer.graded_at = timezone.now()
        answer.save(update_fields=["score", "graded_by", "graded_at", "updated_at"])
        _update_diploma_attempt_grade(sq.attempt)
        record_audit_event(
            actor=grader,
            action=ACTION_GRADE_CHANGED,
            target_type="assessment_attempt_snapshot_answer",
            target_id=str(answer.pk),
            metadata={
                "attempt_id": sq.attempt_id,
                "assessment_id": assessment.id,
                "question_id": sq.pk,
                "section_id": sq.section_id,
                "score": str(score),
            },
        )
    return answer


def can_reviewer_grade_code(assignment, exam_code) -> bool:
    """Check that the exam code belongs to the reviewer's assigned scope."""
    if assignment.access_mode not in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH):
        return False
    if exam_code.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        return False
    if assignment.cohort_id != exam_code.diploma_enrollment.cohort_id:
        return False
    if not _reviewer_assignment_assessments(assignment).filter(pk=exam_code.assessment_id).exists():
        return False
    if assignment.cluster_id and not StudentClusterMembership.objects.filter(
        cluster_id=assignment.cluster_id,
        student_id=exam_code.diploma_enrollment.student_id,
    ).exists():
        return False
    return True


def can_reviewer_grade_section(assignment, section) -> bool:
    """Check that a section belongs to the reviewer's assignment scope."""
    if assignment.section_id and assignment.section_id != section.pk:
        return False
    return _reviewer_assignment_assessments(assignment).filter(pk=section.assessment_id).exists()


def can_take_diploma_assessment(student, assessment):
    if not has_active_enrollment(student, assessment):
        return False
    if assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM:
        code = SemesterExamCode.objects.filter(
            assessment=assessment,
            diploma_enrollment__student__user=student,
            active=True,
        ).first()
        if code and code.delivery_mode == DELIVERY_MODE_OFFLINE:
            return False
    return True


# ---------------------------------------------------------------------------
# Exceptional assessment access overrides
# ---------------------------------------------------------------------------


@transaction.atomic
def create_assessment_access_overrides(
    assessment,
    *,
    diploma_enrollments=None,
    course_enrollments=None,
    open_at,
    close_at,
    reason,
    actor,
    force_online=False,
) -> list[SemesterExamAccessOverride]:
    is_course_quiz = assessment.assessment_type == ASSESSMENT_TYPE_QUIZ
    is_semester_exam = assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
    if not (is_course_quiz or is_semester_exam):
        raise ValueError(_("Exceptional access is only available for quizzes and semester exams."))
    if is_course_quiz and (assessment.course_version_id is None or assessment.cohort_id is not None):
        raise ValueError(_("Quiz exceptional access requires a course-scoped quiz."))
    if is_semester_exam and (
        assessment.course_version_id is not None
        or not assessment.cohort_id
        or not assessment.semester_id
    ):
        raise ValueError(_("Diploma assessment exceptional access requires a diploma-scoped assessment."))
    if open_at >= close_at:
        raise ValueError(_("open_at must be before close_at."))
    if not reason:
        raise ValueError(_("Reason is required."))

    diploma_enrollments = diploma_enrollments or []
    course_enrollments = course_enrollments or []
    if is_course_quiz and diploma_enrollments:
        raise ValueError(_("A course quiz override must target course enrollments."))
    if is_semester_exam and course_enrollments:
        raise ValueError(_("A semester exam override must target diploma enrollments."))

    if is_semester_exam:
        generate_exam_codes(assessment, actor)

    created = []
    for enrollment in diploma_enrollments:
        if not is_semester_exam or enrollment.cohort_id != assessment.cohort_id:
            continue
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            continue
        if SemesterExamAccessOverride.objects.filter(
            assessment=assessment, diploma_enrollment=enrollment,
            status=EXAM_OVERRIDE_ACTIVE,
        ).exists():
            continue

        exam_code = SemesterExamCode.objects.filter(
            assessment=assessment, diploma_enrollment=enrollment,
        ).first()

        if force_online and exam_code:
            exam_code = change_diploma_enrollment_delivery_mode(
                enrollment.id,
                DELIVERY_MODE_ONLINE,
                actor,
                source_code_id=exam_code.id,
            )

        override = SemesterExamAccessOverride.objects.create(
            assessment=assessment,
            diploma_enrollment=enrollment,
            exam_code=exam_code,
            open_at=open_at,
            close_at=close_at,
            max_attempts=1,
            reason=reason,
            created_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
            target_type="SemesterExamAccessOverride",
            target_id=str(override.pk),
            metadata={
                "assessment_id": assessment.pk,
                "enrollment_id": enrollment.pk,
                "reason": reason,
            },
        )
        created.append(override)

    for enrollment in course_enrollments:
        if not is_course_quiz or enrollment.course_version_id != assessment.course_version_id:
            continue
        if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
            continue
        if SemesterExamAccessOverride.objects.filter(
            assessment=assessment, course_enrollment=enrollment,
            status=EXAM_OVERRIDE_ACTIVE,
        ).exists():
            continue
        override = SemesterExamAccessOverride.objects.create(
            assessment=assessment,
            course_enrollment=enrollment,
            open_at=open_at,
            close_at=close_at,
            max_attempts=1,
            reason=reason,
            created_by=actor,
        )
        record_audit_event(
            actor=actor,
            action=ACTION_EXAM_ACCESS_OVERRIDE_CREATED,
            target_type="SemesterExamAccessOverride",
            target_id=str(override.pk),
            metadata={
                "assessment_id": assessment.pk,
                "course_enrollment_id": enrollment.pk,
                "reason": reason,
            },
        )
        created.append(override)
    return created


def create_semester_exam_access_overrides(
    assessment, enrollments, *, open_at, close_at, reason, actor, force_online=False,
) -> list[SemesterExamAccessOverride]:
    """Backward-compatible wrapper for existing semester-exam callers."""
    return create_assessment_access_overrides(
        assessment,
        diploma_enrollments=enrollments,
        open_at=open_at,
        close_at=close_at,
        reason=reason,
        actor=actor,
        force_online=force_online,
    )


def cancel_assessment_access_override(override, *, actor, reason="") -> SemesterExamAccessOverride:
    if not reason.strip():
        raise ValueError(_("Reason is required to cancel an override."))
    if override.status != EXAM_OVERRIDE_ACTIVE:
        raise ValueError(_("Override is not active."))
    override.status = EXAM_OVERRIDE_CANCELLED
    override.cancelled_by = actor
    override.cancelled_at = timezone.now()
    override.save()
    record_audit_event(
        actor=actor,
        action=ACTION_EXAM_ACCESS_OVERRIDE_CANCELLED,
        target_type="SemesterExamAccessOverride",
        target_id=str(override.pk),
        metadata={"reason": reason},
    )
    return override


def cancel_semester_exam_access_override(override, *, actor, reason="") -> SemesterExamAccessOverride:
    """Backward-compatible wrapper for existing semester-exam callers."""
    return cancel_assessment_access_override(override, actor=actor, reason=reason)


def get_active_assessment_access_override(
    student,
    assessment,
    now=None,
    *,
    enrollment=None,
) -> SemesterExamAccessOverride | None:
    now = now or timezone.now()
    if assessment.assessment_type == ASSESSMENT_TYPE_QUIZ and assessment.course_version_id:
        if enrollment is None:
            enrollment = CourseEnrollment.objects.filter(
                student__user=student,
                course_version_id=assessment.course_version_id,
                status=ENROLLMENT_STATUS_ACTIVE,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            ).first()
        if enrollment:
            return SemesterExamAccessOverride.objects.filter(
                course_enrollment=enrollment,
                assessment=assessment,
            ).filter(
                Q(status=EXAM_OVERRIDE_ACTIVE)
                | Q(status=EXAM_OVERRIDE_USED, attempt__student=student, attempt__status=ATTEMPT_STATUS_IN_PROGRESS),
            ).order_by("-created_at").first()
    if (
        assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
        and assessment.cohort_id
        and assessment.semester_id
        and assessment.course_version_id is None
    ):
        if enrollment is None:
            enrollment = DiplomaEnrollment.objects.filter(
                student__user=student,
                cohort_id=assessment.cohort_id,
                status=ENROLLMENT_STATUS_ACTIVE,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            ).first()
        if enrollment:
            return SemesterExamAccessOverride.objects.filter(
                diploma_enrollment=enrollment,
                assessment=assessment,
            ).filter(
                Q(status=EXAM_OVERRIDE_ACTIVE)
                | Q(status=EXAM_OVERRIDE_USED, attempt__student=student, attempt__status=ATTEMPT_STATUS_IN_PROGRESS),
            ).order_by("-created_at").first()
    return None


def get_active_semester_exam_override(student, assessment, now=None) -> SemesterExamAccessOverride | None:
    """Backward-compatible wrapper for existing semester-exam callers."""
    return get_active_assessment_access_override(student, assessment, now)


def check_semester_exam_access(student, assessment, now=None) -> bool:
    try:
        check_assessment_access(student, assessment)
        return True
    except ValueError:
        now = now or timezone.now()
        override = get_active_assessment_access_override(student, assessment, now)
        if override and override.open_at <= now <= override.close_at:
            return True
        return False


@transaction.atomic
def consume_assessment_access_override(override, attempt) -> SemesterExamAccessOverride:
    SemesterExamAccessOverride.objects.select_for_update().get(pk=override.pk)
    override.refresh_from_db()
    override.used_count += 1
    override.used_at = timezone.now()
    override.attempt = attempt
    if override.used_count >= override.max_attempts:
        override.status = EXAM_OVERRIDE_USED
    override.save()
    record_audit_event(
        actor=None,
        action=ACTION_EXAM_ACCESS_OVERRIDE_USED,
        target_type="SemesterExamAccessOverride",
        target_id=str(override.pk),
        metadata={"attempt_id": attempt.pk, "assessment_id": override.assessment_id, "used_count": override.used_count},
    )
    return override


def consume_semester_exam_override(override, attempt) -> SemesterExamAccessOverride:
    """Backward-compatible wrapper for existing semester-exam callers."""
    return consume_assessment_access_override(override, attempt)
