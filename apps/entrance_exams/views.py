"""Views for the Entrance Exams app — separate from normal assessments."""

from __future__ import annotations

import io
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.models import Cohort
from apps.applications.constants import (
    EMAIL_ACTION_EXAM_RESULT,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.applications.models import (
    EMAIL_ACTION_INVITATION,
    EMAIL_ACTION_REJECTION,
    ProgramApplication,
)
from apps.applications.models import ProgramApplicationEmailLog as PAEL  # noqa: N817
from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_REJECTION,
)
from apps.communications.services import send_templated_email
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import is_admin
from apps.students.models import StudentProfile

from .constants import (
    ATTEMPT_STATUS_ASSIGNED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_GRADED,
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
    finalize_all_pending_for_exam,
    get_pending_grading,
    grade_answer,
    save_snapshot,
    start_attempt,
    submit_attempt,
)


def _get_profile(user):
    try:
        return StudentProfile.objects.get(user=user)
    except StudentProfile.DoesNotExist:
        raise Http404 from None


@login_required
def exam_access_token(request, token):
    """Show exam instructions via access token. Students never see integer IDs."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    prog_app = ProgramApplication.objects.filter(
        student=profile,
        cohort=exam.cohort,
        status__in=[
            PROGRAM_APP_STATUS_INVITED,
            PROGRAM_APP_STATUS_ACCEPTED,
            PROGRAM_APP_STATUS_ACTIVATED,
        ],
    ).first()
    if not prog_app:
        raise PermissionDenied(_("You do not have an application for this cohort."))
    attempt = EntranceAttempt.objects.filter(
        exam=exam, program_application=prog_app,
    ).first()
    if not attempt:
        attempt = assign_attempt(exam, prog_app, profile)
    if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
        return redirect("entrance_exams:take_exam", token=token)
    if attempt.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
        return redirect("entrance_exams:results", token=token)
    return render(request, "entrance_exams/exam_instructions.html", {
        "exam": exam,
        "attempt": attempt,
        "access_token": token,
    })


@login_required
def exam_qr_code(request, token):
    """Return a QR code PNG for the exam access URL."""
    exam_url = request.build_absolute_uri(reverse("entrance_exams:exam_access_token", args=[token]))

    img = qrcode.make(exam_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type="image/png")


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@login_required
def student_list(request):
    """Show the student's entrance exam attempts and available exams."""
    if is_admin(request.user):
        return redirect("entrance_exams:admin_list")
    return redirect("students:exam_results")


@login_required
@require_POST
def start_exam(request, token):
    """Start an entrance exam (POST only, token-based)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    attempt = get_object_or_404(
        EntranceAttempt, exam=exam, student=profile,
    )
    try:
        start_attempt(attempt)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("entrance_exams:exam_access_token", token=token)
    return redirect("entrance_exams:take_exam", token=token)


@login_required
def take_exam(request, token):
    """Render the exam-taking page with timer (token-based)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    if attempt.status not in (ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_ASSIGNED):
        return redirect("entrance_exams:results", token=token)
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
        "access_token": token,
    })


@login_required
@require_http_methods(["POST"])
def submit_exam(request, token):
    """Submit an entrance exam (POST only, token-based)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
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
            return redirect("entrance_exams:take_exam", token=token)
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
        return redirect("entrance_exams:take_exam", token=token)
    return redirect("entrance_exams:results", token=token)


@login_required
@require_POST
def save_exam_snapshot(request, token):
    """Save periodic answer snapshots for auto-recovery (AJAX, returns JSON)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile, status=ATTEMPT_STATUS_IN_PROGRESS)
    try:
        data = json.loads(request.body)
        answers = data.get("answers", {})
        for qid_str, value in answers.items():
            qid = int(qid_str)
            if isinstance(value, dict):
                choice_id = value.get("choice")
                written = value.get("text", "")
            elif isinstance(value, str):
                choice_id = int(value) if value.isdigit() else None
                written = ""
            else:
                continue
            save_snapshot(attempt, qid, choice_id=choice_id, written_text=written)
        return JsonResponse({"ok": True})
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({"ok": False, "error": _("Invalid data")}, status=400)


