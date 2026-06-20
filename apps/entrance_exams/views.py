"""Views for the Entrance Exams app — separate from normal assessments."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.applications.models import ProgramApplication
from apps.core.permissions import can_grade_attempt, is_admin, is_moderator

from .constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_IN_PROGRESS,
)
from .forms import EntranceExamForm
from .models import EntranceAttempt, EntranceExam
from .services import (
    AnswerData,
    assign_attempt,
    get_pending_grading,
    get_student_attempts,
    grade_answer,
    start_attempt,
    submit_attempt,
)


def _get_profile(user):
    from apps.students.models import StudentProfile

    try:
        return StudentProfile.objects.get(user=user)
    except StudentProfile.DoesNotExist:
        raise Http404 from None


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@login_required
def student_list(request):
    """Show the student's entrance exam attempts and available exams."""
    profile = _get_profile(request.user)
    attempts = get_student_attempts(profile)
    active_exam_ids = {a.exam_id for a in attempts}
    prog_apps = ProgramApplication.objects.filter(
        student=profile, cohort__isnull=False,
    ).select_related("cohort")
    cohort_ids = set()
    for pa in prog_apps:
        if pa.cohort_id:
            cohort_ids.add(pa.cohort_id)
    available = []
    if cohort_ids:
        now = timezone.now()
        available = list(
            EntranceExam.objects.filter(
                cohort_id__in=cohort_ids,
                is_active=True,
                open_at__lte=now,
                close_at__gte=now,
            ).exclude(
                id__in=active_exam_ids,
            ).select_related("cohort")
        )
    return render(request, "entrance_exams/student_list.html", {
        "attempts": attempts,
        "available_exams": available,
    })


@login_required
def exam_instructions(request, exam_id):
    """Show exam instructions; requires an invited program application."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, id=exam_id, is_active=True)
    prog_app = ProgramApplication.objects.filter(
        student=profile, cohort=exam.cohort,
    ).first()
    if not prog_app:
        raise PermissionDenied(_("You do not have an application for this cohort."))
    attempt = EntranceAttempt.objects.filter(
        exam=exam, program_application=prog_app,
    ).first()
    if not attempt:
        attempt = assign_attempt(exam, prog_app, profile)
    if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
        return redirect("entrance_exams:take_exam", exam_id=exam_id)
    if attempt.status in (ATTEMPT_STATUS_COMPLETED,):
        return redirect("entrance_exams:results", exam_id=exam_id)
    return render(request, "entrance_exams/exam_instructions.html", {
        "exam": exam,
        "attempt": attempt,
    })


@login_required
@require_POST
def start_exam(request, exam_id):
    """Start an entrance exam (POST only)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, id=exam_id, is_active=True)
    attempt = get_object_or_404(
        EntranceAttempt, exam=exam, student=profile,
    )
    try:
        start_attempt(attempt)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("entrance_exams:exam_instructions", exam_id=exam_id)
    return redirect("entrance_exams:take_exam", exam_id=exam_id)


@login_required
def take_exam(request, exam_id):
    """Render the exam-taking page with timer."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, id=exam_id, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    if attempt.status not in (ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_ASSIGNED):
        return redirect("entrance_exams:results", exam_id=exam_id)
    now = timezone.now()
    if attempt.started_at:
        elapsed = (now - attempt.started_at).total_seconds()
        remaining = max(0, int(exam.duration_minutes * 60 - elapsed))
    else:
        remaining = exam.duration_minutes * 60
    questions = exam.questions.prefetch_related("choices").all()
    return render(request, "entrance_exams/take_exam.html", {
        "exam": exam,
        "attempt": attempt,
        "questions": questions,
        "remaining_seconds": remaining,
    })


@login_required
@require_http_methods(["POST"])
def submit_exam(request, exam_id):
    """Submit an entrance exam (POST only)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, id=exam_id, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    answers = []
    questions = {q.id: q for q in exam.questions.all()}
    for key, value in request.POST.items():
        if not key.startswith("question_"):
            continue
        qid = int(key.removeprefix("question_"))
        question = questions.get(qid)
        if not question:
            messages.error(request, _("Unknown question."))
            return redirect("entrance_exams:take_exam", exam_id=exam_id)
        if question.question_type == "mcq":
            choice_id = int(value) if value.isdigit() else None
            answers.append(AnswerData(question_id=qid, selected_choice_id=choice_id))
        else:
            answers.append(AnswerData(question_id=qid, written_text=value))
    try:
        submit_attempt(attempt, answers)
        messages.success(request, _("Entrance exam submitted."))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("entrance_exams:take_exam", exam_id=exam_id)
    return redirect("entrance_exams:results", exam_id=exam_id)


