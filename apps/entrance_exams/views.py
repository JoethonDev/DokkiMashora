"""Views for the Entrance Exams app — separate from normal assessments."""

from __future__ import annotations

import io
import json
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import qrcode

logger = logging.getLogger(__name__)
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
from django.forms.models import inlineformset_factory

from apps.academics.models import Cohort
from apps.applications.constants import (
    EMAIL_ACTION_EXAM_RESULT,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_REJECTED,
)
from apps.applications.models import (
    EMAIL_ACTION_INVITATION,
    EMAIL_ACTION_REJECTION,
    ProgramApplication,
)
from apps.applications.models import ProgramApplicationEmailLog as PAEL  # noqa: N817
from apps.applications.services import change_entrance_decision
from apps.audit.constants import ACTION_BULK_EMAIL_SENT
from apps.audit.services import record_audit_event
from apps.communications.constants import (
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_REJECTION,
)
from apps.communications.services import (
    queue_program_application_email,
    record_program_application_email_log,
)
from apps.core.htmx import hx_toast, is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_EXAM_STAFF, is_admin, role_required, safe_int, student_required
from apps.core.views import safe_next_url
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
from .forms import EntranceChoiceForm, EntranceChoiceFormSet, EntranceExamForm, EntranceQuestionForm
from .models import EntranceAttempt, EntranceChoice, EntranceExam, EntranceQuestion, EntranceSnapshot
from .services import (
    AnswerData,
    assign_attempt,
    attempt_deadline,
    attempt_decision,
    finalize_all_pending_for_exam,
    finalize_expired_attempt,
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


@student_required
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
            PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
        ],
    ).first()
    if not prog_app:
        raise PermissionDenied(_("You do not have an application for this cohort."))
    attempt = EntranceAttempt.objects.filter(
        exam=exam, program_application=prog_app,
    ).first()
    if not attempt:
        attempt = assign_attempt(exam, prog_app, profile)
    # Reopening the issued link after submission must resolve the completed
    # state and route to results; the attempt is never re-taken (E3).
    # ``submitted_at`` is the durable "has taken the exam" marker: even if a
    # stale row still carries an in-progress status, a submitted attempt must
    # never grant exam access again.
    if attempt.submitted_at or attempt.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED):
        return redirect("entrance_exams:results", token=token)
    if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
        return redirect("entrance_exams:take_exam", token=token)
    now = timezone.now()
    if exam.open_at and now < exam.open_at:
        exam_readiness = "scheduled"
    elif exam.close_at and now > exam.close_at:
        exam_readiness = "closed"
    else:
        exam_readiness = "ready"
    return render(request, "ui/pages/student/exam.html", {
        "exam": exam,
        "attempt": attempt,
        "access_token": token,
        "exam_readiness": exam_readiness,
        "question_count": exam.questions.count(),
    })


@student_required
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


@student_required
def student_list(request):
    """Show the student's entrance exam attempts and available exams."""
    if is_admin(request.user):
        return redirect("entrance_exams:admin_list")
    return redirect("students:exam_results")


@student_required
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


@student_required
def take_exam(request, token):
    """Render the exam-taking page with a local countdown (token-based).

    The server provides the authoritative deadline; the browser counts down
    locally and never polls. The latest backend snapshot answers are hydrated
    server-side so the page can restore work without localStorage.
    """
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    # A submitted attempt is final: never render the exam page again, even if
    # a stale row still carries an in-progress status.
    if attempt.submitted_at or attempt.status not in (ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_ASSIGNED):
        return redirect("entrance_exams:results", token=token)
    now = timezone.now()
    deadline = attempt_deadline(attempt)
    remaining = max(0, int((deadline - now).total_seconds())) if deadline else exam.duration_minutes * 60
    questions = exam.questions.prefetch_related("choices").all()
    snapshot_answers = {
        snap.question_id: snap
        for snap in EntranceSnapshot.objects.filter(attempt=attempt)
    }
    return render(request, "ui/pages/student/exam.html", {
        "exam": exam,
        "attempt": attempt,
        "questions": questions,
        "remaining_seconds": remaining,
        "timer_deadline": deadline,
        "timer_server_now": now,
        "exam_version": exam.pk,
        "snapshot_answers": snapshot_answers,
        "access_token": token,
        "student_id": request.user.pk,
    })


