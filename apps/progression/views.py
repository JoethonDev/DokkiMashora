"""Views for progression management (admin and student)."""

import csv
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_RESTRICTED
from apps.academics.models import (
    Cohort,
    Diploma,
    DiplomaEnrollment,
    ModeratorAssignment,
    ModeratorStudentAssignment,
    Semester,
)
from apps.audit.constants import (
    ACTION_COMPLETED,
    ACTION_GRADUATED,
    ACTION_LOCK,
    ACTION_PROGRESSION_CONFIG_UPDATED,
    ACTION_PROGRESSION_GRADE_SYNCED,
    ACTION_RETAKE,
    ACTION_UNLOCK,
    SECTION_PROGRESSION,
)
from apps.audit.services import record_audit_event
from apps.certificates.models import GeneratedCertificate
from apps.core.htmx import is_htmx
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
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_REQUESTED,
    REJOIN_STATUS_ACTIVE,
    REJOIN_STATUS_CANCELLED,
    REJOIN_STATUS_CHOICES,
    REJOIN_STATUS_PLANNED,
    SEMESTER_EVALUATION_FAIL,
    SEMESTER_EVALUATION_PASS,
    SEMESTER_PROGRESS_COMPLETED,
    SEMESTER_PROGRESS_FAILED,
    SEMESTER_PROGRESS_IN_PROGRESS,
    SEMESTER_PROGRESS_LOCKED,
    SEMESTER_PROGRESS_PASSED,
    SEMESTER_PROGRESS_PASSED_CONFIRMED,
    SEMESTER_PROGRESS_PROMOTED,
)
from .forms import DiplomaProgressionRuleForm, SemesterEvaluationSettingsForm
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
    DEFAULT_ALLOW_MISSING_OPTIONAL_ASSESSMENTS,
    DEFAULT_REQUIRE_ALL_REQUIRED_ASSESSMENTS,
    DEFAULT_SEMESTER_PASS_PERCENT,
    activate_rejoin_plan,
    assign_retake,
    cancel_rejoin_plan,
    clear_retake,
    complete_final_interview,
    create_rejoin_plan,
    evaluate_semester_evaluation,
    exceptional_promote_semester,
    finalize_semester_evaluation,
    get_immediate_next_cohort,
    get_rejoin_eligibility_rows,
    get_semester_progression_rows,
    get_student_progress_detail,
    lock_student,
    mark_completed,
    mark_graduated,
    promote_semester,
    reevaluate_semester_evaluation,
    refresh_finalized_semester_evaluations,
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


_EVALUATION_CONFIG_AUDIT_FIELDS = (
    "attendance_weight_percent",
    "homework_weight_percent",
    "semester_exam_weight_percent",
    "attendance_min_percent",
    "attendance_required_sessions",
    "homework_min_percent",
    "semester_exam_min_percent",
    "overall_min_percent",
    "status",
)


def _evaluation_config_audit_snapshot(config) -> dict:
    """JSON-safe before/after snapshot of one cohort/semester evaluation config."""
    snapshot = {}
    for field in _EVALUATION_CONFIG_AUDIT_FIELDS:
        value = getattr(config, field)
        snapshot[field] = str(value) if isinstance(value, Decimal) else value
    return snapshot


def _annotate_last_semester_completion_actions(diploma, semester, rows):
    """Expose completion actions only on the diploma's final semester row.

    The flags mirror ``mark_requirements_met``: every diploma semester must have
    a passed/promoted/completed progress row, with no lock or retake, and the
    enrollment must still be active without an existing completion.
    """
    for row in rows:
        row["can_mark_graduate"] = False
        row["can_mark_complete"] = False
    if not rows:
        return

    last_semester_id = (
        Semester.objects.filter(diploma=diploma).order_by("-order").values_list("id", flat=True).first()
    )
    if semester.id != last_semester_id:
        return

    enrollment_ids = [row["enrollment"].id for row in rows]
    semester_ids = list(Semester.objects.filter(diploma=diploma).values_list("id", flat=True))
    progress_by_enrollment = {}
    for item in StudentSemesterProgress.objects.filter(
        diploma_enrollment_id__in=enrollment_ids,
    ).values("diploma_enrollment_id", "semester_id", "status", "retake_required"):
        progress_by_enrollment.setdefault(item["diploma_enrollment_id"], {})[item["semester_id"]] = item

    completed_enrollment_ids = set(
        StudentDiplomaCompletion.objects.filter(
            diploma_enrollment_id__in=enrollment_ids,
            status__in=(DIPLOMA_COMPLETION_COMPLETED, DIPLOMA_COMPLETION_GRADUATED),
        ).values_list("diploma_enrollment_id", flat=True)
    )
    ready_statuses = {
        SEMESTER_PROGRESS_PASSED,
        SEMESTER_PROGRESS_PROMOTED,
        SEMESTER_PROGRESS_COMPLETED,
    }
    for row in rows:
        enrollment = row["enrollment"]
        progress = progress_by_enrollment.get(enrollment.id, {})
        all_semesters_passed = bool(semester_ids) and all(
            progress.get(semester_id, {}).get("status") in ready_statuses for semester_id in semester_ids
        )
        has_lock = any(item.get("status") == SEMESTER_PROGRESS_LOCKED for item in progress.values())
        has_retake = any(item.get("retake_required") for item in progress.values())
        can_complete = (
            enrollment.status == ENROLLMENT_STATUS_ACTIVE
            and all_semesters_passed
            and not has_lock
            and not has_retake
            and enrollment.id not in completed_enrollment_ids
        )
        row["can_mark_graduate"] = can_complete
        row["can_mark_complete"] = can_complete


def _progression_breadcrumbs(*, diploma=None, cohort=None, semester=None, leaf=None):
    """Build the canonical Diploma → Cohort → Semester progression trail."""
    items = []
    if diploma is not None:
        items.append(
            {
                "label": diploma.title,
                "href": reverse("academics:admin_diploma_detail", args=[diploma.id]),
            }
        )
    if cohort is not None:
        items.append({"label": cohort.name})
    if semester is not None:
        items.append(
            {
                "label": semester.title,
                "href": reverse(
                    "progression:admin_cohort_semester_progression",
                    args=[diploma.id, cohort.id, semester.id],
                ),
            }
        )
    if leaf is not None:
        items.append({"label": leaf})
    return items


def _progression_back_url(*, diploma=None, cohort=None, semester=None):
    if diploma is None:
        return reverse("academics:admin_diploma_list")
    if semester is None:
        return reverse("academics:admin_diploma_detail", args=[diploma.id])
    return reverse(
        "progression:admin_cohort_semester_progression",
        args=[diploma.id, cohort.id, semester.id],
    )


def _get_progression_context(diploma_id, cohort_id, semester_id):
    """Resolve and validate one complete Diploma → Cohort → Semester context."""
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    return diploma, cohort, semester


