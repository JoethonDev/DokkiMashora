import io
import re
from decimal import Decimal
from types import SimpleNamespace

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.permissions import ROLE_EXAM_STAFF, role_required
from apps.exams.constants import (
    ASSIGNMENT_STATUS_ASSIGNED,
    ASSIGNMENT_STATUS_COMPLETED,
    ASSIGNMENT_STATUS_GRADED,
    ASSIGNMENT_STATUS_IN_PROGRESS,
    EXAM_TYPE_ASSESSMENT,
    EXAM_TYPE_ENTRANCE,
    QUESTION_TYPE_MCQ,
)
from apps.exams.forms import ExamForm, QuestionForm
from apps.exams.gemini import GeminiClient
from apps.exams.models import Answer, Assignment, Choice, Exam, Question
from apps.exams.services import (
    ExamAnswerData,
    get_active_exams,
    get_exam_with_questions,
    get_exam_with_questions_by_token,
    get_pending_grading,
    get_student_assignments,
    grade_essay_answer,
    start_exam,
    submit_exam,
)


@login_required
def student_list(request):
    assignments = get_student_assignments(request.user)
    available_exams = get_active_exams().filter(
        exam_type=EXAM_TYPE_ASSESSMENT,
    ).exclude(
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


def _get_or_create_assignment(exam, student):
    """Return the student's assignment for an exam, creating it if needed."""
    assignment = Assignment.objects.filter(exam=exam, student=student).first()
    if not assignment:
        assignment = Assignment.objects.create(
            exam=exam,
            student=student,
            status=ASSIGNMENT_STATUS_ASSIGNED,
        )
    return assignment


def _forbid_entrance_exam_by_id(exam):
    """Entrance exams must be accessed via token, never by exam id."""
    if exam.exam_type == EXAM_TYPE_ENTRANCE:
        raise PermissionDenied(
            _("Entrance exams must be accessed through the access link.")
        )


def _redirect_for_assignment_status(assignment, token):
    """Send the student to the right step of the token-based flow."""
    if assignment.status in (ASSIGNMENT_STATUS_COMPLETED, ASSIGNMENT_STATUS_GRADED):
        return redirect("exams:exam_results_token", token=token)
    if assignment.status == ASSIGNMENT_STATUS_IN_PROGRESS:
        return redirect("exams:take_exam_token", token=token)
    return redirect("exams:exam_instructions_token", token=token)


@login_required
def exam_instructions(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    _forbid_entrance_exam_by_id(exam)
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
def start_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    _forbid_entrance_exam_by_id(exam)
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
    _forbid_entrance_exam_by_id(exam)
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
    _forbid_entrance_exam_by_id(exam)
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
    _forbid_entrance_exam_by_id(exam)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    return _render_results(request, exam, assignment)


@login_required
def exam_instructions_token(request, token):
    exam = get_object_or_404(Exam, access_token=token, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    if assignment.status == ASSIGNMENT_STATUS_IN_PROGRESS:
        return redirect("exams:take_exam_token", token=token)
    if assignment.status in (ASSIGNMENT_STATUS_COMPLETED, ASSIGNMENT_STATUS_GRADED):
        return redirect("exams:exam_results_token", token=token)
    return render(
        request,
        "exams/exam_instructions.html",
        {
            "exam": exam,
            "assignment": assignment,
            "token": token,
        },
    )


@login_required
def start_exam_token(request, token):
    exam = get_object_or_404(Exam, access_token=token, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    if exam.exam_type == EXAM_TYPE_ENTRANCE and assignment.status in (
        ASSIGNMENT_STATUS_COMPLETED,
        ASSIGNMENT_STATUS_GRADED,
    ):
        return redirect("exams:exam_results_token", token=token)
    start_exam(assignment)
    return redirect("exams:take_exam_token", token=token)


@login_required
def take_exam_token(request, token):
    exam = get_exam_with_questions_by_token(token)
    if not exam or not exam.is_active:
        raise Http404
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    if assignment.status in (ASSIGNMENT_STATUS_COMPLETED, ASSIGNMENT_STATUS_GRADED):
        return redirect("exams:exam_results_token", token=token)
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
            "token": token,
        },
    )


@login_required
def submit_exam_token(request, token):
    if request.method != "POST":
        return redirect("exams:take_exam_token", token=token)
    exam = get_object_or_404(Exam, access_token=token, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    _submit_assignment(request, assignment)
    return redirect("exams:exam_results_token", token=token)


@login_required
def exam_results_token(request, token):
    exam = get_object_or_404(Exam, access_token=token)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    return _render_results(request, exam, assignment, token=token)


def _submit_assignment(request, assignment):
    """Parse POST answers and submit the assignment."""
    answers = []
    for key, value in request.POST.items():
        if key.startswith("question_"):
            qid = int(key.removeprefix("question_"))
            answers.append(
                ExamAnswerData(
                    question_id=qid,
                    selected_choice_id=int(value) if value.isdigit() else None,
                    essay_text=value if not value.isdigit() else "",
                )
            )
    submit_exam(assignment, answers)
    messages.success(request, _("Exam submitted successfully."))


def _render_results(request, exam, assignment, token=None):
    """Render the exam results page."""
    answers = assignment.answers.select_related(
        "question",
        "selected_choice",
    ).all()
    context = {
        "exam": exam,
        "assignment": assignment,
        "answers": answers,
    }
    if token:
        context["token"] = token
    return render(request, "exams/exam_results.html", context)


def exam_access(request, token):
    exam = get_object_or_404(Exam, access_token=token, is_active=True)
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    assignment = _get_or_create_assignment(exam, request.user)
    if exam.exam_type == EXAM_TYPE_ENTRANCE and assignment.status in (
        ASSIGNMENT_STATUS_COMPLETED,
        ASSIGNMENT_STATUS_GRADED,
    ):
        messages.info(request, _("You have already completed this entrance exam."))
    return _redirect_for_assignment_status(assignment, token)


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
    access_url = request.build_absolute_uri(
        reverse("exams:exam_access", args=[exam.access_token])
    )
    qr_svg = _generate_qr_svg(access_url)
    return render(
        request,
        "exams/admin_detail.html",
        {
            "exam": exam,
            "access_url": access_url,
            "qr_svg": qr_svg,
        },
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
def admin_toggle_active(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    if not exam.is_active and exam.exam_type == EXAM_TYPE_ENTRANCE:
        exam.regenerate_access_token()
    exam.is_active = not exam.is_active
    exam.save(update_fields=["is_active", "updated_at"])
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


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_with_gemini(request, answer_id):
    answer = get_object_or_404(
        Answer.objects.select_related("question", "assignment__exam"),
        id=answer_id,
    )
    if not answer.essay_text:
        messages.error(request, _("No essay text to grade."))
        return redirect(
            "exams:admin_grade_assignment", assignment_id=answer.assignment_id
        )
    client = GeminiClient()
    if not client.is_available():
        messages.error(request, _("Gemini API is not configured."))
        return redirect(
            "exams:admin_grade_assignment", assignment_id=answer.assignment_id
        )
    result = client.grade_essay(
        question_text=answer.question.text,
        max_points=answer.question.points,
        student_answer=answer.essay_text,
    )
    if result.get("error"):
        messages.error(request, result.get("feedback", _("Grading failed.")))
    else:
        grade_essay_answer(answer, result["score"], result["feedback"], request.user)
        messages.success(
            request,
            _("Gemini grade: %(score)s out of %(max)s")
            % {
                "score": result["score"],
                "max": answer.question.points,
            },
        )
    return redirect("exams:admin_grade_assignment", assignment_id=answer.assignment_id)


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
            messages.success(request, _("Question saved."))
            if _is_htmx(request):
                response = HttpResponse()
                return _htmx_redirect(response, reverse("exams:admin_detail", args=[exam.id]))
            return redirect("exams:admin_detail", exam_id=exam.id)

    choices = _choices_for_template(parsed_choices, question)
    return _render_question_form(request, exam, question=question, form=form, choices=choices)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_question_delete(request, exam_id, question_id):
    """Delete a question."""
    question = get_object_or_404(Question, id=question_id, exam_id=exam_id)
    if request.method == "POST":
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
    if not choices:
        return _("MCQ questions must have at least one choice.")
    correct_count = sum(1 for choice in choices if choice["is_correct"])
    if correct_count == 0:
        return _("Please mark exactly one choice as correct.")
    if correct_count > 1:
        return _("Only one choice can be marked as correct.")
    return ""


def _save_choices_from_parsed(question, parsed_choices):
    """Persist parsed choices and remove any stale ones."""
    seen_ids = set()
    for index, choice_data in enumerate(parsed_choices, start=1):
        suffix = choice_data["suffix"]
        text = choice_data["text"]
        is_correct = choice_data["is_correct"]
        order = choice_data["order"] or index
        if suffix.startswith("new_"):
            choice = Choice.objects.create(
                question=question,
                text=text,
                is_correct=is_correct,
                order=order,
            )
            seen_ids.add(choice.id)
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
            seen_ids.add(choice_id)
    Choice.objects.filter(question=question).exclude(id__in=seen_ids).delete()


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


def _generate_qr_svg(url: str) -> str:
    try:
        factory = qrcode.image.svg.SvgPathFillImage
        img = qrcode.make(url, image_factory=factory, box_size=10)
        buf = io.BytesIO()
        img.save(buf)
        svg = buf.getvalue().decode("utf-8")
        svg = re.sub(r"<\?xml[^?]*\?>", "", svg)
        svg = re.sub(r'width="[^"]*"', 'width="100%"', svg, count=1)
        svg = re.sub(r'height="[^"]*"', 'height="100%"', svg, count=1)
        return svg.strip()
    except Exception:
        return ""