@student_required
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
        qid = safe_int(key.removeprefix("question_"))
        if qid is None:
            messages.error(request, _("Invalid question."))
            return redirect("entrance_exams:take_exam", token=token)
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


@student_required
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


@student_required
@require_POST
def finalize_expired_exam(request, token):
    """Idempotently finalize an attempt when the client countdown reaches zero.

    POST-only (CSRF-protected), safe to call repeatedly: a terminal attempt
    returns its current state; a non-terminal attempt is finalized from the
    latest backend snapshots only after the server independently confirms the
    deadline has passed. localStorage is never trusted here.
    """
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token, is_active=True)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    try:
        finalized = finalize_expired_attempt(attempt)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({
        "ok": True,
        "final": finalized.status in (ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED),
        "redirect": reverse("entrance_exams:results", args=[token]),
    })


@student_required
def exam_results(request, token):
    """Show entrance exam status (token-based). Grades are never exposed."""
    profile = _get_profile(request.user)
    exam = get_object_or_404(EntranceExam, access_token=token)
    attempt = get_object_or_404(EntranceAttempt, exam=exam, student=profile)
    decision = attempt_decision(attempt)
    state_label = decision["label"] if decision else _("Submitted — awaiting review")
    return render(request, "ui/pages/student/exam.html", {
        "exam": exam,
        "attempt": attempt,
        "access_token": token,
        "is_results": True,
        "decision": decision,
        "state_label": state_label,
    })


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_list(request):
    """List all entrance exams for admin."""
    if not is_admin(request.user):
        raise PermissionDenied
    student_id = request.GET.get("student_id", "")
    q = request.GET.get("q", "").strip()
    exams = EntranceExam.objects.select_related("diploma", "cohort", "created_by")
    if student_id.isdigit():
        exams = exams.filter(attempts__student_id=int(student_id)).distinct()
    if q:
        exams = exams.filter(Q(title__icontains=q) | Q(description__icontains=q))
    page_obj, pagination_qs = paginate(request, exams)
    return render(request, "ui/pages/admin/entrance_exam_list.html", {"page_obj": page_obj, "pagination_qs": pagination_qs, "student_id": student_id, "q": q, "page_kind": "exams", "title": _("Entrance exams")})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_cohorts_for_diploma(request):
    """Return cohort <option>s filtered by diploma (HTMX)."""
    if not is_htmx(request):
        raise Http404
    if not is_admin(request.user):
        raise PermissionDenied
    diploma_id = request.GET.get("diploma", "")
    if diploma_id:
        cohorts = Cohort.objects.filter(diploma_id=diploma_id).order_by("name")
    else:
        cohorts = Cohort.objects.none()
    return render(request, "ui/fragments/admin/_options.html", {"options": cohorts})