@login_required
def exam_results(request, token):
    """Show entrance exam results (token-based)."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token)
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
    if not is_admin(request.user):
        raise PermissionDenied
    exams = EntranceExam.objects.select_related("diploma", "cohort", "created_by")
    page_obj, pagination_qs = paginate(request, exams)
    return render(request, "entrance_exams/admin_list.html", {"page_obj": page_obj, "pagination_qs": pagination_qs})


@login_required
def admin_cohorts_for_diploma(request):
    """Return cohort <option>s filtered by diploma (HTMX)."""
    if not is_admin(request.user):
        raise PermissionDenied
    diploma_id = request.GET.get("diploma", "")
    if diploma_id:
        cohorts = Cohort.objects.filter(diploma_id=diploma_id).order_by("name")
    else:
        cohorts = Cohort.objects.none()
    return render(request, "entrance_exams/partials/_cohort_options.html", {"cohorts": cohorts})


@login_required
def admin_detail(request, exam_id):
    """Show entrance exam detail including questions and attempts."""
    if not is_admin(request.user):
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
    if not is_admin(request.user):
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
            "access_token": exam.access_token,
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
    """Toggle entrance exam active/inactive. Regenerates access token."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    exam.is_active = not exam.is_active
    exam.save(update_fields=["is_active", "updated_at"])
    if exam.is_active:
        exam.regenerate_access_token()
    status = _("activated") if exam.is_active else _("deactivated")
    messages.success(request, _("Entrance exam %(status)s.") % {"status": status})
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
@require_POST
def admin_finalize_pending(request, exam_id):
    """Finalize all in-progress attempts for an exam using saved snapshots."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    count = finalize_all_pending_for_exam(exam)
    messages.success(
        request,
        _("Finalized %(count)d pending attempt(s).") % {"count": count},
    )
    return redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
def admin_grade_list(request):
    """List completed entrance attempts ready for grading."""
    if not is_admin(request.user):
        raise PermissionDenied
    pending = get_pending_grading()
    page_obj, pagination_qs = paginate(request, pending)
    return render(request, "entrance_exams/admin_grade_list.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
    })


@login_required
def admin_grade_attempt(request, attempt_id):
    """Grade a specific entrance attempt."""
    if not is_admin(request.user):
        raise PermissionDenied
    attempt = get_object_or_404(
        EntranceAttempt.objects.select_related("exam", "student__user"),
        id=attempt_id,
    )
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
                    try:
                        score = Decimal(str(request.POST[score_key]))
                    except Exception:
                        messages.error(request, _("Invalid score value for question %(q)s.") % {"q": q.order})
                        continue
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
    return question


def _question_json(question):
    return {
        "id": question.id,
        "text": question.text,
        "question_type": question.question_type,
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
        context = {
            "full_name": attempt.student.full_name or attempt.student.user.get_full_name() or "",
            "exam_title": exam.title,
            "cohort_name": attempt.exam.cohort.name or "",
            "exam_url": reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else "",
        }
        if decision == RESULT_FAIL:
            context["rejection_reason"] = attempt.program_application.rejection_reason or ""
        try:
            record = send_templated_email(
                slug=slug,
                to_email=email_to,
                context=context,
                sent_by=request.user,
            )
        except Exception:
            failed += 1
            continue
        if record and record.status == "sent":
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


def _eligible_applications(cohort, action, exam=None, status_filter=None):
    """Return ProgramApplications eligible for the given email action."""
    if action == EMAIL_ACTION_INVITATION:
        qs = ProgramApplication.objects.filter(cohort=cohort).select_related("student__user")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status__in=[PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED])
        already_sent = PAEL.objects.filter(
            program_application__cohort=cohort, action=action, status="sent", is_resend=False,
        ).values_list("program_application_id", flat=True)
        qs = qs.exclude(id__in=list(already_sent))
        if exam:
            attempted = EntranceAttempt.objects.filter(exam=exam).values_list("program_application_id", flat=True)
            qs = qs.exclude(id__in=list(attempted))
        return qs

    status_map = {
        EMAIL_ACTION_REJECTION: PROGRAM_APP_STATUS_REJECTED,
    }
    status_filter = status_map.get(action)
    if not status_filter:
        return ProgramApplication.objects.none()

    already_sent = PAEL.objects.filter(
        program_application__cohort=cohort, action=action, status="sent", is_resend=False,
    ).values_list("program_application_id", flat=True)

    return ProgramApplication.objects.filter(
        cohort=cohort, status=status_filter,
    ).exclude(
        id__in=list(already_sent),
    ).select_related("student__user")


def _send_invitations(request, exam, cohort, student_ids):
    """Send invitation/rejection emails to selected applications."""
    apps = ProgramApplication.objects.filter(
        id__in=student_ids, cohort=cohort,
    ).select_related("student__user")
    sent_count = failed_count = 0
    for prog_app in apps:
        email_to = prog_app.student.user.email
        if not email_to:
            failed_count += 1
            continue
        is_accepted = prog_app.status == PROGRAM_APP_STATUS_ACCEPTED
        slug = TEMPLATE_ENTRANCE_EXAM_INVITATION if is_accepted else TEMPLATE_REJECTION
        action = EMAIL_ACTION_INVITATION if is_accepted else EMAIL_ACTION_REJECTION
        context = {
            "full_name": prog_app.student.full_name or "",
            "email": email_to,
        }
        if is_accepted:
            EntranceAttempt.objects.get_or_create(
                exam=exam, program_application=prog_app, student=prog_app.student,
            )
            context.update(
                phone_number=prog_app.student.phone or "",
                exam_title=exam.title,
                cohort_name=cohort.name or "",
                exam_url=reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else "",
                exam_open_at="",
                exam_close_at="",
            )
        else:
            context.update(
                rejection_reason=prog_app.rejection_reason or "",
                cohort_name=cohort.name or "",
            )
        try:
            record = send_templated_email(slug=slug, to_email=email_to, context=context, sent_by=request.user)
        except Exception:
            failed_count += 1
            continue
        PAEL.objects.get_or_create(
            program_application=prog_app,
            action=action,
            defaults={
                "template_slug": slug,
                "recipient": email_to, "sent_by": request.user,
                "sent_at": record.sent_at or timezone.now(),
                "status": record.status or "failed",
            },
        )
        if record and record.status == "sent":
            sent_count += 1
        else:
            failed_count += 1
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"action": "invitation", "count": len(student_ids), "sent": sent_count, "failed": failed_count},
    )
    msg = _("Sent %(sent)d invitations (%(failed)d failed).") % {"sent": sent_count, "failed": failed_count}
    if sent_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=invitation")


def _send_results(request, exam, student_ids):
    """Send result emails — auto-detects pass/fail from each attempt's result."""
    attempts = exam.attempts.filter(
        id__in=student_ids, status__in=[ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED],
        result__isnull=False,
    ).select_related("student__user", "program_application")
    sent_count = failed_count = 0
    pass_count = fail_count = 0
    for attempt in attempts:
        email_to = attempt.student.user.email
        if not email_to:
            failed_count += 1
            continue
        prog_app = attempt.program_application
        is_pass = attempt.result == RESULT_PASS
        slug = TEMPLATE_ENTRANCE_EXAM_PASS if is_pass else TEMPLATE_ENTRANCE_EXAM_FAIL
        if is_pass:
            pass_count += 1
        else:
            fail_count += 1
        if PAEL.objects.filter(program_application=prog_app, action=EMAIL_ACTION_EXAM_RESULT, status="sent").exists():
            sent_count += 1
            continue
        context = {
            "full_name": attempt.student.full_name or "",
            "exam_title": exam.title,
            "cohort_name": exam.cohort.name or "",
        }
        if attempt.result == RESULT_FAIL:
            context["rejection_reason"] = attempt.program_application.rejection_reason or ""
        try:
            record = send_templated_email(slug=slug, to_email=email_to, context=context, sent_by=request.user)
        except Exception:
            failed_count += 1
            continue
        if record and record.status == "sent":
            PAEL.objects.create(
                program_application=prog_app,
                action=EMAIL_ACTION_EXAM_RESULT, template_slug=slug,
                recipient=email_to, sent_by=request.user,
                sent_at=record.sent_at or timezone.now(),
                status=record.status or "sent",
            )
            sent_count += 1
        else:
            failed_count += 1
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"count": len(student_ids), "sent": sent_count, "failed": failed_count, "pass": pass_count, "fail": fail_count},
    )
    msg = _("Sent %(sent)d result emails (%(failed)d failed).") % {"sent": sent_count, "failed": failed_count}
    if sent_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=results")