def _get_student_progression_target(
    request,
    student,
    *,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    """Resolve a student/enrollment/semester tuple from URL context or legacy form data."""
    route_values = (diploma_id, cohort_id, semester_id, enrollment_id)
    if any(value is not None for value in route_values):
        if any(value is None for value in route_values):
            raise Http404
        _diploma, cohort, semester = _get_progression_context(
            diploma_id,
            cohort_id,
            semester_id,
        )
        enrollment = get_object_or_404(
            DiplomaEnrollment,
            id=enrollment_id,
            student=student,
            cohort=cohort,
        )
        return enrollment, semester

    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    semester_id = safe_int(request.POST.get("semester_id"))
    if enrollment_id is None or semester_id is None:
        raise Http404

    enrollment = get_object_or_404(
        DiplomaEnrollment,
        id=enrollment_id,
        student=student,
    )
    semester = get_object_or_404(
        Semester,
        id=semester_id,
        diploma_id=enrollment.cohort.diploma_id,
    )
    return enrollment, semester


def _progression_student_detail_url(
    student_id,
    *,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    if all(value is not None for value in (diploma_id, cohort_id, semester_id, enrollment_id)):
        return reverse(
            "progression:admin_contextual_student_detail",
            args=[diploma_id, cohort_id, semester_id, student_id, enrollment_id],
        )
    return reverse("progression:admin_student_detail", args=[student_id])


def _get_enrollment_semester_target(
    enrollment_id,
    semester_id,
    *,
    diploma_id=None,
    cohort_id=None,
):
    """Resolve an enrollment/semester pair and enforce URL academic scope."""
    if diploma_id is not None or cohort_id is not None:
        if diploma_id is None or cohort_id is None:
            raise Http404
        diploma = get_object_or_404(Diploma, id=diploma_id)
        cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
        enrollment = get_object_or_404(
            DiplomaEnrollment.objects.select_related("student__user", "cohort__diploma"),
            id=enrollment_id,
            cohort=cohort,
        )
        semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
        return enrollment, semester

    enrollment = get_object_or_404(
        DiplomaEnrollment.objects.select_related("student__user", "cohort__diploma"),
        id=enrollment_id,
    )
    semester = get_object_or_404(
        Semester,
        id=semester_id,
        diploma_id=enrollment.cohort.diploma_id,
    )
    return enrollment, semester


def _get_rejoin_report_context(
    request,
    *,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
):
    """Resolve either a canonical URL context or a validated report filter."""
    path_values = (diploma_id, cohort_id, semester_id)
    is_context_route = any(value is not None for value in path_values)
    if is_context_route:
        if any(value is None for value in path_values):
            raise Http404
        diploma, cohort, semester = _get_progression_context(*path_values)
    else:
        parsed = {}
        for key in ("diploma_id", "cohort_id", "semester_id"):
            raw_value = request.GET.get(key, "")
            parsed[key] = safe_int(raw_value)
            if raw_value and (parsed[key] is None or parsed[key] < 1):
                raise Http404
        diploma = get_object_or_404(Diploma, id=parsed["diploma_id"]) if parsed["diploma_id"] else None
        cohort = (
            Cohort.objects.filter(
                id=parsed["cohort_id"],
                diploma=diploma,
            ).first()
            if diploma and parsed["cohort_id"]
            else None
        )
        semester = (
            Semester.objects.filter(
                id=parsed["semester_id"],
                diploma=diploma,
            ).first()
            if diploma and parsed["semester_id"]
            else None
        )
        context_filter_error = bool(
            (parsed["cohort_id"] and cohort is None)
            or (parsed["semester_id"] and semester is None)
            or ((parsed["cohort_id"] or parsed["semester_id"]) and diploma is None)
        )

    if is_context_route:
        context_filter_error = False

    context = {
        "context_diploma": diploma,
        "context_cohort": cohort,
        "context_semester": semester,
        "context_diploma_id": diploma.id if diploma else "",
        "context_cohort_id": cohort.id if cohort else "",
        "context_semester_id": semester.id if semester else "",
        "context_exact": cohort is not None and semester is not None,
        "context_route": is_context_route,
        "context_filter_error": context_filter_error,
        "diploma_options": Diploma.objects.order_by("title", "id"),
        # The child selectors are populated only for the selected parent.
        # This keeps the initial global reports small; changing the diploma
        # reloads the GET form with only that diploma's cohorts/semesters.
        "cohort_options": (
            Cohort.objects.filter(diploma=diploma).order_by("start_date", "id")
            if diploma
            else Cohort.objects.none()
        ),
        "semester_options": (
            Semester.objects.filter(diploma=diploma).order_by("order", "id")
            if diploma
            else Semester.objects.none()
        ),
    }
    context["context_back_url"] = (
        _progression_back_url(diploma=diploma, cohort=cohort, semester=semester) if diploma else ""
    )
    return context


# ---------------------------------------------------------------------------
# Admin semester progression
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_cohort_semester_progression(request, diploma_id, cohort_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    enrollments = (
        DiplomaEnrollment.objects.filter(
            cohort=cohort,
            status="active",
        )
        .select_related("student__user")
        .order_by("student__user__full_name")
    )
    page_obj, pagination_qs = paginate(request, enrollments, per_page=25)
    # P4-01: set-wise projection from immutable evaluation/final-outcome records.
    table_rows = get_semester_progression_rows(cohort, semester, list(page_obj.object_list))
    workspace_url = reverse(
        "progression:admin_cohort_semester_progression",
        args=[diploma.id, cohort.id, semester.id],
    )
    for row in table_rows:
        row["workspace_url"] = workspace_url
    _annotate_last_semester_completion_actions(diploma, semester, table_rows)

    ctx = {
        "diploma": diploma,
        "cohort": cohort,
        "semester": semester,
        "table_rows": table_rows,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "back_url": _progression_back_url(diploma=diploma, cohort=cohort),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=diploma,
            cohort=cohort,
            semester=semester,
        ),
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
    enrollments = list(
        DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related("student")
    )
    ok = 0
    fail = 0
    failures = []
    with transaction.atomic():
        for enrollment in enrollments:
            try:
                sync_all_grade_items(enrollment, semester, actor=request.user)
                ok += 1
            except (ValueError, ValidationError) as exc:
                logger.warning("Sync failed for enrollment %s: %s", enrollment.id, exc)
                fail += 1
                failures.append(f"{enrollment.student}: {exc}")
        record_audit_event(
            actor=request.user,
            action=ACTION_PROGRESSION_GRADE_SYNCED,
            target_type="semester",
            target_id=str(semester.id),
            section=SECTION_PROGRESSION,
            target_label=(f"{semester.diploma.title} · {cohort.name} · {semester.title}"),
            metadata={
                "diploma_id": semester.diploma_id,
                "diploma_title": semester.diploma.title,
                "cohort_id": cohort.id,
                "cohort_name": cohort.name,
                "semester_id": semester.id,
                "semester_title": semester.title,
                "operation": "semester_grade_sync",
                "synced_students": ok,
                "failed_students": fail,
            },
        )
    message = _("Synced {ok} student(s). {fail} failed.").format(ok=ok, fail=fail)
    if failures:
        messages.warning(request, f"{message} " + _("Failures: ") + "; ".join(failures))
    else:
        messages.success(request, message)
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=cohort.diploma_id,
        cohort_id=cohort.id,
        semester_id=semester.id,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_evaluate_all(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(
        DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related(
            "student", "cohort__diploma"
        )
    )
    ok = 0
    fail = 0
    failures = []
    # P2-06: per-enrollment atomic finalization (the service owns the lock);
    # failures are reported individually, never rolled back together.
    for enrollment in enrollments:
        try:
            finalize_semester_evaluation(enrollment, semester, actor=request.user)
            ok += 1
        except (ValueError, ValidationError) as exc:
            logger.warning("Evaluation failed for enrollment %s: %s", enrollment.id, exc)
            fail += 1
            failures.append(f"{enrollment.student}: {exc}")
    message = _("Evaluated {ok} student(s). {fail} failed.").format(ok=ok, fail=fail)
    if failures:
        messages.warning(request, f"{message} " + _("Failures: ") + "; ".join(failures))
    else:
        messages.success(request, message)
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=cohort.diploma_id,
        cohort_id=cohort.id,
        semester_id=semester.id,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_reevaluate_all(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(
        DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related(
            "student", "cohort__diploma"
        )
    )
    ok = 0
    fail = 0
    failures = []
    for enrollment in enrollments:
        try:
            reevaluate_semester_evaluation(enrollment, semester, actor=request.user)
            ok += 1
        except (ValueError, ValidationError) as exc:
            logger.warning("Re-evaluation failed for enrollment %s: %s", enrollment.id, exc)
            fail += 1
            failures.append(f"{enrollment.student}: {exc}")
    message = _("Re-evaluated {ok} student(s). {fail} failed.").format(ok=ok, fail=fail)
    if failures:
        messages.warning(request, f"{message} " + _("Failures: ") + "; ".join(failures))
    else:
        messages.success(request, message)
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=cohort.diploma_id,
        cohort_id=cohort.id,
        semester_id=semester.id,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_semester_auto_promote(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(
        DiplomaEnrollment.objects.filter(
            cohort=cohort,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).select_related("student", "cohort__diploma")
    )
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
                enrollment,
                semester,
                actor=request.user,
                reason=_("Bulk auto-promote"),
                automatic=True,
            )
            promoted += 1
        except (ValueError, ValidationError) as exc:
            logger.warning("Auto-promote skipped for enrollment %s: %s", enrollment.id, exc)
            fail += 1
    messages.success(
        request, _("{promoted} student(s) promoted. {fail} failed.").format(promoted=promoted, fail=fail)
    )
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=cohort.diploma_id,
        cohort_id=cohort.id,
        semester_id=semester.id,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_export_csv(request, cohort_id, semester_id):
    cohort = get_object_or_404(Cohort, id=cohort_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma_id=cohort.diploma_id)
    enrollments = list(
        DiplomaEnrollment.objects.filter(cohort=cohort, status="active").select_related("student__user")
    )
    rows = get_semester_progression_rows(cohort, semester, enrollments)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="semester_{semester.id}_progression.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            _("Student name"),
            _("Semester exam percentage"),
            _("Attendance percentage"),
            _("Homework and assessment percentage"),
            _("Weighted total percentage"),
            _("Calculated result"),
            _("Final progression decision"),
            _("Interview status"),
            _("Evaluation state"),
        ]
    )

    def safe_csv_text(value):
        text = str(value or "")
        if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return f"'{text}"
        return text

    def percent_cell(value):
        return f"{value:.2f}%" if value is not None else ""

    for row in rows:
        evaluation = row["evaluation"]
        preview = row["preview"]
        outcome = row["outcome"]
        interview = row["interview"]
        components = row["components"] or {}
        if evaluation is not None:
            overall_percent = evaluation.overall_percent
            calculated_result = evaluation.get_result_display()
            evaluation_state = _("Finalized")
        elif preview is None:
            overall_percent = None
            calculated_result = _("Unavailable")
            evaluation_state = _("Unavailable")
        elif preview.get("blocked"):
            overall_percent = preview.get("overall_percent")
            calculated_result = _("Awaiting inputs")
            evaluation_state = _("Preview")
        else:
            overall_percent = preview.get("overall_percent")
            calculated_result = _("Pass") if preview.get("passed") else _("Fail")
            evaluation_state = _("Preview")

        if outcome is None:
            final_decision = _("Pending")
        elif outcome.status == FINAL_PROGRESSION_STATUS_RESOLVED and outcome.final_result:
            final_decision = outcome.get_final_result_display()
        else:
            final_decision = outcome.get_status_display()
        interview_status = _("Not requested")
        if interview is not None:
            interview_status = interview.get_status_display()
            if interview.outcome_result:
                interview_status = f"{interview_status} — {interview.get_outcome_result_display()}"
        writer.writerow(
            [
                safe_csv_text(row["student"]),
                percent_cell(components.get("semester_exam")),
                percent_cell(components.get("attendance")),
                percent_cell(components.get("homework")),
                percent_cell(overall_percent),
                calculated_result,
                final_decision,
                interview_status,
                evaluation_state,
            ]
        )
    return response


# ---------------------------------------------------------------------------
# Admin student detail
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_student_detail(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    context_route = any(value is not None for value in (diploma_id, cohort_id, semester_id, enrollment_id))
    if context_route and any(value is None for value in (diploma_id, cohort_id, semester_id, enrollment_id)):
        raise Http404
    student = _get_student_or_404(student_id)
    enrollments = DiplomaEnrollment.objects.filter(student=student).select_related(
        "cohort__diploma",
    )
    selected_enrollment = None
    selected_diploma = None
    selected_cohort = None
    selected_semester = None
    if context_route:
        selected_diploma, selected_cohort, selected_semester = _get_progression_context(
            diploma_id,
            cohort_id,
            semester_id,
        )
        selected_enrollment = get_object_or_404(
            enrollments,
            id=enrollment_id,
            cohort=selected_cohort,
        )
        enrollments = enrollments.filter(id=selected_enrollment.id)
    if not is_admin(request.user):
        mod_cohorts_qs = ModeratorAssignment.objects.filter(
            moderator=request.user,
            active=True,
        ).values("cohort_id")
        mod_students = ModeratorStudentAssignment.objects.filter(
            moderator=request.user,
            active=True,
        ).values("student_id")
        enrollments = enrollments.filter(Q(cohort_id__in=mod_cohorts_qs) | Q(student_id__in=mod_students))
        mod_wide = ModeratorAssignment.objects.filter(
            moderator=request.user,
            active=True,
            cohort__isnull=True,
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
                moderator=request.user,
                student=student,
                active=True,
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
    cohort_filter = (
        {"diploma_enrollment__cohort_id__in": allowed_cohort_ids}
        if (context_route or not is_admin(request.user)) and allowed_cohort_ids
        else {}
    )
    progress_cohort_ids = (
        [selected_cohort.id] if context_route else allowed_cohort_ids if not is_admin(request.user) else None
    )
    progress_detail = get_student_progress_detail(student, cohort_ids=progress_cohort_ids)
    if selected_enrollment is not None:
        progress_detail["semesters"] = progress_detail["semesters"].filter(
            diploma_enrollment=selected_enrollment,
            semester=selected_semester,
        )
        progress_detail["decisions"] = PromotionDecision.objects.filter(
            student=student,
            diploma_enrollment=selected_enrollment,
            from_semester=selected_semester,
        ).order_by("-decided_at", "-id")[:20]
    current_progress = (
        progress_detail["semesters"].filter(semester=selected_semester).first()
        if selected_semester is not None
        else progress_detail["semesters"].last()
    )
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
        "course_enrollment__course_version__course",
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
    progress_rows = list(
        StudentSemesterProgress.objects.filter(
            diploma_enrollment_id__in=enrollment_ids,
        ).values("diploma_enrollment_id", "semester_id", "status", "retake_required")
    )
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
    ).values("diploma_enrollment_id", "semester_id", "result")
    evaluation_state = {
        (row["diploma_enrollment_id"], row["semester_id"]): row["result"] for row in evaluations
    }
    finalized_semesters = set(evaluation_state)

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
        superseded_at__isnull=True,
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
            student=student,
            status=ENROLLMENT_STATUS_ACTIVE,
        ).values_list("cohort_id", flat=True)
    )

    allowed_actions = {}
    for enrollment in enrollments:
        active = enrollment.status == ENROLLMENT_STATUS_ACTIVE
        locked = locked_by_enrollment.get(enrollment.id, False)
        retake_required = retake_by_enrollment.get(enrollment.id, False)
        all_diploma_semesters = semesters_by_enrollment[enrollment.id]
        diploma_semesters = (
            [semester for semester in all_diploma_semesters if semester.id == selected_semester.id]
            if selected_semester is not None
            else all_diploma_semesters
        )

        # A semester is "decision-ready" when its evaluation is finalized and the
        # outcome is still pending (not resolved, not blocked by a requested interview).
        promotion_targets = []
        demotion_targets = []
        exceptional_promotion_targets = []
        exceptional_demotion_targets = []
        evaluation_targets = []
        failed_targets = []
        interview_requested = False
        completed_statuses = {
            SEMESTER_PROGRESS_COMPLETED,
            SEMESTER_PROGRESS_PASSED,
            SEMESTER_PROGRESS_PASSED_CONFIRMED,
            SEMESTER_PROGRESS_PROMOTED,
        }
        entry_index = next(
            (
                index
                for index, semester in enumerate(all_diploma_semesters)
                if semester.id == enrollment.entry_semester_id
            ),
            0,
        )
        current_semester = next(
            (
                semester
                for semester in all_diploma_semesters[entry_index:]
                if progress_status_by_semester.get((enrollment.id, semester.id)) not in completed_statuses
            ),
            None,
        )
        for semester in diploma_semesters:
            key = (enrollment.id, semester.id)
            if key not in finalized_semesters:
                if semester == current_semester:
                    evaluation_targets.append(semester)
            else:
                status, final_result = outcome_state.get(key, (FINAL_PROGRESSION_STATUS_PENDING, None))
                if status == FINAL_PROGRESSION_STATUS_PENDING:
                    calculated_result = evaluation_state.get(key)
                    interview_status = interview_state.get(key)
                    if calculated_result == SEMESTER_EVALUATION_PASS:
                        promotion_targets.append(semester)
                        if interview_status == INTERVIEW_STATUS_COMPLETED:
                            exceptional_demotion_targets.append(semester)
                    elif calculated_result == SEMESTER_EVALUATION_FAIL:
                        demotion_targets.append(semester)
                        exceptional_promotion_targets.append(semester)
                if interview_state.get(key) == INTERVIEW_STATUS_REQUESTED:
                    interview_requested = True
                if status == FINAL_PROGRESSION_STATUS_RESOLVED and final_result == SEMESTER_EVALUATION_FAIL:
                    failed_targets.append(semester)

        retake_targets = [
            semester
            for semester in diploma_semesters
            if progress_status_by_semester.get((enrollment.id, semester.id)) == SEMESTER_PROGRESS_FAILED
        ]
        clear_retake_targets = [
            semester
            for semester in diploma_semesters
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
        if active and not enrollment.is_access_expired and failed_targets:
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
        all_semesters_passed = bool(all_diploma_semesters) and all(
            progress_status_by_semester.get((enrollment.id, s.id))
            in (SEMESTER_PROGRESS_PASSED, SEMESTER_PROGRESS_PROMOTED, SEMESTER_PROGRESS_COMPLETED)
            for s in all_diploma_semesters
        )
        mark_ready = active and not locked and not retake_required and not completed_or_graduated

        allowed_actions[enrollment.id] = {
            "can_evaluate": active and bool(evaluation_targets),
            "can_promote": active and bool(promotion_targets),
            "can_demote": active and bool(demotion_targets),
            "can_exceptional_promote": active and bool(exceptional_promotion_targets),
            "can_exceptional_demote": active and bool(exceptional_demotion_targets),
            "can_lock": active and not locked,
            "can_unlock": active and locked,
            "can_assign_retake": active and not retake_required and bool(retake_targets),
            "can_clear_retake": active and retake_required and bool(clear_retake_targets),
            "can_mark_complete": mark_ready and all_semesters_passed,
            "can_mark_graduate": mark_ready and all_semesters_passed,
            "can_generate_certificate": active
            and completed_or_graduated
            and enrollment.id not in active_certificate_enrollments,
            "can_create_rejoin_plan": rejoin_eligible,
            "interview_requested": interview_requested,
            "targets": {
                "evaluate": evaluation_targets,
                "promote": promotion_targets,
                "demote": demotion_targets,
                "exceptional_promote": exceptional_promotion_targets,
                "exceptional_demote": exceptional_demotion_targets,
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
            "exceptional_promote",
            "exceptional_demote",
            "evaluate",
            "lock",
            "unlock",
            "assign_retake",
            "clear_retake",
        ]
        if context_route:
            action_keys = [
                "mark_graduate",
                "mark_complete",
                "generate_certificate",
                "create_rejoin_plan",
                "promote",
                "demote",
                "exceptional_promote",
                "exceptional_demote",
                "evaluate",
                "assign_retake",
                "clear_retake",
            ]
        valid_keys = [key for key in action_keys if allowed_actions[enrollment.id].get(f"can_{key}")]
        allowed_actions[enrollment.id]["primary"] = valid_keys[0] if valid_keys else None
        allowed_actions[enrollment.id]["contextual"] = valid_keys[1:] if len(valid_keys) > 1 else []

    grade_components = SemesterGradeComponent.objects.filter(
        cohort__in=[e.cohort_id for e in enrollments],
        active=True,
    ).select_related("semester")
    if selected_enrollment is not None:
        grade_components = grade_components.filter(
            cohort=selected_cohort,
            semester=selected_semester,
        )

    grade_items = StudentGradeItem.objects.filter(student=student)
    if selected_enrollment is not None:
        grade_items = grade_items.filter(
            diploma_enrollment=selected_enrollment,
            component__semester=selected_semester,
        )
    elif cohort_filter:
        grade_items = grade_items.filter(component__cohort_id__in=allowed_cohort_ids)
    grade_items = grade_items.select_related("component__semester", "component__cohort", "diploma_enrollment")

    rejoin_plans = SemesterRejoinPlan.objects.filter(student=student)
    if selected_enrollment is not None:
        rejoin_plans = rejoin_plans.filter(
            from_enrollment=selected_enrollment,
            failed_semester=selected_semester,
        )
    elif cohort_filter:
        rejoin_plans = rejoin_plans.filter(from_enrollment__cohort_id__in=allowed_cohort_ids)
    rejoin_plans = rejoin_plans.order_by("-created_at")

    context_detail_url = (
        reverse(
            "progression:admin_contextual_student_detail",
            args=[
                selected_diploma.id,
                selected_cohort.id,
                selected_semester.id,
                student.id,
                selected_enrollment.id,
            ],
        )
        if context_route
        else ""
    )
    interview_decision_url = (
        reverse(
            "progression:admin_contextual_interview_decision",
            args=[
                selected_diploma.id,
                selected_cohort.id,
                selected_semester.id,
                selected_enrollment.id,
            ],
        )
        if context_route
        else ""
    )
    ctx = {
        "workflow": "student_detail",
        "student": student,
        "is_admin_user": is_admin(request.user),
        "is_contextual_detail": context_route,
        "selected_diploma": selected_diploma,
        "selected_cohort": selected_cohort,
        "selected_semester": selected_semester,
        "selected_enrollment": selected_enrollment,
        "context_detail_url": context_detail_url,
        "interview_decision_url": interview_decision_url,
        "back_url": (
            _progression_back_url(
                diploma=selected_diploma,
                cohort=selected_cohort,
                semester=selected_semester,
            )
            if context_route
            else reverse("academics:admin_diploma_list")
        ),
        "enrollments": enrollments,
        "semesters_by_enrollment": semesters_by_enrollment,
        "allowed_actions": allowed_actions,
        "progress_semesters": progress_detail["semesters"],
        "current_progress": current_progress,
        "decisions": progress_detail["decisions"],
        "eligibility": eligibility,
        "completion": completion,
        "certificates": certificates,
        "grade_components": grade_components,
        "grade_items": grade_items,
        "rejoin_plans": rejoin_plans,
        "breadcrumb_items": (
            _progression_breadcrumbs(
                diploma=selected_diploma,
                cohort=selected_cohort,
                semester=selected_semester,
                leaf=str(student),
            )
            if context_route
            else [
                (
                    {"label": _("Assessments"), "href": reverse("assessments:admin_list")}
                    if not is_admin(request.user)
                    else {"label": _("Diplomas"), "href": reverse("academics:admin_diploma_list")}
                ),
                {"label": str(student)},
            ]
        ),
    }
    return render(
        request, "ui/pages/admin/progression_student_detail.html", {**ctx, "title": _("Student progression")}
    )


# ---------------------------------------------------------------------------
# Admin actions (all POST)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_evaluate_semester(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )

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
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_promote(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    reason = request.POST.get("reason", "")
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )
    try:
        promote_semester(
            enrollment,
            semester,
            actor=request.user,
            reason=reason,
            automatic=False,
        )
        messages.success(request, _("Student promoted."))
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_exceptional_promote(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )
    try:
        exceptional_promote_semester(
            enrollment,
            semester,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, _("Exceptional promotion recorded."))
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_demote(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    reason = request.POST.get("reason", "")
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before the final decision."))
    else:
        try:
            resolve_final_progression(
                evaluation,
                actor=request.user,
                final_result=SEMESTER_EVALUATION_FAIL,
                reason=reason,
            )
            messages.success(request, _("Final progression resolved as Do Not Continue."))
        except ValueError as exc:
            messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_override(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    semester_id = safe_int(request.POST.get("semester_id"))
    new_status = request.POST.get("new_status")
    reason = request.POST.get("reason", "")

    if enrollment_id is None or semester_id is None:
        raise Http404
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, student=student)
    semester = get_object_or_404(Semester, id=semester_id)

    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if new_status not in {SEMESTER_EVALUATION_PASS, SEMESTER_EVALUATION_FAIL}:
        messages.error(request, _("Invalid final progression result."))
    elif evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before the final decision."))
    else:
        try:
            resolve_final_progression(
                evaluation,
                actor=request.user,
                final_result=new_status,
                reason=reason,
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
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(
        DiplomaEnrollment.objects.select_related("cohort__diploma"),
        id=enrollment_id,
        student=student,
    )

    try:
        lock_student(student, enrollment, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_LOCK,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {enrollment.cohort.diploma.title} · {enrollment.cohort.name}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment_id,
                "diploma_id": enrollment.cohort.diploma_id,
                "cohort_id": enrollment.cohort_id,
                "cohort_name": enrollment.cohort.name,
                "status": SEMESTER_PROGRESS_LOCKED,
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
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    reason = request.POST.get("reason", "")

    enrollment = get_object_or_404(
        DiplomaEnrollment.objects.select_related("cohort__diploma"),
        id=enrollment_id,
        student=student,
    )

    try:
        unlock_student(student, enrollment, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_UNLOCK,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {enrollment.cohort.diploma.title} · {enrollment.cohort.name}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment_id,
                "diploma_id": enrollment.cohort.diploma_id,
                "cohort_id": enrollment.cohort_id,
                "cohort_name": enrollment.cohort.name,
                "status_before": SEMESTER_PROGRESS_LOCKED,
                "status": SEMESTER_PROGRESS_IN_PROGRESS,
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
def admin_assign_retake(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    reason = request.POST.get("reason", "")
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )

    try:
        progress = assign_retake(student, enrollment, semester, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_RETAKE,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {semester.diploma.title} · "
                f"{enrollment.cohort.name} · {semester.title}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment.id,
                "semester_id": semester.id,
                "semester_title": semester.title,
                "diploma_id": semester.diploma_id,
                "reason": reason,
                "type": "assign",
                "status": progress.status,
                "retake_required": progress.retake_required,
                "retake_count": progress.retake_count,
            },
        )
        messages.success(request, _("Retake assigned."))
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_clear_retake(
    request,
    student_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
    enrollment_id=None,
):
    student = _get_student_or_404(student_id)
    reason = request.POST.get("reason", "")
    enrollment, semester = _get_student_progression_target(
        request,
        student,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
        enrollment_id=enrollment_id,
    )

    try:
        progress = clear_retake(student, enrollment, semester, actor=request.user, reason=reason)
        record_audit_event(
            actor=request.user,
            action=ACTION_RETAKE,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {semester.diploma.title} · "
                f"{enrollment.cohort.name} · {semester.title}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment.id,
                "semester_id": semester.id,
                "semester_title": semester.title,
                "diploma_id": semester.diploma_id,
                "reason": reason,
                "type": "clear",
                "status": progress.status,
                "retake_required": progress.retake_required,
                "retake_count": progress.retake_count,
            },
        )
        messages.success(request, _("Retake cleared."))
    except ObjectDoesNotExist:
        messages.error(request, _("No progress record found for this semester."))
        next_url = _safe_redirect_url(request, request.POST.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect(
            _progression_student_detail_url(
                student.id,
                diploma_id=diploma_id,
                cohort_id=cohort_id,
                semester_id=semester_id,
                enrollment_id=enrollment.id,
            )
        )
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
            enrollment_id=enrollment.id,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_mark_complete(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    notes = request.POST.get("notes", "")

    enrollment = get_object_or_404(
        DiplomaEnrollment.objects.select_related("cohort__diploma"),
        id=enrollment_id,
        student=student,
    )

    try:
        completion = mark_completed(student, enrollment, actor=request.user, notes=notes)
        record_audit_event(
            actor=request.user,
            action=ACTION_COMPLETED,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {enrollment.cohort.diploma.title} · {enrollment.cohort.name}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment_id,
                "diploma_id": enrollment.cohort.diploma_id,
                "cohort_id": enrollment.cohort_id,
                "status": completion.status,
                "completed_at": (completion.completed_at.isoformat() if completion.completed_at else None),
                "notes": notes,
                "type": "completed",
            },
        )
        messages.success(request, _("Student marked as completed."))
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect("progression:admin_student_detail", student_id=student.id)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_mark_graduate(request, student_id):
    student = _get_student_or_404(student_id)
    enrollment_id = safe_int(request.POST.get("enrollment_id"))
    notes = request.POST.get("notes", "")

    enrollment = get_object_or_404(
        DiplomaEnrollment.objects.select_related("cohort__diploma"),
        id=enrollment_id,
        student=student,
    )

    try:
        completion = mark_graduated(student, enrollment, actor=request.user, notes=notes)
        record_audit_event(
            actor=request.user,
            action=ACTION_GRADUATED,
            target_type="student",
            target_id=str(student.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{student.full_name} · {enrollment.cohort.diploma.title} · {enrollment.cohort.name}"
            ),
            subject_type="student",
            subject_id=str(student.id),
            subject_label=student.full_name,
            metadata={
                "enrollment_id": enrollment_id,
                "diploma_id": enrollment.cohort.diploma_id,
                "cohort_id": enrollment.cohort_id,
                "status": completion.status,
                "completed_at": (completion.completed_at.isoformat() if completion.completed_at else None),
                "graduated_at": (completion.graduated_at.isoformat() if completion.graduated_at else None),
                "notes": notes,
                "type": "graduated",
            },
        )
        messages.success(request, _("Student marked as graduated."))
    except ValueError as exc:
        messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
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
        is_active=True,
        defaults={
            "semester_pass_percent": DEFAULT_SEMESTER_PASS_PERCENT,
            "require_all_required_assessments": DEFAULT_REQUIRE_ALL_REQUIRED_ASSESSMENTS,
            "allow_missing_optional_assessments": DEFAULT_ALLOW_MISSING_OPTIONAL_ASSESSMENTS,
            "final_exam_pass_percent": Decimal("50.00"),
            "require_all_semesters_passed": True,
            "require_final_exam": False,
            "auto_promote_enabled": False,
            "allow_admin_override": True,
            "allow_retake": True,
            "max_retake_count": 0,
            "created_by": request.user,
        },
    )
    if request.method == "POST":
        form = DiplomaProgressionRuleForm(request.POST, instance=rule)
        if form.is_valid():
            try:
                rule = form.save(commit=False)
                save_progression_rule(rule, request.user)
                messages.success(request, _("Progression rule updated."))
                return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
            except (ValueError, ValidationError) as exc:
                form.add_error(None, str(exc))
        # form errors will be rendered via _field.html + non-field errors
        for err in form.non_field_errors():
            messages.error(request, err)
    else:
        form = DiplomaProgressionRuleForm(instance=rule)
    ctx = {
        "workflow": "diploma_rule_edit",
        "diploma": diploma,
        "rule": rule,
        "form": form,
        "breadcrumb_items": [
            {"label": _("Academics"), "href": reverse("academics:admin_diploma_list")},
            {"label": diploma.title, "href": reverse("academics:admin_diploma_detail", args=[diploma.id])},
            {"label": _("Diploma progression defaults")},
        ],
    }
    return render(
        request,
        "ui/pages/admin/progression_rule_form.html",
        {**ctx, "title": _("Diploma progression defaults")},
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_evaluation_config_edit(request, diploma_id, cohort_id, semester_id):
    """Set the Attendance/Homework/Semester Exam weights + minimums for one cohort/semester."""
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    config = SemesterEvaluationConfig.objects.filter(
        cohort=cohort,
        semester=semester,
    ).first()
    if request.method == "POST":
        config_before = (
            _evaluation_config_audit_snapshot(config) if config is not None and config.pk else None
        )
        if config is None:
            # cohort/semester are URL-scoped and excluded from the form, but
            # the model's clean() needs them before form.is_valid() runs.
            config = SemesterEvaluationConfig(cohort=cohort, semester=semester)
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
            record_audit_event(
                actor=request.user,
                action=ACTION_PROGRESSION_CONFIG_UPDATED,
                target_type="semester_evaluation_config",
                target_id=str(config.id),
                section=SECTION_PROGRESSION,
                target_label=f"{diploma.title} · {cohort.name} · {semester.title}",
                metadata={
                    "diploma_id": diploma.id,
                    "diploma_title": diploma.title,
                    "cohort_id": cohort.id,
                    "cohort_name": cohort.name,
                    "semester_id": semester.id,
                    "semester_title": semester.title,
                    "before": config_before,
                    "after": _evaluation_config_audit_snapshot(config),
                },
            )
            refresh = refresh_finalized_semester_evaluations(
                cohort,
                semester,
                actor=request.user,
            )
            message = _("Evaluation settings updated.")
            if refresh["refreshed"] or refresh["failures"]:
                message += " " + _("Re-evaluated {ok} student(s). {fail} failed.").format(
                    ok=refresh["refreshed"],
                    fail=len(refresh["failures"]),
                )
            if refresh["failures"]:
                messages.warning(
                    request,
                    f"{message} " + _("Failures: ") + "; ".join(refresh["failures"]),
                )
            else:
                messages.success(request, message)
            return redirect(
                "progression:admin_cohort_semester_progression",
                diploma_id=diploma.id,
                cohort_id=cohort.id,
                semester_id=semester.id,
            )
    else:
        if config is None:
            form = SemesterEvaluationSettingsForm(
                initial={
                    "attendance_weight_percent": Decimal("20"),
                    "homework_weight_percent": Decimal("30"),
                    "semester_exam_weight_percent": Decimal("50"),
                    "attendance_min_percent": Decimal("0"),
                    "homework_min_percent": Decimal("0"),
                    "semester_exam_min_percent": Decimal("0"),
                    "attendance_required_sessions": 0,
                    "overall_min_percent": Decimal("50"),
                }
            )
        else:
            form = SemesterEvaluationSettingsForm(instance=config)
    ctx = {
        "diploma": diploma,
        "cohort": cohort,
        "semester": semester,
        "form": form,
        "context_description": _("%(cohort)s · %(semester)s")
        % {
            "cohort": cohort.name,
            "semester": semester.title,
        },
        "back_url": _progression_back_url(diploma=diploma, cohort=cohort, semester=semester),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=diploma,
            cohort=cohort,
            semester=semester,
            leaf=_("Evaluation settings"),
        ),
    }
    return render(
        request,
        "ui/pages/admin/progression_evaluation_settings.html",
        {**ctx, "title": _("Evaluation settings")},
    )


# ---------------------------------------------------------------------------
# Admin grade component management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_component_list(request, cohort_id, semester_id, diploma_id=None):
    if diploma_id is None:
        cohort = get_object_or_404(Cohort.objects.select_related("diploma"), id=cohort_id)
        diploma = cohort.diploma
    else:
        diploma = get_object_or_404(Diploma, id=diploma_id)
        cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    components = SemesterGradeComponent.objects.filter(
        cohort=cohort,
        semester=semester,
    ).order_by("order")
    ctx = {
        "workflow": "component_list",
        "diploma": diploma,
        "cohort": cohort,
        "semester": semester,
        "components": components,
        "context_description": _("%(cohort)s · %(semester)s")
        % {
            "cohort": cohort.name,
            "semester": semester.title,
        },
        "back_url": _progression_back_url(diploma=diploma, cohort=cohort, semester=semester),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=diploma,
            cohort=cohort,
            semester=semester,
            leaf=_("Components"),
        ),
    }
    return render(
        request,
        "ui/pages/admin/progression_component_list.html",
        {**ctx, "title": _("Progression components")},
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_grade_sync(request, enrollment_id, semester_id, diploma_id=None, cohort_id=None):
    enrollment, semester = _get_enrollment_semester_target(
        enrollment_id,
        semester_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
    )
    try:
        result = sync_all_grade_items(enrollment, semester, actor=request.user)
        synced = sum(1 for v in result.values() if v is not None)
        record_audit_event(
            actor=request.user,
            action=ACTION_PROGRESSION_GRADE_SYNCED,
            target_type="enrollment",
            target_id=str(enrollment.id),
            section=SECTION_PROGRESSION,
            target_label=(
                f"{enrollment.cohort.diploma.title} · {enrollment.cohort.name} · "
                f"{semester.title} · {enrollment.student.full_name}"
            ),
            subject_type="student",
            subject_id=str(enrollment.student_id),
            subject_label=enrollment.student.full_name,
            metadata={
                "diploma_id": enrollment.cohort.diploma_id,
                "cohort_id": enrollment.cohort_id,
                "cohort_name": enrollment.cohort.name,
                "semester_id": semester.id,
                "semester_title": semester.title,
                "operation": "grade_sync",
                "synced_components": sorted(name for name, item in result.items() if item is not None),
                "synced_count": synced,
                "grade_item_ids": {name: item.id for name, item in result.items() if item is not None},
            },
        )
        messages.success(request, _("Synced {count} grade item(s).").format(count=synced))
    except Exception as exc:
        messages.error(request, str(exc))
    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            enrollment.student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id if diploma_id is not None else None,
            enrollment_id=enrollment.id if diploma_id is not None else None,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_grade_reevaluate(request, enrollment_id, semester_id, diploma_id=None, cohort_id=None):
    enrollment, semester = _get_enrollment_semester_target(
        enrollment_id,
        semester_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
    )
    try:
        evaluation = reevaluate_semester_evaluation(enrollment, semester, actor=request.user)
        messages.success(
            request,
            _("Semester re-evaluated: %(result)s (%(percent)s%%).")
            % {"result": evaluation.get_result_display(), "percent": evaluation.overall_percent},
        )
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            enrollment.student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id if diploma_id is not None else None,
            enrollment_id=enrollment.id if diploma_id is not None else None,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_semester_evaluation(request, enrollment_id, semester_id):
    enrollment, semester = _get_enrollment_semester_target(enrollment_id, semester_id)
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
        "back_url": _progression_back_url(
            diploma=enrollment.cohort.diploma,
            cohort=enrollment.cohort,
            semester=semester,
        ),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=enrollment.cohort.diploma,
            cohort=enrollment.cohort,
            semester=semester,
            leaf=_("Semester Evaluation"),
        ),
    }
    return render(
        request,
        "ui/pages/admin/progression_semester_evaluation.html",
        {**ctx, "title": _("Semester evaluation")},
    )


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
    certificates = (
        GeneratedCertificate.objects.filter(
            student=student_profile,
            status=CERTIFICATE_STATUS_AVAILABLE,
        )
        .select_related(
            "diploma_enrollment__cohort__diploma",
            "course_enrollment__course_version__course",
        )
        .order_by("-generated_at")
    )

    grade_items = (
        StudentGradeItem.objects.filter(
            student=student_profile,
            visible_to_student=True,
        )
        .select_related("component__semester", "component__cohort", "diploma_enrollment")
        .order_by("component__semester__order")
    )

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
def admin_request_interview(request, enrollment_id, semester_id, diploma_id=None, cohort_id=None):
    enrollment, semester = _get_enrollment_semester_target(
        enrollment_id,
        semester_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
    )

    if request.method == "POST":
        notes = request.POST.get("notes", "")
        evaluation = SemesterEvaluation.objects.filter(
            diploma_enrollment=enrollment,
            semester=semester,
        ).first()
        if evaluation is None:
            messages.error(request, _("Finalize the semester evaluation before requesting an interview."))
        else:
            try:
                request_final_interview(evaluation, actor=request.user, notes=notes)
                messages.success(request, _("Interview requested."))
            except ValueError as exc:
                messages.error(request, str(exc))
        if is_htmx(request):
            return _render_semester_row(request, enrollment, semester)
        next_url = _safe_redirect_url(request, request.POST.get("next") or request.GET.get("next"))
        if next_url:
            return redirect(next_url)
        return redirect(
            _progression_student_detail_url(
                enrollment.student.id,
                diploma_id=diploma_id,
                cohort_id=cohort_id,
                semester_id=semester_id if diploma_id is not None else None,
                enrollment_id=enrollment.id if diploma_id is not None else None,
            )
        )

    try:
        result = evaluate_semester_evaluation(enrollment, semester)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(
            _progression_student_detail_url(
                enrollment.student.id,
                diploma_id=diploma_id,
                cohort_id=cohort_id,
                semester_id=semester_id if diploma_id is not None else None,
                enrollment_id=enrollment.id if diploma_id is not None else None,
            )
        )

    contextual_next_url = _safe_redirect_url(
        request,
        request.GET.get("next"),
    )
    ctx = {
        "workflow": "interview_request",
        "enrollment": enrollment,
        "semester": semester,
        "result": result,
        "next_url": contextual_next_url or "",
        "back_url": contextual_next_url
        or _progression_back_url(
            diploma=enrollment.cohort.diploma,
            cohort=enrollment.cohort,
            semester=semester,
        ),
        "breadcrumb_items": [
            *_progression_breadcrumbs(
                diploma=enrollment.cohort.diploma,
                cohort=enrollment.cohort,
                semester=semester,
                leaf=_("Request Interview"),
            ),
        ],
    }
    return render(
        request, "ui/pages/admin/progression_interview_request.html", {**ctx, "title": _("Interview request")}
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_confirm_pass(request, enrollment_id, semester_id, diploma_id=None, cohort_id=None):
    enrollment, semester = _get_enrollment_semester_target(
        enrollment_id,
        semester_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
    )
    notes = request.POST.get("notes", "")

    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before confirming the pass."))
    else:
        try:
            resolve_final_progression(
                evaluation,
                actor=request.user,
                final_result=SEMESTER_EVALUATION_PASS,
                reason=notes,
            )
            messages.success(request, _("Final progression resolved as Pass."))
        except ValueError as exc:
            messages.error(request, str(exc))

    next_url = _safe_redirect_url(request, request.POST.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(
        _progression_student_detail_url(
            enrollment.student.id,
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id if diploma_id is not None else None,
            enrollment_id=enrollment.id if diploma_id is not None else None,
        )
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def admin_interview_decision(request, enrollment_id, semester_id, diploma_id=None, cohort_id=None):
    enrollment, semester = _get_enrollment_semester_target(
        enrollment_id,
        semester_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
    )
    is_contextual = diploma_id is not None
    student_detail_url = _progression_student_detail_url(
        enrollment.student_id,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id if is_contextual else None,
        enrollment_id=enrollment.id if is_contextual else None,
    )
    next_url = _safe_redirect_url(
        request,
        request.POST.get("next") or request.GET.get("next"),
    )
    return_url = next_url or student_detail_url
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    interview = (
        SemesterInterview.objects.filter(
            outcome__evaluation=evaluation,
            superseded_at__isnull=True,
        ).first()
        if evaluation is not None
        else None
    )

    if request.method == "POST":
        decision = request.POST.get("decision")
        notes = request.POST.get("notes", "")
        if evaluation is None:
            messages.error(request, _("Finalize the semester evaluation before completing the interview."))
        elif interview is None or interview.status != INTERVIEW_STATUS_REQUESTED:
            messages.error(request, _("There is no pending interview for this semester."))
        elif decision not in (INTERVIEW_DECISION_PASS, INTERVIEW_DECISION_FAIL):
            messages.error(request, _("Invalid decision."))
        else:
            try:
                complete_final_interview(
                    evaluation,
                    actor=request.user,
                    decision=decision,
                    details=notes,
                )
                messages.success(request, _("Interview completed."))
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect(return_url)

    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before completing the interview."))
        return redirect(return_url)
    if interview is None or interview.status != INTERVIEW_STATUS_REQUESTED:
        messages.error(request, _("There is no pending interview for this semester."))
        return redirect(return_url)

    ctx = {
        "workflow": "interview_decision",
        "enrollment": enrollment,
        "semester": semester,
        "next_url": return_url,
        "back_url": return_url,
        "breadcrumb_items": (
            _progression_breadcrumbs(
                diploma=enrollment.cohort.diploma,
                cohort=enrollment.cohort,
                semester=semester,
                leaf=_("Interview Decision"),
            )
            if is_contextual
            else [
                {"label": _("Diplomas"), "href": reverse("academics:admin_diploma_list")},
                {"label": _("Interview Decision")},
            ]
        ),
    }
    return render(
        request,
        "ui/pages/admin/progression_interview_decision.html",
        {**ctx, "title": _("Interview decision")},
    )


# ---------------------------------------------------------------------------
# HTMX inline interview actions
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_interview_inline_pass(request, enrollment_id, semester_id):
    enrollment, semester = _get_enrollment_semester_target(enrollment_id, semester_id)
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before completing the interview."))
    else:
        try:
            complete_final_interview(evaluation, actor=request.user, decision=INTERVIEW_DECISION_PASS)
            messages.success(request, _("Interview passed."))
        except ValueError as exc:
            messages.error(request, str(exc))
    if is_htmx(request):
        return _render_semester_row(request, enrollment, semester)
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=enrollment.cohort.diploma_id,
        cohort_id=enrollment.cohort_id,
        semester_id=semester.id,
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_interview_inline_fail(request, enrollment_id, semester_id):
    enrollment, semester = _get_enrollment_semester_target(enrollment_id, semester_id)
    evaluation = SemesterEvaluation.objects.filter(
        diploma_enrollment=enrollment,
        semester=semester,
    ).first()
    if evaluation is None:
        messages.error(request, _("Finalize the semester evaluation before completing the interview."))
    else:
        try:
            complete_final_interview(evaluation, actor=request.user, decision=INTERVIEW_DECISION_FAIL)
            messages.success(request, _("Interview failed."))
        except ValueError as exc:
            messages.error(request, str(exc))
    if is_htmx(request):
        return _render_semester_row(request, enrollment, semester)
    return redirect(
        "progression:admin_cohort_semester_progression",
        diploma_id=enrollment.cohort.diploma_id,
        cohort_id=enrollment.cohort_id,
        semester_id=semester.id,
    )


def _render_semester_row(request, enrollment, semester):
    cohort = enrollment.cohort
    row = get_semester_progression_rows(cohort, semester, [enrollment])[0]
    row["workspace_url"] = reverse(
        "progression:admin_cohort_semester_progression",
        args=[cohort.diploma_id, cohort.id, semester.id],
    )
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
def admin_rejoin_plan_list(request, diploma_id=None, cohort_id=None, semester_id=None):
    context = _get_rejoin_report_context(
        request,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
    )
    plans = SemesterRejoinPlan.objects.select_related(
        "student__user",
        "from_enrollment__cohort",
        "failed_semester",
        "target_cohort",
        "planned_by",
        "to_enrollment__cohort",
    ).all()
    if context["context_diploma"] is not None:
        plans = plans.filter(
            from_enrollment__cohort__diploma=context["context_diploma"],
            failed_semester__diploma=context["context_diploma"],
        )
    if context["context_cohort"] is not None:
        plans = plans.filter(from_enrollment__cohort=context["context_cohort"])
    if context["context_semester"] is not None:
        plans = plans.filter(failed_semester=context["context_semester"])
    requested_status = request.GET.get("status", "")
    if requested_status == "all":
        status = "all"
    elif requested_status in {value for value, _label in REJOIN_STATUS_CHOICES}:
        status = requested_status
    else:
        status = "current"

    if status == "current":
        plans = plans.filter(status__in=[REJOIN_STATUS_PLANNED, REJOIN_STATUS_ACTIVE])
    elif status != "all":
        plans = plans.filter(status=status)

    plans = plans.order_by("-created_at", "-id")
    page_obj, pagination_qs = paginate(request, plans, per_page=25)
    for plan in page_obj.object_list:
        plan.can_activate = (
            plan.status == REJOIN_STATUS_PLANNED
            and plan.from_enrollment.status == ENROLLMENT_STATUS_ACTIVE
            and not plan.from_enrollment.is_access_expired
        )
        if plan.status == REJOIN_STATUS_PLANNED:
            plan.next_step = (
                _("Next step: activate to move the student to the target cohort, or cancel the plan.")
                if plan.can_activate
                else _("Cannot activate: the source enrollment is no longer active or has expired.")
            )
        elif plan.status == REJOIN_STATUS_ACTIVE:
            plan.next_step = _(
                "Next step: revoke to delete the replacement enrollment and restore the previous cohort."
            )
        elif plan.status == REJOIN_STATUS_CANCELLED:
            plan.next_step = _("Closed: kept as historical record; no action is required.")
        else:
            plan.next_step = _("Completed: kept as historical record.")
        plan.can_restore_previous_enrollment = (
            plan.status == REJOIN_STATUS_CANCELLED
            and plan.from_enrollment.status == ENROLLMENT_STATUS_RESTRICTED
            and plan.to_enrollment is not None
            and plan.to_enrollment.status == ENROLLMENT_STATUS_ACTIVE
        )
    ctx = {
        "workflow": "rejoin_list",
        "plans": page_obj.object_list,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "current_status": status,
        "rejoin_status_choices": REJOIN_STATUS_CHOICES,
        "context_filter_action": reverse("progression:admin_rejoin_plan_list"),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=context["context_diploma"],
            cohort=context["context_cohort"],
            semester=context["context_semester"],
            leaf=_("Rejoin Plans"),
        ),
        **context,
    }
    return render(request, "ui/pages/admin/progression_rejoin_list.html", {**ctx, "title": _("Rejoin plans")})


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_rejoin_eligibility(request, diploma_id=None, cohort_id=None, semester_id=None):
    """Dedicated Rejoin Eligibility page (P4-02): only immediate-next/same-semester rows."""
    context = _get_rejoin_report_context(
        request,
        diploma_id=diploma_id,
        cohort_id=cohort_id,
        semester_id=semester_id,
    )
    selector_kwargs = {
        "diploma_id": context["context_diploma"].id if context["context_diploma"] else None,
        "cohort_id": context["context_cohort"].id if context["context_cohort"] else None,
        "semester_id": context["context_semester"].id if context["context_semester"] else None,
    }
    page_number = safe_int(request.GET.get("page")) or 1
    _sample_rows, total = get_rejoin_eligibility_rows(offset=0, limit=0, **selector_kwargs)
    paginator = Paginator(range(total), 25)
    page_obj = paginator.get_page(page_number)
    rows, _total = get_rejoin_eligibility_rows(
        offset=page_obj.start_index() - 1 if total else 0,
        limit=25,
        **selector_kwargs,
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
        "context_filter_action": reverse("progression:admin_rejoin_eligibility"),
        "breadcrumb_items": _progression_breadcrumbs(
            diploma=context["context_diploma"],
            cohort=context["context_cohort"],
            semester=context["context_semester"],
            leaf=_("Rejoin Eligibility"),
        ),
        **context,
    }
    return render(
        request,
        "ui/pages/admin/progression_rejoin_eligibility.html",
        {**ctx, "title": _("Rejoin Eligibility")},
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_create_rejoin_plan(
    request,
    enrollment_id,
    semester_id=None,
    diploma_id=None,
    cohort_id=None,
):
    context_route = any(value is not None for value in (diploma_id, cohort_id))
    if context_route:
        if diploma_id is None or cohort_id is None or semester_id is None:
            raise Http404
        diploma, cohort, selected_semester = _get_progression_context(
            diploma_id,
            cohort_id,
            semester_id,
        )
        enrollment = get_object_or_404(
            DiplomaEnrollment.objects.select_related("student__user", "cohort__diploma"),
            id=enrollment_id,
            cohort=cohort,
        )
    else:
        enrollment = get_object_or_404(
            DiplomaEnrollment.objects.select_related("student__user", "cohort__diploma"),
            id=enrollment_id,
        )
        diploma = enrollment.cohort.diploma
        cohort = enrollment.cohort
        selected_semester = (
            get_object_or_404(Semester, id=semester_id, diploma=diploma) if semester_id is not None else None
        )
    route_semester = selected_semester

    if request.method == "POST":
        notes = request.POST.get("notes", "")
        submitted_semester_id = (
            selected_semester.id
            if selected_semester is not None
            else safe_int(request.POST.get("semester_id"))
        )
        if not submitted_semester_id:
            messages.error(request, _("Semester is required."))
        else:
            try:
                semester = get_object_or_404(
                    Semester,
                    id=submitted_semester_id,
                    diploma=diploma,
                )
                create_rejoin_plan(
                    enrollment.student,
                    enrollment,
                    semester,
                    actor=request.user,
                    notes=notes,
                )
                messages.success(request, _("Rejoin plan created."))
                if context_route:
                    return redirect(
                        "progression:admin_contextual_rejoin_plan_list",
                        diploma_id=diploma.id,
                        cohort_id=cohort.id,
                        semester_id=semester.id,
                    )
                return redirect("progression:admin_rejoin_plan_list")
            except ValueError as exc:
                messages.error(request, str(exc))
    eligible_outcomes = FinalProgressionOutcome.objects.filter(
        diploma_enrollment=enrollment,
        status=FINAL_PROGRESSION_STATUS_RESOLVED,
        final_result=SEMESTER_EVALUATION_FAIL,
    ).select_related("semester")
    requested_semester_id = (
        selected_semester.id
        if selected_semester
        else safe_int(
            request.GET.get("semester_id"),
        )
    )
    selected_outcome = (
        eligible_outcomes.filter(semester_id=requested_semester_id).first()
        if requested_semester_id
        else eligible_outcomes.first()
    )
    selected_semester = selected_outcome.semester if selected_outcome else None
    target_cohort = get_immediate_next_cohort(enrollment.cohort) if selected_semester else None
    source_enrollment_eligible = (
        enrollment.status == ENROLLMENT_STATUS_ACTIVE and not enrollment.is_access_expired
    )
    transition_blocked = (
        (
            not source_enrollment_eligible
            or target_cohort is None
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
        )
        if selected_semester
        else True
    )
    transition_blocked_reason = (
        _("Direct Rejoin is available only for an active, non-expired source enrollment.")
        if not source_enrollment_eligible
        else ""
    )
    if transition_blocked:
        selected_semester = None
        target_cohort = None
    ctx = {
        "workflow": "rejoin_create",
        "enrollment": enrollment,
        "selected_semester": selected_semester,
        "target_cohort": target_cohort,
        "transition_blocked_reason": transition_blocked_reason,
        "semester_in_url": semester_id is not None,
        "back_url": (
            _progression_back_url(diploma=diploma, cohort=cohort, semester=route_semester)
            if context_route
            else reverse("progression:admin_rejoin_eligibility")
        ),
        "breadcrumb_items": (
            _progression_breadcrumbs(
                diploma=diploma,
                cohort=cohort,
                semester=route_semester,
                leaf=_("Create Rejoin Plan"),
            )
            if context_route
            else [
                {"label": _("Diplomas"), "href": reverse("academics:admin_diploma_list")},
                {"label": _("Rejoin Eligibility"), "href": reverse("progression:admin_rejoin_eligibility")},
                {"label": _("Create Rejoin Plan")},
            ]
        ),
    }
    return render(
        request, "ui/pages/admin/progression_rejoin_create.html", {**ctx, "title": _("Rejoin plan")}
    )


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_activate_rejoin_plan(
    request,
    plan_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
):
    context_route = any(value is not None for value in (diploma_id, cohort_id, semester_id))
    if context_route:
        if any(value is None for value in (diploma_id, cohort_id, semester_id)):
            raise Http404
        _diploma, cohort, semester = _get_progression_context(
            diploma_id,
            cohort_id,
            semester_id,
        )
        plan = get_object_or_404(
            SemesterRejoinPlan,
            id=plan_id,
            from_enrollment__cohort=cohort,
            failed_semester=semester,
        )
    else:
        plan = get_object_or_404(SemesterRejoinPlan, id=plan_id)
    try:
        activate_rejoin_plan(plan, actor=request.user)
        messages.success(request, _("Rejoin plan activated."))
    except ValueError as exc:
        messages.error(request, str(exc))
    if context_route:
        return redirect(
            "progression:admin_contextual_rejoin_plan_list",
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
        )
    return redirect("progression:admin_rejoin_plan_list")


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_POST
def admin_cancel_rejoin_plan(
    request,
    plan_id,
    diploma_id=None,
    cohort_id=None,
    semester_id=None,
):
    context_route = any(value is not None for value in (diploma_id, cohort_id, semester_id))
    if context_route:
        if any(value is None for value in (diploma_id, cohort_id, semester_id)):
            raise Http404
        _diploma, cohort, semester = _get_progression_context(
            diploma_id,
            cohort_id,
            semester_id,
        )
        plan_queryset = SemesterRejoinPlan.objects.select_related(
            "from_enrollment",
            "to_enrollment",
        ).filter(from_enrollment__cohort=cohort, failed_semester=semester)
    else:
        plan_queryset = SemesterRejoinPlan.objects.select_related(
            "from_enrollment",
            "to_enrollment",
        )
    plan = get_object_or_404(plan_queryset, id=plan_id)
    reason = request.POST.get("reason", "")
    was_active = plan.status == REJOIN_STATUS_ACTIVE
    was_recovery = (
        plan.status == REJOIN_STATUS_CANCELLED
        and plan.from_enrollment.status == ENROLLMENT_STATUS_RESTRICTED
        and plan.to_enrollment is not None
        and plan.to_enrollment.status == ENROLLMENT_STATUS_ACTIVE
    )
    try:
        cancel_rejoin_plan(plan, actor=request.user, reason=reason)
        messages.success(
            request,
            _("Previous enrollment restored.")
            if was_recovery
            else _("Rejoin revoked.")
            if was_active
            else _("Rejoin plan cancelled."),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    if context_route:
        return redirect(
            "progression:admin_contextual_rejoin_plan_list",
            diploma_id=diploma_id,
            cohort_id=cohort_id,
            semester_id=semester_id,
        )
    return redirect("progression:admin_rejoin_plan_list")
