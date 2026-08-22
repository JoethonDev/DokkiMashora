"""Views for progression management (admin and student)."""

import csv
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE
from apps.academics.models import (
    Cohort,
    Diploma,
    DiplomaEnrollment,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    Semester,
)
from apps.audit.constants import (
    ACTION_LOCK,
    ACTION_PROMOTION,
    ACTION_RETAKE,
    ACTION_UNLOCK,
)
from apps.audit.services import record_audit_event
from apps.certificates.models import GeneratedCertificate
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_SENIOR_STAFF,
    can_view_student_progress,
    is_admin,
    role_required,
    safe_int,
)
from apps.students.models import StudentProfile

from .constants import (
    CERTIFICATE_STATUS_AVAILABLE,
    CERTIFICATE_STATUS_GENERATED,
    CERTIFICATE_STATUS_REGENERATION,
    DIPLOMA_COMPLETION_COMPLETED,
    DIPLOMA_COMPLETION_GRADUATED,
    EVALUATION_CONFIG_STATUS_ACTIVE,
    FINAL_PROGRESSION_STATUS_PENDING,
    FINAL_PROGRESSION_STATUS_RESOLVED,
    INTERVIEW_DECISION_FAIL,
    INTERVIEW_DECISION_PASS,
    INTERVIEW_STATUS_REQUESTED,
    PROMOTION_DECISION_INTERVIEW_FAIL,
    PROMOTION_DECISION_INTERVIEW_PASS,
    PROMOTION_DECISION_REQUEST_INTERVIEW,
    REJOIN_STATUS_ACTIVE,
    REJOIN_STATUS_CHOICES,
    REJOIN_STATUS_PLANNED,
    SEMESTER_EVALUATION_FAIL,
    SEMESTER_EVALUATION_PASS,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PROMOTED,
)
from .forms import SemesterEvaluationSettingsForm
from .models import (
    DiplomaProgressionRule,
    FinalExamEligibility,
    FinalProgressionOutcome,
    PromotionDecision,
    SemesterEvaluation,
    SemesterEvaluationConfig,
    SemesterGradeComponent,
    SemesterInterview,
    SemesterRejoinPlan,
    StudentDiplomaCompletion,
    StudentGradeItem,
    StudentSemesterProgress,
)
from .services import (
    activate_rejoin_plan,
    aggregate_semester_scores,
    assign_retake,
    cancel_rejoin_plan,
    clear_retake,
    complete_final_interview,
    create_rejoin_plan,
    evaluate_semester_components,
    evaluate_semester_evaluation,
    finalize_semester_evaluation,
    get_immediate_next_cohort,
    get_rejoin_eligibility_rows,
    get_semester_component_totals,
    get_semester_progression_rows,
    get_student_progress_detail,
    lock_student,
    mark_completed,
    mark_graduated,
    promote_academic_year,
    promote_semester,
    request_final_interview,
    resolve_final_progression,
    save_progression_rule,
    sync_all_grade_items,
    unlock_student,
)

logger = logging.getLogger(__name__)


def _get_student_or_404(student_id):
    return get_object_or_404(StudentProfile, id=student_id)


def _safe_redirect_url(request, url):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return None


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_dashboard(request):
    """Single entry point: active diplomas → cohorts → semester tables."""
    diplomas = Diploma.objects.filter(is_active=True).annotate(
        cohort_count=Count("cohorts"),
    ).prefetch_related("cohorts")
    ctx = {
        "workflow": "dashboard",
        "diplomas": diplomas,
        "breadcrumb_items": [{"label": _("Progression Dashboard")}],
    }
    return render(request, "ui/pages/admin/progression_dashboard.html", {**ctx, "title": _("Progression")})


# ---------------------------------------------------------------------------
# Admin navigation: diploma → cohort → semester
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_cohort_list(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohorts = diploma.cohorts.annotate(
        enrollment_count=Count("enrollments", distinct=True),
        semester_count=Count("diploma__semesters", distinct=True),
    ).order_by("-start_date")
    # Semesters are Diploma-scoped; show them inline so admins reach the
    # semester progression table in one click instead of two list pages.
    semesters = list(Semester.objects.filter(diploma=diploma).order_by("order"))
    ctx = {
        "diploma": diploma,
        "cohorts": cohorts,
        "semesters": semesters,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": diploma.title},
        ],
    }
    return render(request, "ui/pages/admin/progression_cohort_list.html", {**ctx, "title": diploma.title})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_list(request, diploma_id, cohort_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semesters = Semester.objects.filter(diploma=diploma).order_by("order")
    enrollment_count = DiplomaEnrollment.objects.filter(cohort=cohort, status="active").count()
    semesters_data = []
    for semester in semesters:
        semesters_data.append({
            "semester": semester,
            "enrollment_count": enrollment_count,
        })
    ctx = {
        "diploma": diploma,
        "cohort": cohort,
        "semesters_data": semesters_data,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": diploma.title, "href": reverse("progression:admin_cohort_list", args=[diploma.id])},
            {"label": cohort.name},
        ],
    }
    return render(request, "ui/pages/admin/progression_semester_list.html", {**ctx, "title": cohort.name})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_cohort_semester_progression(request, diploma_id, cohort_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    enrollments = DiplomaEnrollment.objects.filter(
        cohort=cohort, status="active",
    ).select_related("student__user").order_by("student__user__full_name")
    page_obj, pagination_qs = paginate(request, enrollments, per_page=25)
    # P4-01: set-wise projection from immutable evaluation/final-outcome records.
    table_rows = get_semester_progression_rows(cohort, semester, list(page_obj.object_list))

    ctx = {
        "diploma": diploma,
        "cohort": cohort,
        "semester": semester,
        "table_rows": table_rows,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": diploma.title, "href": reverse("progression:admin_cohort_list", args=[diploma.id])},
            {"label": cohort.name, "href": reverse("progression:admin_semester_list", args=[diploma.id, cohort.id])},
            {"label": semester.title},
        ],
    }
    return render(request, "ui/pages/admin/progression_semester_table.html", {**ctx, "title": semester.title})


