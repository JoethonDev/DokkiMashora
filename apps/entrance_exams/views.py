"""Views for the Entrance Exams app — separate from normal assessments."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.htmx import is_htmx

from apps.applications.constants import (
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.applications.models import (
    ApplicationEmailLog,
    EMAIL_ACTION_INVITATION,
    EMAIL_ACTION_REJECTION,
    ProgramApplication,
)
from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_REJECTION,
)
from apps.communications.services import send_templated_email
from apps.core.pagination import paginate
from apps.core.permissions import can_grade_attempt, is_admin, is_moderator

from .constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_IN_PROGRESS,
    QUESTION_TYPE_MCQ,
    RESULT_FAIL,
    RESULT_PASS,
)
from .forms import EntranceChoiceFormSet, EntranceExamForm, EntranceQuestionForm
from .models import EntranceAttempt, EntranceChoice, EntranceExam, EntranceQuestion
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
    page_obj, pagination_qs = paginate(request, exams)
    return render(request, "entrance_exams/admin_list.html", {"page_obj": page_obj, "pagination_qs": pagination_qs})


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
def admin_preview(request, exam_id):
    """Render the student-facing take page for admin preview (no attempt)."""
    if not (is_admin(request.user) or is_moderator(request.user)):
        raise PermissionDenied
    exam = get_object_or_404(
        EntranceExam.objects.prefetch_related("questions__choices"),
        id=exam_id,
    )
    questions = exam.questions.all()
    return render(
        request,
        "entrance_exams/take_exam.html",
        {
            "exam": exam,
            "is_preview": True,
            "attempt": None,
            "questions": questions,
            "remaining_seconds": 0,
        },
    )


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
    page_obj, pagination_qs = paginate(request, pending)
    return render(request, "entrance_exams/admin_grade_list.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
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


# ---------------------------------------------------------------------------
# Question builder (Phase 9)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Question builder helpers (inline HTMX)
# ---------------------------------------------------------------------------

def _question_form_context(exam, question=None, form=None, formset=None):
    if form is None:
        form = EntranceQuestionForm(instance=question)
    if formset is None:
        formset = EntranceChoiceFormSet(instance=question or EntranceQuestion())
    return {
        "exam": exam,
        "question": question,
        "form": form,
        "formset": formset,
        "title": _("Edit question") if question else _("Add question"),
    }


def _save_question_from_json(request, exam, question=None):
    data = json.loads(request.body)
    if question is None:
        question = EntranceQuestion(exam=exam)
        last = exam.questions.order_by("order").last()
        question.order = (last.order + 1) if last else 1
    question.text = data.get("text", "")
    question.question_type = data.get("question_type", QUESTION_TYPE_MCQ)
    try:
        question.points = Decimal(str(data.get("points", 0) or 0))
    except (InvalidOperation, ValueError, TypeError):
        question.points = Decimal('0')
    question.rubric = data.get("rubric", "")
    question.save()
    existing = {c.id: c for c in question.choices.all()}
    submitted_ids = set()
    for i, c in enumerate(data.get("choices", [])):
        cid = c.get("id")
        if cid and int(cid) in existing:
            choice = existing[int(cid)]
        else:
            choice = EntranceChoice(question=question)
        choice.text = c["text"]
        try:
            choice.score = Decimal(str(c.get("score", 0) or 0))
        except (InvalidOperation, ValueError, TypeError):
            choice.score = Decimal('0')
        choice.order = c.get("order", i + 1)
        choice.save()
        submitted_ids.add(choice.id)
    for old_id in set(existing.keys()) - submitted_ids:
        existing[old_id].delete()
    exam.recompute_total_points()
    return question


def _question_json(question):
    return {
        "id": question.id,
        "text": question.text,
        "question_type": question.question_type,
        "points": str(question.points or 0),
        "rubric": question.rubric,
        "choices": [
            {
                "id": c.id,
                "text": c.text,
                "score": str(c.score or 0),
                "order": c.order,
            }
            for c in question.choices.all()
        ],
    }


def _render_question_form(request, exam, question=None, form=None, formset=None):
    context = _question_form_context(exam, question=question, form=form, formset=formset)
    template = "entrance_exams/partials/_question_form.html" if is_htmx(request) else "entrance_exams/admin_question_form.html"
    return render(request, template, context)


@login_required
def admin_add_question(request, exam_id):
    """Add a new question with inline choices to an entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    if request.method == "POST":
        if request.content_type == "application/json":
            question = _save_question_from_json(request, exam)
            messages.success(request, _("Question added."))
            if is_htmx(request):
                data = _question_json(question)
                response = JsonResponse(data, status=201)
                response["HX-Trigger"] = json.dumps({"question-saved": None})
                return response
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
        form = EntranceQuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.exam = exam
            question.save()
            formset = EntranceChoiceFormSet(request.POST, instance=question)
            if formset.is_valid():
                formset.save()
                exam.recompute_total_points()
                messages.success(request, _("Question added."))
                if is_htmx(request):
                    data = _question_json(question)
                    response = JsonResponse(data, status=201)
                    response["HX-Trigger"] = json.dumps({"question-saved": None})
                    return response
                return redirect("entrance_exams:admin_detail", exam_id=exam.id)
            question.delete()
        formset = EntranceChoiceFormSet(request.POST, instance=EntranceQuestion())
    else:
        last = exam.questions.order_by("order").last()
        form = EntranceQuestionForm(initial={
            "order": (last.order + 1) if last else 1,
            "question_type": QUESTION_TYPE_MCQ,
        })
        formset = EntranceChoiceFormSet(instance=EntranceQuestion())
    return _render_question_form(request, exam, form=form, formset=formset)


