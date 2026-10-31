"""Views for the assessments app (student and admin)."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from django.views.decorators.http import require_GET, require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    DiplomaLecture,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    Semester,
    StudentCluster,
)
from apps.assessments import write_behind as write_behind_protocol
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ASSESSMENT_TYPE_QUIZ,
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_RELEASED,
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    DELIVERY_MODE_CHOICES,
    DELIVERY_MODE_OFFLINE,
    GRADE_VISIBILITY_RELEASED,
    LEGACY_STATUS_ACTIVE,
    QUESTION_TYPE_MCQ,
    QUESTION_TYPE_WRITTEN,
    REVIEWER_ACCESS_BOTH,
    REVIEWER_ACCESS_CHOICES,
    REVIEWER_ACCESS_OFFLINE,
    REVIEWER_ACCESS_ONLINE,
    REVIEWER_CLAIM_IN_PROGRESS,
    REVIEWER_SCOPE_CHOICES,
    REVIEWER_SCOPE_COHORT,
    REVIEWER_SCOPE_EXAM,
    REVIEWER_SCOPE_SEMESTER,
)
from apps.assessments.forms import (
    AssessmentForm,
    AssessmentLectureSectionForm,
    AssessmentSectionManualQuestionForm,
    DiplomaLectureQuestionForm,
    QuestionForm,
    _choices_for_template,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentAttemptQuestion,
    AssessmentAttemptSnapshotAnswer,
    AssessmentChoice,
    AssessmentLectureSection,
    AssessmentQuestion,
    AssessmentSectionManualQuestion,
    DiplomaLectureChoice,
    DiplomaLectureQuestion,
    HomeworkSubmission,
    OfflineSectionGrade,
    ReviewerAssignment,
    ReviewerExamClaim,
    SemesterExamAccessOverride,
    SemesterExamCode,
)
from apps.assessments.prepared_take import chunk_snapshot_questions, get_chunk, get_meta
from apps.assessments.services import (
    AssessmentAnswerData,
    _semester_exam_attempt_details,
    _update_attempt_grade,
    _update_diploma_attempt_grade,
    accept_diploma_submission,
    add_homework_attachment,
    admin_review_semester_exam,
    annotate_reviewer_counts,
    assessment_audit_target,
    attempt_grades_visible,
    autosave_attempt,
    can_reviewer_grade_code,
    cancel_assessment_access_override,
    change_exam_code_delivery_mode,
    check_assessment_access,
    check_delivery_mode,
    claim_next_reviewer_submission,
    create_assessment_access_overrides,
    delete_assessment_record,
    diploma_autosave_attempt,
    ensure_active_assessment,
    enter_offline_section_grade,
    finalize_reviewer_claim,
    generate_exam_codes,
    get_active_reviewer_claim,
    get_assessment_readiness,
    get_available_bank_mcq_count,
    get_grading_queue,
    get_homework_pending_grading,
    get_recently_graded_attempts,
    get_semester_exam_review_queue,
    get_student_assessments,
    get_student_attempts,
    get_student_homework_submission,
    get_student_result_projection,
    grade_diploma_snapshot_answer,
    grade_homework,
    grade_written_answer,
    grading_audit_metadata,
    has_active_enrollment,
    hide_all_released,
    hide_attempt_grades,
    release_all_graded,
    release_attempt_grades,
    remove_homework_attachment,
    reviewer_claim_items,
    reviewer_grade_section,
    reviewer_grade_written_answer,
    revoke_reviewer_assignment,
    set_assessment_grade_visibility,
    start_assessment,
    student_audit_subject,
    submit_assessment,
    submit_homework_submission,
    sync_course_quiz_total_points,
    valid_diploma_submission_id,
)
from apps.audit.constants import (
    ACTION_ASSESSMENT_RECALCULATED,
    ACTION_EXAM_CREATED,
    ACTION_EXAM_TOGGLED,
    ACTION_EXAM_UPDATED,
    ACTION_GRADE_CHANGED,
    ACTION_GRADE_OVERRIDE,
    ACTION_QUESTION_CREATED,
    ACTION_QUESTION_DELETED,
    ACTION_QUESTION_UPDATED,
    ACTION_REVIEWER_ASSIGNMENT_CREATED,
    SECTION_ASSESSMENTS,
)
from apps.audit.services import record_audit_event
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.performance_middleware import stage as performance_stage
from apps.core.permissions import (
    ROLE_EXAM_STAFF,
    ROLE_HOMEWORK_GRADING_STAFF,
    ROLE_REVIEW_STAFF,
    ROLE_REVIEWER,
    ROLE_SENIOR_STAFF,
    assessment_scope_filter,
    can_moderator_grade_homework,
    is_admin,
    role_required,
    safe_int,
    student_required,
    validate_score,
)
from apps.core.views import safe_next_url
from apps.storage.services import FileValidationError, StorageError, storage_service

# A recorded submission must open the read-only review, never the take flow.
_COMPLETED_ATTEMPT_STATUSES = (
    ATTEMPT_STATUS_SUBMITTED,
    ATTEMPT_STATUS_UNDER_REVIEW,
    ATTEMPT_STATUS_GRADED,
    ATTEMPT_STATUS_RELEASED,
)


def _completed_attempt_redirect(student, assessment):
    """Send a submitted attempt to its read-only review instead of the take flow."""
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment, student=student,
    ).order_by("-created_at").first()
    if not attempt or attempt.status not in _COMPLETED_ATTEMPT_STATUSES:
        return None
    readiness = get_assessment_readiness(student, assessment, attempt)
    if readiness["state"] in ("ready", "continue"):
        return None
    return redirect("assessments:results", assessment_id=assessment.id)


def _get_assessment_queue_context(request, page_label):
    """Resolve a safe course-version or cohort/semester queue context."""
    def query_id(name):
        raw_value = request.GET.get(name)
        if not raw_value:
            return None
        value = safe_int(raw_value)
        if value is None or value < 1:
            raise Http404
        return value

    course_version_id = query_id("course_version_id")
    cohort_id = query_id("cohort_id")
    semester_id = query_id("semester_id")
    if course_version_id and (cohort_id or semester_id):
        raise Http404
    if bool(cohort_id) != bool(semester_id):
        raise Http404

    scope = None
    back_url = ""
    scope_filters = {
        "course_version_id": None,
        "cohort_id": None,
        "semester_id": None,
    }
    breadcrumbs = [{"label": _("Assessments")}]

    if course_version_id:
        if not is_admin(request.user):
            raise Http404
        version = get_object_or_404(
            CourseVersion.objects.select_related("course"),
            id=course_version_id,
        )
        scope = {"course_version": version}
        scope_filters["course_version_id"] = version.id
        if is_admin(request.user):
            back_url = reverse(
                "academics:admin_version_detail",
                args=[version.course_id, version.id],
            )
        breadcrumbs.extend([
            {"label": version.course.title},
            {"label": version.label},
        ])
    elif cohort_id and semester_id:
        cohort = get_object_or_404(Cohort.objects.select_related("diploma"), id=cohort_id)
        semester = get_object_or_404(Semester.objects.select_related("diploma"), id=semester_id)
        if cohort.diploma_id != semester.diploma_id:
            raise Http404
        if not is_admin(request.user) and not (
            ModeratorAssignment.objects.filter(
                moderator=request.user,
                cohort=cohort,
                active=True,
            ).exists()
            or ModeratorStudentAssignment.objects.filter(
                moderator=request.user,
                cohort=cohort,
                active=True,
            ).exists()
        ):
            raise Http404
        scope = {"cohort": cohort, "semester": semester}
        scope_filters["cohort_id"] = cohort.id
        scope_filters["semester_id"] = semester.id
        if is_admin(request.user):
            back_url = reverse(
                "progression:admin_cohort_semester_progression",
                args=[cohort.diploma_id, cohort.id, semester.id],
            )
        breadcrumbs.extend([
            {"label": cohort.diploma.title},
            {"label": cohort.name},
            {"label": semester.title},
        ])

    breadcrumbs.append({"label": page_label})
    return {
        "assessment_scope": scope,
        "assessment_scope_filters": scope_filters,
        "breadcrumb_items": breadcrumbs,
        "back_url": back_url,
        "page_title": page_label,
    }


def _redirect_to_grading_detail(request, route_name, **route_kwargs):
    detail_url = reverse(route_name, kwargs=route_kwargs)
    next_url = safe_next_url(request)
    if next_url:
        detail_url = f"{detail_url}?{urlencode({'next': next_url})}"
    return redirect(detail_url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _htmx_redirect(response, target):
    response["HX-Redirect"] = target
    return response


def _legacy_redirect(request, assessment):
    """Return a redirect response when the assessment is quarantined legacy data.

    Legacy/archived/review-required rows are preserved read-only (P2-01).
    Returning ``None`` means the assessment is active and the caller may proceed.
    """
    if assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        messages.error(request, _("This assessment is preserved legacy data and is read-only."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    return None


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@student_required
def student_list(request):
    assessments = list(get_student_assessments(request.user))
    attempts = list(get_student_attempts(request.user))
    latest_attempts = {}
    for attempt in attempts:
        latest_attempts.setdefault(attempt.assessment_id, attempt)
    cards = [
        {
            "assessment": assessment,
            "latest_attempt": latest_attempts.get(assessment.id),
            "readiness": get_assessment_readiness(
                request.user, assessment, latest_attempts.get(assessment.id)
            ),
        }
        for assessment in assessments
    ]
    # P5-02: separate Homework, Semester Exam, and Course Quiz into focused
    # sections so actions match each workflow's readiness and release state.
    groups = {
        "homework": [c for c in cards if c["assessment"].assessment_type == ASSESSMENT_TYPE_HOMEWORK],
        "semester_exam": [c for c in cards if c["assessment"].assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM],
        "quiz": [c for c in cards if c["assessment"].assessment_type == ASSESSMENT_TYPE_QUIZ],
    }
    return render(
        request,
        "ui/pages/student/assessment_list.html",
        {
            "assessment_cards": cards,
            "assessment_groups": groups,
            "attempts": attempts,
        },
    )


@student_required
def assessment_instructions(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        return redirect("assessments:take", assessment_id=assessment.id)
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment, student=request.user,
    ).order_by("-created_at").first()
    readiness = get_assessment_readiness(request.user, assessment, attempt)
    if (
        attempt
        and attempt.status in _COMPLETED_ATTEMPT_STATUSES
        and readiness["state"] not in ("ready", "continue")
    ):
        return redirect("assessments:results", assessment_id=assessment.id)
    has_in_progress_attempt = bool(attempt and attempt.status == ATTEMPT_STATUS_IN_PROGRESS)
    if readiness.get("state") == "restricted":
        messages.error(
            request,
            readiness.get("block_reason") or _("Complete required lectures"),
        )
        if assessment.course_version_id:
            return redirect(
                "academics:student_course_detail",
                version_id=assessment.course_version_id,
            )
        return redirect("assessments:student_list")
    try:
        check_assessment_access(
            request.user,
            assessment,
            existing_attempt=has_in_progress_attempt,
            allow_late_submission=assessment.allow_late_submission,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    question_type_counts = dict(
        assessment.questions.values("question_type").annotate(total=Count("id")).values_list("question_type", "total")
    )
    sections = list(
        assessment.lecture_sections.select_related("lecture").prefetch_related("manual_questions").order_by("order")
    )
    if assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id:
        question_type_counts = {
            QUESTION_TYPE_MCQ: sum(section.mcq_count for section in sections),
            QUESTION_TYPE_WRITTEN: sum(section.manual_questions.count() for section in sections),
        }
    return render(
        request,
        "ui/pages/student/assessment_instructions.html",
        {
            "assessment": assessment,
            "attempt": attempt,
            "readiness": readiness,
            "question_count": sum(question_type_counts.values()),
            "mcq_count": question_type_counts.get(QUESTION_TYPE_MCQ, 0),
            "written_count": question_type_counts.get(QUESTION_TYPE_WRITTEN, 0),
            "sections": sections,
            "is_diploma_assessment": bool(
                assessment.course_version_id is None
                and assessment.semester_id
                and assessment.cohort_id
            ),
            "is_semester_evaluation_assessment": bool(
                assessment.assessment_type in (
                    ASSESSMENT_TYPE_HOMEWORK,
                    ASSESSMENT_TYPE_SEMESTER_EXAM,
                )
                and assessment.course_version_id is None
                and assessment.semester_id
                and assessment.cohort_id
            ),
            "uses_autosave": True,
        },
    )


@student_required
@require_POST
def start_assessment_view(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("Homework assessments use the upload flow."))
        return redirect("assessments:take", assessment_id=assessment_id)
    try:
        with performance_stage(request, "start_transition"):
            start_assessment(request.user, assessment)
        messages.success(request, _("Assessment started."))
        return redirect("assessments:take", assessment_id=assessment.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:instructions", assessment_id=assessment_id)


@student_required
def take_assessment(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        with performance_stage(request, "eligibility"):
            try:
                ensure_active_assessment(assessment)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("assessments:student_list")
            try:
                check_delivery_mode(request.user, assessment)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("assessments:student_list")
        now = timezone.now()
        if not has_active_enrollment(request.user, assessment):
            messages.error(request, _("You do not have an active enrollment for this course."))
            return redirect("assessments:student_list")
        if assessment.open_at and now < assessment.open_at:
            messages.error(request, _("Assessment has not opened yet."))
            return redirect("assessments:student_list")
        if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
            messages.error(request, _("Assessment has already closed."))
            return redirect("assessments:student_list")
        submission = get_student_homework_submission(request.user, assessment)
        attachments = list(submission.attachments.all()) if submission else []
        remaining_uploads = None
        if assessment.max_upload_files is not None:
            remaining_uploads = max(0, assessment.max_upload_files - len(attachments))
        homework_download_urls = {}
        for attachment in attachments:
            homework_download_urls[attachment.id] = storage_service.get_protected_download_url(
                attachment.storage_key,
                user=request.user,
                permission_fn=lambda u, _a=attachment: (
                    u == _a.student
                    or can_moderator_grade_homework(u, _a)
                ),
            )
        return render(
            request,
            "ui/pages/student/assessment_take.html",
            {
                "assessment": assessment,
                "homework_submission": submission,
                "homework_attachments": attachments,
                "homework_download_urls": homework_download_urls,
                "remaining_uploads": remaining_uploads,
            },
        )
    is_diploma = bool(
        assessment.course_version_id is None
        and assessment.semester_id
        and assessment.cohort_id
    )
    if not is_diploma:
        attempt = AssessmentAttempt.objects.filter(
            assessment=assessment,
            student=request.user,
            status=ATTEMPT_STATUS_IN_PROGRESS,
        ).first()
        if not attempt:
            completed_redirect = _completed_attempt_redirect(request.user, assessment)
            if completed_redirect:
                return completed_redirect
        try:
            check_assessment_access(
                request.user,
                assessment,
                existing_attempt=bool(attempt),
                allow_late_submission=assessment.allow_late_submission,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("assessments:student_list")
        if not attempt:
            messages.error(request, _("No active attempt found."))
            return redirect("assessments:instructions", assessment_id=assessment_id)
        questions = assessment.questions.prefetch_related("choices").all()
        saved_answers = {
            answer.question_id: answer
            for answer in AssessmentAnswer.objects.filter(attempt=attempt)
        }
        return render(
            request,
            "ui/pages/student/assessment_take.html",
            {
                "assessment": assessment,
                "attempt": attempt,
                "questions": questions,
                "saved_answers": saved_answers,
            },
        )
    # Diploma / semester exam hot path: ≤3 domain queries
    # Q1: attempt + assessment + enrollment exists.
    now = timezone.now()
    with performance_stage(request, "eligibility_attempt"):
        enrolled_exists = DiplomaEnrollment.objects.filter(
            student__user=request.user,
            cohort_id=OuterRef("assessment__cohort_id"),
            status=ENROLLMENT_STATUS_ACTIVE,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        attempt_qs = AssessmentAttempt.objects.filter(
            assessment_id=assessment_id,
            student=request.user,
            status=ATTEMPT_STATUS_IN_PROGRESS,
        ).select_related("assessment").annotate(has_enrollment=Exists(enrolled_exists))
        attempt = attempt_qs.first()
        if not attempt:
            completed_redirect = _completed_attempt_redirect(request.user, assessment)
            if completed_redirect:
                return completed_redirect
            messages.error(request, _("No active attempt found."))
            return redirect("assessments:instructions", assessment_id=assessment_id)
        assessment = attempt.assessment
        # Validate remaining eligibility without extra queries
        if not assessment.is_active or assessment.legacy_status != LEGACY_STATUS_ACTIVE:
            messages.error(request, _("Assessment is not active."))
            return redirect("assessments:student_list")
        if not attempt.has_enrollment:
            messages.error(request, _("You do not have an active enrollment for this course."))
            return redirect("assessments:student_list")
        if assessment.open_at and now < assessment.open_at:
            messages.error(request, _("Assessment has not opened yet."))
            return redirect("assessments:student_list")
        if assessment.close_at and now > assessment.close_at and not assessment.allow_late_submission:
            messages.error(request, _("Assessment has already closed."))
            return redirect("assessments:student_list")
        try:
            check_assessment_access(
                request.user,
                assessment,
                existing_attempt=True,
                allow_late_submission=assessment.allow_late_submission,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("assessments:student_list")
    # Timer metadata (no query)
    remaining = 0
    timer_enabled = bool(attempt.started_at and assessment.duration_minutes)
    timer_deadline = None
    if timer_enabled:
        timer_deadline = attempt.started_at + timedelta(minutes=assessment.duration_minutes)
        elapsed = (now - attempt.started_at).total_seconds()
        remaining = max(0, int(assessment.duration_minutes * 60 - elapsed))
    # Q2: current persisted answers in one query (values, no model graph) - collapsed from 2 to 1
    with performance_stage(request, "answers"):
        rows = list(
            AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt
            ).values_list("attempt_question__order", "selected_choice_id", "written_text")
        )
        persisted = {}
        written_map = {}
        for order, choice_id, written_text in rows:
            if choice_id is not None:
                persisted[order] = choice_id
            if written_text:
                written_map[order] = written_text
    # Q3: Redis pending overlay (not SQL) + build answer_state JSON
    with performance_stage(request, "redis_pending_overlay"):
        pending = write_behind_protocol.latest_values(attempt.pk)
        answers = {}
        for order, choice_id in persisted.items():
            answers[f"q_{order}"] = choice_id
        for order, txt in written_map.items():
            answers[f"q_{order}"] = txt
        for k, v in pending.items():
            answers[k] = v
    # Determine chunk count from prepared meta; fall back to DB packing
    with performance_stage(request, "prepared_meta"):
        meta = get_meta(attempt.pk)
        if meta and meta.get("chunk_count"):
            chunk_count = meta["chunk_count"]
            question_count = meta["question_count"]
        else:
            chunk_count = None
            question_count = None
        # Inline first chunk HTML to avoid extra HTMX round-trip for interactive
        first_chunk_html = ""
        try:
            cbytes = get_chunk(attempt.pk, 1)
            if cbytes is not None:
                try:
                    first_chunk_html = gzip.decompress(cbytes).decode("utf-8")
                except Exception:
                    first_chunk_html = cbytes.decode("utf-8", errors="ignore")
        except Exception:
            first_chunk_html = ""
        if not first_chunk_html:
            # Fallback render the first chunk from the same lecture-aligned packing
            qs = list(
                AssessmentAttemptQuestion.objects.filter(attempt=attempt)
                .select_related("section__lecture")
                .order_by("order")
                .prefetch_related("choices")
            )
            chunks = chunk_snapshot_questions(qs)
            if chunks:
                first_chunk_html = render_to_string(
                    "ui/fragments/take_chunk.html", {"questions": chunks[0], "attempt": attempt}
                )
            if chunk_count is None:
                chunk_count = len(chunks)
                question_count = len(qs)
        if chunk_count is None:
            chunk_count = 0
        if question_count is None:
            question_count = 0
    chunk_numbers = list(range(2, chunk_count + 1)) if chunk_count > 1 else []
    answer_state = {"answers": answers, "generation": 0, "question_count": question_count, "chunk_count": chunk_count}
    answer_state_json = json.dumps(answer_state)
    with performance_stage(request, "template_render_shell"):
        return render(
            request,
            "ui/pages/student/assessment_take_shell.html",
            {
                "assessment": assessment,
                "attempt": attempt,
                "remaining_seconds": remaining,
                "timer_enabled": timer_enabled,
                "timer_deadline": timer_deadline,
                "timer_server_now": now,
                "chunk_numbers": chunk_numbers,
                "first_chunk_html": first_chunk_html,
                "answer_state_json": answer_state_json,
                "is_diploma_random": True,
            },
        )


@student_required
def take_chunk_view(request, assessment_id, chunk_no):
    """Serve pre-rendered gzip chunk. No template rendering on hot path."""
    # Collapse auth: attempt ownership + assessment in one query
    with performance_stage(request, "take_chunk_scope"):
        now = timezone.now()
        enrolled_exists = DiplomaEnrollment.objects.filter(
            student__user=request.user,
            cohort_id=OuterRef("assessment__cohort_id"),
            status=ENROLLMENT_STATUS_ACTIVE,
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        attempt = (
            AssessmentAttempt.objects.filter(
                assessment_id=assessment_id, student=request.user, status=ATTEMPT_STATUS_IN_PROGRESS
            )
            .select_related("assessment")
            .annotate(has_enrollment=Exists(enrolled_exists))
            .first()
        )
    if not attempt or not attempt.has_enrollment:
        return JsonResponse({"error": "no_active_attempt"}, status=404)
    try:
        check_assessment_access(
            request.user,
            attempt.assessment,
            existing_attempt=True,
            allow_late_submission=attempt.assessment.allow_late_submission,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    # Try prepared cache
    chunk_bytes = get_chunk(attempt.pk, int(chunk_no))
    if chunk_bytes is not None:
        # Check client accepts gzip
        accept = request.headers.get("Accept-Encoding", "")
        if "gzip" in accept:
            resp = HttpResponse(chunk_bytes, content_type="text/html; charset=utf-8")
            resp["Content-Encoding"] = "gzip"
            resp["Content-Length"] = str(len(chunk_bytes))
            resp["Vary"] = "Accept-Encoding"
            resp["Cache-Control"] = "private, max-age=60"
            # Avoid nginx double gzip
            resp["X-Accel-Buffering"] = "no"
            return resp
        else:
            try:
                html = gzip.decompress(chunk_bytes).decode("utf-8")
            except Exception:
                html = chunk_bytes.decode("utf-8", errors="ignore")
            return HttpResponse(html, content_type="text/html; charset=utf-8")
    # Fallback renders only this chunk from DB (still avoids full 100 rendering).
    qs = list(
        AssessmentAttemptQuestion.objects.filter(attempt=attempt)
        .select_related("section__lecture")
        .order_by("order")
        .prefetch_related("choices")
    )
    chunks = chunk_snapshot_questions(qs)
    chunk_index = int(chunk_no) - 1
    if chunk_index < 0 or chunk_index >= len(chunks):
        return HttpResponse("", content_type="text/html")
    chunk_qs = chunks[chunk_index]
    html = render_to_string("ui/fragments/take_chunk.html", {"questions": chunk_qs, "attempt": attempt})
    return HttpResponse(html, content_type="text/html")


@student_required
def take_answers_view(request, assessment_id):
    """Small JSON with current answer state (persisted + pending overlay)."""
    now = timezone.now()
    enrolled_exists = DiplomaEnrollment.objects.filter(
        student__user=request.user,
        cohort_id=OuterRef("assessment__cohort_id"),
        status=ENROLLMENT_STATUS_ACTIVE,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    attempt = (
        AssessmentAttempt.objects.filter(
            assessment_id=assessment_id, student=request.user, status=ATTEMPT_STATUS_IN_PROGRESS
        )
        .select_related("assessment")
        .annotate(has_enrollment=Exists(enrolled_exists))
        .first()
    )
    if not attempt or not attempt.has_enrollment:
        return JsonResponse({"error": "no_active_attempt"}, status=404)
    try:
        check_assessment_access(
            request.user,
            attempt.assessment,
            existing_attempt=True,
            allow_late_submission=attempt.assessment.allow_late_submission,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    persisted = dict(
        AssessmentAttemptSnapshotAnswer.objects.filter(attempt_question__attempt=attempt).values_list(
            "attempt_question__order", "selected_choice_id"
        )
    )
    written_map = dict(
        AssessmentAttemptSnapshotAnswer.objects.filter(
            attempt_question__attempt=attempt, attempt_question__question_type=QUESTION_TYPE_WRITTEN
        ).values_list("attempt_question__order", "written_text")
    )
    pending = write_behind_protocol.latest_values(attempt.pk)
    answers = {}
    for order, cid in persisted.items():
        if cid is not None:
            answers[f"q_{order}"] = cid
    for order, txt in written_map.items():
        if txt:
            answers[f"q_{order}"] = txt
    for k, v in pending.items():
        answers[k] = v
    return JsonResponse({"answers": answers, "attempt_id": attempt.pk})


@student_required
def submit_assessment_view(request, assessment_id):
    if request.method != "POST":
        return redirect("assessments:take", assessment_id=assessment_id)
    with performance_stage(request, "submit_scope"):
        attempt = (
            AssessmentAttempt.objects.filter(
                assessment_id=assessment_id, student=request.user, status=ATTEMPT_STATUS_IN_PROGRESS
            )
            .select_related("assessment")
            .first()
        )
    if not attempt:
        messages.error(request, _("No active attempt found."))
        return redirect("assessments:student_list")
    assessment = attempt.assessment
    # Lightweight active checks without extra queries
    if not assessment.is_active or assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        messages.error(request, _("Assessment is not active."))
        return redirect("assessments:student_list")
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("Homework assessments must be submitted through the upload form."))
        return redirect("assessments:take", assessment_id=assessment_id)
    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    if not is_diploma and not has_active_enrollment(request.user, assessment):
        messages.error(request, _("No active attempt found."))
        return redirect("assessments:student_list")
    if is_diploma:
        with performance_stage(request, "submit_payload"):
            answer_data = {k: v for k, v in request.POST.items() if k.startswith("q_")}
            submission_id = request.POST.get("submission_id") or request.headers.get("X-Submission-ID")
            if not valid_diploma_submission_id(submission_id):
                fingerprint = json.dumps(
                    {"attempt_id": attempt.pk, "answers": answer_data},
                    separators=(",", ":"), sort_keys=True,
                ).encode()
                submission_id = hashlib.sha256(fingerprint).hexdigest()
        try:
            with performance_stage(request, "submit_durable_accept"):
                accept_diploma_submission(attempt, submission_id, answer_data)
            # Pending autosave generations must not overwrite final submission.
            try:
                write_behind_protocol.discard(attempt.pk)
            except Exception:
                pass
            messages.info(request, _("Assessment received and will be finalized shortly."))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        try:
            answers = _parse_answers(request, assessment)
            submit_assessment(attempt, answers)
            messages.success(request, _("Assessment submitted successfully."))
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("assessments:results", assessment_id=assessment_id)


@student_required
@require_POST
def homework_add_attachment_view(request, assessment_id):
    """Add one or more files to the student's draft homework submission."""
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    try:
        ensure_active_assessment(assessment)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    uploaded_files = request.FILES.getlist("homework_files")
    if not uploaded_files:
        messages.error(request, _("Select at least one file to upload."))
        return redirect("assessments:take", assessment_id=assessment_id)
    added = 0
    for uploaded in uploaded_files:
        try:
            add_homework_attachment(
                student=request.user,
                assessment=assessment,
                file_data=uploaded.read(),
                original_filename=uploaded.name,
                content_type=uploaded.content_type,
            )
            added += 1
        except (FileValidationError, StorageError, ValueError) as exc:
            messages.error(request, f"{uploaded.name}: {exc}")
    if added:
        messages.success(
            request,
            ngettext_lazy("%(count)d file added.", "%(count)d files added.", added) % {"count": added},
        )
    return redirect("assessments:take", assessment_id=assessment_id)