# ---------------------------------------------------------------------------
# Bulk semester actions
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_sync_all(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(DiplomaEnrollment.objects.filter(cohort=cohort, status="active"))
    ok = 0
    fail = 0
    with transaction.atomic():
        for enrollment in enrollments:
            try:
                sync_all_grade_items(enrollment, semester, actor=request.user)
                ok += 1
            except (ValueError, ValidationError) as exc:
                logger.warning("Sync failed for enrollment %s: %s", enrollment.id, exc)
                fail += 1
    messages.success(request, _("Synced {ok} student(s). {fail} failed.").format(ok=ok, fail=fail))
    return redirect("progression:admin_cohort_semester_progression", diploma_id=cohort.diploma_id, cohort_id=cohort.id, semester_id=semester.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_evaluate_all(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related("student"))
    ok = 0
    fail = 0
    # P2-06: per-enrollment atomic finalization (the service owns the lock);
    # failures are reported individually, never rolled back together.
    for enrollment in enrollments:
        try:
            finalize_semester_evaluation(enrollment, semester, actor=request.user)
            ok += 1
        except (ValueError, ValidationError) as exc:
            logger.warning("Evaluation failed for enrollment %s: %s", enrollment.id, exc)
            fail += 1
    messages.success(request, _("Evaluated {ok} student(s). {fail} failed.").format(ok=ok, fail=fail))
    return redirect("progression:admin_cohort_semester_progression", diploma_id=cohort.diploma_id, cohort_id=cohort.id, semester_id=semester.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_auto_promote(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(DiplomaEnrollment.objects.filter(
        cohort=cohort, status=ENROLLMENT_STATUS_ACTIVE,
    ).select_related("student"))
    # BUG-019: eligibility comes from the canonical synchronized evaluation
    # (finalized SemesterEvaluation) — only PASS results are promotion candidates.
    eligible_ids = set(
        SemesterEvaluation.objects.filter(
            diploma_enrollment__in=enrollments,
            semester=semester,
            result=SEMESTER_EVALUATION_PASS,
        ).values_list("diploma_enrollment_id", flat=True)
    )
    promoted = 0
    fail = 0
    for enrollment in enrollments:
        if enrollment.id not in eligible_ids:
            continue
        try:
            promote_semester(
                enrollment, semester,
                actor=request.user, reason="Bulk auto-promote", automatic=True,
            )
            promoted += 1
        except (ValueError, ValidationError) as exc:
            logger.warning("Auto-promote skipped for enrollment %s: %s", enrollment.id, exc)
            fail += 1
    messages.success(request, _("{promoted} student(s) promoted. {fail} failed.").format(promoted=promoted, fail=fail))
    return redirect("progression:admin_cohort_semester_progression", diploma_id=cohort.diploma_id, cohort_id=cohort.id, semester_id=semester.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_export_csv(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related("student__user"))

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="semester_{semester.id}_progression.csv"'
    writer = csv.writer(response)
    writer.writerow(["Student", "Semester Exam %", "Attendance %", "Homework/Assessment %", "Weighted Total %", "Status"])

    grade_items = StudentGradeItem.objects.filter(
        diploma_enrollment__in=enrollments,
        component__semester=semester,
        component__cohort=cohort,
    ).select_related("component")
    gi_by_enrollment = {}
    for gi in grade_items:
        gi_by_enrollment.setdefault(gi.diploma_enrollment_id, {})[gi.component.component_type] = gi

    enrollment_ids = [enrollment.id for enrollment in enrollments]
    progress_by_enrollment = {
        progress.diploma_enrollment_id: progress
        for progress in StudentSemesterProgress.objects.filter(
            diploma_enrollment_id__in=enrollment_ids,
            semester=semester,
        )
    }
    try:
        totals_by_enrollment = get_semester_component_totals(enrollments, cohort, semester)
    except ValueError:
        totals_by_enrollment = {enrollment.id: None for enrollment in enrollments}

    for enrollment in enrollments:
        items = gi_by_enrollment.get(enrollment.id, {})
        exam = items.get("semester_exam")
        att = items.get("attendance")
        hw = items.get("homework_assessment")
        total = totals_by_enrollment.get(enrollment.id)
        progress = progress_by_enrollment.get(enrollment.id)
        status = progress.get_status_display() if progress else "Not started"
        writer.writerow([
            str(enrollment.student),
            exam.score_percent if exam else "",
            att.score_percent if att else "",
            hw.score_percent if hw else "",
            f"{total:.2f}%" if total is not None else "",
            status,
        ])
    return response


# ---------------------------------------------------------------------------
# Admin student detail
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_student_detail(request, student_id):
    student = _get_student_or_404(student_id)
    enrollments = DiplomaEnrollment.objects.filter(student=student).select_related(
        "cohort__diploma",
    )
    if not is_admin(request.user):
        mod_cohorts_qs = ModeratorAssignment.objects.filter(
            moderator=request.user, active=True,
        ).values("cohort_id")
        mod_students = ModeratorStudentAssignment.objects.filter(
            moderator=request.user, active=True,
        ).values("student_id")
        enrollments = enrollments.filter(
            Q(cohort_id__in=mod_cohorts_qs)
            | Q(student_id__in=mod_students)
        )
        mod_wide = ModeratorAssignment.objects.filter(
            moderator=request.user, active=True, cohort__isnull=True,
        ).exists()
        if not mod_wide and not mod_cohorts_qs and not mod_students:
            enrollments = DiplomaEnrollment.objects.none()
        # S2-14: pass the moderator's assigned cohort for this student so the
        # shared helper stays scoped (a missing cohort must never be an
        # unrestricted match); admins and teachers retain prior behavior. The
        # cohort comes from the moderator's explicit assignment rows and must
        # match one of this student's scoped enrollments.
        assigned_cohort_ids = set(mod_cohorts_qs.values_list("cohort_id", flat=True))
        assigned_cohort_ids.update(
            ModeratorStudentAssignment.objects.filter(
                moderator=request.user, student=student, active=True,
            ).values_list("cohort_id", flat=True)
        )
        scoped_cohort = (
            enrollments.filter(cohort_id__in=assigned_cohort_ids)
            .order_by("id")
            .values_list("cohort_id", flat=True)
            .first()
        )
    else:
        scoped_cohort = None
    if not can_view_student_progress(request.user, student, cohort=scoped_cohort):
        raise PermissionDenied
    allowed_cohort_ids = list(enrollments.values_list("cohort_id", flat=True).distinct())
    cohort_filter = {"diploma_enrollment__cohort_id__in": allowed_cohort_ids} if not is_admin(request.user) and allowed_cohort_ids else {}
    progress_detail = get_student_progress_detail(student, cohort_ids=allowed_cohort_ids if not is_admin(request.user) else None)
    current_progress = progress_detail["semesters"].last()
    eligibility_qs = FinalExamEligibility.objects.filter(student=student)
    if cohort_filter:
        eligibility_qs = eligibility_qs.filter(**cohort_filter)
    eligibility = eligibility_qs.first()
    completion_qs = StudentDiplomaCompletion.objects.filter(student=student)
    if cohort_filter:
        completion_qs = completion_qs.filter(**cohort_filter)
    completion = completion_qs.first()
    certificates = GeneratedCertificate.objects.filter(student=student).select_related(
        "diploma_enrollment__cohort__diploma",
    )
    if cohort_filter:
        certificates = certificates.filter(**cohort_filter)
    certificates = certificates.order_by("-generated_at")

    # L6: offer only the enrollment's own diploma semesters and only the
    # action buttons that match the enrollment's current state.
    diploma_ids = list(enrollments.values_list("cohort__diploma_id", flat=True).distinct())
    semesters_by_diploma = {}
    for sem in Semester.objects.filter(diploma_id__in=diploma_ids).order_by("diploma_id", "order"):
        semesters_by_diploma.setdefault(sem.diploma_id, []).append(sem)

    enrollment_ids = list(enrollments.values_list("id", flat=True))
    progress_rows = list(StudentSemesterProgress.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
    ).values("diploma_enrollment_id", "semester_id", "status", "retake_required"))
    locked_by_enrollment = {}
    retake_by_enrollment = {}
    progress_status_by_semester = {}
    for row in progress_rows:
        eid = row["diploma_enrollment_id"]
        if row["status"] == SEMESTER_PROGRESS_LOCKED:
            locked_by_enrollment[eid] = True
        if row["retake_required"]:
            retake_by_enrollment[eid] = True
        progress_status_by_semester[(eid, row["semester_id"])] = row["status"]

    semesters_by_enrollment = {
        enrollment.id: semesters_by_diploma.get(enrollment.cohort.diploma_id, [])
        for enrollment in enrollments
    }

    # Authoritative state per enrollment/semester (P2-07/P4-02): the action
    # projection must expose only transitions the mutation services accept.
    # A SemesterEvaluation row means the semester is finalized/immutable.
    evaluations = SemesterEvaluation.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
    ).values("diploma_enrollment_id", "semester_id")
    finalized_semesters = {(row["diploma_enrollment_id"], row["semester_id"]) for row in evaluations}

    outcomes = FinalProgressionOutcome.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
    ).select_related("evaluation")
    outcome_state = {}  # (enrollment_id, semester_id) -> (status, final_result)
    for outcome in outcomes:
        outcome_state[(outcome.diploma_enrollment_id, outcome.semester_id)] = (
            outcome.status,
            outcome.final_result,
        )

    interviews = SemesterInterview.objects.filter(
        outcome__diploma_enrollment_id__in=enrollment_ids,
    ).values("outcome__diploma_enrollment_id", "outcome__semester_id", "status")
    interview_state = {}  # (enrollment_id, semester_id) -> status
    for row in interviews:
        interview_state[(row["outcome__diploma_enrollment_id"], row["outcome__semester_id"])] = row["status"]

    completion_state = {
        c.diploma_enrollment_id: c.status
        for c in StudentDiplomaCompletion.objects.filter(diploma_enrollment_id__in=enrollment_ids)
    }
    active_certificate_enrollments = set(
        GeneratedCertificate.objects.filter(
            diploma_enrollment_id__in=enrollment_ids,
            status__in=[
                CERTIFICATE_STATUS_GENERATED,
                CERTIFICATE_STATUS_AVAILABLE,
                CERTIFICATE_STATUS_REGENERATION,
            ],
        ).values_list("diploma_enrollment_id", flat=True)
    )
    has_active_rejoin_plan = SemesterRejoinPlan.objects.filter(
        student=student,
        status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
    ).exists()
    active_student_cohort_ids = set(
        DiplomaEnrollment.objects.filter(
            student=student, status=ENROLLMENT_STATUS_ACTIVE,
        ).values_list("cohort_id", flat=True)
    )

    allowed_actions = {}
    for enrollment in enrollments:
        active = enrollment.status == ENROLLMENT_STATUS_ACTIVE
        locked = locked_by_enrollment.get(enrollment.id, False)
        retake_required = retake_by_enrollment.get(enrollment.id, False)
        diploma_semesters = semesters_by_enrollment[enrollment.id]

        # A semester is "decision-ready" when its evaluation is finalized and the
        # outcome is still pending (not resolved, not blocked by a requested interview).
        decision_targets = []
        evaluation_targets = []
        failed_targets = []
        interview_requested = False
        for semester in diploma_semesters:
            key = (enrollment.id, semester.id)
            if key not in finalized_semesters:
                evaluation_targets.append(semester)
            else:
                status, final_result = outcome_state.get(key, (FINAL_PROGRESSION_STATUS_PENDING, None))
                if status == FINAL_PROGRESSION_STATUS_PENDING:
                    decision_targets.append(semester)
                if interview_state.get(key) == INTERVIEW_STATUS_REQUESTED:
                    interview_requested = True
                if status == FINAL_PROGRESSION_STATUS_RESOLVED and final_result == SEMESTER_EVALUATION_FAIL:
                    failed_targets.append(semester)

        retake_targets = [
            semester for semester in diploma_semesters
            if progress_status_by_semester.get((enrollment.id, semester.id)) == SEMESTER_PROGRESS_FAILED
        ]
        clear_retake_targets = [
            semester for semester in diploma_semesters
            if any(
                row["diploma_enrollment_id"] == enrollment.id
                and row["semester_id"] == semester.id
                and row["retake_required"]
                for row in progress_rows
            )
        ]

        # Rejoin eligibility (P4-02): finalized FAILED outcome + immediate next
        # cohort + same semester not finalized there + no existing plan.
        next_cohort = None
        rejoin_eligible = False
        rejoin_targets = []
        if active and failed_targets:
            next_cohort = get_immediate_next_cohort(enrollment.cohort)
            if (
                next_cohort is not None
                and not has_active_rejoin_plan
                and next_cohort.id not in active_student_cohort_ids
            ):
                finalized_target_ids = set(
                    SemesterEvaluation.objects.filter(
                        diploma_enrollment__cohort=next_cohort,
                        semester_id__in=[semester.id for semester in failed_targets],
                    ).values_list("semester_id", flat=True)
                )
                rejoin_targets = [
                    semester for semester in failed_targets if semester.id not in finalized_target_ids
                ]
            rejoin_eligible = bool(rejoin_targets)

        completion_status = completion_state.get(enrollment.id)
        completed_or_graduated = completion_status in (
            DIPLOMA_COMPLETION_COMPLETED,
            DIPLOMA_COMPLETION_GRADUATED,
        )
        # mark_complete/mark_graduate require every diploma semester to have a
        # StudentSemesterProgress row in passed/promoted/completed (services.py
        # mark_requirements_met). Same check as the service.
        all_semesters_passed = bool(diploma_semesters) and all(
            progress_status_by_semester.get((enrollment.id, s.id))
            in (SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_PROMOTED, SEMESTER_PROGRESS_COMPLETED)
            for s in diploma_semesters
        )
        mark_ready = active and not locked and not retake_required and not completed_or_graduated

        allowed_actions[enrollment.id] = {
            "can_evaluate": active and bool(evaluation_targets),
            "can_promote": active and bool(decision_targets),
            "can_demote": active and bool(decision_targets),
            "can_lock": active and not locked,
            "can_unlock": active and locked,
            "can_assign_retake": active and not retake_required and bool(retake_targets),
            "can_clear_retake": active and retake_required and bool(clear_retake_targets),
            "can_mark_complete": mark_ready and all_semesters_passed,
            "can_mark_graduate": mark_ready and all_semesters_passed,
            "can_generate_certificate": active and completed_or_graduated
            and enrollment.id not in active_certificate_enrollments,
            "can_create_rejoin_plan": rejoin_eligible,
            "interview_requested": interview_requested,
            "targets": {
                "evaluate": evaluation_targets,
                "promote": decision_targets,
                "demote": decision_targets,
                "assign_retake": retake_targets,
                "clear_retake": clear_retake_targets,
                "create_rejoin_plan": rejoin_targets,
            },
        }

        # One primary action + a small contextual set (locked review rule):
        # never flood the page with every possible transition.
        action_keys = [
            "generate_certificate",
            "create_rejoin_plan",
            "mark_graduate",
            "mark_complete",
            "promote",
            "demote",
            "evaluate",
            "lock",
            "unlock",
            "assign_retake",
            "clear_retake",
        ]
        valid_keys = [key for key in action_keys if allowed_actions[enrollment.id].get(f"can_{key}")]
        allowed_actions[enrollment.id]["primary"] = valid_keys[0] if valid_keys else None
        allowed_actions[enrollment.id]["contextual"] = valid_keys[1:] if len(valid_keys) > 1 else []

    # Compute requirement summaries per progress semester
    requirement_summaries = {}
    for prog in progress_detail["semesters"]:
        result = aggregate_semester_scores(student, prog.semester)
        requirement_summaries[prog.semester.id] = result

    grade_components = SemesterGradeComponent.objects.filter(
        cohort__in=[e.cohort_id for e in enrollments],
        active=True,
    ).select_related("semester")

    grade_items = StudentGradeItem.objects.filter(student=student)
    if cohort_filter:
        grade_items = grade_items.filter(component__cohort_id__in=allowed_cohort_ids)
    grade_items = grade_items.select_related("component__semester", "component__cohort", "diploma_enrollment")

    rejoin_plans = SemesterRejoinPlan.objects.filter(student=student)
    if cohort_filter:
        rejoin_plans = rejoin_plans.filter(from_enrollment__cohort_id__in=allowed_cohort_ids)
    rejoin_plans = rejoin_plans.order_by("-created_at")

    ctx = {
        "workflow": "student_detail",
        "student": student,
        "is_admin_user": is_admin(request.user),
        "enrollments": enrollments,
        "semesters_by_enrollment": semesters_by_enrollment,
        "allowed_actions": allowed_actions,
        "progress_semesters": progress_detail["semesters"],
        "current_progress": current_progress,
        "requirement_summaries": requirement_summaries,
        "decisions": progress_detail["decisions"],
        "eligibility": eligibility,
        "completion": completion,
        "certificates": certificates,
        "grade_components": grade_components,
        "grade_items": grade_items,
        "rejoin_plans": rejoin_plans,
        "breadcrumb_items": [
            ({"label": _("Assessments"), "href": reverse("assessments:admin_list")} if not is_admin(request.user) else {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")}),
            {"label": str(student)},
        ],
    }
    return render(request, "ui/pages/admin/progression_student_detail.html", {**ctx, "title": _("Student progression")})


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
        evaluation = finalize_semester_evaluation(enrollment, semester, actor=request.user)
        messages.success(
            request,
            _("Semester evaluated: %(result)s (%(percent)s%%).")
            % {"result": evaluation.get_result_display(), "percent": evaluation.overall_percent},
        )
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
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
        promote_semester(
            enrollment, semester,
            actor=request.user, reason=reason, automatic=False,
        )
        messages.success(request, _("Student promoted."))
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_promote_academic_year(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment = get_object_or_404(DiplomaEnrollment, id=request.POST.get("enrollment_id"), student=student)
    from_semester = get_object_or_404(Semester, id=request.POST.get("from_semester_id"))
    to_semester = get_object_or_404(Semester, id=request.POST.get("to_semester_id"))
    try:
        promote_academic_year(
            student, enrollment, from_semester, to_semester,
            actor=request.user, reason=request.POST.get("reason", ""),
        )
        messages.success(request, _("Student promoted to the next academic year."))
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
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before the final decision."))
    else:
        try:
            resolve_final_progression(
                evaluation, actor=request.user,
                final_result=SEMESTER_EVALUATION_FAIL, reason=reason,
            )
            messages.success(request, _("Final progression resolved as Do Not Continue."))
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

    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if new_status not in {SEMESTER_EVALUATION_PASS, SEMESTER_EVALUATION_FAIL}:
        messages.error(request, _("Invalid final progression result."))
    elif evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before the final decision."))
    else:
        try:
            resolve_final_progression(
                evaluation, actor=request.user, final_result=new_status, reason=reason,
            )
            messages.success(request, _("Final progression decision saved."))
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
    except ObjectDoesNotExist:
        messages.error(request, _("No progress record found for this semester."))
        return redirect("progression:admin_student_detail", student_id=student.id)
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
# Diploma Progression Rule CRUD
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_diploma_rule_edit(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    rule, _created = DiplomaProgressionRule.objects.get_or_create(
        diploma=diploma,
        defaults={
            "is_active": True,
            "semester_pass_percent": Decimal("50.00"),
            "final_exam_pass_percent": Decimal("50.00"),
            "require_all_semesters_passed": True,
            # Kept on the model for historical rows; the deprecated separate
            # Diploma final-exam workflow is not part of new progression.
            "require_final_exam": False,
            "auto_promote_enabled": False,
            "allow_admin_override": True,
            "allow_retake": True,
            "max_retake_count": 0,
            "created_by": request.user,
        },
    )
    if request.method == "POST":
        errors = []
        semester_pass = request.POST.get("semester_pass_percent", "").strip()
        max_retake = request.POST.get("max_retake_count", "0").strip()
        try:
            parsed = Decimal(semester_pass) if semester_pass else Decimal("50.00")
            if not parsed.is_finite() or parsed < 0 or parsed > 100:
                errors.append(_("Semester pass percent must be between 0 and 100."))
            else:
                rule.semester_pass_percent = parsed
        except (ValueError, TypeError):
            errors.append(_("Invalid semester pass percent."))
        try:
            parsed = int(max_retake) if max_retake else 0
            if parsed < 0:
                errors.append(_("Max retake count cannot be negative."))
            else:
                rule.max_retake_count = parsed
        except (ValueError, TypeError):
            errors.append(_("Invalid max retake count."))
        rule.require_all_semesters_passed = request.POST.get("require_all_semesters_passed") == "1"
        rule.auto_promote_enabled = request.POST.get("auto_promote_enabled") == "1"
        rule.allow_admin_override = request.POST.get("allow_admin_override") == "1"
        rule.allow_retake = request.POST.get("allow_retake") == "1"
        if not errors:
            try:
                # S2-18/S2-19: save + full_clean() + bounded reevaluation are
                # one atomic, actor-attributed step (never a hidden signal).
                save_progression_rule(rule, request.user)
                messages.success(request, _("Progression rule updated."))
                return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
            except (ValueError, ValidationError) as exc:
                errors.append(str(exc))
        for err in errors:
            messages.error(request, err)
    ctx = {
        "workflow": "diploma_rule_edit",
        "diploma": diploma,
        "rule": rule,
        "breadcrumb_items": [
            {"label": _("Academics"), "href": reverse("academics:admin_diploma_list")},
            {"label": diploma.title, "href": reverse("academics:admin_diploma_detail", args=[diploma.id])},
            {"label": _("Progression Rule")},
        ],
    }
    return render(request, "ui/pages/admin/progression_rule_form.html", {**ctx, "title": _("Progression rule")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_evaluation_config_edit(request, diploma_id, cohort_id, semester_id):
    """Set the Attendance/Homework/Semester Exam weights + minimums for one cohort/semester."""
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    config = SemesterEvaluationConfig.objects.filter(
        cohort=cohort, semester=semester,
    ).first()
    if request.method == "POST":
        form = SemesterEvaluationSettingsForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save(commit=False)
            config.cohort = cohort
            config.semester = semester
            config.status = EVALUATION_CONFIG_STATUS_ACTIVE
            if config.created_by_id is None:
                config.created_by = request.user
            config.updated_by = request.user
            config.save()
            messages.success(request, _("Evaluation settings updated."))
            return redirect(
                "progression:admin_cohort_semester_progression",
                diploma_id=diploma.id, cohort_id=cohort.id, semester_id=semester.id,
            )
    else:
        if config is None:
            form = SemesterEvaluationSettingsForm(initial={
                "attendance_weight_percent": Decimal("20"),
                "homework_weight_percent": Decimal("30"),
                "semester_exam_weight_percent": Decimal("50"),
                "attendance_min_percent": Decimal("0"),
                "homework_min_percent": Decimal("0"),
                "semester_exam_min_percent": Decimal("0"),
                "attendance_required_sessions": 0,
                "overall_min_percent": Decimal("50"),
            })
        else:
            form = SemesterEvaluationSettingsForm(instance=config)
    ctx = {
        "diploma": diploma,
        "cohort": cohort,
        "semester": semester,
        "form": form,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": diploma.title, "href": reverse("progression:admin_cohort_list", args=[diploma.id])},
            {"label": cohort.name, "href": reverse("progression:admin_semester_list", args=[diploma.id, cohort.id])},
            {"label": semester.title, "href": reverse("progression:admin_cohort_semester_progression", args=[diploma.id, cohort.id, semester.id])},
            {"label": _("Evaluation settings")},
        ],
    }
    return render(request, "ui/pages/admin/progression_evaluation_settings.html", {**ctx, "title": _("Evaluation settings")})


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
        "workflow": "component_list",
        "cohort": cohort,
        "semester": semester,
        "components": components,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Grade Components")},
        ],
    }
    return render(request, "ui/pages/admin/progression_component_list.html", {**ctx, "title": _("Progression components")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_grade_sync(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    try:
        result = sync_all_grade_items(enrollment, semester, actor=request.user)
        synced = sum(1 for v in result.values() if v is not None)
        messages.success(request, _("Synced {count} grade item(s).").format(count=synced))
    except Exception as exc:
        messages.error(request, str(exc))
    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect("progression:admin_student_detail", student_id=enrollment.student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_evaluation(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    try:
        result = evaluate_semester_evaluation(enrollment, semester)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)
    ctx = {
        "workflow": "semester_evaluation",
        "enrollment": enrollment,
        "semester": semester,
        "result": result,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Semester Evaluation")},
        ],
    }
    return render(request, "ui/pages/admin/progression_semester_evaluation.html", {**ctx, "title": _("Semester evaluation")})


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
    return render(request, "ui/pages/student/progression.html", {**ctx, "title": _("Progress")})


# ---------------------------------------------------------------------------
# Interview workflow
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_request_interview(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)

    if request.method == "POST":
        notes = request.POST.get("notes", "")
        evaluation = SemesterEvaluation.objects.filter(
            diploma_enrollment=enrollment, semester=semester,
        ).first()
        if evaluation is None:
            messages.error(request, _("Finalize the semester evaluation before requesting an interview."))
        else:
            try:
                request_final_interview(evaluation, actor=request.user, notes=notes)
                messages.success(request, _("Interview requested."))
            except ValueError as exc:
                messages.error(request, str(exc))
        if request.headers.get("HX-Request"):
            return _render_semester_row(request, enrollment, semester)
        next_url = _safe_redirect_url(request, request.POST.get("next") or request.GET.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    try:
        result = evaluate_semester_evaluation(enrollment, semester)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    ctx = {
        "workflow": "interview_request",
        "enrollment": enrollment,
        "semester": semester,
        "result": result,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Request Interview")},
        ],
    }
    return render(request, "ui/pages/admin/progression_interview_request.html", {**ctx, "title": _("Interview request")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_confirm_pass(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    notes = request.POST.get("notes", "")

    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before confirming the pass."))
    else:
        try:
            resolve_final_progression(
                evaluation, actor=request.user,
                final_result=SEMESTER_EVALUATION_PASS, reason=notes,
            )
            messages.success(request, _("Final progression resolved as Pass."))
        except ValueError as exc:
            messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect("progression:admin_student_detail", student_id=enrollment.student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_interview_decision(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)

    if request.method == "POST":
        decision = request.POST.get("decision")
        notes = request.POST.get("notes", "")
        evaluation = SemesterEvaluation.objects.filter(
            diploma_enrollment=enrollment, semester=semester,
        ).first()
        if evaluation is None:
            messages.error(request, _("Finalize the semester evaluation before completing the interview."))
        elif decision not in (INTERVIEW_DECISION_PASS, INTERVIEW_DECISION_FAIL):
            messages.error(request, _("Invalid decision."))
            return redirect("progression:admin_interview_decision", enrollment_id=enrollment.id, semester_id=semester.id)
        else:
            try:
                complete_final_interview(
                    evaluation, actor=request.user,
                    decision=INTERVIEW_DECISION_PASS if decision == INTERVIEW_DECISION_PASS else INTERVIEW_DECISION_FAIL,
                    details=notes,
                )
                messages.success(request, _("Interview completed."))
            except ValueError as exc:
                messages.error(request, str(exc))
        next_url = _safe_redirect_url(request, request.POST.get("next") or request.GET.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect("progression:admin_student_detail", student_id=enrollment.student.id)

    ctx = {
        "workflow": "interview_decision",
        "enrollment": enrollment,
        "semester": semester,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Interview Decision")},
        ],
    }
    return render(request, "ui/pages/admin/progression_interview_decision.html", {**ctx, "title": _("Interview decision")})


# ---------------------------------------------------------------------------
# HTMX inline interview actions
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_interview_inline_pass(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before completing the interview."))
    else:
        try:
            complete_final_interview(evaluation, actor=request.user, decision=INTERVIEW_DECISION_PASS)
            messages.success(request, _("Interview passed."))
        except ValueError as exc:
            messages.error(request, str(exc))
    if request.headers.get("HX-Request"):
        return _render_semester_row(request, enrollment, semester)
    return redirect("progression:admin_cohort_semester_progression", diploma_id=enrollment.cohort.diploma_id, cohort_id=enrollment.cohort_id, semester_id=semester.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_interview_inline_fail(request, enrollment_id, semester_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    semester = get_object_or_404(Semester, id=semester_id)
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment, semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before completing the interview."))
    else:
        try:
            complete_final_interview(evaluation, actor=request.user, decision=INTERVIEW_DECISION_FAIL)
            messages.success(request, _("Interview failed."))
        except ValueError as exc:
            messages.error(request, str(exc))
    if request.headers.get("HX-Request"):
        return _render_semester_row(request, enrollment, semester)
    return redirect("progression:admin_cohort_semester_progression", diploma_id=enrollment.cohort.diploma_id, cohort_id=enrollment.cohort_id, semester_id=semester.id)


def _render_semester_row(request, enrollment, semester):
    cohort = enrollment.cohort
    try:
        result = evaluate_semester_components(enrollment, cohort, semester)
    except ValueError:
        result = None
    progress = StudentSemesterProgress.objects.filter(diploma_enrollment=enrollment, semester=semester).first()
    grade_items = StudentGradeItem.objects.filter(
        diploma_enrollment=enrollment,
        component__semester=semester,
        component__cohort=cohort,
    ).select_related("component")
    gi_by_type = {gi.component.component_type: gi for gi in grade_items}
    interview = PromotionDecision.objects.filter(
        diploma_enrollment=enrollment,
        from_semester=semester,
        decision_type__in=[
            PROMOTION_DECISION_REQUEST_INTERVIEW,
            PROMOTION_DECISION_INTERVIEW_PASS,
            PROMOTION_DECISION_INTERVIEW_FAIL,
        ],
    ).order_by("-decided_at").first()
    row = {
        "enrollment": enrollment,
        "student": enrollment.student,
        "progress": progress,
        "result": result,
        "semester_exam": gi_by_type.get("semester_exam"),
        "attendance": gi_by_type.get("attendance"),
        "homework_assessment": gi_by_type.get("homework_assessment"),
        "interview": interview,
    }
    ctx = {
        "row": row,
        "diploma": cohort.diploma,
        "cohort": cohort,
        "semester": semester,
    }
    return render(request, "ui/fragments/admin/_semester_table_row.html", ctx)


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
    status = request.GET.get("status", "")
    if status:
        plans = plans.filter(status=status)
    ctx = {
        "workflow": "rejoin_list",
        "plans": plans,
        "current_status": status,
        "rejoin_status_choices": REJOIN_STATUS_CHOICES,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Rejoin Plans")},
        ],
    }
    return render(request, "ui/pages/admin/progression_rejoin_list.html", {**ctx, "title": _("Rejoin plans")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_rejoin_eligibility(request):
    """Dedicated Rejoin Eligibility page (P4-02): only immediate-next/same-semester rows."""
    page_number = safe_int(request.GET.get("page")) or 1
    _sample_rows, total = get_rejoin_eligibility_rows(offset=0, limit=0)
    paginator = Paginator(range(total), 25)
    page_obj = paginator.get_page(page_number)
    rows, _total = get_rejoin_eligibility_rows(
        offset=page_obj.start_index() - 1 if total else 0,
        limit=25,
    )
    page_obj.object_list = rows
    pagination_qs = request.GET.copy()
    pagination_qs.pop("page", None)
    pagination_qs = pagination_qs.urlencode()
    ctx = {
        "workflow": "rejoin_eligibility",
        "rows": page_obj,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Rejoin Eligibility")},
        ],
    }
    return render(request, "ui/pages/admin/progression_rejoin_eligibility.html", {**ctx, "title": _("Rejoin Eligibility")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_create_rejoin_plan(request, enrollment_id):
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if request.method == "POST":
        semester_id = request.POST.get("semester_id")
        notes = request.POST.get("notes", "")
        if not semester_id:
            messages.error(request, _("Semester is required."))
        else:
            try:
                semester = get_object_or_404(Semester, id=semester_id)
                create_rejoin_plan(
                    enrollment.student,
                    enrollment,
                    semester,
                    actor=request.user,
                    notes=notes,
                )
                messages.success(request, _("Rejoin plan created."))
                return redirect("progression:admin_rejoin_plan_list")
            except ValueError as exc:
                messages.error(request, str(exc))
    eligible_outcomes = FinalProgressionOutcome.objects.filter(
        diploma_enrollment=enrollment,
        status=FINAL_PROGRESSION_STATUS_RESOLVED,
        final_result=SEMESTER_EVALUATION_FAIL,
    ).select_related("semester")
    requested_semester_id = safe_int(request.GET.get("semester_id"))
    selected_outcome = (
        eligible_outcomes.filter(semester_id=requested_semester_id).first()
        if requested_semester_id else eligible_outcomes.first()
    )
    selected_semester = selected_outcome.semester if selected_outcome else None
    target_cohort = get_immediate_next_cohort(enrollment.cohort) if selected_semester else None
    transition_blocked = (
        target_cohort is None
        or SemesterRejoinPlan.objects.filter(
            student=enrollment.student,
            status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE],
        ).exists()
        or DiplomaEnrollment.objects.filter(
            student=enrollment.student,
            cohort=target_cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).exists()
        or SemesterEvaluation.objects.filter(
            diploma_enrollment__cohort=target_cohort,
            semester=selected_semester,
        ).exists()
    ) if selected_semester else True
    if transition_blocked:
        selected_semester = None
        target_cohort = None
    ctx = {
        "workflow": "rejoin_create",
        "enrollment": enrollment,
        "selected_semester": selected_semester,
        "target_cohort": target_cohort,
        "breadcrumb_items": [
            {"label": _("Progression Dashboard"), "href": reverse("progression:admin_dashboard")},
            {"label": _("Rejoin Plans"), "href": reverse("progression:admin_rejoin_plan_list")},
            {"label": _("Create Rejoin Plan")},
        ],
    }
    return render(request, "ui/pages/admin/progression_rejoin_create.html", {**ctx, "title": _("Rejoin plan")})


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