@login_required
def admin_edit_question(request, exam_id, question_id):
    """Edit an existing question and its choices."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    question = get_object_or_404(EntranceQuestion, id=question_id, exam=exam)
    if request.method == "POST":
        if request.content_type == "application/json":
            _save_question_from_json(request, exam, question=question)
            messages.success(request, _("Question updated."))
            if is_htmx(request):
                data = _question_json(question)
                response = JsonResponse(data)
                response["HX-Trigger"] = json.dumps({"question-saved": None})
                return response
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
        form = EntranceQuestionForm(request.POST, instance=question)
        formset = EntranceChoiceFormSet(request.POST, instance=question)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            exam.recompute_total_points()
            messages.success(request, _("Question updated."))
            if is_htmx(request):
                data = _question_json(question)
                response = JsonResponse(data)
                response["HX-Trigger"] = json.dumps({"question-saved": None})
                return response
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
    else:
        form = EntranceQuestionForm(instance=question)
        formset = EntranceChoiceFormSet(instance=question)
    return _render_question_form(request, exam, question=question, form=form, formset=formset)


@login_required
@require_POST
def admin_delete_question(request, exam_id, question_id):
    """Delete a question."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    question = get_object_or_404(EntranceQuestion, id=question_id, exam=exam)
    question.delete()
    messages.success(request, _("Question deleted."))
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps({"question-saved": None})
        return response
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
@require_POST
def admin_bulk_send_results(request, exam_id):
    """Send pass/fail email templates to all students of an exam (V024)."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    decision = request.POST.get("decision", "")
    if decision not in (RESULT_PASS, RESULT_FAIL):
        messages.error(request, _("Invalid decision. Choose pass or fail."))
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    slug = TEMPLATE_ENTRANCE_EXAM_PASS if decision == RESULT_PASS else TEMPLATE_ENTRANCE_EXAM_FAIL
    attempts = list(
        exam.attempts.filter(result=decision).select_related("student__user")
    )
    if not attempts:
        messages.warning(
            request,
            _("No attempts with result '%(result)s' to email.") % {"result": decision},
        )
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    sent = 0
    failed = 0
    for attempt in attempts:
        email_to = attempt.student.user.email
        if not email_to:
            failed += 1
            continue
        try:
            record = send_templated_email(
                slug=slug,
                to_email=email_to,
                context={
                    "full_name": attempt.student.full_name or attempt.student.user.get_full_name() or "",
                    "exam_title": exam.title,
                },
                sent_by=request.user,
            )
        except Exception:
            failed += 1
            continue
        if record.status == "sent":
            sent += 1
        else:
            failed += 1

    record_audit_event(
        actor=request.user,
        action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam",
        target_id=str(exam.id),
        metadata={
            "decision": decision,
            "attempt_count": len(attempts),
            "sent": sent,
            "failed": failed,
        },
    )
    messages.success(
        request,
        _("Sent %(sent)d emails (%(failed)d failed) for result '%(result)s'.")
        % {"sent": sent, "failed": failed, "result": decision},
    )
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


def _eligible_applications(cohort, action):
    """Return ProgramApplications eligible for the given email action."""
    status_map = {
        EMAIL_ACTION_INVITATION: PROGRAM_APP_STATUS_INVITED,
        EMAIL_ACTION_REJECTION: PROGRAM_APP_STATUS_REJECTED,
    }
    status_filter = status_map.get(action)
    if not status_filter:
        return ProgramApplication.objects.none()

    already_sent = ApplicationEmailLog.objects.filter(
        cohort=cohort, action=action, status="sent", is_resend=False,
    ).values_list("application_id", flat=True)

    return ProgramApplication.objects.filter(
        cohort=cohort, status=status_filter,
    ).exclude(
        root_application_id__in=list(already_sent),
    ).select_related("student__user", "root_application")


@login_required
@require_http_methods(["GET", "POST"])
def admin_cohort_email(request, exam_id):
    """Bulk-send invitation or rejection emails for this exam's cohort."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam.objects.select_related("cohort__diploma"), id=exam_id)
    cohort = exam.cohort

    if request.method == "POST":
        action = request.POST.get("action")
        if action not in (EMAIL_ACTION_INVITATION, EMAIL_ACTION_REJECTION):
            messages.error(request, _("Invalid email action."))
            return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)

        eligible = _eligible_applications(cohort, action)
        if not eligible:
            messages.warning(request, _("No eligible applications for this action."))
            return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)

        slug = (
            TEMPLATE_ENTRANCE_EXAM_INVITATION
            if action == EMAIL_ACTION_INVITATION
            else TEMPLATE_REJECTION
        )
        sent_count = 0
        failed_count = 0
        for prog_app in eligible:
            email_to = prog_app.student.user.email
            if not email_to:
                failed_count += 1
                continue
            try:
                record = send_templated_email(
                    slug=slug,
                    to_email=email_to,
                    context={
                        "full_name": prog_app.student.full_name or "",
                        "exam_title": exam.title,
                        "cohort_name": cohort.name if cohort else "",
                    },
                    sent_by=request.user,
                )
            except Exception:
                failed_count += 1
                ApplicationEmailLog.objects.create(
                    application=prog_app.root_application,
                    cohort=cohort, action=action, template_slug=slug,
                    recipient=email_to, status="failed",
                    error="Exception during send",
                )
                continue

            ApplicationEmailLog.objects.create(
                application=prog_app.root_application,
                cohort=cohort, action=action, template_slug=slug,
                recipient=email_to, sent_at=record.sent_at or timezone.now(),
                sent_by=request.user, status=record.status,
            )
            if record.status == "sent":
                sent_count += 1
            else:
                failed_count += 1

        record_audit_event(
            actor=request.user, action=ACTION_BULK_EMAIL_SENT,
            target_type="entrance_exam", target_id=str(exam.id),
            metadata={
                "action": action, "eligible_count": len(eligible),
                "sent": sent_count, "failed": failed_count,
            },
        )
        messages.success(
            request,
            _("Sent %(sent)d emails (%(failed)d failed).")
            % {"sent": sent_count, "failed": failed_count},
        )
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    sent_logs = ApplicationEmailLog.objects.filter(
        cohort=cohort, status="sent",
    ).select_related("application__student", "sent_by").order_by("-sent_at")[:50]
    context = {
        "exam": exam,
        "eligible_invitation": _eligible_applications(cohort, EMAIL_ACTION_INVITATION),
        "eligible_rejection": _eligible_applications(cohort, EMAIL_ACTION_REJECTION),
        "sent_logs": sent_logs,
    }
    # ponytail: batch-lookup ProgramApplication ids for resend links
    prog_app_ids = dict(
        ProgramApplication.objects.filter(
            root_application_id__in=[l.application_id for l in sent_logs],
            cohort=cohort,
        ).values_list("root_application_id", "id")
    )
    for log in sent_logs:
        log.prog_app_id = prog_app_ids.get(log.application_id)
    
    return render(request, "entrance_exams/admin_cohort_email.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def admin_app_email_resend(request, exam_id, app_id):
    """Resend an email to a single application with confirmation."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    cohort = exam.cohort
    prog_app = get_object_or_404(
        ProgramApplication.objects.select_related("student__user", "root_application"),
        id=app_id, cohort=cohort,
    )

    if request.method == "GET":
        action = request.GET.get("action")
        if action not in (EMAIL_ACTION_INVITATION, EMAIL_ACTION_REJECTION):
            messages.error(request, _("Invalid email action."))
            return redirect("entrance_exams:admin_detail", exam_id=exam.id)
        email_to = prog_app.student.user.email
        slug = (
            TEMPLATE_ENTRANCE_EXAM_INVITATION
            if action == EMAIL_ACTION_INVITATION
            else TEMPLATE_REJECTION
        )
        return render(request, "entrance_exams/admin_email_resend_confirm.html", {
            "exam": exam,
            "prog_app": prog_app,
            "action": action,
            "slug": slug,
            "email_to": email_to or "",
        })

    action = request.POST.get("action")
    if action not in (EMAIL_ACTION_INVITATION, EMAIL_ACTION_REJECTION):
        messages.error(request, _("Invalid email action."))
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    email_to = prog_app.student.user.email
    if not email_to:
        messages.error(request, _("Student has no email address."))
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    slug = (
        TEMPLATE_ENTRANCE_EXAM_INVITATION
        if action == EMAIL_ACTION_INVITATION
        else TEMPLATE_REJECTION
    )
    try:
        record = send_templated_email(
            slug=slug, to_email=email_to,
            context={
                "full_name": prog_app.student.full_name or "",
                "exam_title": exam.title,
                "cohort_name": cohort.name if cohort else "",
            },
            sent_by=request.user,
        )
    except Exception:
        ApplicationEmailLog.objects.create(
            application=prog_app.root_application, cohort=cohort,
            action=action, template_slug=slug, recipient=email_to,
            status="failed", error="Exception during send", is_resend=True,
        )
        messages.error(request, _("Failed to resend email."))
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    ApplicationEmailLog.objects.create(
        application=prog_app.root_application, cohort=cohort,
        action=action, template_slug=slug, recipient=email_to,
        sent_at=record.sent_at or timezone.now(),
        sent_by=request.user, status=record.status, is_resend=True,
    )
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"action": "resend_" + action, "application_id": app_id, "status": record.status},
    )
    messages.success(request, _("Email resent."))
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
def admin_email_log_list(request):
    """List ApplicationEmailLog records in the custom admin UI."""
    if not is_admin(request.user):
        raise PermissionDenied
    from apps.applications.models import ApplicationEmailLog as AEL
    qs = AEL.objects.select_related("application__student", "cohort", "sent_by").all()
    page_obj, pagination_qs = paginate(request, qs, per_page=25)
    return render(request, "entrance_exams/admin_email_log_list.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
    })
