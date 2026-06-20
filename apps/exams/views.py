"""Views for assessment exams — separate from entrance exams."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.audit.constants import (
    ACTION_EXAM_CREATED,
    ACTION_EXAM_TOGGLED,
    ACTION_EXAM_UPDATED,
    ACTION_GRADE_CHANGED,
    ACTION_QUESTION_CREATED,
    ACTION_QUESTION_DELETED,
    ACTION_QUESTION_UPDATED,
)
from apps.audit.services import record_audit_event
from apps.core.permissions import ROLE_EXAM_STAFF, role_required
from apps.exams.constants import (
    ASSIGNMENT_STATUS_COMPLETED,
    ASSIGNMENT_STATUS_GRADED,
    ASSIGNMENT_STATUS_IN_PROGRESS,
    QUESTION_TYPE_ESSAY,
    QUESTION_TYPE_MCQ,
)
from apps.exams.forms import ExamForm, QuestionForm
from apps.exams.models import Assignment, Choice, Exam, Question
from apps.exams.services import (
    ExamAnswerData,
    get_active_exams,
    get_exam_with_questions,
    get_pending_grading,
    get_student_assignments,
    grade_essay_answer,
    start_exam,
    submit_exam,
)


@login_required
def student_list(request):
    assignments = get_student_assignments(request.user)
    available_exams = get_active_exams().exclude(
        assignments__student=request.user,
    )
    return render(
        request,
        "exams/student_list.html",
        {
            "assignments": assignments,
            "available_exams": available_exams,
        },
    )


@login_required
def exam_instructions(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = Assignment.objects.filter(
        exam=exam,
        student=request.user,
    ).first()
    if not assignment:
        return redirect("exams:student_list")
    if assignment.status == ASSIGNMENT_STATUS_IN_PROGRESS:
        return redirect("exams:take_exam", exam_id=exam_id)
    if assignment.status in (ASSIGNMENT_STATUS_COMPLETED, ASSIGNMENT_STATUS_GRADED):
        return redirect("exams:results", exam_id=exam_id)
    return render(
        request,
        "exams/exam_instructions.html",
        {
            "exam": exam,
            "assignment": assignment,
        },
    )


@login_required
@require_POST
def start_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    start_exam(assignment)
    return redirect("exams:take_exam", exam_id=exam_id)


@login_required
def take_exam(request, exam_id):
    exam = get_exam_with_questions(exam_id)
    if not exam or not exam.is_active:
        raise Http404
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    if assignment.status in (ASSIGNMENT_STATUS_COMPLETED, ASSIGNMENT_STATUS_GRADED):
        return redirect("exams:results", exam_id=exam_id)
    now = timezone.now()
    if assignment.started_at:
        elapsed = (now - assignment.started_at).total_seconds()
        remaining = max(0, int(exam.duration_minutes * 60 - elapsed))
    else:
        remaining = exam.duration_minutes * 60
    questions = exam.questions.prefetch_related("choices").all()
    return render(
        request,
        "exams/take_exam.html",
        {
            "exam": exam,
            "assignment": assignment,
            "questions": questions,
            "remaining_seconds": remaining,
        },
    )


@login_required
def submit_exam_view(request, exam_id):
    if request.method != "POST":
        return redirect("exams:take_exam", exam_id=exam_id)
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    _submit_assignment(request, assignment)
    return redirect("exams:results", exam_id=exam_id)


@login_required
def exam_results(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    return _render_results(request, exam, assignment)


def _submit_assignment(request, assignment):
    """Parse POST answers and submit the assignment."""
    answers = []
    questions = {q.id: q for q in assignment.exam.questions.prefetch_related("choices").all()}
    for key, value in request.POST.items():
        if not key.startswith("question_"):
            continue
        qid = int(key.removeprefix("question_"))
        question = questions.get(qid)
        if not question:
            messages.error(request, _("Unknown question."))
            return
        if question.question_type == QUESTION_TYPE_MCQ:
            choice_id = int(value) if value.isdigit() else None
            if choice_id:
                valid_choice = question.choices.filter(id=choice_id).exists()
                if not valid_choice:
                    messages.error(request, _("Invalid choice for question %(id)s.") % {"id": qid})
                    return
            answers.append(
                ExamAnswerData(question_id=qid, selected_choice_id=choice_id)
            )
        elif question.question_type == QUESTION_TYPE_ESSAY:
            answers.append(
                ExamAnswerData(question_id=qid, essay_text=value)
            )
    try:
        submit_exam(assignment, answers)
        messages.success(request, _("Exam submitted successfully."))
    except ValueError as exc:
        messages.error(request, str(exc))


def _render_results(request, exam, assignment):
    """Render the exam results page."""
    answers = assignment.answers.select_related(
        "question",
        "selected_choice",
    ).all()
    return render(request, "exams/exam_results.html", {
        "exam": exam,
        "assignment": assignment,
        "answers": answers,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_list(request):
    exams = Exam.objects.all().select_related("created_by")
    return render(request, "exams/admin_list.html", {"exams": exams})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_detail(request, exam_id):
    exam = get_object_or_404(
        Exam.objects.prefetch_related("questions__choices", "assignments__student"),
        id=exam_id,
    )
    return render(
        request,
        "exams/admin_detail.html",
        {"exam": exam},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_create_exam(request):
    if request.method == "POST":
        form = ExamForm(request.POST)
        if form.is_valid():
            exam = form.save(commit=False)
            exam.created_by = request.user
            exam.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_CREATED,
                target_type="exam",
                target_id=str(exam.id),
                metadata={"title": exam.title},
            )
            messages.success(request, _("Exam created."))
            return redirect("exams:admin_detail", exam_id=exam.id)
    else:
        form = ExamForm()
    return render(
        request,
        "exams/admin_exam_form.html",
        {
            "form": form,
            "title": _("Create exam"),
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_edit_exam(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if request.method == "POST":
        form = ExamForm(request.POST, instance=exam)
        if form.is_valid():
            form.save()
            record_audit_event(
                actor=request.user,
                action=ACTION_EXAM_UPDATED,
                target_type="exam",
                target_id=str(exam.id),
                metadata={"title": exam.title},
            )
            messages.success(request, _("Exam updated."))
            return redirect("exams:admin_detail", exam_id=exam.id)
    else:
        form = ExamForm(instance=exam)
    return render(
        request,
        "exams/admin_exam_form.html",
        {
            "form": form,
            "title": _("Edit exam"),
            "exam": exam,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_toggle_active(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    exam.is_active = not exam.is_active
    exam.save(update_fields=["is_active", "updated_at"])
    record_audit_event(
        actor=request.user,
        action=ACTION_EXAM_TOGGLED,
        target_type="exam",
        target_id=str(exam.id),
        metadata={"is_active": exam.is_active, "title": exam.title},
    )
    status = _("activated") if exam.is_active else _("deactivated")
    messages.success(request, _("Exam %(status)s.") % {"status": status})
    return redirect("exams:admin_detail", exam_id=exam.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_list(request):
    pending = get_pending_grading()
    return render(request, "exams/admin_grade_list.html", {"assignments": pending})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_assignment(request, assignment_id):
    assignment = get_object_or_404(
        Assignment.objects.select_related("exam", "student"),
        id=assignment_id,
    )
    questions = assignment.exam.questions.prefetch_related("choices").all()
    answers = {
        a.question_id: a
        for a in assignment.answers.select_related("question", "selected_choice").all()
    }
    if request.method == "POST":
        with transaction.atomic():
            for q in questions:
                answer = answers.get(q.id)
                if not answer:
                    continue
                score_key = f"score_{q.id}"
                feedback_key = f"feedback_{q.id}"
                if score_key in request.POST:
                    score = Decimal(str(request.POST[score_key]))
                    feedback = request.POST.get(feedback_key, "")
                    grade_essay_answer(answer, score, feedback, request.user)
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADE_CHANGED,
            target_type="assignment",
            target_id=str(assignment_id),
            metadata={"exam_id": assignment.exam_id, "student_id": assignment.student_id},
        )
        messages.success(request, _("Grades saved."))
        return redirect("exams:admin_grade_assignment", assignment_id=assignment_id)
    question_answer_pairs = [(q, answers.get(q.id)) for q in questions]
    return render(
        request,
        "exams/admin_grade_assignment.html",
        {
            "assignment": assignment,
            "question_answer_pairs": question_answer_pairs,
        },
    )


# ---------------------------------------------------------------------------
# Inline question / choice management via HTMX
# ---------------------------------------------------------------------------


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _htmx_redirect(response, target):
    response["HX-Redirect"] = target
    return response


def _question_form_context(exam, question=None, form=None, choices=None):
    if form is None:
        form = QuestionForm(instance=question)
    if choices is None:
        choices = list(question.choices.all()) if question else []
    return {
        "exam": exam,
        "question": question,
        "form": form,
        "choices": choices,
        "question_type_mcq": QUESTION_TYPE_MCQ,
        "full_page": False,
        "question_save_url": reverse(
            "exams:admin_question_save",
            args=[exam.id] + ([question.id] if question else []),
        ),
    }


def _render_question_form(request, exam, question=None, form=None, choices=None):
    context = _question_form_context(exam, question=question, form=form, choices=choices)
    context["full_page"] = not _is_htmx(request)
    template = "exams/partials/question_form.html" if _is_htmx(request) else "exams/admin_question_form.html"
    return render(request, template, context)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_question_form(request, exam_id, question_id=None):
    """Return an inline question form (with choices) for HTMX."""
    exam = get_object_or_404(Exam, id=exam_id)
    question = get_object_or_404(Question, id=question_id, exam=exam) if question_id else None
    form = QuestionForm(instance=question)
    return _render_question_form(request, exam, question=question, form=form)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_question_save(request, exam_id, question_id=None):
    """Create or update a question and its choices."""
    exam = get_object_or_404(Exam, id=exam_id)
    question = get_object_or_404(Question, id=question_id, exam=exam) if question_id else None
    form = QuestionForm(request.POST, instance=question)
    parsed_choices = _parse_inline_choices(request)
    if form.is_valid():
        if form.cleaned_data["question_type"] == QUESTION_TYPE_MCQ:
            error = _validate_mcq_choices(parsed_choices)
            if error:
                form.add_error(None, error)
        if not form.errors:
            question = form.save(commit=False)
            question.exam = exam
            if not question_id:
                question.order = exam.questions.count() + 1
            question.save()
            if question.question_type == QUESTION_TYPE_MCQ:
                _save_choices_from_parsed(question, parsed_choices)
            elif question.question_type == QUESTION_TYPE_ESSAY:
                Choice.objects.filter(question=question).delete()
            record_audit_event(
                actor=request.user,
                action=ACTION_QUESTION_CREATED if not question_id else ACTION_QUESTION_UPDATED,
                target_type="question",
                target_id=str(question.id),
                metadata={"exam_id": exam_id, "question_type": question.question_type},
            )
            messages.success(request, _("Question saved."))
            if _is_htmx(request):
                response = HttpResponse()
                return _htmx_redirect(response, reverse("exams:admin_detail", args=[exam.id]))
            return redirect("exams:admin_detail", exam_id=exam.id)

    choices = _choices_for_template(parsed_choices, question)
    return _render_question_form(request, exam, question=question, form=form, choices=choices)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_question_delete(request, exam_id, question_id):
    """Delete a question."""
    question = get_object_or_404(Question, id=question_id, exam_id=exam_id)
    record_audit_event(
        actor=request.user,
        action=ACTION_QUESTION_DELETED,
        target_type="question",
        target_id=str(question_id),
        metadata={"exam_id": exam_id, "question_type": getattr(question, "question_type", "")},
    )
    question.delete()
    messages.success(request, _("Question deleted."))
    if _is_htmx(request):
        response = HttpResponse()
        return _htmx_redirect(response, reverse("exams:admin_detail", args=[exam_id]))
    return redirect("exams:admin_detail", exam_id=exam_id)


def _parse_inline_choices(request):
    """Return a list of choice dicts parsed from POST data."""
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
        choices.append(
            {
                "suffix": suffix,
                "text": text,
                "is_correct": is_correct,
                "order": order,
            }
        )
    return sorted(choices, key=lambda c: c["order"])


def _validate_mcq_choices(choices):
    """Return a translated error string if MCQ choices are invalid."""
    if len(choices) < 2:
        return _("MCQ questions must have at least two choices.")
    correct_count = sum(1 for choice in choices if choice["is_correct"])
    if correct_count == 0:
        return _("Please mark exactly one choice as correct.")
    if correct_count > 1:
        return _("Only one choice can be marked as correct.")
    return ""


def _save_choices_from_parsed(question, parsed_choices):
    """Persist parsed choices and remove any stale ones."""
    non_new_ids = []
    for _idx, choice_data in enumerate(parsed_choices, start=1):
        suffix = choice_data["suffix"]
        if not suffix.startswith("new_"):
            try:
                non_new_ids.append(int(suffix))
            except (TypeError, ValueError):
                pass
    if non_new_ids:
        current_ids = set(Choice.objects.filter(question=question).values_list("id", flat=True))
        stale_ids = current_ids - set(non_new_ids)
        Choice.objects.filter(question=question, id__in=stale_ids).delete()
    else:
        Choice.objects.filter(question=question).delete()
    for index, choice_data in enumerate(parsed_choices, start=1):
        suffix = choice_data["suffix"]
        text = choice_data["text"]
        is_correct = choice_data["is_correct"]
        order = choice_data["order"] or index
        if suffix.startswith("new_"):
            Choice.objects.create(
                question=question,
                text=text,
                is_correct=is_correct,
                order=order,
            )
        else:
            try:
                choice_id = int(suffix)
            except (TypeError, ValueError):
                continue
            Choice.objects.filter(id=choice_id, question=question).update(
                text=text,
                is_correct=is_correct,
                order=order,
            )


def _choices_for_template(parsed_choices, question):
    """Return choice-like objects for re-rendering the form after a validation error."""
    if not parsed_choices and question:
        return list(question.choices.all())
    return [
        SimpleNamespace(
            id=choice_data["suffix"],
            text=choice_data["text"],
            is_correct=choice_data["is_correct"],
            order=choice_data["order"] or index,
        )
        for index, choice_data in enumerate(parsed_choices, start=1)
    ]
