"""Views for the assessments app (student and admin)."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from django.views.decorators.http import require_POST

from apps.assessments.constants import (
    ASSESSMENT_TYPE_HOMEWORK,
    ATTEMPT_STATUS_IN_PROGRESS,
)
from apps.assessments.forms import AssessmentForm, QuestionForm, _choices_for_template
from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentChoice,
    AssessmentQuestion,
    HomeworkSubmission,
)
from apps.assessments.services import (
    AssessmentAnswerData,
    check_assessment_access,
    get_grading_queue,
    get_homework_pending_grading,
    get_student_assessments,
    get_student_attempts,
    grade_homework,
    grade_written_answer,
    has_active_enrollment,
    hide_grades,
    release_grades,
    start_assessment,
    submit_assessment,
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
    assessment_scope_filter,
    can_grade_attempt,
    is_admin,
    is_moderator,
    role_required,
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
    questions = assessment.questions.prefetch_related("choices").all()
    now = timezone.now()
    remaining = 0
    if attempt.started_at and assessment.duration_minutes:
        elapsed = (now - attempt.started_at).total_seconds()
        remaining = max(0, int(assessment.duration_minutes * 60 - elapsed))
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
    if assessment.assessment_type == ASSESSMENT_TYPE_HOMEWORK:
        messages.error(request, _("Homework assessments must be submitted through the upload form."))
        return redirect("assessments:take", assessment_id=assessment_id)
    attempt = get_object_or_404(
        AssessmentAttempt,
        assessment=assessment,
        student=request.user,
        status=ATTEMPT_STATUS_IN_PROGRESS,
    )
    answers = _parse_answers(request, assessment)
    try:
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
        qid = int(key.removeprefix("question_"))
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
@role_required(*ROLE_EXAM_STAFF)
def admin_list(request):
    q = request.GET.get("q", "")
    type_filter = request.GET.get("type", "")
    assessments = Assessment.objects.all().select_related("created_by")
    assessments = assessments.filter(assessment_scope_filter(request.user))
    if q:
        assessments = assessments.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if type_filter:
        assessments = assessments.filter(assessment_type=type_filter)
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
    ctx = {"page_obj": page_obj, "pagination_qs": pagination_qs, "search_value": q, "filters": filters}
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
    return render(
        request,
        "assessments/admin_detail.html",
        {"assessment": assessment},
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
    if scope_type == "diploma":
        data["diploma"] = request.GET.get("diploma", "")
        data["semester"] = request.GET.get("semester", "")
        data["cohort"] = request.GET.get("cohort", "")
    form = AssessmentForm(data)
    return render(request, "assessments/partials/_scope_fields.html", {"form": form})


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
        {"form": form, "title": _("Create assessment"), "next": next_url},
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
        {"form": form, "title": _("Edit assessment"), "assessment": assessment, "next": next_url},
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
        is_correct = request.POST.get(f"choice_correct_{suffix}") == "1"
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
@role_required(*ROLE_EXAM_STAFF)
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
@role_required(*ROLE_EXAM_STAFF)
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
        score = Decimal(str(request.POST.get("score", 0)))
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
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_detail(request, attempt_id):
    attempt = get_object_or_404(
        AssessmentAttempt.objects.select_related("assessment", "student"),
        id=attempt_id,
    )
    if not can_grade_attempt(request.user, attempt):
        raise PermissionDenied
    questions = attempt.assessment.questions.prefetch_related("choices").all()
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
                    score = Decimal(str(request.POST[score_key]))
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
        {"attempt": attempt, "question_answer_pairs": question_answer_pairs},
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
def admin_grade_answer(request, answer_id):
    answer = get_object_or_404(
        AssessmentAnswer.objects.select_related("attempt__assessment", "attempt__student"),
        id=answer_id,
    )
    if not can_grade_attempt(request.user, answer.attempt):
        raise PermissionDenied
    score = Decimal(str(request.POST.get("score", 0)))
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
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_grade_homework_submission(request, submission_id):
    submission = get_object_or_404(
        HomeworkSubmission.objects.select_related("assessment", "student", "attempt"),
        id=submission_id,
    )
    if not can_grade_attempt(request.user, submission):
        raise PermissionDenied
    score = Decimal(str(request.POST.get("score", 0)))
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
