"""Views for the assessments app (student and admin)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from django.views.decorators.http import require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, DiplomaLecture, Semester
from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ATTEMPT_STATUS_IN_PROGRESS,
    DELIVERY_MODE_OFFLINE,
    DELIVERY_MODE_ONLINE,
    QUESTION_TYPE_MCQ,
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
    SemesterExamAccessOverride,
    SemesterExamCode,
)
from apps.assessments.services import (
    AssessmentAnswerData,
    can_reviewer_grade_code,
    cancel_semester_exam_access_override,
    check_assessment_access,
    check_delivery_mode,
    create_semester_exam_access_overrides,
    diploma_autosave_attempt,
    enter_offline_section_grade,
    generate_exam_codes,
    get_available_bank_mcq_count,
    get_grading_queue,
    get_homework_pending_grading,
    get_reviewer_ungraded_count,
    get_student_assessments,
    get_student_attempts,
    grade_diploma_snapshot_answer,
    grade_homework,
    grade_written_answer,
    has_active_enrollment,
    hide_all_released,
    hide_grades,
    release_all_graded,
    release_grades,
    reviewer_grade_section,
    set_exam_code_delivery_mode,
    start_assessment,
    submit_assessment,
    submit_diploma_assessment,
    submit_homework,
)
from apps.audit.constants import (
    ACTION_EXAM_CREATED,
    ACTION_EXAM_TOGGLED,
    ACTION_EXAM_UPDATED,
    ACTION_GRADE_CHANGED,
    ACTION_GRADE_HIDDEN,
    ACTION_GRADE_OVERRIDE,
    ACTION_GRADE_RELEASED,
    ACTION_QUESTION_CREATED,
    ACTION_QUESTION_DELETED,
    ACTION_QUESTION_UPDATED,
)
from apps.audit.services import record_audit_event
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_EXAM_STAFF,
    ROLE_GRADING_QUEUE,
    ROLE_GRADING_STAFF,
    ROLE_REVIEWER,
    ROLE_REVIEW_STAFF,
    ROLE_SENIOR_STAFF,
    assessment_scope_filter,
    can_grade_attempt,
    is_admin,
    is_moderator,
    role_required,
    safe_int,
    validate_score,
)
from apps.core.views import safe_next_url
from apps.storage.services import FileValidationError, StorageError, storage_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _htmx_redirect(response, target):
    response["HX-Redirect"] = target
    return response


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@login_required
def student_list(request):
    assessments = list(get_student_assessments(request.user))
    attempts = list(get_student_attempts(request.user))
    latest_attempts = {}
    for attempt in attempts:
        latest_attempts.setdefault(attempt.assessment_id, attempt)
    return render(
        request,
        "assessments/student_list.html",
        {
            "assessment_cards": [
                {
                    "assessment": assessment,
                    "latest_attempt": latest_attempts.get(assessment.id),
                }
                for assessment in assessments
            ],
            "attempts": attempts,
        },
    )


@login_required
def assessment_instructions(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        return redirect("assessments:take", assessment_id=assessment.id)
    try:
        check_assessment_access(request.user, assessment)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment, student=request.user,
    ).order_by("-created_at").first()
    return render(
        request,
        "assessments/assessment_instructions.html",
        {"assessment": assessment, "attempt": attempt},
    )


@login_required
@require_POST
def start_assessment_view(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("Homework assessments use the upload flow."))
        return redirect("assessments:take", assessment_id=assessment_id)
    try:
        start_assessment(request.user, assessment)
        messages.success(request, _("Assessment started."))
        return redirect("assessments:take", assessment_id=assessment.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:instructions", assessment_id=assessment_id)


@login_required
def take_assessment(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    try:
        check_delivery_mode(request.user, assessment)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
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
        existing_qs = HomeworkSubmission.objects.filter(
            assessment=assessment, student=request.user,
        ).select_related("attempt").order_by("-created_at")
        existing_submissions = list(existing_qs)
        remaining_uploads = max(0, assessment.max_upload_files - len(existing_submissions))
        existing_download_urls = {}
        for s in existing_submissions:
            existing_download_urls[s.id] = storage_service.get_protected_download_url(
                s.storage_key,
                user=request.user,
                permission_fn=lambda u, _s=s: (
                    u == _s.student
                    or is_admin(u) or is_moderator(u)
                ),
            )
        return render(
            request,
            "assessments/take_assessment.html",
            {
                "assessment": assessment,
                "existing_submissions": existing_submissions,
                "existing_download_urls": existing_download_urls,
                "remaining_uploads": remaining_uploads,
            },
        )
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment,
        student=request.user,
        status=ATTEMPT_STATUS_IN_PROGRESS,
    ).first()
    if not attempt:
        messages.error(request, _("No active attempt found."))
        return redirect("assessments:instructions", assessment_id=assessment_id)

    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    now = timezone.now()
    remaining = 0
    if attempt.started_at and assessment.duration_minutes:
        elapsed = (now - attempt.started_at).total_seconds()
        remaining = max(0, int(assessment.duration_minutes * 60 - elapsed))

    if is_diploma:
        snapshot_questions = attempt.snapshot_questions.order_by("order").prefetch_related("choices")
        return render(
            request,
            "assessments/take_assessment.html",
            {
                "assessment": assessment,
                "attempt": attempt,
                "snapshot_questions": snapshot_questions,
                "remaining_seconds": remaining,
                "is_diploma_random": True,
            },
        )

    questions = assessment.questions.prefetch_related("choices").all()
    return render(
        request,
        "assessments/take_assessment.html",
        {
            "assessment": assessment,
            "attempt": attempt,
            "questions": questions,
            "remaining_seconds": remaining,
        },
    )


@login_required
def submit_assessment_view(request, assessment_id):
    if request.method != "POST":
        return redirect("assessments:take", assessment_id=assessment_id)
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    try:
        check_delivery_mode(request.user, assessment)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:student_list")
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("Homework assessments must be submitted through the upload form."))
        return redirect("assessments:take", assessment_id=assessment_id)
    attempt = get_object_or_404(
        AssessmentAttempt,
        assessment=assessment,
        student=request.user,
        status=ATTEMPT_STATUS_IN_PROGRESS,
    )
    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    if is_diploma:
        answer_data = {k: v for k, v in request.POST.items() if k.startswith("q_")}
        try:
            submit_diploma_assessment(attempt, answer_data)
            messages.success(request, _("Assessment submitted successfully."))
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


@login_required
def submit_homework_view(request, assessment_id):
    if request.method != "POST":
        return redirect("assessments:take", assessment_id=assessment_id)
    assessment = get_object_or_404(Assessment, id=assessment_id, is_active=True)
    if assessment.assessment_type != ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("This is not a homework assessment."))
        return redirect("assessments:instructions", assessment_id=assessment_id)
    uploaded_files = request.FILES.getlist("homework_files")
    if not uploaded_files:
        messages.error(request, _("Please select at least one file to upload."))
        return redirect("assessments:take", assessment_id=assessment_id)
    submitted = 0
    errors = []
    for uploaded in uploaded_files:
        try:
            submit_homework(
                student=request.user,
                assessment=assessment,
                file_data=uploaded.read(),
                original_filename=uploaded.name,
                content_type=uploaded.content_type,
            )
            submitted += 1
        except (FileValidationError, StorageError, ValueError) as exc:
            errors.append(f"{uploaded.name}: {exc}")
    if submitted and not errors:
        messages.success(
            request,
            ngettext_lazy(
                "Homework submitted successfully.",
                "%(count)d files submitted successfully.",
                submitted,
            ) % {"count": submitted},
        )
        return redirect("assessments:results", assessment_id=assessment_id)
    if submitted:
        messages.warning(
            request,
            ngettext_lazy(
                "One file submitted; the rest failed.",
                "%(count)d files submitted; the rest failed.",
                submitted,
            ) % {"count": submitted},
        )
    for err in errors:
        messages.error(request, err)
    return redirect("assessments:take", assessment_id=assessment_id)


@login_required
@require_POST
def api_diploma_autosave(request, assessment_id):
    """POST autosave for diploma random assessment in-progress attempt."""

    try:
        student = request.user
        assessment = Assessment.objects.get(id=assessment_id)
        attempt = AssessmentAttempt.objects.filter(
            assessment=assessment, student=student, status=ATTEMPT_STATUS_IN_PROGRESS,
        ).first()
        if not attempt:
            return JsonResponse({"error": "no_active_attempt"}, status=404)

        try:
            data = json.loads(request.body)
        except (ValueError, TypeError):
            return JsonResponse({"error": "invalid_json"}, status=400)

        answer_data = data.get("answers", {})
        diploma_autosave_attempt(attempt, answer_data)
        return JsonResponse({"status": "ok"})
    except Assessment.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)


@login_required
def assessment_results(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        submission = HomeworkSubmission.objects.filter(
            assessment=assessment, student=request.user,
        ).order_by("-created_at").first()
        download_url = storage_service.get_protected_download_url(
            submission.storage_key,
            user=request.user,
            permission_fn=lambda u: (
                u == submission.student
                or is_admin(u) or is_moderator(u)
            ),
        ) if submission else None
        return render(
            request,
            "assessments/assessment_results.html",
            {
                "assessment": assessment,
                "submission": submission,
                "submission_download_url": download_url,
            },
        )
    attempt = AssessmentAttempt.objects.filter(
        assessment=assessment,
        student=request.user,
    ).order_by("-created_at").first()
    if not attempt:
        return redirect("assessments:student_list")

    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    if is_diploma:
        snapshot_questions = attempt.snapshot_questions.order_by("order").prefetch_related("choices")
        snapshot_answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt,
            ).select_related("selected_choice")
        }
        return render(
            request,
            "assessments/assessment_results.html",
            {
                "assessment": assessment,
                "attempt": attempt,
                "snapshot_questions": snapshot_questions,
                "snapshot_answers": snapshot_answers,
                "is_diploma_random": True,
            },
        )

    answers = attempt.answers.select_related("question", "selected_choice").all()
    return render(
        request,
        "assessments/assessment_results.html",
        {
            "assessment": assessment,
            "attempt": attempt,
            "answers": answers,
        },
    )


def _parse_answers(request, assessment):
    answers = []
    questions = {q.id: q for q in assessment.questions.prefetch_related("choices").all()}
    for key, value in request.POST.items():
        if not key.startswith("question_"):
            continue
        qid = safe_int(key.removeprefix("question_"))
        if qid is None:
            raise ValueError(_("Invalid question."))
        question = questions.get(qid)
        if not question:
            continue
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
@role_required(*ROLE_GRADING_STAFF)
def admin_list(request):
    q = request.GET.get("q", "")
    type_filter = request.GET.get("type", "")
    student_id = request.GET.get("student_id", "")
    assessments = Assessment.objects.all().select_related("created_by")
    assessments = assessments.filter(assessment_scope_filter(request.user))
    if q:
        assessments = assessments.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if type_filter:
        assessments = assessments.filter(assessment_type=type_filter)
    if student_id.isdigit():
        assessments = assessments.filter(attempts__student_id=int(student_id)).distinct()
    filters = []
    if type_filter is not None:
        filters.append({
            "name": "type",
            "label": _("Type"),
            "value": type_filter,
            "options": [
                {"value": "exam", "label": _("Exam"), "selected": type_filter == "exam"},
                {"value": "quiz", "label": _("Quiz"), "selected": type_filter == "quiz"},
                {"value": "homework", "label": _("Homework"), "selected": type_filter == "homework"},
            ],
        })
    page_obj, pagination_qs = paginate(request, assessments)
    ctx = {"page_obj": page_obj, "pagination_qs": pagination_qs, "search_value": q, "filters": filters, "is_admin_user": is_admin(request.user), "student_id": student_id}
    if is_htmx(request):
        return render(request, "assessments/partials/_assessment_list_table.html", ctx)
    return render(request, "assessments/admin_list.html", ctx)


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
    if is_diploma:
        sections_qs = assessment.lecture_sections.select_related("lecture").prefetch_related("manual_questions").all()
        for s in sections_qs:
            s.available_count = get_available_bank_mcq_count(s.lecture)
            calculated = s.calculated_max_grade()
            if s.max_grade != calculated:
                s.max_grade = calculated
                s.save(update_fields=["max_grade", "updated_at"])
            section_total += calculated
            sections.append(s)
        if assessment.total_points != section_total:
            assessment.total_points = section_total
            assessment.save(update_fields=["total_points", "updated_at"])
    return render(
        request,
        "assessments/admin_detail.html",
        {
            "assessment": assessment,
            "sections": sections,
            "section_total": section_total,
            "is_diploma": is_diploma,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_preview(request, assessment_id):
    """Render the student-facing take page for admin preview (no attempt/upload)."""
    assessment = get_object_or_404(Assessment, id=assessment_id)
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        return render(
            request,
            "assessments/take_assessment.html",
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
        "assessments/take_assessment.html",
        {
            "assessment": assessment,
            "is_preview": True,
            "attempt": None,
            "questions": questions,
            "remaining_seconds": 0,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_scope_fields(request):
    scope_type = request.GET.get("scope_type", "")
    data = {"scope_type": scope_type}
    diploma_val = request.GET.get("diploma", "")
    if scope_type == "diploma":
        data["diploma"] = diploma_val
        data["semester"] = request.GET.get("semester", "")
        data["cohort"] = request.GET.get("cohort", "")
    form = AssessmentForm(data)
    return render(request, "assessments/partials/_scope_fields.html", {
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
                target_type="assessment",
                target_id=str(assessment.id),
                metadata={"title": assessment.title, "assessment_type": assessment.assessment_type},
            )
            messages.success(request, _("Assessment created."))
            return redirect(next_url) if next_url else redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentForm()
    return render(
        request,
        "assessments/admin_assessment_form.html",
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
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = AssessmentForm(request.POST, instance=assessment)
        if form.is_valid():
            form.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                target_type="assessment",
                target_id=str(assessment.id),
                metadata={"title": assessment.title},
            )
            messages.success(request, _("Assessment updated."))
            return redirect(next_url) if next_url else redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentForm(instance=assessment)
    return render(
        request,
        "assessments/admin_assessment_form.html",
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
def admin_toggle_active(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    assessment.is_active = not assessment.is_active
    assessment.save(update_fields=["is_active", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_EXAM_TOGGLED,
        target_type="assessment",
        target_id=str(assessment.id),
        metadata={"is_active": assessment.is_active, "title": assessment.title},
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
    return {
        "assessment": assessment,
        "question": question,
        "form": form,
        "choices": choices,
        "full_page": False,
        "question_save_url": reverse(
            "assessments:admin_question_save",
            args=[assessment.id] + ([question.id] if question else []),
        ),
    }


def _render_question_form(request, assessment, question=None, form=None, choices=None):
    context = _question_form_context(assessment, question=question, form=form, choices=choices)
    context["full_page"] = not is_htmx(request)
    template = "assessments/partials/question_form.html" if is_htmx(request) else "assessments/admin_question_form.html"
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
    question = get_object_or_404(AssessmentQuestion, id=question_id, assessment=assessment) if question_id else None
    form = QuestionForm(request.POST, instance=question)
    parsed_choices = _parse_inline_choices(request)
    if form.is_valid():
        if form.cleaned_data["question_type"] == "mcq":
            error = _validate_mcq_choices(parsed_choices)
            if error:
                form.add_error(None, error)
        if not form.errors:
            question = form.save(commit=False)
            question.assessment = assessment
            if not question_id:
                question.order = assessment.questions.count() + 1
            question.save()
            if question.question_type == "mcq":
                _save_choices_from_parsed(question, parsed_choices)
            elif question.question_type == "written":
                AssessmentChoice.objects.filter(question=question).delete()
            record_audit_event(
                actor=request.user,
                action=ACTION_QUESTION_CREATED if not question_id else ACTION_QUESTION_UPDATED,
                target_type="assessment_question",
                target_id=str(question.id),
                metadata={"assessment_id": assessment_id, "question_type": question.question_type},
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
    record_audit_event(
        actor=request.user,
        action=ACTION_QUESTION_DELETED,
        target_type="assessment_question",
        target_id=str(question_id),
        metadata={"assessment_id": assessment_id},
    )
    question.delete()
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
            order = 0
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
@role_required(*ROLE_GRADING_QUEUE)
def admin_grade_list(request):
    queue = get_grading_queue(request.user)
    homework_queue = get_homework_pending_grading(request.user)
    queue_page, _ = paginate(request, queue)
    hw_page, _ = paginate(request, homework_queue)
    return render(
        request,
        "assessments/admin_grade_list.html",
        {"queue_page": queue_page, "homework_queue_page": hw_page},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_homework_grade_list(request):
    submissions = get_homework_pending_grading(request.user).order_by("-created_at")
    page_obj, pagination_qs = paginate(request, submissions)
    return render(
        request,
        "assessments/admin_homework_list.html",
        {"page_obj": page_obj, "pagination_qs": pagination_qs},
    )


@login_required
@role_required(*ROLE_GRADING_QUEUE)
def admin_homework_grade_detail(request, submission_id):
    submission = get_object_or_404(
        HomeworkSubmission.objects.select_related("assessment", "student", "attempt"),
        id=submission_id,
    )
    if not can_grade_attempt(request.user, submission):
        raise PermissionDenied
    download_url = storage_service.get_protected_download_url(
        submission.storage_key,
        user=request.user,
        permission_fn=lambda u: can_grade_attempt(u, submission),
    )
    if request.method == "POST":
        try:
            score = validate_score(request.POST.get("score", 0))
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("assessments:admin_homework_grade_detail", submission_id=submission_id)
        feedback = request.POST.get("feedback", "")
        try:
            grade_homework(submission.id, score, feedback, request.user)
            record_audit_event(
                actor=request.user,
                action=ACTION_GRADE_CHANGED,
                target_type="homework_submission",
                target_id=str(submission_id),
                metadata={"assessment_id": submission.assessment_id, "student_id": submission.student_id},
            )
            messages.success(request, _("Homework graded."))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("assessments:admin_homework_grade_detail", submission_id=submission_id)
    return render(
        request,
        "assessments/admin_homework_grade_detail.html",
        {"submission": submission, "download_url": download_url},
    )


@login_required
@role_required(*ROLE_GRADING_QUEUE)
def admin_grade_detail(request, attempt_id):
    attempt = get_object_or_404(
        AssessmentAttempt.objects.select_related("assessment", "student"),
        id=attempt_id,
    )
    if not can_grade_attempt(request.user, attempt):
        raise PermissionDenied
    assessment = attempt.assessment
    is_diploma = bool(assessment.course_version_id is None and assessment.semester_id and assessment.cohort_id)
    ctx_base = {"attempt": attempt, "is_admin_user": is_admin(request.user)}

    if is_diploma:
        snapshot_questions = attempt.snapshot_questions.order_by("order").prefetch_related("choices")
        snapshot_answers = {
            a.attempt_question_id: a
            for a in AssessmentAttemptSnapshotAnswer.objects.filter(
                attempt_question__attempt=attempt,
            ).select_related("selected_choice")
        }
        if request.method == "POST":
            with transaction.atomic():
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
                        try:
                            grade_diploma_snapshot_answer(
                                answer.pk, score, feedback, request.user,
                                override_reason=override_reason,
                            )
                        except ValueError as exc:
                            messages.error(request, str(exc))
            record_audit_event(
                actor=request.user,
                action=ACTION_GRADE_CHANGED,
                target_type="assessment_attempt",
                target_id=str(attempt_id),
                metadata={"assessment_id": attempt.assessment_id, "student_id": attempt.student_id},
            )
            messages.success(request, _("Grades saved."))
            return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)
        snapshot_pairs = [(sq, snapshot_answers.get(sq.pk)) for sq in snapshot_questions]
        return render(
            request,
            "assessments/admin_grade_detail.html",
            {**ctx_base, "snapshot_pairs": snapshot_pairs, "is_diploma_random": True},
        )

    questions = assessment.questions.prefetch_related("choices").all()
    answers = {a.question_id: a for a in attempt.answers.select_related("question", "selected_choice").all()}
    if request.method == "POST":
        with transaction.atomic():
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
                    try:
                        grade_written_answer(
                            answer.id, score, feedback, request.user,
                            override_reason=override_reason,
                        )
                    except ValueError as exc:
                        messages.error(request, str(exc))
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_CHANGED,
            target_type="assessment_attempt",
            target_id=str(attempt_id),
            metadata={"assessment_id": attempt.assessment_id, "student_id": attempt.student_id},
        )
        messages.success(request, _("Grades saved."))
        return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)
    question_answer_pairs = [(q, answers.get(q.id)) for q in questions]
    return render(
        request,
        "assessments/admin_grade_detail.html",
        {**ctx_base, "question_answer_pairs": question_answer_pairs},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_release_grades(request, attempt_id):
    attempt = get_object_or_404(AssessmentAttempt, id=attempt_id)
    if not can_grade_attempt(request.user, attempt):
        raise PermissionDenied
    release_grades(attempt)
    record_audit_event(
        actor=request.user,
        action=ACTION_GRADE_RELEASED,
        target_type="assessment_attempt",
        target_id=str(attempt_id),
        metadata={"assessment_id": attempt.assessment_id, "student_id": attempt.student_id},
    )
    messages.success(request, _("Grades released to student."))
    return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_hide_grades(request, attempt_id):
    attempt = get_object_or_404(AssessmentAttempt, id=attempt_id)
    if not can_grade_attempt(request.user, attempt):
        raise PermissionDenied
    hide_grades(attempt)
    record_audit_event(
        actor=request.user,
        action=ACTION_GRADE_HIDDEN,
        target_type="assessment_attempt",
        target_id=str(attempt_id),
        metadata={"assessment_id": attempt.assessment_id, "student_id": attempt.student_id},
    )
    messages.success(request, _("Grades hidden from student."))
    return redirect("assessments:admin_grade_detail", attempt_id=attempt_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_release_all_grades(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    count = release_all_graded(assessment)
    record_audit_event(
        actor=request.user,
        action=ACTION_GRADE_RELEASED,
        target_type="assessment",
        target_id=str(assessment_id),
        metadata={"count": count},
    )
    messages.success(request, _("Released %(count)s graded attempt(s).") % {"count": count})
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_hide_all_grades(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    count = hide_all_released(assessment)
    record_audit_event(
        actor=request.user,
        action=ACTION_GRADE_HIDDEN,
        target_type="assessment",
        target_id=str(assessment_id),
        metadata={"count": count},
    )
    messages.success(request, _("Hidden %(count)s released attempt(s).") % {"count": count})
    return redirect("assessments:admin_detail", assessment_id=assessment_id)


@login_required
@role_required(*ROLE_GRADING_QUEUE)
@require_POST
def admin_grade_answer(request, answer_id):
    answer = get_object_or_404(
        AssessmentAnswer.objects.select_related("attempt__assessment", "attempt__student"),
        id=answer_id,
    )
    if not can_grade_attempt(request.user, answer.attempt):
        raise PermissionDenied
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:admin_grade_detail", attempt_id=answer.attempt_id)
    feedback = request.POST.get("feedback", "")
    override_reason = request.POST.get("override_reason", "")
    try:
        grade_written_answer(
            answer.id, score, feedback, request.user,
            override_reason=override_reason,
        )
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_OVERRIDE if override_reason else ACTION_GRADE_CHANGED,
            target_type="assessment_answer",
            target_id=str(answer_id),
            metadata={
                "attempt_id": answer.attempt_id,
                "question_id": answer.question_id,
                "score": str(score),
                "override_reason": override_reason or "",
            },
        )
        messages.success(request, _("Answer graded."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_grade_detail", attempt_id=answer.attempt_id)


@login_required
@role_required(*ROLE_GRADING_QUEUE)
@require_POST
def admin_grade_homework_submission(request, submission_id):
    submission = get_object_or_404(
        HomeworkSubmission.objects.select_related("assessment", "student", "attempt"),
        id=submission_id,
    )
    if not can_grade_attempt(request.user, submission):
        raise PermissionDenied
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:admin_grade_detail", attempt_id=submission.attempt_id)
    feedback = request.POST.get("feedback", "")
    try:
        grade_homework(submission.id, score, feedback, request.user)
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_CHANGED,
            target_type="homework_submission",
            target_id=str(submission_id),
            metadata={
                "attempt_id": submission.attempt_id,
                "assessment_id": submission.assessment_id,
                "score": str(score),
            },
        )
        messages.success(request, _("Homework graded."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_grade_detail", attempt_id=submission.attempt_id)


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
    return render(request, "assessments/admin_lecture_bank_list.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "questions": questions,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_bank_question_create(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
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
                messages.success(request, _("Bank question created."))
                return redirect("assessments:admin_lecture_bank_list", diploma_id=diploma.id, cohort_id=cohort.id, semester_id=semester.id, lecture_id=lecture.id)
        choices = _choices_for_template(parsed_choices, None)
    else:
        form = DiplomaLectureQuestionForm()
        choices = []
    return render(request, "assessments/admin_lecture_bank_question_form.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "form": form, "choices": choices, "question": None,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_bank_question_edit(request, diploma_id, cohort_id, semester_id, lecture_id, question_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    question = get_object_or_404(DiplomaLectureQuestion, id=question_id, lecture=lecture)
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
                messages.success(request, _("Bank question updated."))
                return redirect("assessments:admin_lecture_bank_list", diploma_id=diploma.id, cohort_id=cohort.id, semester_id=semester.id, lecture_id=lecture.id)
        choices = _choices_for_template(parsed_choices, question)
    else:
        form = DiplomaLectureQuestionForm(instance=question)
        choices = _choices_for_template(None, question)
    return render(request, "assessments/admin_lecture_bank_question_form.html", {
        "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture,
        "form": form, "choices": choices, "question": question,
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
    question.delete()
    _sync_assessment_grades_for_lecture(lecture)
    messages.success(request, _("Bank question deleted."))
    return redirect("assessments:admin_lecture_bank_list", diploma_id=diploma.id, cohort_id=cohort.id, semester_id=semester.id, lecture_id=lecture.id)


# ---------------------------------------------------------------------------
# Diploma section management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_create(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
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
                messages.success(request, _("Section added."))
                return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentLectureSectionForm(assessment=assessment)
    return render(request, "assessments/admin_section_form.html", {
        "form": form, "assessment": assessment, "title": _("Add Section"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_edit(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
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
                messages.success(request, _("Section updated."))
                return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentLectureSectionForm(instance=section, assessment=assessment)
    return render(request, "assessments/admin_section_form.html", {
        "form": form, "assessment": assessment, "section": section, "title": _("Edit Section"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_section_delete(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    if assessment.attempts.exists():
        messages.error(request, _("Cannot delete section after attempts exist."))
        return redirect("assessments:admin_detail", assessment_id=assessment.id)
    section.delete()
    assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
    assessment.save(update_fields=["total_points", "updated_at"])
    messages.success(request, _("Section deleted."))
    return redirect("assessments:admin_detail", assessment_id=assessment.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_manual_question_create(request, assessment_id, section_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
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
            messages.success(request, _("Manual question added."))
            return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentSectionManualQuestionForm()
    return render(request, "assessments/admin_manual_question_form.html", {
        "form": form, "assessment": assessment, "section": section, "title": _("Add Manual Question"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_section_manual_question_edit(request, assessment_id, section_id, mq_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
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
            messages.success(request, _("Manual question updated."))
            return redirect("assessments:admin_detail", assessment_id=assessment.id)
    else:
        form = AssessmentSectionManualQuestionForm(instance=mq)
    return render(request, "assessments/admin_manual_question_form.html", {
        "form": form, "assessment": assessment, "section": section, "mq": mq, "title": _("Edit Manual Question"),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_section_manual_question_delete(request, assessment_id, section_id, mq_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
    section = get_object_or_404(AssessmentLectureSection, id=section_id, assessment=assessment)
    mq = get_object_or_404(AssessmentSectionManualQuestion, id=mq_id, section=section)
    mq.delete()
    section.max_grade = section.calculated_max_grade()
    section.save(update_fields=["max_grade", "updated_at"])
    assessment.total_points = sum(s.calculated_max_grade() for s in assessment.lecture_sections.all())
    assessment.save(update_fields=["total_points", "updated_at"])
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
    return render(request, "assessments/admin_roster_list.html", {
        "assessment": assessment, "codes": codes,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_generate_codes(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)
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
def admin_toggle_delivery_mode(request, assessment_id, code_id):
    code = get_object_or_404(SemesterExamCode, pk=code_id, assessment_id=assessment_id)
    new_mode = DELIVERY_MODE_OFFLINE if code.delivery_mode == DELIVERY_MODE_ONLINE else DELIVERY_MODE_ONLINE
    set_exam_code_delivery_mode(code_id, new_mode)
    messages.success(request, _("Delivery mode toggled."))
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
        name = code.diploma_enrollment.student.user.student_profile.full_name
        response.write(f"{code.code_number}\t{name}\n")
    return response


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_offline_grade_entry(request, assessment_id):
    assessment = get_object_or_404(Assessment.objects.prefetch_related("lecture_sections"), id=assessment_id)
    code = None
    sections = []
    grades = {}
    if request.method == "POST":
        code_number = request.POST.get("code_number", "").strip()
        code = SemesterExamCode.objects.filter(
            assessment=assessment, code_number=code_number,
        ).select_related("diploma_enrollment").first()
        if not code:
            messages.error(request, _("No exam code found."))
        else:
            sections = list(assessment.lecture_sections.all())
            grade_qs = OfflineSectionGrade.objects.filter(exam_code=code)
            grades = {g.section_id: g for g in grade_qs}
    return render(request, "assessments/admin_offline_grade_entry.html", {
        "assessment": assessment, "code": code, "sections": sections, "grades": grades,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_offline_grade_section(request, assessment_id, code_id, section_id):
    get_object_or_404(Assessment, id=assessment_id)
    try:
        score = validate_score(request.POST.get("score", 0))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("assessments:admin_offline_grade_entry", assessment_id=assessment_id)
    try:
        enter_offline_section_grade(code_id, section_id, score, request.user)
        messages.success(request, _("Grade saved."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_offline_grade_entry", assessment_id=assessment_id)


# ---------------------------------------------------------------------------
# Reviewer views (Phase 8)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_REVIEWER)
def reviewer_dashboard(request):
    assignments = ReviewerAssignment.objects.filter(
        reviewer=request.user, active=True,
    ).select_related("cohort", "semester", "section", "section__lecture", "section__assessment")
    groups = {}
    for a in assignments:
        key = (a.cohort_id, a.semester_id, a.cohort.name, a.semester.title)
        if key not in groups:
            groups[key] = {"cohort": a.cohort, "semester": a.semester, "assignments": []}
        ungraded = get_reviewer_ungraded_count(a)
        groups[key]["assignments"].append({"assignment": a, "ungraded": ungraded})
    return render(request, "assessments/reviewer_dashboard.html", {"groups": groups})


@login_required
@role_required(*ROLE_REVIEWER)
def reviewer_section_grading(request, assignment_id):
    assignment = get_object_or_404(ReviewerAssignment, id=assignment_id, reviewer=request.user, active=True)
    if not assignment.section_id:
        messages.error(request, _("This assignment has no section to grade."))
        return redirect("assessments:reviewer_dashboard")

    section = assignment.section
    code = None
    grade = None

    if request.method == "POST":
        code_number = request.POST.get("code_number", "").strip()
        score_raw = request.POST.get("score", "").strip()
        if code_number and score_raw:
            code = SemesterExamCode.objects.filter(
                code_number=code_number,
                assessment=section.assessment,
                active=True,
                diploma_enrollment__cohort=assignment.cohort,
            ).select_related("diploma_enrollment").first()
            if not code:
                messages.error(request, _("No exam code found for this assessment and cohort."))
            elif not can_reviewer_grade_code(assignment, code):
                messages.error(request, _("Exam code does not belong to your assigned cohort."))
            else:
                try:
                    score = validate_score(score_raw)
                    reviewer_grade_section(code.id, section.id, score, request.user)
                    messages.success(request, _("Grade saved for code %(code)s.") % {"code": code.code_number})
                    return redirect("assessments:reviewer_section_grading", assignment_id=assignment_id)
                except ValueError as exc:
                    messages.error(request, str(exc))
        elif code_number:
            code = SemesterExamCode.objects.filter(
                code_number=code_number,
                assessment=section.assessment,
                active=True,
                diploma_enrollment__cohort=assignment.cohort,
            ).select_related("diploma_enrollment").first()
            if not code:
                messages.error(request, _("No exam code found for this assessment and cohort."))
            elif not can_reviewer_grade_code(assignment, code):
                messages.error(request, _("Exam code does not belong to your assigned cohort."))
            else:
                grade = OfflineSectionGrade.objects.filter(exam_code=code, section=section).first()

    code_number = request.POST.get("code_number", request.GET.get("code_number", ""))
    if not code and code_number:
        code = SemesterExamCode.objects.filter(
            code_number=code_number,
            assessment=section.assessment,
            active=True,
            diploma_enrollment__cohort=assignment.cohort,
        ).select_related("diploma_enrollment").first()
        if code and can_reviewer_grade_code(assignment, code):
            grade = OfflineSectionGrade.objects.filter(exam_code=code, section=section).first()

    return render(request, "assessments/reviewer_section_grading.html", {
        "assignment": assignment,
        "section": section,
        "code": code,
        "grade": grade,
        "code_number": code_number,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_reviewer_assignment_create(request):
    user_model = get_user_model()
    reviewers = user_model.objects.filter(groups__name__in=ROLE_REVIEW_STAFF).distinct()
    cohorts = Cohort.objects.all()
    semesters = Semester.objects.all()
    sections = AssessmentLectureSection.objects.select_related("lecture", "assessment").all()
    if request.method == "POST":
        reviewer_id = request.POST.get("reviewer")
        cohort_id = request.POST.get("cohort")
        semester_id = request.POST.get("semester")
        section_id = request.POST.get("section") or None
        if not all([reviewer_id, cohort_id, semester_id]):
            messages.error(request, _("Reviewer, cohort, and semester are required."))
        else:
            ReviewerAssignment.objects.create(
                reviewer_id=reviewer_id,
                cohort_id=cohort_id,
                semester_id=semester_id,
                section_id=section_id,
                assigned_by=request.user,
            )
            messages.success(request, _("Reviewer assignment created."))
            return redirect("assessments:admin_reviewer_assignment_list")
    return render(request, "assessments/admin_reviewer_assignment_form.html", {
        "reviewers": reviewers,
        "cohorts": cohorts,
        "semesters": semesters,
        "sections": sections,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_reviewer_assignment_list(request):
    assignments = ReviewerAssignment.objects.select_related(
        "reviewer", "cohort", "semester", "section", "section__lecture", "assigned_by",
    ).all()
    return render(request, "assessments/admin_reviewer_assignment_list.html", {
        "assignments": assignments,
    })


# ---------------------------------------------------------------------------
# Exceptional semester exam access (Phase 9)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_exceptional_access(request, assessment_id):
    assessment = get_object_or_404(Assessment, id=assessment_id)

    if request.method == "POST":
        enrollment_ids = request.POST.getlist("enrollments")
        open_at_str = request.POST.get("open_at", "")
        close_at_str = request.POST.get("close_at", "")
        reason = request.POST.get("reason", "").strip()
        force_online = request.POST.get("force_online") == "on"

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

        enrollments = DiplomaEnrollment.objects.filter(
            id__in=enrollment_ids,
            cohort=assessment.cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        )

        try:
            create_semester_exam_access_overrides(
                assessment, enrollments,
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
        "created_by",
    ).order_by("-created_at")

    enrollments = DiplomaEnrollment.objects.filter(
        cohort=assessment.cohort,
        status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("student__user__student_profile").order_by(
        "student__user__student_profile__full_name",
    )

    return render(request, "assessments/admin_exceptional_access.html", {
        "assessment": assessment,
        "overrides": overrides,
        "enrollments": enrollments,
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_cancel_exceptional_access(request, assessment_id, override_id):
    override = get_object_or_404(
        SemesterExamAccessOverride, id=override_id, assessment_id=assessment_id,
    )
    reason = request.POST.get("reason", "").strip()
    try:
        cancel_semester_exam_access_override(override, actor=request.user, reason=reason)
        messages.success(request, _("Override cancelled."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("assessments:admin_exceptional_access", assessment_id=assessment_id)