def _resend_emails(request, exam, cohort, log_ids):
    """Bulk-resend selected email logs."""
    if not log_ids:
        messages.warning(request, _("No emails selected."))
        return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)
    logs = PAEL.objects.filter(
        id__in=log_ids, program_application__cohort=cohort,
    ).select_related("program_application__student")
    sent_count = failed_count = 0
    for log in logs:
        email_to = log.recipient or (log.program_application.student.user.email if log.program_application else None)
        if not email_to:
            failed_count += 1
            continue
        context = {
            "full_name": log.program_application.student.full_name or "",
            "email": email_to,
            "exam_title": exam.title,
            "cohort_name": cohort.name or "",
        }
        if log.template_slug == TEMPLATE_ENTRANCE_EXAM_INVITATION:
            context.update(
                exam_url=reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else "",
                exam_open_at="",
                exam_close_at="",
            )
        try:
            record = send_templated_email(
                slug=log.template_slug, to_email=email_to,
                context=context, sent_by=request.user,
            )
        except Exception:
            failed_count += 1
            continue
        PAEL.objects.create(
            program_application=log.program_application,
            action=log.action, template_slug=log.template_slug,
            recipient=email_to, sent_by=request.user,
            sent_at=record.sent_at or timezone.now(),
            status=record.status or "failed", is_resend=True,
        )
        if record and record.status == "sent":
            sent_count += 1
        else:
            failed_count += 1
    msg = _("Resent %(sent)d emails (%(failed)d failed).") % {"sent": sent_count, "failed": failed_count}
    if sent_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=history")