@login_required
def exam_results(request, exam_id):
    """Show entrance exam results."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, id=exam_id)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    answers = attempt.answers.select_related("question", "selected_choice").all()
    return render(request, "entrance_exams/exam_results.html", {
        "exam": exam,
        "attempt": attempt,
        "answers": answers,
    })


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


@login_required
def admin_list(request):
    """List all entrance exams for admin."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    exams = EntranceExam.objects.all().select_related("diploma", "cohort", "created_by")
    return render(request, "entrance_exams/admin_list.html", {"exams": exams})


@login_required
def admin_detail(request, exam_id):
    """Show entrance exam detail including questions and attempts."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    exam = get_object_or_404(
        EntranceExam.objects.prefetch_related(
            "questions__choices", "attempts__student__user",
        ),
        id=exam_id,
    )
    return render(request, "entrance_exams/admin_detail.html", {"exam": exam})


@login_required
def admin_create_exam(request):
    """Create a new entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    if request.method == "POST":
        form = EntranceExamForm(request.POST)
        if form.is_valid():
            exam = form.save(commit=False)
            exam.created_by = request.user
            exam.save()
            messages.success(request, _("Entrance exam created."))
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
    else:
        form = EntranceExamForm()
    return render(request, "entrance_exams/admin_exam_form.html", {
        "form": form,
        "title": _("Create entrance exam"),
    })


@login_required
def admin_edit_exam(request, exam_id):
    """Edit an existing entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    if request.method == "POST":
        form = EntranceExamForm(request.POST, instance=exam)
        if form.is_valid():
            form.save()
            messages.success(request, _("Entrance exam updated."))
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
    else:
        form = EntranceExamForm(instance=exam)
    return render(request, "entrance_exams/admin_exam_form.html", {
        "form": form,
        "title": _("Edit entrance exam"),
        "exam": exam,
    })


@login_required
@require_POST
def admin_toggle_active(request, exam_id):
    """Toggle entrance exam active/inactive."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    exam.is_active = not exam.is_active
    exam.save(update_fields=["is_active", "updated_at"])
    status = _("activated") if exam.is_active else _("deactivated")
    messages.success(request, _("Entrance exam %(status)s.") % {"status": status})
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
def admin_grade_list(request):
    """List completed entrance attempts ready for grading."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    pending = get_pending_grading(request.user)
    return render(request, "entrance_exams/admin_grade_list.html", {
        "attempts": pending,
    })


@login_required
def admin_grade_attempt(request, attempt_id):
    """Grade a specific entrance attempt."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    attempt = get_object_or_404(
        EntranceAttempt.objects.select_related("exam", "student__user"),
        id=attempt_id,
    )
    if not can_grade_attempt(request.user, attempt):
        raise PermissionDenied
    questions = attempt.exam.questions.prefetch_related("choices").all()
    answers = {a.question_id: a for a in attempt.answers.all()}
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
                    grade_answer(answer, score, feedback, request.user)
        messages.success(request, _("Grades saved."))
        return redirect("entrance_exams:admin_grade_attempt", attempt_id=attempt_id)
    question_answer_pairs = [(q, answers.get(q.id)) for q in questions]
    return render(request, "entrance_exams/admin_grade_attempt.html", {
        "attempt": attempt,
        "question_answer_pairs": question_answer_pairs,
    })