@login_required
@role_required(*ROLE_EXAM_STAFF)
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
    return render(request, "ui/pages/admin/entrance_exam_detail.html", {
        "exam": exam,
        "title": _("Entrance exam"),
        "next": safe_next_url(request),
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_preview(request, exam_id):
    """Render an admin-facing preview of the entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(
        EntranceExam.objects.prefetch_related("questions__choices"),
        id=exam_id,
    )
    questions = exam.questions.all()
    return render(
        request,
        "ui/pages/admin/entrance_exam_preview.html",
        {
            "exam": exam,
            "questions": questions,
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_create_exam(request):
    """Create a new entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = EntranceExamForm(request.POST)
        if form.is_valid():
            exam = form.save(commit=False)
            exam.created_by = request.user
            exam.save()
            messages.success(request, _("Entrance exam created."))
            return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)
    else:
        form = EntranceExamForm()
    return render(request, "ui/pages/admin/entrance_exam_form.html", {
        "form": form,
        "title": _("Create entrance exam"),
        "next": next_url,
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_edit_exam(request, exam_id):
    """Edit an existing entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = EntranceExamForm(request.POST, instance=exam)
        if form.is_valid():
            form.save()
            messages.success(request, _("Entrance exam updated."))
            return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)
    else:
        form = EntranceExamForm(instance=exam)
    return render(request, "ui/pages/admin/entrance_exam_form.html", {
        "form": form,
        "title": _("Edit entrance exam"),
        "exam": exam,
        "next": next_url,
    })


@login_required
@require_POST
@role_required(*ROLE_EXAM_STAFF)
def admin_toggle_active(request, exam_id):
    """Toggle entrance exam active/inactive. Regenerates access token."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    next_url = safe_next_url(request)
    exam.is_active = not exam.is_active
    exam.save(update_fields=["is_active", "updated_at"])
    if exam.is_active:
        exam.regenerate_access_token()
    status = _("activated") if exam.is_active else _("deactivated")
    messages.success(request, _("Entrance exam %(status)s.") % {"status": status})
    return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
@require_POST
@role_required(*ROLE_EXAM_STAFF)
def admin_finalize_pending(request, exam_id):
    """Finalize all in-progress attempts for an exam using saved snapshots."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    next_url = safe_next_url(request)
    count = finalize_all_pending_for_exam(exam)
    messages.success(
        request,
        _("Finalized %(count)d pending attempt(s).") % {"count": count},
    )
    return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_list(request):
    """List completed entrance attempts ready for grading."""
    if not is_admin(request.user):
        raise PermissionDenied
    pending = get_pending_grading()
    page_obj, pagination_qs = paginate(request, pending)
    return render(request, "ui/pages/admin/entrance_exam_grade_list.html", {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "page_kind": "grading",
    })


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_grade_attempt(request, attempt_id):
    """Grade a specific entrance attempt."""
    if not is_admin(request.user):
        raise PermissionDenied
    attempt = get_object_or_404(
        EntranceAttempt.objects.select_related("exam", "student__user", "program_application"),
        id=attempt_id,
    )
    questions = attempt.exam.questions.prefetch_related("choices").all()
    answers = {a.question_id: a for a in attempt.answers.all()}
    if request.method == "POST":
        graded = 0
        try:
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
                        except (ValueError, TypeError, InvalidOperation):
                            logger.exception("Invalid Decimal score for question %s", q.order)
                            messages.error(request, _("Invalid score value for question %(q)s.") % {"q": q.order})
                            continue
                        max_score = max((c.score for c in q.choices.all()), default=0) if q.question_type == "mcq" else q.max_score
                        if max_score and score > max_score:
                            messages.error(request, _("Score for question %(q)s cannot exceed %(max)s.") % {"q": q.order, "max": max_score})
                            continue
                        feedback = request.POST.get(feedback_key, "")
                        grade_answer(answer, score, feedback, request.user)
                        graded += 1
            if graded:
                messages.success(request, _("Grades saved."))
            return redirect("entrance_exams:admin_grade_attempt", attempt_id=attempt_id)
        except Exception:
            logger.exception("Grade save failed for attempt %s", attempt_id)
            messages.error(request, _("An unexpected error occurred while saving grades."))
    question_answer_pairs = []
    for q in questions:
        if q.question_type == "mcq":
            max_score = max((c.score for c in q.choices.all()), default=0)
        else:
            max_score = q.max_score
        question_answer_pairs.append((q, answers.get(q.id), max_score))
    application = attempt.program_application
    decision_allowed = {PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED}
    percentage = None
    if attempt.score is not None and attempt.exam.total_score:
        percentage = round(attempt.score / attempt.exam.total_score * Decimal("100"), 2)
    change_targets = []
    if application.status in decision_allowed or application.status in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED):
        if application.status != PROGRAM_APP_STATUS_ACCEPTED:
            change_targets.append("pass")
        if application.status != PROGRAM_APP_STATUS_REJECTED:
            change_targets.append("fail")
        if application.status != PROGRAM_APP_STATUS_INTERVIEW_REQUESTED:
            change_targets.append("interview")
    return render(request, "ui/pages/admin/entrance_exam_grade_attempt.html", {
        "attempt": attempt,
        "question_answer_pairs": question_answer_pairs,
        "exam_total_score": attempt.exam.total_score,
        "percentage": percentage,
        "pass_percentage": attempt.exam.pass_percentage,
        "application_decision": attempt_decision(attempt),
        "application_status_label": application.get_status_display(),
        "can_change_decision": bool(change_targets),
        "change_targets": change_targets,
        "change_default": change_targets[0] if change_targets else "",
        "can_undo_decision": application.status in {PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED},
        "application_detail_url": reverse("applications:program_detail", args=[application.id]),
    })


def _parse_local_datetime(value: str):
    """Parse a form datetime-local value into a timezone-aware datetime (or None)."""
    value = (value or "").strip()
    if not value:
        return None
    dt = timezone.datetime.fromisoformat(value)
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_attempt_decision(request, attempt_id):
    """Admin decision for a graded entrance attempt: pass / fail / interview / revert.

    The decision transitions the attempt's ProgramApplication through
    ``change_entrance_decision``, which stays available while the application is
    still in a decision state (entrance_submitted / interview_requested /
    accepted / rejected) and locks once the application advances. The calculated
    entrance score is advisory only (§7.3 DOKKIMASHORA): the Admin decides
    independently, and the academic result is never rewritten to match.
    """
    if not is_admin(request.user):
        raise PermissionDenied
    attempt = get_object_or_404(
        EntranceAttempt.objects.select_related("exam", "student__user", "program_application"),
        id=attempt_id,
    )
    application = attempt.program_application
    action = request.POST.get("action")
    send_email = request.POST.get("send_email") == "on"
    try:
        if action == "accept":
            change_entrance_decision(
                application, target=PROGRAM_APP_STATUS_ACCEPTED, actor=request.user,
                notes=(request.POST.get("admin_notes") or "").strip(),
                send_email=send_email,
            )
            messages.success(request, _("Application accepted."))
        elif action == "reject":
            change_entrance_decision(
                application, target=PROGRAM_APP_STATUS_REJECTED, actor=request.user,
                reason=(request.POST.get("rejection_reason") or "").strip(),
                notes=(request.POST.get("admin_notes") or "").strip(),
                send_email=send_email,
            )
            messages.warning(request, _("Application rejected."))
        elif action == "request_interview":
            try:
                interview_at = _parse_local_datetime(request.POST.get("interview_at"))
                expires_at = _parse_local_datetime(request.POST.get("expires_at"))
            except (ValueError, TypeError):
                raise ValueError(_("Invalid date/time format.")) from None
            change_entrance_decision(
                application, target=PROGRAM_APP_STATUS_INTERVIEW_REQUESTED, actor=request.user,
                interview_at=interview_at,
                phone_number=(request.POST.get("phone_number") or "").strip(),
                expires_at=expires_at,
                notes=(request.POST.get("notes") or "").strip(),
                send_email=send_email,
            )
            messages.success(request, _("Interview requested."))
        elif action == "revert_decision":
            change_entrance_decision(
                application, target=PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, actor=request.user,
                reason=(request.POST.get("revert_reason") or "").strip(),
            )
            messages.info(request, _("Decision reverted."))
        else:
            raise ValueError(_("This action is not available for the current application status."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("entrance_exams:admin_grade_attempt", attempt_id=attempt.id)


# ---------------------------------------------------------------------------
# Question builder (Phase 9)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Question builder helpers (inline HTMX)
# ---------------------------------------------------------------------------

def _question_form_context(exam, question=None, form=None, formset=None, next_url=""):
    if form is None:
        form = EntranceQuestionForm(instance=question)
    if formset is None:
        extra = 0 if question and question.pk else 2
        fs_cls = inlineformset_factory(
            EntranceQuestion, EntranceChoice,
            form=EntranceChoiceForm, extra=extra, can_delete=True,
        )
        formset = fs_cls(instance=question or EntranceQuestion())
    return {
        "exam": exam,
        "question": question,
        "form": form,
        "formset": formset,
        "title": _("Edit question") if question else _("Add question"),
        "next": next_url,
    }



def _render_question_form(request, exam, question=None, form=None, formset=None, next_url=""):
    context = _question_form_context(
        exam, question=question, form=form, formset=formset, next_url=next_url,
    )
    template = "ui/fragments/admin/_question_form.html" if is_htmx(request) else "ui/pages/admin/entrance_exam_question_form.html"
    return render(request, template, context)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_add_question(request, exam_id):
    """Add a new question with inline choices to an entrance exam."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = EntranceQuestionForm(request.POST)
        if form.is_valid():
            question = form.save(commit=False)
            question.exam = exam
            question.save()
            if question.question_type == QUESTION_TYPE_MCQ:
                formset = EntranceChoiceFormSet(request.POST, instance=question)
                if formset.is_valid():
                    formset.save()
                else:
                    question.delete()
                    formset = EntranceChoiceFormSet(request.POST, instance=EntranceQuestion())
                    return _render_question_form(request, exam, form=form, formset=formset, next_url=next_url)
            else:
                EntranceChoice.objects.filter(question=question).delete()
            messages.success(request, _("Question added."))
            if is_htmx(request):
                response = HttpResponse()
                response["HX-Redirect"] = next_url or reverse("entrance_exams:admin_detail", args=[exam.id])
                return response
            return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)
        formset = EntranceChoiceFormSet(request.POST, instance=EntranceQuestion())
    else:
        last = exam.questions.order_by("order").last()
        form = EntranceQuestionForm(initial={
            "order": (last.order + 1) if last else 1,
            "question_type": QUESTION_TYPE_MCQ,
        })
        formset = EntranceChoiceFormSet(instance=EntranceQuestion())
    return _render_question_form(request, exam, form=form, formset=formset, next_url=next_url)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_edit_question(request, exam_id, question_id):
    """Edit an existing question and its choices."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    question = get_object_or_404(EntranceQuestion, id=question_id, exam=exam)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = EntranceQuestionForm(request.POST, instance=question)
        if form.is_valid():
            question = form.save(commit=False)
            question.exam = exam
            question.save()
            if question.question_type == QUESTION_TYPE_MCQ:
                formset = EntranceChoiceFormSet(request.POST, instance=question)
                if formset.is_valid():
                    formset.save()
                else:
                    return _render_question_form(request, exam, question=question, form=form, formset=formset, next_url=next_url)
            else:
                EntranceChoice.objects.filter(question=question).delete()
            messages.success(request, _("Question updated."))
            if is_htmx(request):
                response = HttpResponse()
                response["HX-Redirect"] = next_url or reverse("entrance_exams:admin_detail", args=[exam.id])
                return response
            return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)
        formset = EntranceChoiceFormSet(request.POST, instance=question)
    else:
        form = EntranceQuestionForm(instance=question)
        formset = EntranceChoiceFormSet(instance=question)
        formset.extra = 0
    return _render_question_form(request, exam, question=question, form=form, formset=formset, next_url=next_url)


@login_required
@require_POST
@role_required(*ROLE_EXAM_STAFF)
def admin_delete_question(request, exam_id, question_id):
    """Delete a question."""
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    question = get_object_or_404(EntranceQuestion, id=question_id, exam=exam)
    next_url = safe_next_url(request)
    question.delete()
    msg = _("Question deleted.")
    messages.success(request, msg)
    if is_htmx(request):
        response = HttpResponse(status=204)
        return hx_toast(response, msg, type="success", extra_events={"question-saved": None})
    return redirect(next_url) if next_url else redirect("entrance_exams:admin_detail", exam_id=exam.id)


def _eligible_applications(cohort, action, exam=None, status_filter=None):
    """Return ProgramApplications eligible for the given email action."""
    dispatched_statuses = ["sent", "queued"]
    if action == EMAIL_ACTION_INVITATION:
        qs = ProgramApplication.objects.filter(cohort=cohort).select_related("student__user")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status__in=[PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_INVITED, PROGRAM_APP_STATUS_REJECTED])
        already_sent = PAEL.objects.filter(
            program_application__cohort=cohort, action=action, status__in=dispatched_statuses, is_resend=False,
        ).values_list("program_application_id", flat=True)
        qs = qs.exclude(id__in=list(already_sent))
        return qs

    status_map = {
        EMAIL_ACTION_REJECTION: PROGRAM_APP_STATUS_REJECTED,
    }
    status_filter = status_map.get(action)
    if not status_filter:
        return ProgramApplication.objects.none()

    already_sent = PAEL.objects.filter(
        program_application__cohort=cohort, action=action, status__in=dispatched_statuses, is_resend=False,
    ).values_list("program_application_id", flat=True)

    return ProgramApplication.objects.filter(
        cohort=cohort, status=status_filter,
    ).exclude(
        id__in=list(already_sent),
    ).select_related("student__user")


def _send_invitations(request, exam, cohort, student_ids):
    """Queue invitation/rejection emails to selected applications."""
    apps = ProgramApplication.objects.filter(
        id__in=student_ids, cohort=cohort,
    ).select_related("student__user")
    queued_count = failed_count = 0
    for prog_app in apps:
        email_to = prog_app.student.user.email
        if not email_to:
            failed_count += 1
            continue
        is_rejected = prog_app.status == PROGRAM_APP_STATUS_REJECTED
        slug = TEMPLATE_REJECTION if is_rejected else TEMPLATE_ENTRANCE_EXAM_INVITATION
        action = EMAIL_ACTION_REJECTION if is_rejected else EMAIL_ACTION_INVITATION
        context = {
            "full_name": prog_app.student.full_name or "",
            "email": email_to,
        }
        if is_rejected:
            context.update(
                rejection_reason=prog_app.rejection_reason or "",
                cohort_name=cohort.name or "",
            )
        else:
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
        try:
            record, _log = queue_program_application_email(
                program_application=prog_app,
                action=action,
                template_slug=slug,
                to_email=email_to,
                context=context,
                sent_by=request.user,
                idempotency_key=f"entrance_exam:{action}:app:{prog_app.pk}",
                reference=f"app:{prog_app.pk}",
            )
        except Exception:
            logger.exception("Exam assign email queue failed for application %s", prog_app.id)
            failed_count += 1
            continue
        if record is not None:
            queued_count += 1
        else:
            failed_count += 1
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"action": "invitation", "count": len(student_ids), "queued": queued_count, "failed": failed_count},
    )
    msg = _("Queued %(queued)d invitations (%(failed)d could not be queued).") % {"queued": queued_count, "failed": failed_count}
    if queued_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=invitation")


def _send_results(request, exam, student_ids):
    """Queue result emails — auto-detects pass/fail from each attempt's result."""
    attempts = exam.attempts.filter(
        id__in=student_ids, status__in=[ATTEMPT_STATUS_COMPLETED, ATTEMPT_STATUS_GRADED],
        result__isnull=False,
    ).select_related("student__user", "program_application")
    queued_count = failed_count = 0
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
        if PAEL.objects.filter(program_application=prog_app, action=EMAIL_ACTION_EXAM_RESULT, status__in=["sent", "queued"]).exists():
            queued_count += 1
            continue
        context = {
            "full_name": attempt.student.full_name or "",
            "exam_title": exam.title,
            "cohort_name": exam.cohort.name or "",
        }
        if attempt.result == RESULT_FAIL:
            context["rejection_reason"] = attempt.program_application.rejection_reason or ""
        try:
            record, _log = queue_program_application_email(
                program_application=prog_app,
                action=EMAIL_ACTION_EXAM_RESULT,
                template_slug=slug,
                to_email=email_to,
                context=context,
                sent_by=request.user,
                idempotency_key=f"entrance_exam:result:attempt:{attempt.pk}",
                reference=f"attempt:{attempt.pk}",
            )
        except Exception:
            logger.exception("Exam result email queue failed for attempt %s", attempt.id)
            failed_count += 1
            continue
        if record is not None:
            queued_count += 1
        else:
            failed_count += 1
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"count": len(student_ids), "queued": queued_count, "failed": failed_count, "pass": pass_count, "fail": fail_count},
    )
    msg = _("Queued %(queued)d result emails (%(failed)d could not be queued).") % {"queued": queued_count, "failed": failed_count}
    if queued_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=results")


