"""Views for progression management (admin and student)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.models import Cohort, Diploma, DiplomaEnrollment, Semester
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

from .constants import CERTIFICATE_STATUS_AVAILABLE, COMPONENT_TYPE_CHOICES, REJOIN_STATUS_CHOICES
from .models import (
    FinalExamEligibility,
    SemesterGradeComponent,
    SemesterRejoinPlan,
    StudentDiplomaCompletion,
    StudentGradeItem,
)
from .services import (
    activate_rejoin_plan,
    aggregate_semester_scores,
    assign_retake,
    cancel_rejoin_plan,
    clear_retake,
    confirm_interview_fail,
    confirm_interview_pass,
    confirm_pass,
    create_rejoin_plan,
    demote_student,
    evaluate_semester_components,
    evaluate_semester_progress,
    get_progression_dashboard_data,
    get_student_progress_detail,
    lock_student,
    manual_promote,
    mark_completed,
    mark_graduated,
    override_student_progress,
    request_interview,
    set_custom_activity_grade,
    sync_all_grade_items,
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

    grade_components = SemesterGradeComponent.objects.filter(
        cohort__in=[e.cohort_id for e in enrollments],
        active=True,
    ).select_related("semester")

    grade_items = StudentGradeItem.objects.filter(
        student=student,
    ).select_related("component__semester", "component__cohort", "diploma_enrollment")

    rejoin_plans = SemesterRejoinPlan.objects.filter(student=student).order_by("-created_at")

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
        "grade_components": grade_components,
        "grade_items": grade_items,
        "rejoin_plans": rejoin_plans,
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
# Admin grade component management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_component_list(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id)
    components = SemesterGradeComponent.objects.filter(
        cohort=cohort, semester=semester,
    ).order_by("order")
    ctx = {
        "cohort": cohort,
        "semester": semester,
        "components": components,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Grade Components")},
        ],
    }
    return render(request, "progression/admin_component_list.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_component_create(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        component_type = request.POST.get("component_type", "")
        weight_percent = request.POST.get("weight_percent")
        required_min_percent = request.POST.get("required_min_percent", 0)
        required_to_pass = request.POST.get("required_to_pass") == "1"
        order = request.POST.get("order")
        active = request.POST.get("active") == "1"
        if not name or not component_type or not weight_percent or not order:
            messages.error(request, _("Name, type, weight, and order are required."))
        else:
            try:
                SemesterGradeComponent.objects.create(
                    cohort=cohort,
                    semester=semester,
                    name=name,
                    component_type=component_type,
                    weight_percent=weight_percent,
                    required_min_percent=required_min_percent,
                    required_to_pass=required_to_pass,
                    order=order,
                    active=active,
                )
                messages.success(request, _("Component created."))
                return redirect("progression:admin_component_list", cohort_id=cohort.id, semester_id=semester.id)
            except (ValueError, ValidationError) as exc:
                messages.error(request, str(exc))
    ctx = {
        "cohort": cohort,
        "semester": semester,
        "component": None,
        "component_type_choices": COMPONENT_TYPE_CHOICES,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Grade Components"), "href": reverse("progression:admin_component_list", args=[cohort.id, semester.id])},
            {"label": _("Create Component")},
        ],
    }
    return render(request, "progression/admin_component_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_component_edit(request, component_id):
    component = get_object_or_404(SemesterGradeComponent, id=component_id)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        component_type = request.POST.get("component_type", "")
        weight_percent = request.POST.get("weight_percent")
        required_min_percent = request.POST.get("required_min_percent", 0)
        required_to_pass = request.POST.get("required_to_pass") == "1"
        order = request.POST.get("order")
        active = request.POST.get("active") == "1"
        if not name or not component_type or not weight_percent or not order:
            messages.error(request, _("Name, type, weight, and order are required."))
        else:
            try:
                component.name = name
                component.component_type = component_type
                component.weight_percent = weight_percent
                component.required_min_percent = required_min_percent
                component.required_to_pass = required_to_pass
                component.order = order
                component.active = active
                component.save()
                messages.success(request, _("Component updated."))
                return redirect("progression:admin_component_list", cohort_id=component.cohort_id, semester_id=component.semester_id)
            except Exception as exc:
                messages.error(request, str(exc))
    ctx = {
        "cohort": component.cohort,
        "semester": component.semester,
        "component": component,
        "component_type_choices": COMPONENT_TYPE_CHOICES,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Grade Components"), "href": reverse("progression:admin_component_list", args=[component.cohort_id, component.semester_id])},
            {"label": _("Edit Component")},
        ],
    }
    return render(request, "progression/admin_component_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_component_delete(request, component_id):
    component = get_object_or_404(SemesterGradeComponent, id=component_id)
    cohort_id = component.cohort_id
    semester_id = component.semester_id
    component.active = False
    component.save()
    messages.success(request, _("Component deactivated."))
    return redirect("progression:admin_component_list", cohort_id=cohort_id, semester_id=semester_id)


# ---------------------------------------------------------------------------
# Admin custom activity grade entry
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_custom_activity_grade(request, enrollment_id, component_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    component = get_object_or_404(SemesterGradeComponent, id=component_id)
    grade_item = StudentGradeItem.objects.filter(
        component=component, diploma_enrollment=enrollment,
    ).first()
    if request.method == "POST":
        score = request.POST.get("score")
        max_score = request.POST.get("max_score")
        notes = request.POST.get("notes", "")
        if not score or not max_score:
            messages.error(request, _("Score and max score are required."))
        else:
            try:
                set_custom_activity_grade(
                    component, enrollment,
                    score=score, max_score=max_score,
                    actor=request.user, notes=notes,
                )
                messages.success(request, _("Grade saved."))
                return redirect("progression:admin_custom_activity_grade", enrollment_id=enrollment.id, component_id=component.id)
            except (ValueError, ValidationError) as exc:
                messages.error(request, str(exc))
    ctx = {
        "enrollment": enrollment,
        "component": component,
        "grade_item": grade_item,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Custom Activity Grade")},
        ],
    }
    return render(request, "progression/admin_custom_activity_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_grade_sync(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    try:
        result = sync_all_grade_items(enrollment, semester, actor=request.user)
        synced = sum(1 for v in result.values() if v is not None)
        messages.success(request, _("Synced {count} grade item(s).").format(count=synced))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("progression:admin_student_detail", student_id=enrollment.student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_evaluation(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    try:
        result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)
    ctx = {
        "enrollment": enrollment,
        "semester": semester,
        "result": result,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Semester Evaluation")},
        ],
    }
    return render(request, "progression/admin_semester_evaluation.html", ctx)


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

    grade_items = StudentGradeItem.objects.filter(
        student=student_profile,
        visible_to_student=True,
    ).select_related("component__semester", "component__cohort", "diploma_enrollment").order_by("component__semester__order")

    ctx = {
        "semesters": progress_detail["semesters"],
        "completions": progress_detail["completions"],
        "eligibilities": progress_detail["eligibilities"],
        "eligibility": eligibility,
        "decisions": progress_detail["decisions"],
        "certificates": certificates,
        "grade_items": grade_items,
    }
    return render(request, "progression/student_progress.html", ctx)


# ---------------------------------------------------------------------------
# Interview workflow
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_request_interview(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)

    if request.method == "POST":
        interview_at = request.POST.get("interview_at", "") or None
        notes = request.POST.get("notes", "")
        try:
            request_interview(
                enrollment.student, enrollment, semester,
                actor=request.user, interview_at=interview_at, notes=notes,
            )
            messages.success(request, _("Interview requested."))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    try:
        result = evaluate_semester_components(enrollment, enrollment.cohort, semester)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    ctx = {
        "enrollment": enrollment,
        "semester": semester,
        "result": result,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Request Interview")},
        ],
    }
    return render(request, "progression/admin_interview_request.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_confirm_pass(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    skip_interview = request.POST.get("skip_interview") == "1"
    notes = request.POST.get("notes", "")

    try:
        confirm_pass(
            enrollment.student, enrollment, semester,
            actor=request.user, notes=notes, skip_interview=skip_interview,
        )
        messages.success(request, _("Pass confirmed."))
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("progression:admin_student_detail", student_id=enrollment.student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_interview_decision(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)

    if request.method == "POST":
        decision = request.POST.get("decision")
        notes = request.POST.get("notes", "")
        try:
            if decision == "pass":
                confirm_interview_pass(
                    enrollment.student, enrollment, semester,
                    actor=request.user, notes=notes,
                )
                messages.success(request, _("Interview marked as passed."))
            elif decision == "fail":
                confirm_interview_fail(
                    enrollment.student, enrollment, semester,
                    actor=request.user, notes=notes,
                )
                messages.success(request, _("Interview marked as failed."))
            else:
                messages.error(request, _("Invalid decision."))
                return redirect("progression:admin_interview_decision", enrollment_id=enrollment.id, semester_id=semester.id)
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    ctx = {
        "enrollment": enrollment,
        "semester": semester,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Interview Decision")},
        ],
    }
    return render(request, "progression/admin_interview_decision.html", ctx)


# ---------------------------------------------------------------------------
# Semester rejoin plan views
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_rejoin_plan_list(request):
    plans = SemesterRejoinPlan.objects.select_related(
        "student__user", "from_enrollment__cohort", "failed_semester",
        "target_cohort", "planned_by", "to_enrollment__cohort",
    ).all()
    ctx = {
        "plans": plans,
        "rejoin_status_choices": REJOIN_STATUS_CHOICES,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Rejoin Plans")},
        ],
    }
    return render(request, "progression/admin_rejoin_plan_list.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_create_rejoin_plan(request, enrollment_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if request.method == "POST":
        semester_id = request.POST.get("semester_id")
        cohort_id = request.POST.get("cohort_id")
        notes = request.POST.get("notes", "")
        if not semester_id or not cohort_id:
            messages.error(request, _("Semester and target cohort are required."))
        else:
            try:
                semester = get_object_or_404(Semester, id=semester_id)
                cohort = get_object_or_404(Cohort, id=cohort_id)
                create_rejoin_plan(
                    enrollment.student,
                    enrollment,
                    semester,
                    cohort,
                    actor=request.user,
                    notes=notes,
                )
                messages.success(request, _("Rejoin plan created."))
                return redirect("progression:admin_rejoin_plan_list")
            except ValueError as exc:
                messages.error(request, str(exc))
    semesters = Semester.objects.filter(diploma=enrollment.cohort.diploma).order_by("order")
    cohorts = Cohort.objects.filter(diploma=enrollment.cohort.diploma, is_active=True)
    ctx = {
        "enrollment": enrollment,
        "semesters": semesters,
        "cohorts": cohorts,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Rejoin Plans"), "href": reverse("progression:admin_rejoin_plan_list")},
            {"label": _("Create Rejoin Plan")},
        ],
    }
    return render(request, "progression/admin_rejoin_plan_form.html", ctx)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_activate_rejoin_plan(request, plan_id):
    plan = get_object_or_404(SemesterRejoinPlan, id=plan_id)
    try:
        activate_rejoin_plan(plan, actor=request.user)
        messages.success(request, _("Rejoin plan activated."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("progression:admin_rejoin_plan_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_cancel_rejoin_plan(request, plan_id):
    plan = get_object_or_404(SemesterRejoinPlan, id=plan_id)
    reason = request.POST.get("reason", "")
    try:
        cancel_rejoin_plan(plan, actor=request.user, reason=reason)
        messages.success(request, _("Rejoin plan cancelled."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("progression:admin_rejoin_plan_list")
