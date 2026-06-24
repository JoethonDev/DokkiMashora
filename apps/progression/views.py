"""Views for progression management (admin and student)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.models import Diploma, DiplomaEnrollment, Semester
from apps.audit.constants import (
    ACTION_DEMOTION,
    ACTION_LOCK,
    ACTION_PROMOTION,
    ACTION_RETAKE,
    ACTION_UNLOCK,
)
from apps.audit.services import record_audit_event
from apps.certificates.models import GeneratedCertificate
from apps.core.permissions import (
    ROLE_EXAM_STAFF,
    ROLE_SENIOR_STAFF,
    can_view_student_progress,
    role_required,
    safe_int,
)
from apps.students.models import StudentProfile

from .constants import CERTIFICATE_STATUS_AVAILABLE
from .models import FinalExamEligibility, StudentDiplomaCompletion
from .services import (
    aggregate_semester_scores,
    assign_retake,
    clear_retake,
    demote_student,
    evaluate_semester_progress,
    get_progression_dashboard_data,
    get_student_progress_detail,
    lock_student,
    manual_promote,
    mark_completed,
    mark_graduated,
    override_student_progress,
    unlock_student,
)


def _get_student_or_404(student_id):
    return get_object_or_404(StudentProfile, id=student_id)


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_dashboard(request):
    diplomas = Diploma.objects.filter(is_active=True).prefetch_related("cohorts")
    diploma_id = request.GET.get("diploma_id")
    cohort_id = request.GET.get("cohort_id")

    progress_qs = get_progression_dashboard_data(
        diploma_id=safe_int(diploma_id),
        cohort_id=safe_int(cohort_id),
    )

    students_data = {}
    for prog in progress_qs:
        sid = prog.student_id
        if sid not in students_data:
            students_data[sid] = {
                "student": prog.student,
                "semesters": [],
            }
        students_data[sid]["semesters"].append(prog)

    ctx = {
        "diplomas": diplomas,
        "students_data": list(students_data.values()),
        "selected_diploma_id": diploma_id,
        "selected_cohort_id": cohort_id,
        "breadcrumb_items": [{"label": _("Progression Dashboard")}],
    }
    return render(request, "progression/admin_dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Admin student detail
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_student_detail(request, student_id):
    student = _get_student_or_404(student_id)
    if not can_view_student_progress(request.user, student):
        raise PermissionDenied
    enrollments = DiplomaEnrollment.objects.filter(student=student).select_related(
        "cohort__diploma",
    )
    progress_detail = get_student_progress_detail(student)
    eligibility = FinalExamEligibility.objects.filter(student=student).first()
    completion = StudentDiplomaCompletion.objects.filter(student=student).first()
    certificates = GeneratedCertificate.objects.filter(student=student).select_related(
        "diploma_enrollment__cohort__diploma",
    ).order_by("-generated_at")

    semesters = Semester.objects.all().order_by("diploma", "order")

    # Compute requirement summaries per progress semester
    requirement_summaries = {}
    for prog in progress_detail["semesters"]:
        result = aggregate_semester_scores(student, prog.semester)
        requirement_summaries[prog.semester.id] = result

    ctx = {
        "student": student,
        "enrollments": enrollments,
        "progress_semesters": progress_detail["semesters"],
        "requirement_summaries": requirement_summaries,
        "decisions": progress_detail["decisions"],
        "eligibility": eligibility,
        "completion": completion,
        "certificates": certificates,
        "semesters": semesters,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": str(student)},
        ],
    }
    return render(request, "progression/admin_student_detail.html", ctx)


# ---------------------------------------------------------------------------
# Admin actions (all POST)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_evaluate_semester(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        evaluate_semester_progress(student, enrollment, semester)
        messages.success(request, _("Semester evaluated."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_promote(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        override_failed = request.POST.get("override_failed") == "1"
        manual_promote(student, enrollment, semester, actor=request.user, reason=reason, override_failed=override_failed)
        record_audit_event(
            actor=request.user,
            action=ACTION_PROMOTION,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "semester_id": semester_id,
                "reason": reason,
                "override_failed": override_failed,
            },
        )
        messages.success(request, _("Student promoted."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_demote(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        demote_student(student, enrollment, semester, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_DEMOTION,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "semester_id": semester_id,
                "reason": reason,
            },
        )
        messages.success(request, _("Student demoted."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_override(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")
    new_status = request.POST.get("new_status")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        override_student_progress(student, enrollment, semester, new_status, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_PROMOTION,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "semester_id": semester_id,
                "new_status": new_status,
                "reason": reason,
                "type": "override",
            },
        )
        messages.success(request, _("Progress overridden."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_lock(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        lock_student(student, enrollment, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_LOCK,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "reason": reason,
            },
        )
        messages.success(request, _("Student locked."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_unlock(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        unlock_student(student, enrollment, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_UNLOCK,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "reason": reason,
            },
        )
        messages.success(request, _("Student unlocked."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_assign_retake(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        assign_retake(student, enrollment, semester, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_RETAKE,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "semester_id": semester_id,
                "reason": reason,
                "type": "assign",
            },
        )
        messages.success(request, _("Retake assigned."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_clear_retake(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    semester_id = request.POST.get("semester_id")
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    try:
        clear_retake(student, enrollment, semester, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_RETAKE,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "semester_id": semester_id,
                "reason": reason,
                "type": "clear",
            },
        )
        messages.success(request, _("Retake cleared."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_mark_complete(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    notes = request.POST.get("notes", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        mark_completed(student, enrollment, actor=request.user, notes=notes)
        record_audit_event(
            actor=request.user,
            action=ACTION_PROMOTION,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "notes": notes,
                "type": "completed",
            },
        )
        messages.success(request, _("Student marked as completed."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_mark_graduate(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = request.POST.get("enrollment_id")
    notes = request.POST.get("notes", "")

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)

    try:
        mark_graduated(student, enrollment, actor=request.user, notes=notes)
        record_audit_event(
            actor=request.user,
            action=ACTION_PROMOTION,
            target_type="student",
            target_id=str(student.id),
            metadata={
                "enrollment_id": enrollment_id,
                "notes": notes,
                "type": "graduated",
            },
        )
        messages.success(request, _("Student marked as graduated."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=student.id)


# ---------------------------------------------------------------------------
# Student views
# ---------------------------------------------------------------------------


@login_required
def student_progress(request):
    student_profile = getattr(request.user, "student_profile", None)
    if not student_profile:
        messages.error(request, _("Student profile not found."))
        return redirect("core:home")

    progress_detail = get_student_progress_detail(student_profile)
    eligibility = FinalExamEligibility.objects.filter(student=student_profile).first()
    certificates = GeneratedCertificate.objects.filter(
        student=student_profile,
        status=CERTIFICATE_STATUS_AVAILABLE,
    ).select_related("diploma_enrollment__cohort__diploma").order_by("-generated_at")

    ctx = {
        "semesters": progress_detail["semesters"],
        "completions": progress_detail["completions"],
        "eligibilities": progress_detail["eligibilities"],
        "eligibility": eligibility,
        "decisions": progress_detail["decisions"],
        "certificates": certificates,
    }
    return render(request, "progression/student_progress.html", ctx)