def _resend_emails(request, exam, cohort, log_ids):
    """Bulk-queue resends for selected email logs."""
    if not log_ids:
        messages.warning(request, _("No emails selected."))
        return redirect("entrance_exams:admin_cohort_email", exam_id=exam.id)
    logs = PAEL.objects.filter(
        id__in=log_ids, program_application__cohort=cohort,
    ).select_related("program_application__student")
    queued_count = failed_count = 0
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
        resend_seq = PAEL.objects.filter(
            program_application=log.program_application, action=log.action, is_resend=True,
        ).count()
        try:
            record, _log = queue_program_application_email(
                program_application=log.program_application,
                action=log.action,
                template_slug=log.template_slug,
                to_email=email_to,
                context=context,
                sent_by=request.user,
                idempotency_key=f"entrance_exam:resend:{log.action}:app:{log.program_application_id}:{resend_seq}",
                reference=f"app:{log.program_application_id}",
                is_resend=True,
            )
        except Exception:
            logger.exception("Resend bulk email queue failed for log %s", log.id)
            failed_count += 1
            continue
        if record is not None:
            queued_count += 1
        else:
            failed_count += 1
    msg = _("Queued %(queued)d email resends (%(failed)d could not be queued).") % {"queued": queued_count, "failed": failed_count}
    if queued_count:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect(request.path + "?tab=history")