@login_required
@require_http_methods(["GET", "POST"])
def admin_cohort_email(request, exam_id):
    """3-tab email management: invitations, results, sent history."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam.objects.select_related("cohort__diploma"), id=exam_id)
    cohort = exam.cohort

    if request.method == "POST":
        tab = request.POST.get("tab")
        student_ids = request.POST.getlist("student_ids")
        log_ids = request.POST.getlist("log_ids")
        if tab == "history":
            return _resend_emails(request, exam, cohort, log_ids)
        if not student_ids:
            messages.warning(request, _("No students selected."))
            return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)
        if tab == "invitation":
            return _send_invitations(request, exam, cohort, student_ids)
        elif tab == "results":
            return _send_results(request, exam, student_ids)
        messages.error(request, _("Invalid tab."))
        return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)

    tab = request.GET.get("tab", "invitation")
    result_filter = request.GET.get("result", "")
    iq = request.GET.get("iq", "")
    rq = request.GET.get("rq", "")
    hq = request.GET.get("hq", "")
    ha = request.GET.get("ha", "")
    inv_status = request.GET.get("inv_status", "")

    invitation_qs = _eligible_applications(cohort, EMAIL_ACTION_INVITATION, exam=exam, status_filter=inv_status or None)
    if tab == "invitation" and iq:
        invitation_qs = invitation_qs.filter(
            Q(student__full_name__icontains=iq) | Q(student__user__email__icontains=iq)
        )

    results_qs = exam.attempts.filter(
        status__in=[ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED],
        result__isnull=False,
    ).select_related("student__user", "program_application")
    if tab == "results" and rq:
        results_qs = results_qs.filter(
            Q(student__user__email__icontains=rq) | Q(student__full_name__icontains=rq)
        )
    if result_filter in (RESULT_PASS, RESULT_FAIL):
        results_qs = results_qs.filter(result=result_filter)

    history_qs = PAEL.objects.filter(program_application__cohort=cohort).select_related("program_application__student", "sent_by").order_by("-sent_at")
    if tab == "history" and hq:
        history_qs = history_qs.filter(
            Q(program_application__student__full_name__icontains=hq) | Q(recipient__icontains=hq)
        )
    if ha:
        history_qs = history_qs.filter(action=ha)

    inv_page, _unused = paginate(request, invitation_qs, per_page=20)
    res_page, _unused = paginate(request, results_qs, per_page=20)
    hist_page, _unused = paginate(request, history_qs, per_page=25)

    def _make_pq(**extra):
        return urlencode({"tab": tab, **extra})

    return render(request, "entrance_exams/admin_cohort_email.html", {
        "exam": exam,
        "tab": tab,
        "result_filter": result_filter,
        "iq": iq,
        "rq": rq,
        "hq": hq,
        "ha": ha,
        "inv_status": inv_status,
        "inv_pagination_qs": _make_pq(**({"iq": iq} if iq else {}), **({"inv_status": inv_status} if inv_status else {})),
        "res_pagination_qs": _make_pq(**({"rq": rq} if rq else {}), **({"result": result_filter} if result_filter else {})),
        "hist_pagination_qs": _make_pq(**({"hq": hq} if hq else {}), **({"ha": ha} if ha else {})),
        "invitation_page": inv_page,
        "results_page": res_page,
        "history_page": hist_page,
    })


@login_required
@require_http_methods(["GET", "POST"])
def admin_app_email_resend(request, exam_id, app_id):
    """Resend an email to a single application with confirmation."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    cohort = exam.cohort
    prog_app = get_object_or_404(
        ProgramApplication.objects.select_related("student__user"),
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
    context = {
        "full_name": prog_app.student.full_name or "",
        "email": email_to,
        "phone_number": prog_app.student.phone or "",
    }
    if slug == TEMPLATE_ENTRANCE_EXAM_INVITATION:
        context.update(
            exam_title=exam.title,
            cohort_name=cohort.name or "",
            exam_url=reverse("entrance_exams:exam_access_token", args=[exam.access_token]) if exam.access_token else "",
            exam_open_at="",
            exam_close_at="",
        )
    else:
        context.update(
            rejection_reason=prog_app.rejection_reason or "",
            cohort_name=cohort.name or "",
        )
    try:
        record = send_templated_email(
            slug=slug, to_email=email_to,
            context=context,
            sent_by=request.user,
        )
    except Exception:
        PAEL.objects.create(
            program_application=prog_app,
            action=action, template_slug=slug, recipient=email_to,
            status="failed", error="Exception during send", is_resend=True,
        )
        messages.error(request, _("Failed to resend email."))
        return redirect("entrance_exams:admin_detail", exam_id=exam.id)

    PAEL.objects.create(
        program_application=prog_app,
        action=action, template_slug=slug, recipient=email_to,
        sent_at=record.sent_at or timezone.now() if record else None,
        sent_by=request.user, status=record.status if record else "failed", is_resend=True,
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
    qs = PAEL.objects.select_related("program_application__student", "sent_by").all()
    action = request.GET.get("action", "")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    if action:
        qs = qs.filter(action=action)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(
            Q(recipient__icontains=search)
            | Q(program_application__student__full_name__icontains=search)
            | Q(program_application__student__user__email__icontains=search)
        )
    qs = qs.order_by("-sent_at", "-id")
    page_obj, pagination_qs = paginate(request, qs, per_page=25)
    return render(request, "entrance_exams/admin_email_log_list.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "action_choices": PAEL._meta.get_field("action").choices,
        "status_choices": PAEL._meta.get_field("status").choices,
        "current_action": action,
        "current_status": status,
        "search_value": search,
    })