@student_required
@require_POST
def homework_remove_attachment_view(request, assessment_id, attachment_id):
    """Remove one file from the student's draft homework submission."""
    try:
        assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
        remove_homework_attachment(request.user, assessment, attachment_id)
        messages.success(request, _("File removed."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:take", assessment_id=assessment_id)


@student_required
def submit_homework_view(request, assessment_id):
    """Explicitly submit the draft homework (one logical attempt)."""
    if request.method != "POST":
        return redirect("assessments:take", assessment_id=assessment_id)
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    try:
        ensure_active_assessment(assessment)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    try:
        submit_homework_submission(request.user, assessment)
        messages.success(request, _("Homework submitted successfully."))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:take", assessment_id=assessment_id)
    return redirect("assessments:results", assessment_id=assessment_id)


@student_required
@require_POST
def api_diploma_autosave(request, assessment_id):
    """POST autosave for any normal assessment in-progress attempt."""

    try:
        with performance_stage(request, "autosave_scope"):
            student = request.user
            assessment = Assessment.objects.get(id=assessment_id)
            attempt = AssessmentAttempt.objects.filter(
                assessment=assessment, student=student, status=ATTEMPT_STATUS_IN_PROGRESS,
            ).first()
        if not attempt:
            return JsonResponse({"error": "no_active_attempt"}, status=404)

        with performance_stage(request, "autosave_payload"):
            try:
                data = json.loads(request.body)
            except (ValueError, TypeError):
                return JsonResponse({"error": "invalid_json"}, status=400)
            if not isinstance(data, dict):
                return JsonResponse({"error": "invalid_json"}, status=400)

        answer_data = data.get("answers", {})
        if not isinstance(answer_data, dict):
            return JsonResponse({"error": "invalid_answers"}, status=400)
        try:
            is_diploma = bool(
                assessment.course_version_id is None
                and assessment.semester_id
                and assessment.cohort_id
            )
            with performance_stage(request, "autosave_persist"):
                if is_diploma:
                    diploma_autosave_attempt(attempt, answer_data)
                else:
                    autosave_attempt(attempt, _parse_json_autosave_answers(assessment, answer_data))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({"status": "ok", "saved_at": timezone.now().isoformat()})
    except Assessment.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)


def _parse_json_autosave_answers(assessment, answer_data):
    """Convert the take-page JSON payload into validated course answer data."""
    question_ids = []
    for key in answer_data:
        if key.startswith("question_"):
            try:
                question_ids.append(int(key.removeprefix("question_")))
            except (TypeError, ValueError):
                continue
    questions = {
        question.id: question
        for question in assessment.questions.prefetch_related("choices").filter(id__in=question_ids)
    }
    answers = []
    for key, raw in answer_data.items():
        if not key.startswith("question_"):
            continue
        try:
            question_id = int(key.removeprefix("question_"))
        except (TypeError, ValueError):
            continue
        question = questions.get(question_id)
        if not question:
            continue
        if isinstance(raw, dict):
            raw = raw.get("choice") if question.question_type == QUESTION_TYPE_MCQ else raw.get("text", "")
        raw = "" if raw is None else str(raw)
        if question.question_type == QUESTION_TYPE_MCQ:
            try:
                choice_id = int(raw) if raw else None
            except (TypeError, ValueError):
                choice_id = None
            answers.append(AssessmentAnswerData(question_id=question_id, selected_choice_id=choice_id))
        elif question.question_type == QUESTION_TYPE_WRITTEN:
            answers.append(AssessmentAnswerData(question_id=question_id, written_text=raw))
    return answers


@student_required
def assessment_results(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        submission = get_student_homework_submission(request.user, assessment)
        attachments = list(submission.attachments.all()) if submission else []
        homework_can_view_score = bool(
            submission and submission.attempt and attempt_grades_visible(submission.attempt)
        )
        homework_download_urls = {}
        for attachment in attachments:
            homework_download_urls[attachment.id] = storage_service.get_protected_download_url(
                attachment.storage_key,
                user=request.user,
                permission_fn=lambda u, _a=attachment: (
                    u == _a.student
                    or can_moderator_grade_homework(u, _a)
                ),
            )
        homework_result = None
        if submission:
            homework_result = {
                "status": submission.status,
                "status_label": submission.get_status_display(),
                "assessment_title": str(assessment),
                "total_points": assessment.total_points,
                "score": submission.score if homework_can_view_score else None,
                "feedback": submission.feedback if homework_can_view_score else "",
            }
        return render(
            request,
            "ui/pages/student/assessment_results.html",
            {
                "homework_result": homework_result,
                "homework_attachments": [
                    {"id": item.id, "original_filename": item.original_filename}
                    for item in attachments
                ],
                "homework_download_urls": homework_download_urls,
            },
        )
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment,
        student=request.user,
    ).order_by("-created_at").first()
    if not attempt:
        return redirect("assessments:student_list")

    # P2-04: students receive only the safe projection — no correctness flags,
    # answer keys, rubrics, or model answers, and no unreleased scores.
    result_projection = get_student_result_projection(attempt)
    visible = result_projection["visible_to_student"]
    attempt_projection = {
        "status": attempt.status if visible else "awaiting-release",
        "status_label": attempt.get_status_display() if visible else _("Awaiting release"),
        "assessment_title": str(assessment),
    }
    return render(
        request,
        "ui/pages/student/assessment_results.html",
        {
            "attempt_projection": attempt_projection,
            "result_projection": result_projection,
        },
    )


def _parse_answers(request, assessment):
    answers = []
    questions = {q.id: q for q in assessment.questions.prefetch_related("choices").all()}
    for key in request.POST:
        if not key.startswith("question_"):
            continue
        qid = safe_int(key.removeprefix("question_"))
        if qid is None:
            raise ValueError(_("Invalid question."))
    for qid, question in questions.items():
        value = request.POST.get(f"question_{qid}", "")
        if question.question_type == "mcq":
            choice_id = int(value) if value.isdigit() else None
            answers.append(
                AssessmentAnswerData(question_id=qid, selected_choice_id=choice_id)
            )
        elif question.question_type == "written":
            answers.append(
                AssessmentAnswerData(question_id=qid, written_text=value)
            )
    return answers


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_list(request):
    q = request.GET.get("q", "")
    type_filter = request.GET.get("type", "")
    legacy_filter = request.GET.get("legacy", "")
    student_id = request.GET.get("student_id", "")
    assessments = Assessment.objects.all().select_related("created_by").annotate(
        attempt_count=Count("attempts"),
    ).order_by("-created_at")
    assessments = assessments.filter(assessment_scope_filter(request.user))
    if q:
        assessments = assessments.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if type_filter:
        assessments = assessments.filter(assessment_type=type_filter)
    if legacy_filter == "active":
        assessments = assessments.filter(legacy_status=LEGACY_STATUS_ACTIVE)
    elif legacy_filter == "legacy":
        assessments = assessments.exclude(legacy_status=LEGACY_STATUS_ACTIVE)
    if student_id.isdigit():
        assessments = assessments.filter(attempts__student_id=int(student_id)).distinct()
    filters = []
    if type_filter is not None:
        filters.append({
            "name": "type",
            "label": _("Type"),
            "value": type_filter,
            "options": [
                {"value": "semester_exam", "label": _("Semester exam"), "selected": type_filter == "semester_exam"},
                {"value": "quiz", "label": _("Quiz"), "selected": type_filter == "quiz"},
                {"value": "homework", "label": _("Homework"), "selected": type_filter == "homework"},
            ],
        })
    if legacy_filter is not None:
        filters.append({
            "name": "legacy",
            "label": _("Status"),
            "value": legacy_filter,
            "options": [
                {"value": "active", "label": _("Active"), "selected": legacy_filter == "active"},
                {"value": "legacy", "label": _("Legacy / read-only"), "selected": legacy_filter == "legacy"},
            ],
        })
    page_obj, pagination_qs = paginate(request, assessments)
    ctx = {"page_obj": page_obj, "pagination_qs": pagination_qs, "search_value": q, "filters": filters, "is_admin_user": is_admin(request.user), "student_id": student_id}
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_assessment_table.html", ctx)
    return render(request, "ui/pages/admin/assessment_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_detail(request, assessment_id):
    assessment = get_object_or_404(
        Assessment.objects.prefetch_related("questions__choices", "attempts__student"),
        id=assessment_id,
    )
    sections = []
    section_total = 0
    is_diploma = bool(assessment.semester_id and assessment.cohort_id and not assessment.course_version_id)
    is_course_quiz = assessment.assessment_type == ASSESSMENT_TYPE_QUIZ and bool(assessment.course_version_id)
    if is_diploma:
        sections_qs = assessment.lecture_sections.select_related("lecture").prefetch_related("manual_questions").all()
        for s in sections_qs:
            s.available_count = get_available_bank_mcq_count(s.lecture)
            s.preview_bank = list(s.lecture.question_bank.filter(
                active=True, question_type=QUESTION_TYPE_MCQ,
            ).order_by("-points", "id").prefetch_related("choices")[:s.mcq_count])
            calculated = s.calculated_max_grade()
            section_total += calculated
            sections.append(s)
    return render(
        request,
        "ui/pages/admin/assessment_detail.html",
        {
            "assessment": assessment,
            "attempts": assessment.attempts.all(),
            "sections": sections,
            "section_total": section_total,
            "is_diploma": is_diploma,
            "is_course_quiz": is_course_quiz,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_recalculate_totals(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    is_diploma = bool(assessment.semester_id and assessment.cohort_id and not assessment.course_version_id)
    if not is_diploma:
        messages.error(request, _("Total recalculation is only available for diploma-scoped assessments."))
        return redirect("assessments:admin_detail", assessment_id=assessment_id)
    sections = list(assessment.lecture_sections.select_related("lecture").all())
    total_before = assessment.total_points
    section_total = 0
    for s in sections:
        calculated = s.calculated_max_grade()
        if s.max_grade != calculated:
            s.max_grade = calculated
            s.save(update_fields=["max_grade", "updated_at"])
        section_total += calculated
    if assessment.total_points != section_total:
        assessment.total_points = section_total
        assessment.save(update_fields=["total_points", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_ASSESSMENT_RECALCULATED,
        **assessment_audit_target(assessment),
        metadata={
            "assessment_id": assessment.pk,
            "assessment_name": assessment.title,
            "assessment_type": assessment.assessment_type,
            "section_count": len(sections),
            "section_ids": [section.pk for section in sections],
            "total_before": str(total_before),
            "total_after": str(section_total),
        },
    )
    messages.success(request, _("Assessment totals recalculated."))
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_preview(request, assessment_id):
    """Render the student-facing take page for admin preview (no attempt/upload)."""
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        return render(
            request,
            "ui/pages/student/assessment_take.html",
            {
                "assessment": assessment,
                "is_preview": True,
                "existing_submissions": [],
                "existing_download_urls": {},
                "remaining_uploads": 0,
            },
        )
    questions = assessment.questions.prefetch_related("choices").all()
    return render(
        request,
        "ui/pages/student/assessment_take.html",
        {
            "assessment": assessment,
            "is_preview": True,
            "attempt": None,
            "questions": questions,
            "saved_answers": {},
            "saved_snapshot_answers": {},
            "remaining_seconds": 0,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_scope_fields(request):
    scope_type = request.GET.get("scope_type", "")
    if request.GET.get("format") == "json":
        diploma_id = safe_int(request.GET.get("diploma"))
        course_id = safe_int(request.GET.get("course"))
        return JsonResponse({
            "semesters": [
                {"value": semester.id, "label": str(semester)}
                for semester in Semester.objects.filter(
                    is_active=True, diploma_id=diploma_id,
                ).select_related("diploma")
            ] if diploma_id else [],
            "cohorts": [
                {"value": cohort.id, "label": str(cohort)}
                for cohort in Cohort.objects.filter(
                    is_active=True, diploma_id=diploma_id,
                ).select_related("diploma")
            ] if diploma_id else [],
            "course_versions": [
                {"value": version.id, "label": str(version)}
                for version in CourseVersion.objects.filter(
                    is_active=True, course_id=course_id,
                ).select_related("course")
            ] if course_id else [],
        })
    data = {"scope_type": scope_type}
    diploma_val = request.GET.get("diploma", "")
    if scope_type == "diploma":
        data["diploma"] = diploma_val
        data["semester"] = request.GET.get("semester", "")
        data["cohort"] = request.GET.get("cohort", "")
    form = AssessmentForm(data)
    return render(request, "ui/fragments/admin/_assessment_scope.html", {
        "form": form,
        "semester_options": Semester.objects.filter(is_active=True).select_related("diploma"),
        "cohort_options": Cohort.objects.filter(is_active=True).select_related("diploma"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_create(request):
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = AssessmentForm(request.POST)
        if form.is_valid():
            assessment = form.save(commit=False)
            assessment.created_by = request.user
            assessment.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_CREATED,
                **assessment_audit_target(assessment),
                metadata={
                    "title": assessment.title,
                    "assessment_type": assessment.assessment_type,
                    "assessment_id": assessment.pk,
                    "assessment_name": assessment.title,
                },
            )
            messages.success(request, _("Assessment created."))
            return redirect(next_url) if next_url else redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentForm()
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Create assessment"),
            "next": next_url,
            "semester_options": Semester.objects.filter(is_active=True).select_related("diploma"),
            "cohort_options": Cohort.objects.filter(is_active=True).select_related("diploma"),
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_edit(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = AssessmentForm(request.POST, instance=assessment)
        if form.is_valid():
            changed_fields = list(form.changed_data)
            form.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                **assessment_audit_target(assessment),
                metadata={
                    "title": assessment.title,
                    "assessment_type": assessment.assessment_type,
                    "assessment_id": assessment.pk,
                    "assessment_name": assessment.title,
                    "changed_fields": changed_fields,
                },
            )
            messages.success(request, _("Assessment updated."))
            return redirect(next_url) if next_url else redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentForm(instance=assessment)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Edit assessment"),
            "assessment": assessment,
            "next": next_url,
            "semester_options": Semester.objects.filter(is_active=True).select_related("diploma"),
            "cohort_options": Cohort.objects.filter(is_active=True).select_related("diploma"),
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_delete(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    next_url = safe_next_url(request)
    try:
        delete_assessment_record(assessment=assessment, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    messages.success(request, _("Assessment deleted."))
    return redirect(next_url) if next_url else redirect("assessments:admin_list")


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_toggle_active(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    assessment.is_active = not assessment.is_active
    assessment.save(update_fields=["is_active", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_EXAM_TOGGLED,
        **assessment_audit_target(assessment),
        metadata={
            "is_active": assessment.is_active,
            "title": assessment.title,
            "assessment_id": assessment.pk,
            "assessment_name": assessment.title,
            "assessment_type": assessment.assessment_type,
        },
    )
    status = _("activated") if assessment.is_active else _("deactivated")
    messages.success(request, _("Assessment %(status)s.") % {"status": status})
    return redirect("assessments:admin_detail", assessment_id=assessment.id)


# ---------------------------------------------------------------------------
# Question management (inline HTMX)
# ---------------------------------------------------------------------------


def _question_form_context(assessment, question=None, form=None, choices=None):
    if form is None:
        form = QuestionForm(instance=question)
    if choices is None:
        choices = list(question.choices.all()) if question else []
    save_name = "assessments:admin_question_edit_save" if question and question.id else "assessments:admin_question_save"
    return {
        "assessment": assessment,
        "question": question,
        "form": form,
        "choices": choices,
        "question_choices": True,
        "form_action": reverse(
            save_name,
            args=[assessment.id] + ([question.id] if question and question.id else []),
        ),
        "full_page": False,
        "question_save_url": reverse(
            save_name,
            args=[assessment.id] + ([question.id] if question and question.id else []),
        ),
    }


def _render_question_form(request, assessment, question=None, form=None, choices=None):
    context = _question_form_context(assessment, question=question, form=form, choices=choices)
    context["full_page"] = not is_htmx(request)
    template = "ui/fragments/admin/_assessment_question_form.html" if is_htmx(request) else "ui/pages/admin/academic_form.html"
    return render(request, template, context)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_question_form(request, assessment_id, question_id=None):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    question = get_object_or_404(AssessmentQuestion, id=question_id, assessment=assessment) if question_id else None
    form = QuestionForm(instance=question)
    return _render_question_form(request, assessment, question=question, form=form)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_question_save(request, assessment_id, question_id=None):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    question = get_object_or_404(AssessmentQuestion, id=question_id, assessment=assessment) if question_id else None
    form = QuestionForm(request.POST, instance=question)
    parsed_choices = _parse_inline_choices(request)
    if form.is_valid():
        if form.cleaned_data["question_type"] == "mcq":
            error = _validate_mcq_choices(parsed_choices)
            if error:
                form.add_error(None, error)
        if not form.errors:
            with transaction.atomic():
                question = form.save(commit=False)
                question.assessment = assessment
                if not question_id:
                    question.order = assessment.questions.count() + 1
                question.save()
                if question.question_type == "mcq":
                    _save_choices_from_parsed(question, parsed_choices)
                elif question.question_type == "written":
                    AssessmentChoice.objects.filter(question=question).delete()
                sync_course_quiz_total_points(assessment)
                record_audit_event(
                    actor=request.user,
                    action=ACTION_QUESTION_CREATED if not question_id else ACTION_QUESTION_UPDATED,
                    **assessment_audit_target(assessment),
                    metadata={
                        "entity_type": "question",
                        "entity_id": question.pk,
                        "entity_label": question.text[:255],
                        "entity_action": "created" if not question_id else "updated",
                        "assessment_id": assessment_id,
                        "assessment_name": assessment.title,
                        "question_type": question.question_type,
                        "points": str(question.points),
                        "order": question.order,
                    },
                )
            messages.success(request, _("Question saved."))
            if is_htmx(request):
                response = HttpResponse()
                return _htmx_redirect(response, reverse("assessments:admin_detail", args=[assessment.id]))
            return redirect("assessments:admin_detail", assessment_id=assessment.id)
    choices = _choices_for_template(parsed_choices, question)
    return _render_question_form(request, assessment, question=question, form=form, choices=choices)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_question_delete(request, assessment_id, question_id):
    question = get_object_or_404(AssessmentQuestion, id=question_id, assessment_id=assessment_id)
    assessment = question.assessment
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    record_audit_event(
        actor=request.user,
        action=ACTION_QUESTION_DELETED,
        **assessment_audit_target(assessment),
        metadata={
            "entity_type": "question",
            "entity_id": question.pk,
            "entity_label": question.text[:255],
            "entity_action": "deleted",
            "assessment_id": assessment_id,
            "assessment_name": assessment.title,
            "question_type": question.question_type,
            "points": str(question.points),
        },
    )
    question.delete()
    sync_course_quiz_total_points(assessment)
    messages.success(request, _("Question deleted."))
    if is_htmx(request):
        response = HttpResponse()
        return _htmx_redirect(response, reverse("assessments:admin_detail", args=[assessment_id]))
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


def _parse_inline_choices(request):
    choices = []
    for key in request.POST:
        if not key.startswith("choice_text_"):
            continue
        suffix = key.removeprefix("choice_text_")
        text = request.POST.get(key, "").strip()
        if not text:
            continue
        is_correct = (
            request.POST.get("choice_correct") == suffix
            or request.POST.get(f"choice_correct_{suffix}") == "1"
        )
        order_raw = request.POST.get(f"choice_order_{suffix}", "")
        try:
            order = int(order_raw)
        except (TypeError, ValueError):
            order = len(choices) + 1
        if order < 1:
            order = len(choices) + 1
        choices.append({
            "suffix": suffix,
            "text": text,
            "is_correct": is_correct,
            "order": order,
        })
    return sorted(choices, key=lambda c: c["order"])


def _validate_mcq_choices(choices):
    if len(choices) < 2:
        return _("MCQ questions must have at least two choices.")
    orders = [choice["order"] for choice in choices]
    if len(orders) != len(set(orders)):
        return _("Choice orders must be unique.")
    correct_count = sum(1 for choice in choices if choice["is_correct"])
    if correct_count == 0:
        return _("Please mark exactly one choice as correct.")
    if correct_count > 1:
        return _("Only one choice can be marked as correct.")
    return ""


def _save_choices_from_parsed(question, parsed_choices):
    seen_ids = set()
    non_new_ids = []
    for choice_data in parsed_choices:
        suffix = choice_data["suffix"]
        if not suffix.startswith("new_"):
            try:
                non_new_ids.append(int(suffix))
            except (TypeError, ValueError):
                pass
    if non_new_ids:
        current_ids = set(AssessmentChoice.objects.filter(question=question).values_list("id", flat=True))
        stale_ids = current_ids - set(non_new_ids)
        AssessmentChoice.objects.filter(question=question, id__in=stale_ids).delete()
    else:
        AssessmentChoice.objects.filter(question=question).delete()
    for index, choice_data in enumerate(parsed_choices, start=1):
        suffix = choice_data["suffix"]
        text = choice_data["text"]
        is_correct = choice_data["is_correct"]
        order = choice_data["order"] or index
        if suffix.startswith("new_"):
            choice = AssessmentChoice.objects.create(
                question=question, text=text, is_correct=is_correct, order=order,
            )
            seen_ids.add(choice.id)
        else:
            try:
                choice_id = int(suffix)
            except (TypeError, ValueError):
                continue
            AssessmentChoice.objects.filter(id=choice_id, question=question).update(
                text=text, is_correct=is_correct, order=order,
            )
            seen_ids.add(choice_id)


# ---------------------------------------------------------------------------
# Admin grading views
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_list(request):
    page_context = _get_assessment_queue_context(request, _("Grading"))
    scope_filters = page_context.pop("assessment_scope_filters")
    queue = get_grading_queue(request.user, **scope_filters)
    homework_queue = get_homework_pending_grading(request.user, **scope_filters)
    recently_graded = get_recently_graded_attempts(request.user, **scope_filters)
    queue_page, queue_pagination_qs = paginate(request, queue)
    hw_page, homework_pagination_qs = paginate(request, homework_queue)
    return render(
        request,
        "ui/pages/admin/grading_list.html",
        {
            **page_context,
            "queue_page": queue_page,
            "queue_pagination_qs": queue_pagination_qs,
            "homework_queue_page": hw_page,
            "homework_pagination_qs": homework_pagination_qs,
            "recently_graded": recently_graded,
            "detail_return_url": request.get_full_path(),
        },
    )


@login_required
@role_required(*ROLE_HOMEWORK_GRADING_STAFF)
def admin_homework_grade_list(request):
    page_context = _get_assessment_queue_context(request, _("Homework submissions"))
    scope_filters = page_context.pop("assessment_scope_filters")
    submissions = get_homework_pending_grading(request.user, **scope_filters).order_by(
        "-submitted_at", "-id",
    )
    page_obj, pagination_qs = paginate(request, submissions)
    return render(
        request,
        "ui/pages/admin/grading_list.html",
        {
            **page_context,
            "page_obj": page_obj,
            "pagination_qs": pagination_qs,
            "detail_return_url": request.get_full_path(),
        },
    )


@login_required
@role_required(*ROLE_HOMEWORK_GRADING_STAFF)
def admin_homework_grade_detail(request, submission_id):
    submission = get_object_or_404(
        HomeworkSubmission.objects.select_related("assessment", "student", "attempt").prefetch_related("attachments"),
        id=submission_id,
    )
    if not can_moderator_grade_homework(request.user, submission):
        raise PermissionDenied
    attachments = list(submission.attachments.all())
    download_urls = {}
    for attachment in attachments:
        download_urls[attachment.id] = storage_service.get_protected_download_url(
            attachment.storage_key,
            user=request.user,
            permission_fn=lambda u, _a=attachment: can_moderator_grade_homework(u, submission),
        )
    if request.method == "POST":
        if submission.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
            messages.error(request, _("This assessment is preserved legacy data and is read-only."))
            return _redirect_to_grading_detail(
                request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
            )
        try:
            score = validate_score(request.POST.get("score", 0))
        except ValueError as exc:
            messages.error(request, str(exc))
            return _redirect_to_grading_detail(
                request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
            )
        feedback = request.POST.get("feedback", "")
        score_before = submission.score
        status_before = submission.status
        attempt_status_before = submission.attempt.status if submission.attempt else ""
        visible_before = submission.attempt.visible_to_student if submission.attempt else False
        try:
            graded = grade_homework(submission.id, score, feedback, request.user)
            attempt = graded.attempt
            if attempt is not None:
                attempt.refresh_from_db(
                    fields=["score_points", "status", "visible_to_student", "graded_by"],
                )
            record_audit_event(
                actor=request.user,
                action=ACTION_GRADE_CHANGED,
                **assessment_audit_target(graded.assessment),
                **student_audit_subject(graded.student),
                metadata=grading_audit_metadata(
                    assessment=graded.assessment,
                    submission=graded,
                    student=graded.student,
                    grader=request.user,
                    score_before=score_before,
                    score_after=graded.score,
                    max_score=graded.assessment.total_points,
                    release_state_before={
                        "status": status_before,
                        "attempt_status": attempt_status_before,
                        "visible_to_student": visible_before,
                    },
                    release_state_after={
                        "status": graded.status,
                        "attempt_status": attempt.status if attempt else "",
                        "visible_to_student": attempt.visible_to_student if attempt else False,
                    },
                    reason=feedback,
                    score=str(score),
                ),
            )
            messages.success(request, _("Homework graded."))
        except ValueError as exc:
            messages.error(request, str(exc))
        return _redirect_to_grading_detail(
            request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
        )
    back_url = safe_next_url(
        request,
        default=reverse("assessments:admin_homework_list"),
    )
    return render(
        request,
        "ui/pages/admin/grading_detail.html",
        {
            "submission": submission,
            "attachments": attachments,
            "attachment_download_urls": download_urls,
            "back_url": back_url,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_detail(request, attempt_id):
    attempt = get_object_or_404(
        AssessmentAttempt.objects.select_related("assessment", "student", "graded_by"),
        id=attempt_id,
    )
    if not is_admin(request.user):
        raise PermissionDenied
    assessment = attempt.assessment
    if request.method == "POST" and assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        messages.error(request, _("This assessment is preserved legacy data and is read-only."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    ctx_base = {
        "attempt": attempt,
        "is_admin_user": is_admin(request.user),
        "back_url": safe_next_url(
            request,
            default=reverse("assessments:admin_grade_list"),
        ),
    }

    if is_diploma:
        snapshot_questions = (
            attempt.snapshot_questions
            .filter(question_type=QUESTION_TYPE_WRITTEN)
            .select_related("source_lecture", "section__lecture")
            .order_by("order")
            .prefetch_related("choices")
        )
        snapshot_answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt,
            ).select_related("selected_choice")
        }
        if request.method == "POST":
            attempt_score_before = attempt.score_points
            release_state_before = {
                "status": attempt.status,
                "visible_to_student": attempt.visible_to_student,
            }
            answer_changes = []
            with transaction.atomic():
                graded_any = False
                for sq in snapshot_questions:
                    answer = snapshot_answers.get(sq.pk)
                    if not answer:
                        continue
                    score_key = f"score_sq_{sq.order}"
                    feedback_key = f"feedback_sq_{sq.order}"
                    reason_key = f"override_reason_sq_{sq.order}"
                    if score_key in request.POST:
                        try:
                            score = Decimal(str(request.POST[score_key]))
                        except (ValueError, InvalidOperation):
                            continue
                        feedback = request.POST.get(feedback_key, "")
                        override_reason = request.POST.get(reason_key, "")
                        score_before = answer.score
                        try:
                            # RV-014: grade without per-answer recalc; one
                            # recalculation runs after the loop.
                            grade_diploma_snapshot_answer(
                                answer.pk, score, feedback, request.user,
                                override_reason=override_reason,
                                recalculate=False,
                            )
                            answer_changes.append({
                                "answer_id": answer.pk,
                                "attempt_question_id": sq.pk,
                                "section_id": sq.section_id,
                                "section_title": sq.section.lecture.title,
                                "score_before": "" if score_before is None else str(score_before),
                                "score_after": str(score),
                                "max_score": str(sq.points),
                                "reason": override_reason,
                            })
                            graded_any = True
                        except ValueError as exc:
                            messages.error(request, str(exc))
                if graded_any:
                    _update_diploma_attempt_grade(attempt, graded_by=request.user)
                    attempt.refresh_from_db(
                        fields=[
                            "score_points", "score_percent", "possible_points_snapshot",
                            "status", "visible_to_student", "graded_by",
                        ],
                    )
            record_audit_event(
                actor=request.user,
                action=ACTION_GRADE_CHANGED,
                **assessment_audit_target(assessment),
                **student_audit_subject(attempt.student),
                metadata=grading_audit_metadata(
                    assessment=assessment,
                    attempt=attempt,
                    student=attempt.student,
                    grader=request.user,
                    score_before=attempt_score_before,
                    score_after=attempt.score_points,
                    max_score=attempt.possible_points_snapshot,
                    release_state_before=release_state_before,
                    release_state_after={
                        "status": attempt.status,
                        "visible_to_student": attempt.visible_to_student,
                    },
                    answers=answer_changes,
                    graded_answer_count=len(answer_changes),
                ),
            )
            messages.success(request, _("Grades saved."))
            return _redirect_to_grading_detail(
                request, "assessments:admin_grade_detail", attempt_id=attempt_id,
            )
        snapshot_pairs = [(sq, snapshot_answers.get(sq.pk)) for sq in snapshot_questions]
        return render(
            request,
            "ui/pages/admin/grading_detail.html",
            {**ctx_base, "snapshot_pairs": snapshot_pairs, "is_diploma_random": True},
        )

    questions = (
        assessment.questions
        .filter(question_type=QUESTION_TYPE_WRITTEN)
        .prefetch_related("choices")
        .all()
    )
    answers = {a.question_id: a for a in attempt.answers.select_related("question", "selected_choice").all()}
    if request.method == "POST":
        attempt_score_before = attempt.score_points
        release_state_before = {
            "status": attempt.status,
            "visible_to_student": attempt.visible_to_student,
        }
        answer_changes = []
        with transaction.atomic():
            graded_any = False
            for q in questions:
                answer = answers.get(q.id)
                if not answer:
                    continue
                score_key = f"score_{q.id}"
                feedback_key = f"feedback_{q.id}"
                reason_key = f"override_reason_{q.id}"
                if score_key in request.POST:
                    try:
                        score = Decimal(str(request.POST[score_key]))
                    except (ValueError, InvalidOperation):
                        continue
                    feedback = request.POST.get(feedback_key, "")
                    override_reason = request.POST.get(reason_key, "")
                    score_before = answer.score
                    try:
                        # RV-014: grade without per-answer recalc; one
                        # recalculation runs after the loop.
                        grade_written_answer(
                            answer.id, score, feedback, request.user,
                            override_reason=override_reason,
                            recalculate=False,
                        )
                        answer_changes.append({
                            "answer_id": answer.pk,
                            "question_id": q.id,
                            "score_before": "" if score_before is None else str(score_before),
                            "score_after": str(score),
                            "max_score": str(q.points),
                            "reason": override_reason,
                        })
                        graded_any = True
                    except ValueError as exc:
                        messages.error(request, str(exc))
            if graded_any:
                _update_attempt_grade(attempt, graded_by=request.user)
                attempt.refresh_from_db(
                    fields=[
                        "score_points", "score_percent", "possible_points_snapshot",
                        "status", "visible_to_student", "graded_by",
                    ],
                )
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_CHANGED,
            **assessment_audit_target(assessment),
            **student_audit_subject(attempt.student),
            metadata=grading_audit_metadata(
                assessment=assessment,
                attempt=attempt,
                student=attempt.student,
                grader=request.user,
                score_before=attempt_score_before,
                score_after=attempt.score_points,
                max_score=attempt.possible_points_snapshot,
                release_state_before=release_state_before,
                release_state_after={
                    "status": attempt.status,
                    "visible_to_student": attempt.visible_to_student,
                },
                answers=answer_changes,
                graded_answer_count=len(answer_changes),
            ),
        )
        messages.success(request, _("Grades saved."))
        return _redirect_to_grading_detail(
            request, "assessments:admin_grade_detail", attempt_id=attempt_id,
        )
    question_answer_pairs = [(q, answers.get(q.id)) for q in questions]
    return render(
        request,
        "ui/pages/admin/grading_detail.html",
        {**ctx_base, "question_answer_pairs": question_answer_pairs},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_release_grades(request, attempt_id):
    attempt = get_object_or_404(AssessmentAttempt, id=attempt_id)
    if not is_admin(request.user):
        raise PermissionDenied
    legacy_response = _legacy_redirect(request, attempt.assessment)
    if legacy_response is not None:
        return legacy_response
    try:
        release_attempt_grades(attempt_id, actor=request.user, source="manual")
        messages.success(request, _("Grades released to student."))
    except ValueError as exc:
        messages.error(request, str(exc))
    next_url = safe_next_url(request)
    if next_url:
        return redirect(next_url)
    return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_hide_grades(request, attempt_id):
    attempt = get_object_or_404(AssessmentAttempt, id=attempt_id)
    if not is_admin(request.user):
        raise PermissionDenied
    legacy_response = _legacy_redirect(request, attempt.assessment)
    if legacy_response is not None:
        return legacy_response
    try:
        hide_attempt_grades(attempt_id, actor=request.user)
        messages.success(request, _("Grades hidden from student."))
    except ValueError as exc:
        messages.error(request, str(exc))
    next_url = safe_next_url(request)
    if next_url:
        return redirect(next_url)
    return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_toggle_global_grade_visibility(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if not is_admin(request.user):
        raise PermissionDenied
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    visible = assessment.grade_visibility != GRADE_VISIBILITY_RELEASED
    try:
        set_assessment_grade_visibility(
            assessment.id,
            visible=visible,
            actor=request.user,
            reason="global grade visibility toggle",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if visible:
            messages.success(
                request,
                _("Student grades are now globally visible. Per-student toggles still control each student."),
            )
        else:
            messages.success(
                request,
                _("Student grades are now globally hidden for all students."),
            )
    next_url = safe_next_url(request)
    if next_url:
        return redirect(next_url)
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_toggle_all_grades(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    released = AssessmentAttempt.objects.filter(assessment=assessment, status=ATTEMPT_STATUS_RELEASED)
    graded = AssessmentAttempt.objects.filter(assessment=assessment, status=ATTEMPT_STATUS_GRADED)
    if released.exists():
        count = hide_all_released(assessment, actor=request.user)
        set_assessment_grade_visibility(
            assessment.id, visible=False, actor=request.user, reason="bulk hide",
        )
        messages.success(request, _("Hidden %(count)s released attempt(s).") % {"count": count})
    elif graded.exists():
        count = release_all_graded(assessment, actor=request.user)
        set_assessment_grade_visibility(
            assessment.id, visible=True, actor=request.user, reason="bulk release",
        )
        messages.success(request, _("Released %(count)s graded attempt(s).") % {"count": count})
    else:
        messages.info(request, _("No graded or released attempts to toggle."))
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


# ---------------------------------------------------------------------------
# Admin Semester Exam stage review (P3-03)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_exam_review_list(request):
    page_context = _get_assessment_queue_context(request, _("Semester Exam review"))
    scope_filters = page_context.pop("assessment_scope_filters")
    queue = get_semester_exam_review_queue(request.user, **scope_filters).order_by(
        "-graded_at", "-id",
    )
    page_obj, pagination_qs = paginate(request, queue, per_page=25)
    return render(request, "ui/pages/admin/exam_review_list.html", {
        **page_context,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "detail_return_url": request.get_full_path(),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_exam_review_detail(request, attempt_id):
    attempt = get_object_or_404(
        AssessmentAttempt.objects.select_related("assessment", "student"),
        id=attempt_id,
    )
    if not is_admin(request.user):
        raise PermissionDenied
    if attempt.assessment.assessment_type != ASSESSMENT_TYPE_SEMESTER_EXAM or attempt.assessment.course_version_id is not None:
        messages.error(request, _("Exam-stage review applies only to diploma-scoped Semester Exams."))
        return redirect("assessments:admin_exam_review_list")
    details = _semester_exam_attempt_details(attempt)
    back_url = safe_next_url(
        request,
        default=reverse("assessments:admin_exam_review_list"),
    )
    return render(request, "ui/pages/admin/exam_review_detail.html", {
        "attempt": attempt,
        "details": details,
        "passing_score": attempt.assessment.passing_score,
        "back_url": back_url,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_exam_review_submit(request, attempt_id):
    # 404 guard for nonexistent attempt ids; the service re-locks and re-validates.
    get_object_or_404(AssessmentAttempt, id=attempt_id)
    if not is_admin(request.user):
        raise PermissionDenied
    decision = request.POST.get("decision")
    note = request.POST.get("note", "")
    try:
        admin_review_semester_exam(attempt_id, decision, actor=request.user, note=note)
        messages.success(request, _("Exam stage decision saved."))
    except ValueError as exc:
        messages.error(request, str(exc))
    next_url = safe_next_url(request)
    if next_url:
        return redirect(next_url)
    return redirect("assessments:admin_exam_review_detail", attempt_id=attempt_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_grade_answer(request, answer_id):
    answer = get_object_or_404(
        AssessmentAnswer.objects.select_related(
            "attempt__assessment", "attempt__student", "question",
        ),
        id=answer_id,
    )
    if not is_admin(request.user):
        raise PermissionDenied
    if answer.attempt.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        messages.error(request, _("This assessment is preserved legacy data and is read-only."))
        return _redirect_to_grading_detail(
            request, "assessments:admin_grade_detail", attempt_id=answer.attempt_id,
        )
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return _redirect_to_grading_detail(
            request, "assessments:admin_grade_detail", attempt_id=answer.attempt_id,
        )
    feedback = request.POST.get("feedback", "")
    override_reason = request.POST.get("override_reason", "")
    score_before = answer.score
    release_state_before = {
        "status": answer.attempt.status,
        "visible_to_student": answer.attempt.visible_to_student,
    }
    try:
        grade_written_answer(
            answer.id, score, feedback, request.user,
            override_reason=override_reason,
        )
        answer.attempt.refresh_from_db(
            fields=[
                "score_points", "score_percent", "possible_points_snapshot",
                "status", "visible_to_student", "graded_by",
            ],
        )
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_OVERRIDE if override_reason else ACTION_GRADE_CHANGED,
            **assessment_audit_target(answer.attempt.assessment),
            **student_audit_subject(answer.attempt.student),
            metadata=grading_audit_metadata(
                assessment=answer.attempt.assessment,
                attempt=answer.attempt,
                student=answer.attempt.student,
                grader=request.user,
                score_before=score_before,
                score_after=score,
                max_score=answer.question.points,
                release_state_before=release_state_before,
                release_state_after={
                    "status": answer.attempt.status,
                    "visible_to_student": answer.attempt.visible_to_student,
                },
                reason=override_reason,
                score=str(score),
                override_reason=override_reason or "",
                question_id=answer.question_id,
                answer_id=answer.pk,
            ),
        )
        messages.success(request, _("Answer graded."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_to_grading_detail(
        request, "assessments:admin_grade_detail", attempt_id=answer.attempt_id,
    )


@login_required
@role_required(*ROLE_HOMEWORK_GRADING_STAFF)
@require_POST
def admin_grade_homework_submission(request, submission_id):
    submission = get_object_or_404(
        HomeworkSubmission.objects.select_related("assessment", "student", "attempt"),
        id=submission_id,
    )
    if not can_moderator_grade_homework(request.user, submission):
        raise PermissionDenied
    if submission.assessment.legacy_status != LEGACY_STATUS_ACTIVE:
        messages.error(request, _("This assessment is preserved legacy data and is read-only."))
        return _redirect_to_grading_detail(
            request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
        )
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return _redirect_to_grading_detail(
            request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
        )
    feedback = request.POST.get("feedback", "")
    score_before = submission.score
    status_before = submission.status
    attempt_status_before = submission.attempt.status if submission.attempt else ""
    visible_before = submission.attempt.visible_to_student if submission.attempt else False
    try:
        graded = grade_homework(submission.id, score, feedback, request.user)
        attempt = graded.attempt
        if attempt is not None:
            attempt.refresh_from_db(
                fields=["score_points", "status", "visible_to_student", "graded_by"],
            )
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_CHANGED,
            **assessment_audit_target(graded.assessment),
            **student_audit_subject(graded.student),
            metadata=grading_audit_metadata(
                assessment=graded.assessment,
                submission=graded,
                student=graded.student,
                grader=request.user,
                score_before=score_before,
                score_after=graded.score,
                max_score=graded.assessment.total_points,
                release_state_before={
                    "status": status_before,
                    "attempt_status": attempt_status_before,
                    "visible_to_student": visible_before,
                },
                release_state_after={
                    "status": graded.status,
                    "attempt_status": attempt.status if attempt else "",
                    "visible_to_student": attempt.visible_to_student if attempt else False,
                },
                reason=feedback,
                score=str(score),
            ),
        )
        messages.success(request, _("Homework graded."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return _redirect_to_grading_detail(
        request, "assessments:admin_homework_grade_detail", submission_id=submission_id,
    )


# ---------------------------------------------------------------------------
# Diploma lecture question bank admin (Phase 7)
# ---------------------------------------------------------------------------


def _save_diploma_choices(question, parsed_choices):
    seen_ids = set()
    non_new_ids = []
    for choice_data in parsed_choices:
        suffix = choice_data["suffix"]
        if not suffix.startswith("new_"):
            try:
                non_new_ids.append(int(suffix))
            except (TypeError, ValueError):
                pass
    if non_new_ids:
        current_ids = set(DiplomaLectureChoice.objects.filter(question=question).values_list("id", flat=True))
        stale_ids = current_ids - set(non_new_ids)
        DiplomaLectureChoice.objects.filter(question=question, id__in=stale_ids).delete()
    else:
        DiplomaLectureChoice.objects.filter(question=question).delete()
    for index, choice_data in enumerate(parsed_choices, start=1):
        suffix = choice_data["suffix"]
        text = choice_data["text"]
        is_correct = choice_data["is_correct"]
        order = choice_data["order"] or index
        if suffix.startswith("new_"):
            DiplomaLectureChoice.objects.create(question=question, text=text, is_correct=is_correct, order=order)
        else:
            try:
                choice_id = int(suffix)
            except (TypeError, ValueError):
                continue
            DiplomaLectureChoice.objects.filter(id=choice_id, question=question).update(
                text=text, is_correct=is_correct, order=order,
            )
            seen_ids.add(choice_id)


def _sync_assessment_grades_for_lecture(lecture):
    for assessment in Assessment.objects.filter(lecture_sections__lecture=lecture).distinct():
        for section in assessment.lecture_sections.all():
            section.max_grade = section.calculated_max_grade()
            section.save(update_fields=["max_grade", "updated_at"])
        assessment.total_points = sum(section.max_grade for section in assessment.lecture_sections.all())
        assessment.save(update_fields=["total_points", "updated_at"])


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_bank_list(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    questions = lecture.question_bank.filter(active=True).prefetch_related("choices")
    return render(request, "ui/pages/admin/lecture_bank.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "questions": questions,
        "title": lecture.title,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_bank_question_create(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaLectureQuestionForm(request.POST)
        parsed_choices = _parse_inline_choices(request)
        if form.is_valid():
            if form.cleaned_data["question_type"] == QUESTION_TYPE_MCQ:
                error = _validate_mcq_choices(parsed_choices)
                if error:
                    form.add_error(None, error)
            if not form.errors:
                question = form.save(commit=False)
                question.lecture = lecture
                question.created_by = request.user
                question.save()
                if question.question_type == QUESTION_TYPE_MCQ:
                    _save_diploma_choices(question, parsed_choices)
                _sync_assessment_grades_for_lecture(lecture)
                record_audit_event(
                    actor=request.user,
                    action=ACTION_QUESTION_CREATED,
                    target_type="diploma_lecture_question",
                    target_id=str(question.pk),
                    target_label=question.text[:255],
                    section=SECTION_ASSESSMENTS,
                    metadata={
                        "entity_type": "diploma_lecture_question",
                        "entity_id": question.pk,
                        "entity_action": "created",
                        "diploma_id": diploma.pk,
                        "diploma_title": diploma.title,
                        "cohort_id": cohort.pk,
                        "cohort_title": cohort.name,
                        "semester_id": semester.pk,
                        "semester_title": semester.title,
                        "lecture_id": lecture.pk,
                        "lecture_title": lecture.title,
                        "question_type": question.question_type,
                        "points": str(question.points),
                    },
                )
                messages.success(request, _("Bank question created."))
                return redirect(next_url) if next_url else redirect(
                    "assessments:admin_lecture_bank_list",
                    diploma_id=diploma.id,
                    cohort_id=cohort.id,
                    semester_id=semester.id,
                    lecture_id=lecture.id,
                )
        choices = _choices_for_template(parsed_choices, None)
    else:
        form = DiplomaLectureQuestionForm()
        choices = []
    return render(request, "ui/pages/admin/academic_form.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "form": form, "choices": choices, "question": None, "question_choices": True,
        "title": _("Add lecture question"),
        "back_url": next_url or reverse("assessments:admin_lecture_bank_list", args=[diploma.id, cohort.id, semester.id, lecture.id]),
        "back_label": _("Back to questions"),
        "next": next_url,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_bank_question_edit(request, diploma_id, cohort_id, semester_id, lecture_id, question_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    question = get_object_or_404(DiplomaLectureQuestion, id=question_id, lecture=lecture)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaLectureQuestionForm(request.POST, instance=question)
        parsed_choices = _parse_inline_choices(request)
        if form.is_valid():
            if form.cleaned_data["question_type"] == QUESTION_TYPE_MCQ:
                error = _validate_mcq_choices(parsed_choices)
                if error:
                    form.add_error(None, error)
            if not form.errors:
                form.save()
                if question.question_type == QUESTION_TYPE_MCQ:
                    _save_diploma_choices(question, parsed_choices)
                elif question.question_type != QUESTION_TYPE_MCQ:
                    question.choices.all().delete()
                _sync_assessment_grades_for_lecture(lecture)
                record_audit_event(
                    actor=request.user,
                    action=ACTION_QUESTION_UPDATED,
                    target_type="diploma_lecture_question",
                    target_id=str(question.pk),
                    target_label=question.text[:255],
                    section=SECTION_ASSESSMENTS,
                    metadata={
                        "entity_type": "diploma_lecture_question",
                        "entity_id": question.pk,
                        "entity_action": "updated",
                        "diploma_id": diploma.pk,
                        "diploma_title": diploma.title,
                        "cohort_id": cohort.pk,
                        "cohort_title": cohort.name,
                        "semester_id": semester.pk,
                        "semester_title": semester.title,
                        "lecture_id": lecture.pk,
                        "lecture_title": lecture.title,
                        "question_type": question.question_type,
                        "points": str(question.points),
                    },
                )
                messages.success(request, _("Bank question updated."))
                return redirect(next_url) if next_url else redirect(
                    "assessments:admin_lecture_bank_list",
                    diploma_id=diploma.id,
                    cohort_id=cohort.id,
                    semester_id=semester.id,
                    lecture_id=lecture.id,
                )
        choices = _choices_for_template(parsed_choices, question)
    else:
        form = DiplomaLectureQuestionForm(instance=question)
        choices = _choices_for_template(None, question)
    return render(request, "ui/pages/admin/academic_form.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "form": form, "choices": choices, "question": question, "question_choices": True,
        "title": _("Edit lecture question"),
        "back_url": next_url or reverse("assessments:admin_lecture_bank_list", args=[diploma.id, cohort.id, semester.id, lecture.id]),
        "back_label": _("Back to questions"),
        "next": next_url,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_lecture_bank_question_delete(request, diploma_id, cohort_id, semester_id, lecture_id, question_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    question = get_object_or_404(DiplomaLectureQuestion, id=question_id, lecture=lecture)
    next_url = safe_next_url(request)
    record_audit_event(
        actor=request.user,
        action=ACTION_QUESTION_DELETED,
        target_type="diploma_lecture_question",
        target_id=str(question.pk),
        target_label=question.text[:255],
        section=SECTION_ASSESSMENTS,
        metadata={
            "entity_type": "diploma_lecture_question",
            "entity_id": question.pk,
            "entity_action": "deleted",
            "diploma_id": diploma.pk,
            "diploma_title": diploma.title,
            "cohort_id": cohort.pk,
            "cohort_title": cohort.name,
            "semester_id": semester.pk,
            "semester_title": semester.title,
            "lecture_id": lecture.pk,
            "lecture_title": lecture.title,
            "question_type": question.question_type,
            "points": str(question.points),
        },
    )
    question.delete()
    _sync_assessment_grades_for_lecture(lecture)
    messages.success(request, _("Bank question deleted."))
    return redirect(next_url) if next_url else redirect(
        "assessments:admin_lecture_bank_list",
        diploma_id=diploma.id,
        cohort_id=cohort.id,
        semester_id=semester.id,
        lecture_id=lecture.id,
    )


# ---------------------------------------------------------------------------
# Diploma section management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_create(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    if request.method == "POST":
        form = AssessmentLectureSectionForm(request.POST, assessment=assessment)
        if form.is_valid():
            section = form.save(commit=False)
            section.assessment = assessment
            section.max_grade = section.calculated_max_grade()
            try:
                section.save()
            except IntegrityError:
                form.add_error("order", _("A section with this order already exists in this assessment."))
            else:
                assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
                assessment.save(update_fields=["total_points", "updated_at"])
                record_audit_event(
                    actor=request.user,
                    action=ACTION_EXAM_UPDATED,
                    **assessment_audit_target(assessment),
                    metadata={
                        "entity_type": "section",
                        "entity_id": section.pk,
                        "entity_label": section.lecture.title,
                        "entity_action": "created",
                        "assessment_id": assessment.pk,
                        "assessment_name": assessment.title,
                        "section_id": section.pk,
                        "section_title": section.lecture.title,
                        "section_order": section.order,
                        "mcq_count": section.mcq_count,
                        "max_grade": str(section.max_grade),
                    },
                )
                messages.success(request, _("Section added."))
                return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentLectureSectionForm(assessment=assessment)
    return render(request, "ui/pages/admin/academic_form.html", {
        "form": form, "assessment": assessment, "title": _("Add Section"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_edit(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    if assessment.attempts.exists():
        messages.error(request, _("Cannot edit section after attempts exist."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    if request.method == "POST":
        form = AssessmentLectureSectionForm(request.POST, instance=section, assessment=assessment)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.max_grade = updated.calculated_max_grade()
            try:
                updated.save()
            except IntegrityError:
                form.add_error("order", _("A section with this order already exists in this assessment."))
            else:
                assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
                assessment.save(update_fields=["total_points", "updated_at"])
                record_audit_event(
                    actor=request.user,
                    action=ACTION_EXAM_UPDATED,
                    **assessment_audit_target(assessment),
                    metadata={
                        "entity_type": "section",
                        "entity_id": section.pk,
                        "entity_label": section.lecture.title,
                        "entity_action": "updated",
                        "assessment_id": assessment.pk,
                        "assessment_name": assessment.title,
                        "section_id": section.pk,
                        "section_title": section.lecture.title,
                        "section_order": section.order,
                        "mcq_count": section.mcq_count,
                        "max_grade": str(section.max_grade),
                    },
                )
                messages.success(request, _("Section updated."))
                return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentLectureSectionForm(instance=section, assessment=assessment)
    return render(request, "ui/pages/admin/academic_form.html", {
        "form": form, "assessment": assessment, "section": section, "title": _("Edit Section"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_section_delete(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    if assessment.attempts.exists():
        messages.error(request, _("Cannot delete section after attempts exist."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    if section.exam_code_maximum_snapshots.exists():
        messages.error(request, _("This section is preserved by issued exam codes and cannot be deleted."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    section_id_value = section.pk
    section_title = section.lecture.title
    section_order = section.order
    section.delete()
    assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
    assessment.save(update_fields=["total_points", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_EXAM_UPDATED,
        **assessment_audit_target(assessment),
        metadata={
            "entity_type": "section",
            "entity_id": section_id_value,
            "entity_label": section_title,
            "entity_action": "deleted",
            "assessment_id": assessment.pk,
            "assessment_name": assessment.title,
            "section_id": section_id_value,
            "section_title": section_title,
            "section_order": section_order,
            "total_after": str(assessment.total_points),
        },
    )
    messages.success(request, _("Section deleted."))
    return redirect("assessments:admin_detail", assessment_id=assessment.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_manual_question_create(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    if request.method == "POST":
        form = AssessmentSectionManualQuestionForm(request.POST)
        if form.is_valid():
            mq = form.save(commit=False)
            mq.section = section
            mq.save()
            section.max_grade = section.calculated_max_grade()
            section.save(update_fields=["max_grade", "updated_at"])
            assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
            assessment.save(update_fields=["total_points", "updated_at"])
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                **assessment_audit_target(assessment),
                metadata={
                    "entity_type": "manual_question",
                    "entity_id": mq.pk,
                    "entity_label": mq.text[:255],
                    "entity_action": "created",
                    "assessment_id": assessment.pk,
                    "assessment_name": assessment.title,
                    "section_id": section.pk,
                    "section_title": section.lecture.title,
                    "question_type": mq.question_type,
                    "points": str(mq.points),
                    "order": mq.order,
                },
            )
            messages.success(request, _("Manual question added."))
            return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentSectionManualQuestionForm()
    return render(request, "ui/pages/admin/academic_form.html", {
        "form": form, "assessment": assessment, "section": section, "title": _("Add Manual Question"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_manual_question_edit(request, assessment_id, section_id, mq_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    mq = get_object_or_404(AssessmentSectionManualQuestion, id=mq_id, section=section)
    if request.method == "POST":
        form = AssessmentSectionManualQuestionForm(request.POST, instance=mq)
        if form.is_valid():
            form.save()
            section.max_grade = section.calculated_max_grade()
            section.save(update_fields=["max_grade", "updated_at"])
            assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
            assessment.save(update_fields=["total_points", "updated_at"])
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                **assessment_audit_target(assessment),
                metadata={
                    "entity_type": "manual_question",
                    "entity_id": mq.pk,
                    "entity_label": mq.text[:255],
                    "entity_action": "updated",
                    "assessment_id": assessment.pk,
                    "assessment_name": assessment.title,
                    "section_id": section.pk,
                    "section_title": section.lecture.title,
                    "question_type": mq.question_type,
                    "points": str(mq.points),
                    "order": mq.order,
                },
            )
            messages.success(request, _("Manual question updated."))
            return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentSectionManualQuestionForm(instance=mq)
    return render(request, "ui/pages/admin/academic_form.html", {
        "form": form, "assessment": assessment, "section": section, "mq": mq, "title": _("Edit Manual Question"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_section_manual_question_delete(request, assessment_id, section_id, mq_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    mq = get_object_or_404(AssessmentSectionManualQuestion, id=mq_id, section=section)
    mq_id_value = mq.pk
    mq_text = mq.text
    mq_type = mq.question_type
    mq_points = mq.points
    mq_order = mq.order
    mq.delete()
    section.max_grade = section.calculated_max_grade()
    section.save(update_fields=["max_grade", "updated_at"])
    assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
    assessment.save(update_fields=["total_points", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_EXAM_UPDATED,
        **assessment_audit_target(assessment),
        metadata={
            "entity_type": "manual_question",
            "entity_id": mq_id_value,
            "entity_label": mq_text[:255],
            "entity_action": "deleted",
            "assessment_id": assessment.pk,
            "assessment_name": assessment.title,
            "section_id": section.pk,
            "section_title": section.lecture.title,
            "question_type": mq_type,
            "points": str(mq_points),
            "order": mq_order,
        },
    )
    messages.success(request, _("Manual question deleted."))
    return redirect("assessments:admin_detail", assessment_id=assessment.id)


# ---------------------------------------------------------------------------
# Exam code roster management and offline grade entry (Phase 7)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_roster_list(request, assessment_id):
    assessment = get_object_or_404(Assessment.objects.prefetch_related("exam_codes__diploma_enrollment__student__user"), id=assessment_id)
    codes = assessment.exam_codes.select_related("diploma_enrollment__student__user__student_profile").all()
    return render(request, "ui/pages/admin/exam_code_roster.html", {
        "assessment": assessment,
        "codes": codes,
        "delivery_mode_choices": DELIVERY_MODE_CHOICES,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_generate_codes(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    try:
        count = generate_exam_codes(assessment, request.user)
        if count:
            messages.success(request, _("%(count)d exam codes generated.") % {"count": count})
        else:
            messages.info(request, _("All codes already exist."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_roster", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_change_roster_delivery_mode(request, assessment_id, code_id):
    code = get_object_or_404(
        SemesterExamCode.objects.select_related(
            "assessment", "diploma_enrollment__student__user",
        ),
        pk=code_id,
        assessment_id=assessment_id,
    )
    legacy_response = _legacy_redirect(request, code.assessment)
    if legacy_response is not None:
        return legacy_response
    delivery_mode_before = code.delivery_mode
    requested_mode = request.POST.get("delivery_mode", "").strip()
    try:
        change_exam_code_delivery_mode(
            code.id,
            requested_mode,
            request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        student = code.diploma_enrollment.student
        if delivery_mode_before != requested_mode:
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                **assessment_audit_target(code.assessment),
                **student_audit_subject(student),
                metadata={
                    "entity_type": "exam_code",
                    "entity_id": code.pk,
                    "entity_label": code.code_number,
                    "entity_action": "delivery_mode_changed",
                    "field": "delivery_mode",
                    "assessment_id": code.assessment_id,
                    "assessment_name": code.assessment.title,
                    "assessment_type": code.assessment.assessment_type,
                    "student_id": student.pk,
                    "student_user_id": student.user_id,
                    "student_name": student.full_name,
                    "exam_code_id": code.pk,
                    "code_number": code.code_number,
                    "delivery_mode_before": delivery_mode_before,
                    "delivery_mode_after": requested_mode,
                },
            )
        messages.success(request, _("Student delivery mode updated."))
    return redirect("assessments:admin_roster", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_export_offline_codes(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    codes = SemesterExamCode.objects.filter(
        assessment=assessment, delivery_mode=DELIVERY_MODE_OFFLINE,
    ).select_related("diploma_enrollment__student__user__student_profile")
    response = HttpResponse(content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f"attachment; filename=offline_codes_{assessment_id}.txt"
    for code in codes:
        name = code.diploma_enrollment.student.full_name
        response.write(f"{code.code_number}\t{name}\n")
    return response


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_offline_grade_entry(request, assessment_id):
    assessment = get_object_or_404(Assessment.objects.prefetch_related("lecture_sections"), id=assessment_id)
    code = None
    sections = []
    grades = {}
    code_number = request.POST.get("code_number", request.GET.get("code_number", "")).strip()
    if code_number:
        code = SemesterExamCode.objects.filter(
            assessment=assessment,
            code_number=code_number,
            active=True,
            delivery_mode=DELIVERY_MODE_OFFLINE,
        ).select_related("diploma_enrollment").first()
        if not code:
            messages.error(request, _("No exam code found."))
        else:
            snapshots = list(
                code.section_maximums.select_related("section", "section__lecture")
                .order_by("order_snapshot", "pk")
            )
            if snapshots:
                sections = [
                    {
                        "section": row.section,
                        "section_title": row.section_title_snapshot,
                        "max_grade": row.max_grade_snapshot,
                    }
                    for row in snapshots
                ]
            else:
                sections = [
                    {
                        "section": section,
                        "section_title": section.lecture.title,
                        "max_grade": section.max_grade,
                    }
                    for section in assessment.lecture_sections.select_related("lecture").order_by("order", "pk")
                ]
            grades = {
                grade.section_id: grade
                for grade in OfflineSectionGrade.objects.filter(
                    exam_code=code,
                    section_id__in=[row["section"].id for row in sections],
                )
            }
            for row in sections:
                row["grade"] = grades.get(row["section"].id)
    graded_total = sum(
        (row["grade"].score for row in sections if row.get("grade") is not None),
        Decimal("0.00"),
    )
    max_total = sum((row["max_grade"] for row in sections), Decimal("0.00"))
    return render(request, "ui/pages/admin/admin_offline_grade_entry.html", {
        "assessment": assessment, "code": code, "sections": sections, "grades": grades,
        "graded_total": graded_total, "max_total": max_total,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_offline_grade_section(request, assessment_id, code_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    legacy_response = _legacy_redirect(request, assessment)
    if legacy_response is not None:
        return legacy_response
    code = SemesterExamCode.objects.filter(id=code_id, assessment=assessment).only("code_number").first()
    entry_url = reverse("assessments:admin_offline_grade_entry", args=[assessment_id])
    if code is not None:
        entry_url = f"{entry_url}?{urlencode({'code_number': code.code_number})}"
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(entry_url)
    try:
        enter_offline_section_grade(
            code_id,
            section_id,
            score,
            request.user,
            assessment_id=assessment.id,
        )
        messages.success(request, _("Grade saved."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(entry_url)


# ---------------------------------------------------------------------------
# Reviewer views (Phase 8)
# ---------------------------------------------------------------------------


def _reviewer_assignment_context(assignment):
    """Return the minimal non-identity projection used by Reviewer templates."""
    return {
        "id": assignment.id,
        "section": str(assignment.section) if assignment.section_id else "",
        "cluster": str(assignment.cluster) if assignment.cluster_id else "",
        "scope": assignment.scope,
        "scope_label": assignment.get_scope_display(),
        "semester": str(assignment.semester) if assignment.semester_id else str(_("All semesters")),
        "assessment": str(assignment.assessment) if assignment.assessment_id else str(_("All Semester Exams")),
        "access_mode": assignment.access_mode,
        "access_mode_label": assignment.get_access_mode_display(),
        "title": str(assignment.assessment) if assignment.assessment_id else assignment.get_scope_display(),
    }


@login_required
@role_required(*ROLE_REVIEWER)
def reviewer_dashboard(request):
    assignments = list(
        ReviewerAssignment.objects.filter(
            reviewer=request.user, active=True,
        ).select_related(
            "cohort__diploma", "semester", "cluster", "section", "section__lecture", "section__assessment", "assessment",
        )
    )
    ungraded_by_id, online_ungraded_by_id = annotate_reviewer_counts(assignments)
    active_claims = {
        claim.assignment_id: {
            "status": claim.status,
            "claimed_at": claim.claimed_at,
        }
        for claim in ReviewerExamClaim.objects.filter(
            assignment_id__in=[assignment.id for assignment in assignments],
            status=REVIEWER_CLAIM_IN_PROGRESS,
        )
    }

    diplomas_by_id = {}
    for a in assignments:
        diploma = a.cohort.diploma
        diploma_entry = diplomas_by_id.get(diploma.id)
        if diploma_entry is None:
            diploma_entry = {
                "id": diploma.id,
                "title": diploma.title,
                "_cohorts": {},
                "assignment_count": 0,
                "online_pending": 0,
                "offline_pending": 0,
            }
            diplomas_by_id[diploma.id] = diploma_entry
        cohort_entry = diploma_entry["_cohorts"].get(a.cohort_id)
        if cohort_entry is None:
            cohort_entry = {
                "id": a.cohort_id,
                "name": a.cohort.name,
                "semesters": [],
                "_semesters": {},
                "assignment_count": 0,
                "online_pending": 0,
                "offline_pending": 0,
                "claim_count": 0,
            }
            diploma_entry["_cohorts"][a.cohort_id] = cohort_entry

        semester_key = a.semester_id or 0
        semester_entry = cohort_entry["_semesters"].get(semester_key)
        if semester_entry is None:
            semester_entry = {
                "title": a.semester.title if a.semester_id else str(_("Whole cohort")),
                "order": a.semester.order if a.semester_id else -1,
                "assignments": [],
            }
            cohort_entry["_semesters"][semester_key] = semester_entry

        item = {
            "assignment": _reviewer_assignment_context(a),
            "assessment": str(a.assessment) if a.assessment_id else None,
            "claim": active_claims.get(a.id),
            "ungraded": ungraded_by_id.get(a.id, 0),
            "online_ungraded": online_ungraded_by_id.get(a.id, 0),
            "can_online": a.access_mode in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH),
            "can_offline": a.access_mode in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH),
        }
        semester_entry["assignments"].append(item)
        cohort_entry["assignment_count"] += 1
        cohort_entry["online_pending"] += item["online_ungraded"]
        cohort_entry["offline_pending"] += item["ungraded"]
        diploma_entry["assignment_count"] += 1
        diploma_entry["online_pending"] += item["online_ungraded"]
        diploma_entry["offline_pending"] += item["ungraded"]
        if item["claim"]:
            cohort_entry["claim_count"] += 1

    diplomas = sorted(diplomas_by_id.values(), key=lambda row: (row["title"].casefold(), row["id"]))
    for diploma_entry in diplomas:
        cohorts = sorted(
            diploma_entry.pop("_cohorts").values(),
            key=lambda row: (row["name"].casefold(), row["id"]),
        )
        for cohort_entry in cohorts:
            cohort_entry["semesters"] = sorted(
                cohort_entry.pop("_semesters").values(),
                key=lambda row: (row["order"], row["title"].casefold()),
            )
            for semester_entry in cohort_entry["semesters"]:
                semester_entry["assignments"].sort(
                    key=lambda item: (
                        (item["assessment"] or "").casefold(),
                        (item["assignment"]["cluster"] or "").casefold(),
                        item["assignment"]["id"],
                    )
                )
        diploma_entry["cohorts"] = cohorts
        diploma_entry["cohort_count"] = len(cohorts)

    summary = {
        "diplomas": len(diplomas),
        "cohorts": sum(len(d["cohorts"]) for d in diplomas),
        "assignments": len(assignments),
        "online_pending": sum(
            cohort["online_pending"] for diploma in diplomas for cohort in diploma["cohorts"]
        ),
        "offline_pending": sum(
            cohort["offline_pending"] for diploma in diplomas for cohort in diploma["cohorts"]
        ),
    }

    cohort_rows = [
        {"diploma": diploma, "cohort": cohort}
        for diploma in diplomas
        for cohort in diploma["cohorts"]
    ]
    page_obj, pagination_qs = paginate(request, cohort_rows, per_page=6)
    page_diploma_map = {}
    page_diplomas = []
    for row in page_obj:
        source = row["diploma"]
        diploma_page_entry = page_diploma_map.get(source["id"])
        if diploma_page_entry is None:
            diploma_page_entry = {
                "id": source["id"],
                "title": source["title"],
                "cohort_count": source["cohort_count"],
                "assignment_count": source["assignment_count"],
                "cohorts": [],
            }
            page_diploma_map[source["id"]] = diploma_page_entry
            page_diplomas.append(diploma_page_entry)
        diploma_page_entry["cohorts"].append(row["cohort"])

    return render(request, "ui/pages/admin/reviewer_dashboard.html", {
        "page_diplomas": page_diplomas,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "summary": summary,
        "title": _("Reviewer dashboard"),
    })


@login_required
@role_required(*ROLE_REVIEWER)
def reviewer_section_grading(request, assignment_id):
    assignment = get_object_or_404(ReviewerAssignment, id=assignment_id, reviewer=request.user, active=True)
    if assignment.access_mode not in (REVIEWER_ACCESS_OFFLINE, REVIEWER_ACCESS_BOTH):
        messages.error(request, _("This assignment does not allow offline grading."))
        return redirect("assessments:reviewer_dashboard")
    code_number = request.POST.get("code_number", request.GET.get("code_number", "")).strip()
    section_id = safe_int(request.POST.get("section_id")) if request.method == "POST" else None
    score_raw = request.POST.get("score", "").strip() if request.method == "POST" else ""
    code = None
    graded_code_number = ""
    section_rows = []
    if code_number:
        code = SemesterExamCode.objects.filter(
            code_number=code_number,
            active=True,
            delivery_mode=DELIVERY_MODE_OFFLINE,
            diploma_enrollment__cohort=assignment.cohort,
        ).select_related("assessment", "diploma_enrollment").first()
        if not code:
            messages.error(request, _("No offline exam code found in your assigned scope."))
        elif not can_reviewer_grade_code(assignment, code):
            messages.error(request, _("Exam code does not belong to your assigned scope."))
        else:
            snapshots = list(
                code.section_maximums.select_related("section", "section__lecture")
                .order_by("order_snapshot", "pk")
            )
            if snapshots:
                sections = [
                    {
                        "section": row.section,
                        "section_title": row.section_title_snapshot,
                        "max_grade": row.max_grade_snapshot,
                    }
                    for row in snapshots
                ]
            else:
                sections = [
                    {
                        "section": section,
                        "section_title": section.lecture.title,
                        "max_grade": section.max_grade,
                    }
                    for section in AssessmentLectureSection.objects.filter(assessment_id=code.assessment_id)
                    .select_related("lecture")
                    .order_by("order", "id")
                ]
            if assignment.section_id:
                sections = [row for row in sections if row["section"].id == assignment.section_id]
            section_row = next(
                (row for row in sections if row["section"].id == section_id),
                None,
            ) if section_id else None
            if request.method == "POST" and section_id and not section_row:
                messages.error(request, _("Select a section within your assigned scope."))
            elif section_row and not score_raw:
                messages.error(request, _("Enter a score for this section."))
            elif section_row and score_raw:
                try:
                    score = validate_score(score_raw, max_score=section_row["max_grade"])
                    reviewer_grade_section(code.id, section_row["section"].id, score, request.user, assignment)
                    messages.success(request, _("Grade saved for code %(code)s.") % {"code": code.code_number})
                    url = reverse("assessments:reviewer_section_grading", args=[assignment_id])
                    return redirect(f"{url}?{urlencode({'code_number': code_number})}")
                except ValueError as exc:
                    messages.error(request, str(exc))
            grades = {
                grade.section_id: grade
                for grade in OfflineSectionGrade.objects.filter(
                    exam_code=code,
                    section_id__in=[row["section"].id for row in sections],
                ).only("id", "section_id", "score")
            }
            if sections and all(row["section"].id in grades for row in sections):
                graded_code_number = code.code_number
                code = None
            else:
                section_rows = [
                    {
                        "section": row["section"],
                        "section_title": row["section_title"],
                        "max_grade": row["max_grade"],
                        "grade": grades.get(row["section"].id),
                        "score": score_raw if row["section"].id == section_id and score_raw else (
                            grades[row["section"].id].score if row["section"].id in grades else ""
                        ),
                    }
                    for row in sections
                ]

    return render(request, "ui/pages/admin/reviewer_section_grading.html", {
        "assignment": _reviewer_assignment_context(assignment),
        "code": code,
        "code_number": code_number,
        "graded_code_number": graded_code_number,
        "section_rows": section_rows,
    })


@login_required
@role_required(*ROLE_REVIEWER)
def reviewer_online_grading(request, assignment_id):
    assignment = get_object_or_404(ReviewerAssignment, id=assignment_id, reviewer=request.user, active=True)
    wants_json = request.method == "POST" and (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )
    if assignment.access_mode not in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH):
        error = _("This assignment does not allow online grading.")
        if wants_json:
            return JsonResponse({
                "ok": False,
                "error": str(error),
                "redirect": reverse("assessments:reviewer_dashboard"),
            }, status=403)
        messages.error(request, error)
        return redirect("assessments:reviewer_dashboard")
    claim = get_active_reviewer_claim(assignment)
    if not claim:
        error = _("Request one anonymous submission before grading.")
        if wants_json:
            return JsonResponse({
                "ok": False,
                "error": str(error),
                "redirect": reverse("assessments:reviewer_dashboard"),
            }, status=409)
        messages.info(request, error)
        return redirect("assessments:reviewer_dashboard")

    if request.method == "POST":
        if request.POST.get("action") == "finalize":
            try:
                finalize_reviewer_claim(claim, request.user)
            except ValueError as exc:
                if wants_json:
                    return JsonResponse({"ok": False, "error": str(exc)}, status=400)
                messages.error(request, str(exc))
            else:
                if wants_json:
                    return JsonResponse({
                        "ok": True,
                        "message": str(_("Submission grading finalized.")),
                        "redirect": reverse("assessments:reviewer_dashboard"),
                    })
                messages.success(request, _("Submission grading finalized."))
            return redirect("assessments:reviewer_dashboard")

        answer_id = safe_int(request.POST.get("answer_id"))
        score_raw = request.POST.get("score", "").strip()
        if answer_id is None or not score_raw:
            error = _("Answer and score are required.")
            if wants_json:
                return JsonResponse({"ok": False, "error": str(error)}, status=400)
            messages.error(request, error)
        else:
            try:
                score = validate_score(score_raw)
                answer = reviewer_grade_written_answer(answer_id, score, request.user, assignment)
            except ValueError as exc:
                if wants_json:
                    return JsonResponse({"ok": False, "error": str(exc)}, status=400)
                messages.error(request, str(exc))
            else:
                if wants_json:
                    section_id = answer.attempt_question.section_id
                    written = AssessmentAttemptSnapshotAnswer.objects.filter(
                        attempt_question__attempt_id=claim.attempt_id,
                        attempt_question__question_type=QUESTION_TYPE_WRITTEN,
                    )
                    progress = written.aggregate(
                        total=Count("id"),
                        graded=Count("id", filter=Q(score__isnull=False)),
                        section_total=Count(
                            "id",
                            filter=Q(attempt_question__section_id=section_id),
                        ),
                        section_graded=Count(
                            "id",
                            filter=Q(
                                attempt_question__section_id=section_id,
                                score__isnull=False,
                            ),
                        ),
                    )
                    remaining = progress["total"] - progress["graded"]
                    message = (
                        _("All written answers are graded. You can finalize the submission.")
                        if remaining == 0
                        else _("Answer graded.")
                    )
                    return JsonResponse({
                        "ok": True,
                        "answer_id": answer.pk,
                        "score": str(answer.score),
                        "points": str(answer.attempt_question.points),
                        "section_id": section_id,
                        "section_graded_count": progress["section_graded"],
                        "section_total_count": progress["section_total"],
                        "graded_count": progress["graded"],
                        "total_count": progress["total"],
                        "remaining_count": remaining,
                        "message": str(message),
                    })
                messages.success(request, _("Answer graded."))
            return redirect("assessments:reviewer_online_grading", assignment_id=assignment_id)

    items = list(reviewer_claim_items(claim))
    section_groups = []
    for item in items:
        if not section_groups or section_groups[-1]["id"] != item["section_id"]:
            section_groups.append({
                "id": item["section_id"],
                "order": item["section_order"],
                "title": item["section_title"],
                "items": [],
            })
        section_groups[-1]["items"].append(item)
    for group in section_groups:
        group["total_count"] = len(group["items"])
        group["graded_count"] = sum(1 for item in group["items"] if item["score"] is not None)
    graded_count = sum(group["graded_count"] for group in section_groups)
    item_states = {
        str(item["answer_id"]): {
            "graded": item["score"] is not None,
            "score": str(item["score"]) if item["score"] is not None else "",
            "saving": False,
            "error": "",
        }
        for item in items
    }
    return render(request, "ui/pages/admin/reviewer_online_grading.html", {
        "assignment": _reviewer_assignment_context(assignment),
        "section_groups": section_groups,
        "section_count": len(section_groups),
        "item_count": len(items),
        "graded_count": graded_count,
        "remaining_count": len(items) - graded_count,
        "item_states": item_states,
        "submission_code": items[0]["code_number"] if items else "",
    })


@login_required
@role_required(*ROLE_REVIEWER)
@require_POST
def reviewer_claim_submission(request, assignment_id):
    assignment = get_object_or_404(ReviewerAssignment, id=assignment_id, reviewer=request.user, active=True)
    if assignment.access_mode not in (REVIEWER_ACCESS_ONLINE, REVIEWER_ACCESS_BOTH):
        messages.error(request, _("This assignment does not allow online grading."))
        return redirect("assessments:reviewer_dashboard")
    try:
        claim_next_reviewer_submission(assignment)
        return redirect("assessments:reviewer_online_grading", assignment_id=assignment.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:reviewer_dashboard")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_reviewer_assignment_create(request):
    """Create one reviewer assignment for one logical scope.

    Exam sections are intentionally not part of this assignment. A claim
    reserves the whole anonymous attempt; sections are grouped only for the
    grading display so the dashboard does not render one row per section.
    """

    user_model = get_user_model()
    reviewers = user_model.objects.filter(
        groups__name__in=ROLE_REVIEW_STAFF
    ).distinct().order_by("full_name", "email")
    cohorts = Cohort.objects.select_related("diploma").order_by("diploma__title", "name")
    semesters = Semester.objects.select_related("diploma").order_by("diploma_id", "order")
    clusters = StudentCluster.objects.select_related("cohort").order_by("cohort_id", "name")
    semester_exams = Assessment.objects.filter(
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        legacy_status=LEGACY_STATUS_ACTIVE,
    ).select_related("cohort", "semester").order_by("cohort_id", "semester_id", "title")
    reviewer_scope = request.POST.get("scope", "") if request.method == "POST" else ""
    reviewer_cohort = request.POST.get("cohort", "") if request.method == "POST" else ""
    reviewer_semester = request.POST.get("semester", "") if request.method == "POST" else ""
    reviewer_assessment = request.POST.get("assessment", "") if request.method == "POST" else ""
    reviewer_cluster = request.POST.get("cluster", "") if request.method == "POST" else ""
    reviewer_access_mode = request.POST.get("access_mode", REVIEWER_ACCESS_BOTH) if request.method == "POST" else REVIEWER_ACCESS_BOTH
    reviewer_ids = request.POST.getlist("reviewers") if request.method == "POST" else []
    if request.method == "POST":
        reviewer_ids = [value for value in reviewer_ids if value]
        reviewer_id_ints = [safe_int(value) for value in reviewer_ids]
        scope = request.POST.get("scope")
        cohort_id = safe_int(request.POST.get("cohort"))
        semester_id = safe_int(request.POST.get("semester"))
        assessment_id = safe_int(request.POST.get("assessment"))
        cluster_id = safe_int(request.POST.get("cluster"))
        invalid_scope_id = any(
            raw and parsed is None
            for raw, parsed in (
                (request.POST.get("semester"), semester_id),
                (request.POST.get("assessment"), assessment_id),
                (request.POST.get("cluster"), cluster_id),
            )
        )
        if not reviewer_ids or not scope or cohort_id is None:
            messages.error(request, _("Select at least one reviewer, a scope, and a cohort."))
        elif any(reviewer_id is None for reviewer_id in reviewer_id_ints):
            messages.error(request, _("Select valid reviewers."))
        elif invalid_scope_id:
            messages.error(request, _("Selected cohort or cluster is invalid."))
        elif scope not in dict(REVIEWER_SCOPE_CHOICES):
            messages.error(request, _("Select a valid Reviewer assignment scope."))
        elif reviewer_access_mode not in dict(REVIEWER_ACCESS_CHOICES):
            messages.error(request, _("Select a valid Reviewer access mode."))
        elif user_model.objects.filter(
            pk__in=reviewer_id_ints, groups__name__in=ROLE_REVIEW_STAFF
        ).distinct().count() != len(set(reviewer_id_ints)):
            messages.error(request, _("Every selected reviewer must be review staff."))
        else:
            cohort = Cohort.objects.filter(pk=cohort_id).first()
            semester = Semester.objects.filter(pk=semester_id).first() if semester_id else None
            exam = semester_exams.filter(pk=assessment_id).first() if assessment_id else None
            cluster = StudentCluster.objects.filter(pk=cluster_id, cohort_id=cohort_id).first() if cluster_id else None
            if scope == REVIEWER_SCOPE_EXAM and exam:
                # The selected exam is the source of truth for its semester.
                semester = exam.semester
            if not cohort or (cluster_id and not cluster):
                messages.error(request, _("Selected cohort or cluster is invalid."))
            elif scope == REVIEWER_SCOPE_SEMESTER and (not semester or semester.diploma_id != cohort.diploma_id):
                messages.error(request, _("Select a semester belonging to the selected cohort's Diploma."))
            elif scope == REVIEWER_SCOPE_EXAM and (
                not exam or exam.cohort_id != cohort.id
            ):
                messages.error(request, _("Select a Semester Exam belonging to the selected cohort."))
            elif scope == REVIEWER_SCOPE_COHORT and (semester or exam):
                messages.error(request, _("A cohort assignment cannot include a semester or exam."))
            elif scope == REVIEWER_SCOPE_SEMESTER and assessment_id:
                messages.error(request, _("A semester assignment cannot include a specific exam."))
            else:
                conflict = None
                cohort_missing = False
                reviewers_by_id = {reviewer.pk: reviewer for reviewer in reviewers}
                try:
                    with transaction.atomic():
                        # Scope overlap spans different uniqueness keys. Lock
                        # the parent cohort so concurrent assignment requests
                        # for this cohort serialize around the check and write.
                        cohort = Cohort.objects.select_for_update().filter(pk=cohort.pk).first()
                        if cohort is None:
                            cohort_missing = True
                        else:
                            cluster_overlap = (
                                Q(cluster__isnull=True) | Q(cluster_id=cluster.id)
                                if cluster
                                else Q()
                            )
                            for reviewer_id in set(reviewer_id_ints):
                                candidates = ReviewerAssignment.objects.filter(
                                    reviewer_id=reviewer_id,
                                    active=True,
                                    cohort_id=cohort.id,
                                )
                                exact = candidates.filter(
                                    section__isnull=True,
                                    scope=scope,
                                    semester_id=semester.id if semester else None,
                                    assessment_id=exam.id if exam else None,
                                    cluster_id=cluster.id if cluster else None,
                                ).first()
                                overlapping = candidates.exclude(pk=exact.pk) if exact else candidates
                                if scope == REVIEWER_SCOPE_COHORT:
                                    conflict = overlapping.filter(cluster_overlap).first()
                                elif scope == REVIEWER_SCOPE_SEMESTER:
                                    conflict = overlapping.filter(
                                        cluster_overlap
                                        & (
                                            Q(scope=REVIEWER_SCOPE_COHORT)
                                            | Q(scope=REVIEWER_SCOPE_SEMESTER, semester_id=semester.id)
                                            | Q(scope=REVIEWER_SCOPE_EXAM, semester_id=semester.id)
                                        ),
                                    ).first()
                                else:
                                    conflict = overlapping.filter(
                                        cluster_overlap
                                        & (
                                            Q(scope=REVIEWER_SCOPE_COHORT)
                                            | Q(scope=REVIEWER_SCOPE_SEMESTER, semester_id=semester.id)
                                            | Q(scope=REVIEWER_SCOPE_EXAM, assessment_id=exam.id)
                                        ),
                                    ).first()
                                if conflict:
                                    break
                            if conflict is None:
                                for reviewer_id in set(reviewer_id_ints):
                                    assignment, created = ReviewerAssignment.objects.update_or_create(
                                        reviewer_id=reviewer_id,
                                        cohort_id=cohort.id,
                                        scope=scope,
                                        semester_id=semester.id if semester else None,
                                        assessment_id=exam.id if exam else None,
                                        cluster_id=cluster.id if cluster else None,
                                        section=None,
                                        defaults={
                                            "active": True,
                                            "access_mode": reviewer_access_mode,
                                            "assigned_by": request.user,
                                        },
                                    )
                                    reviewer = reviewers_by_id[reviewer_id]
                                    if exam is not None:
                                        target = assessment_audit_target(exam)
                                    else:
                                        target = {
                                            "section": SECTION_ASSESSMENTS,
                                            "target_type": "reviewer_assignment",
                                            "target_id": assignment.pk,
                                            "target_label": f"Reviewer assignment #{assignment.pk}",
                                        }
                                    record_audit_event(
                                        actor=request.user,
                                        action=ACTION_REVIEWER_ASSIGNMENT_CREATED,
                                        **target,
                                        subject_type="user",
                                        subject_id=reviewer.pk,
                                        subject_label=reviewer.full_name or reviewer.email,
                                        metadata={
                                            "assignment_id": assignment.pk,
                                            "created": created,
                                            "reviewer_id": reviewer.pk,
                                            "reviewer_name": reviewer.full_name,
                                            "reviewer_role": reviewer.primary_role,
                                            "cohort_id": cohort.pk,
                                            "cohort_name": cohort.name,
                                            "semester_id": semester.pk if semester else None,
                                            "semester_title": semester.title if semester else "",
                                            "assessment_id": exam.pk if exam else None,
                                            "assessment_name": exam.title if exam else "",
                                            "assessment_type": exam.assessment_type if exam else "",
                                            "cluster_id": cluster.pk if cluster else None,
                                            "cluster_name": cluster.name if cluster else "",
                                            "scope": scope,
                                            "access_mode": reviewer_access_mode,
                                            "active_after": assignment.active,
                                        },
                                    )
                except IntegrityError:
                    messages.error(request, _("The assignment changed while it was being saved. Refresh and try again."))
                else:
                    if cohort_missing:
                        messages.error(request, _("The assignment changed while it was being saved. Refresh and try again."))
                    elif conflict:
                        messages.error(request, _("This Reviewer already has a broader or overlapping assignment for the selected scope."))
                    else:
                        messages.success(request, _("Reviewer assignments saved."))
                        return redirect("assessments:admin_reviewer_assignment_list")
    return render(request, "ui/pages/admin/reviewer_assignment_form.html", {
        "reviewers": reviewers,
        "cohorts": cohorts,
        "semesters": semesters,
        "clusters": clusters,
        "semester_exams": semester_exams,
        "reviewer_scope_choices": REVIEWER_SCOPE_CHOICES,
        "reviewer_scope": reviewer_scope,
        "reviewer_cohort": reviewer_cohort,
        "reviewer_semester": reviewer_semester,
        "reviewer_assessment": reviewer_assessment,
        "reviewer_cluster": reviewer_cluster,
        "reviewer_access_mode": reviewer_access_mode,
        "reviewer_ids": reviewer_ids,
        "reviewer_access_choices": REVIEWER_ACCESS_CHOICES,
        "back_url": reverse("assessments:admin_reviewer_assignment_list"),
        "title": _("Create reviewer assignment"),
        "eyebrow": _("Semester exam"),
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_reviewer_assignment_revoke(request, assignment_id):
    assignment = get_object_or_404(ReviewerAssignment, pk=assignment_id, active=True)
    try:
        revoke_reviewer_assignment(assignment, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Reviewer assignment revoked."))
    return redirect(safe_next_url(
        request,
        default=reverse("assessments:admin_reviewer_assignment_list"),
    ))


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_reviewer_assignment_list(request):
    page_context = _get_assessment_queue_context(request, _("Reviewer assignments"))
    scope_filters = page_context.pop("assessment_scope_filters")
    assignments = ReviewerAssignment.objects.select_related(
        "reviewer", "cohort", "semester", "assessment", "cluster", "section", "section__lecture", "assigned_by",
    ).filter(active=True).order_by("-created_at", "-pk")
    claims = ReviewerExamClaim.objects.select_related(
        "assignment__reviewer", "attempt__assessment", "claimed_by", "finalized_by",
    ).order_by("-claimed_at", "-id")
    if scope_filters["course_version_id"] is not None:
        assignments = assignments.none()
        claims = claims.none()
    elif scope_filters["cohort_id"] is not None:
        assignments = assignments.filter(
            cohort_id=scope_filters["cohort_id"],
        ).filter(
            Q(semester__isnull=True) | Q(semester_id=scope_filters["semester_id"]),
        ).distinct()
        claims = claims.filter(
            assignment__cohort_id=scope_filters["cohort_id"],
            attempt__assessment__cohort_id=scope_filters["cohort_id"],
            attempt__assessment__semester_id=scope_filters["semester_id"],
        )
    claims = claims[:100]
    page_obj, pagination_qs = paginate(request, assignments, per_page=25)
    page_assignments = list(page_obj)
    offline_by_assignment, available_by_assignment = annotate_reviewer_counts(page_assignments)
    for assignment in page_assignments:
        assignment.available_submissions = available_by_assignment.get(assignment.id, 0)
        assignment.available_offline = offline_by_assignment.get(assignment.id, 0)
    return render(request, "ui/pages/admin/reviewer_assignment_list.html", {
        **page_context,
        "assignments": page_assignments,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "claims": claims,
    })


# ---------------------------------------------------------------------------
# Exceptional assessment access
# ---------------------------------------------------------------------------


def _exceptional_access_enrollment_queryset(assessment, *, query=""):
    """Return the active enrollment scope that can receive an override."""
    if assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM:
        queryset = DiplomaEnrollment.objects.filter(
            cohort=assessment.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )
    else:
        queryset = CourseEnrollment.objects.filter(
            course_version=assessment.course_version,
            status=ENROLLMENT_STATUS_ACTIVE,
        )

    queryset = queryset.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
    )
    if query:
        queryset = queryset.filter(
            Q(student__full_name__icontains=query)
            | Q(student__user__full_name__icontains=query)
            | Q(student__user__email__icontains=query)
        )
    return queryset.select_related("student__user").order_by(
        "student__full_name",
        "id",
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_exceptional_access(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type not in (ASSESSMENT_TYPE_QUIZ, ASSESSMENT_TYPE_SEMESTER_EXAM):
        messages.error(request, _("Exceptional access is only available for quizzes and semester exams."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)

    if request.method == "POST":
        legacy_response = _legacy_redirect(request, assessment)
        if legacy_response is not None:
            return legacy_response
        diploma_enrollment_ids = [safe_int(value) for value in request.POST.getlist("diploma_enrollments")]
        course_enrollment_ids = [safe_int(value) for value in request.POST.getlist("course_enrollments")]
        open_at_str = request.POST.get("open_at", "")
        close_at_str = request.POST.get("close_at", "")
        reason = request.POST.get("reason", "").strip()
        force_online = request.POST.get("force_online") == "on"

        if any(value is None for value in [*diploma_enrollment_ids, *course_enrollment_ids]):
            messages.error(request, _("Invalid enrollment selection."))
            return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)

        if not reason:
            messages.error(request, _("Reason is required."))
            return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)
        if not open_at_str or not close_at_str:
            messages.error(request, _("Open and close times are required."))
            return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)

        try:
            open_at = timezone.make_aware(datetime.fromisoformat(open_at_str))
            close_at = timezone.make_aware(datetime.fromisoformat(close_at_str))
        except (ValueError, TypeError):
            messages.error(request, _("Invalid datetime format."))
            return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)

        diploma_enrollments = DiplomaEnrollment.objects.none()
        course_enrollments = CourseEnrollment.objects.none()
        if assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM:
            diploma_enrollments = _exceptional_access_enrollment_queryset(assessment).filter(
                id__in=diploma_enrollment_ids,
            )
        else:
            course_enrollments = _exceptional_access_enrollment_queryset(assessment).filter(
                id__in=course_enrollment_ids,
            )

        if not diploma_enrollments.exists() and not course_enrollments.exists():
            messages.error(request, _("Select at least one active enrollment."))
            return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)

        try:
            create_assessment_access_overrides(
                assessment,
                diploma_enrollments=diploma_enrollments,
                course_enrollments=course_enrollments,
                open_at=open_at, close_at=close_at,
                reason=reason, actor=request.user,
                force_online=force_online,
            )
            messages.success(request, _("Exceptional access overrides created."))
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect("assessments:admin_exceptional_access", assessment_id=assessment.id)

    overrides = SemesterExamAccessOverride.objects.filter(
        assessment=assessment,
    ).select_related(
        "diploma_enrollment__student__user__student_profile",
        "course_enrollment__student__user__student_profile",
        "created_by",
    ).order_by("-created_at")

    return render(request, "ui/pages/admin/exceptional_access.html", {
        "assessment": assessment,
        "overrides": overrides,
        "exceptional_access": True,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_GET
def admin_exceptional_access_enrollments(request, assessment_id):
    """Render one searchable page of eligible exceptional-access students."""
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type not in (ASSESSMENT_TYPE_QUIZ, ASSESSMENT_TYPE_SEMESTER_EXAM):
        return HttpResponse(
            _("Exceptional access is only available for quizzes and semester exams."),
            status=400,
        )

    query = request.GET.get("q", "").strip()[:100]
    page_obj, _pagination_qs = paginate(
        request,
        _exceptional_access_enrollment_queryset(assessment, query=query),
        per_page=25,
    )
    return render(request, "ui/fragments/admin/_exceptional_access_enrollment_picker.html", {
        "assessment": assessment,
        "page_obj": page_obj,
        "query": query,
        "enrollment_field": (
            "diploma_enrollments"
            if assessment.assessment_type == ASSESSMENT_TYPE_SEMESTER_EXAM
            else "course_enrollments"
        ),
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_cancel_exceptional_access(request, assessment_id, override_id):
    override = get_object_or_404(
        SemesterExamAccessOverride, id=override_id, assessment_id=assessment_id,
    )
    legacy_response = _legacy_redirect(request, override.assessment)
    if legacy_response is not None:
        return legacy_response
    reason = request.POST.get("reason", "").strip()
    try:
        cancel_assessment_access_override(override, actor=request.user, reason=reason)
        messages.success(request, _("Override cancelled."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_exceptional_access", assessment_id=assessment_id)