@login_required
@require_http_methods(["GET", "POST"])
@role_required(*ROLE_EXAM_STAFF)
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

    ctx = {
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
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_email_tab_content.html", ctx)
    return render(request, "ui/pages/admin/entrance_exams.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
@role_required(*ROLE_EXAM_STAFF)
def admin_app_email_resend(request, exam_id, app_id):
    """Resend an email to a single application with confirmation.

    GET shows a confirmation/context page. POST validates the application,
    resolves the email/template, and queues the resend through the canonical
    Celery email service — the request never sends email directly.
    """
    if not is_admin(request.user):
        raise PermissionDenied
    exam = get_object_or_404(EntranceExam, id=exam_id)
    cohort = exam.cohort
    prog_app = get_object_or_404(
        ProgramApplication.objects.select_related("student__user"),
        id=app_id, cohort=cohort,
    )
    next_url = safe_next_url(request, default=reverse("entrance_exams:admin_detail", args=[exam_id]))

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
        action_label = dict(PAEL._meta.get_field("action").choices).get(action, action)
        return render(request, "ui/pages/admin/entrance_exam_email_resend.html", {
            "exam": exam,
            "prog_app": prog_app,
            "action": action,
            "action_label": action_label,
            "slug": slug,
            "email_to": email_to or "",
            "next_url": next_url,
        })

    action = request.POST.get("action")
    if action not in (EMAIL_ACTION_INVITATION, EMAIL_ACTION_REJECTION):
        messages.error(request, _("Invalid email action."))
        return redirect(next_url)

    email_to = prog_app.student.user.email
    if not email_to:
        messages.error(request, _("Student has no email address."))
        return redirect(next_url)

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
    resend_seq = PAEL.objects.filter(
        program_application=prog_app, action=action, is_resend=True,
    ).count()
    try:
        record, _log = queue_program_application_email(
            program_application=prog_app,
            action=action,
            template_slug=slug,
            to_email=email_to,
            context=context,
            sent_by=request.user,
            idempotency_key=f"entrance_exam:resend:{action}:app:{prog_app.pk}:{resend_seq}",
            reference=f"app:{prog_app.pk}",
            is_resend=True,
        )
    except Exception:
        logger.exception("Resend single email queue failed for application %s", prog_app.id)
        record_program_application_email_log(
            program_application=prog_app,
            action=action, template_slug=slug, recipient=email_to,
            sent_by=request.user, status="failed", error="Exception during queue", is_resend=True,
        )
        messages.error(request, _("Failed to queue the resend email."))
        return redirect(next_url)

    if record is None:
        messages.error(request, _("No active email template is available for this action."))
        return redirect(next_url)
    record_audit_event(
        actor=request.user, action=ACTION_BULK_EMAIL_SENT,
        target_type="entrance_exam", target_id=str(exam.id),
        metadata={"action": "resend_" + action, "application_id": app_id, "status": record.status},
    )
    messages.success(request, _("Resend email queued for delivery."))
    return redirect(next_url)


@login_required
@role_required(*ROLE_EXAM_STAFF)
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
    app_ids = [item.program_application_id for item in page_obj if item.program_application_id]
    resend_exam_ids: dict[int, int] = {}
    if app_ids:
        attempt_exams = EntranceAttempt.objects.filter(
            program_application_id__in=app_ids, exam_id__isnull=False,
        ).order_by("-id")
        for app_id, exam_id in attempt_exams.values_list("program_application_id", "exam_id"):
            resend_exam_ids.setdefault(app_id, exam_id)
        missing_apps = list(
            ProgramApplication.objects.filter(
                id__in=[a for a in app_ids if a not in resend_exam_ids],
            ).select_related("cohort")
        )
        cohort_ids = {a.cohort_id for a in missing_apps if a.cohort_id}
        cohort_exam_ids: dict[int, int] = {}
        if cohort_ids:
            for cohort_id, exam_id in EntranceExam.objects.filter(
                cohort_id__in=cohort_ids, is_active=True,
            ).order_by("-id").values_list("cohort_id", "id"):
                cohort_exam_ids.setdefault(cohort_id, exam_id)
        for app in missing_apps:
            if app.cohort_id in cohort_exam_ids:
                resend_exam_ids[app.pk] = cohort_exam_ids[app.cohort_id]
    ctx = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "page_kind": "email_log",
        "action_choices": PAEL._meta.get_field("action").choices,
        "status_choices": PAEL._meta.get_field("status").choices,
        "current_action": action,
        "current_status": status,
        "search_value": search,
        "resend_exam_ids": resend_exam_ids,
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_email_log_list.html", ctx)
    return render(request, "ui/pages/admin/entrance_exam_email_log.html", ctx)
