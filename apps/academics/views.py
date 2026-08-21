"""Views for academic structure management and student content access."""

import base64
import io as _io
import json
import logging
import re
import uuid
from decimal import Decimal

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.audio import validate_audio_file
from apps.academics.constants import (
    AUDIO_PROCESSING_FAILED,
    AUDIO_PROCESSING_PENDING,
    ENROLLMENT_STATUS_ACTIVE,
    ENROLLMENT_STATUS_CHOICES,
)
from apps.academics.forms import (
    CohortForm,
    CourseForm,
    CourseVersionForm,
    DiplomaForm,
    DiplomaLectureForm,
    LectureForm,
    SemesterForm,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseEnrollment,
    CourseVersion,
    Diploma,
    DiplomaEnrollment,
    DiplomaLecture,
    DiplomaLectureWatchProgress,
    Lecture,
    LectureAudioProgress,
    LectureWatchProgress,
    StudentCluster,
    Semester,
)
from apps.assessments.constants import ATTEMPT_STATUS_ASSIGNED, ATTEMPT_STATUS_IN_PROGRESS
from apps.assessments.models import AssessmentAttempt
from apps.assessments.services import get_student_assessments
from apps.academics.services import (
    _course_lecture_completion_map,
    _course_unlock_map,
    can_access_course,
    can_access_diploma_lecture,
    can_access_lecture,
    course_progress_for_enrollment,
    create_cohort,
    delete_cohort_record,
    delete_course_record,
    delete_course_version_record,
    delete_diploma_lecture_record,
    delete_diploma_record,
    delete_lecture_record,
    delete_semester_record,
    diploma_progress_for_enrollment,
    enqueue_youtube_metadata_refresh,
    extract_youtube_video_id,
    get_student_diploma_access,
    get_visible_diploma_lectures,
    get_visible_lectures,
    record_diploma_lecture_checkpoint,
    record_lecture_audio_checkpoint,
    record_lecture_checkpoint,
    start_diploma_lecture_watch,
    start_lecture_audio,
    start_lecture_watch,
)
from apps.attendance.models import AttendanceCode
from apps.attendance.services import build_attendance_qr_payload, get_attendance_summary
from apps.progression.models import DiplomaProgressionRule, SemesterProgressionRule
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_EXAM_STAFF,
    enrollment_scope_filter,
    is_admin,
    is_student,
    role_required,
    student_required,
)
from apps.core.views import safe_next_url
from apps.intake_forms.models import IntakeForm
from apps.storage.services import record_deferred_storage_deletion, storage_service
from apps.students.models import StudentProfile

logger = logging.getLogger(__name__)


def _diploma_breadcrumbs(diploma=None):
    items = [{"label": _("Academics"), "href": reverse("academics:admin_diploma_list")}]
    if diploma:
        items.append({
            "label": diploma.title,
            "href": reverse("academics:admin_diploma_detail", args=[diploma.id]),
        })
    return items


# ---------------------------------------------------------------------------
# Admin: Diploma management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_list(request):
    q = request.GET.get("q", "")
    is_active_filter = request.GET.get("is_active", "")
    diplomas = Diploma.objects.select_related("created_by").annotate(
        semester_count=Count("semesters", distinct=True),
        cohort_count=Count("cohorts", distinct=True),
        enrollment_count=Count("cohorts__enrollments", distinct=True),
    ).order_by("title")
    if q:
        diplomas = diplomas.filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )
    if is_active_filter:
        diplomas = diplomas.filter(is_active=is_active_filter == "1")
    filters = []
    if is_active_filter is not None:
        filters.append({
            "name": "is_active",
            "label": _("Status"),
            "value": is_active_filter,
            "options": [
                {"value": "1", "label": _("Active"), "selected": is_active_filter == "1"},
                {"value": "0", "label": _("Inactive"), "selected": is_active_filter == "0"},
            ],
        })
    page_obj, pagination_qs = paginate(request, diplomas)
    ctx = {"page_obj": page_obj, "search_value": q, "filters": filters, "pagination_qs": pagination_qs}
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_diploma_table.html", ctx)
    return render(request, "ui/pages/admin/diploma_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_detail(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semesters = diploma.semesters.all()
    cohorts = diploma.cohorts.prefetch_related(
        Prefetch(
            "clusters",
            queryset=StudentCluster.objects.annotate(
                member_count=Count("memberships", distinct=True),
            ).prefetch_related(
                "memberships__student__user",
            ),
        ),
        "enrollments__student__user",
    )
    # Annotate each cohort with diploma lecture count
    cohorts = cohorts.annotate(diploma_lecture_count=Count("lectures", distinct=True))
    # Batch-count lectures per (cohort, semester) in one query
    lecture_counts = (
        DiplomaLecture.objects
        .filter(cohort__in=cohorts, semester__in=semesters)
        .values("cohort_id", "semester_id")
        .annotate(count=Count("id"))
    )
    count_lookup = {(item["cohort_id"], item["semester_id"]): item["count"] for item in lecture_counts}
    # Build cohort-grouped structure
    cohort_groups = []
    for cohort in cohorts:
        semesters_list = []
        for semester in semesters:
            l_count = count_lookup.get((cohort.id, semester.id), 0)
            semesters_list.append({"semester": semester, "lecture_count": l_count})
        cohort_groups.append({"cohort": cohort, "semesters": semesters_list})
    # Fetch recent enrollments for display
    enrollments = DiplomaEnrollment.objects.filter(cohort__in=cohorts).select_related(
        "student__user", "cohort"
    ).order_by("-created_at")[:20]
    return render(
        request,
        "ui/pages/admin/diploma_detail.html",
        {
            "diploma": diploma,
            "semesters": semesters,
            "cohorts": cohorts,
            "cohort_groups": cohort_groups,
            "enrollments": enrollments,
            "breadcrumb_items": _diploma_breadcrumbs(diploma),
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_create(request):
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaForm(request.POST)
        if form.is_valid():
            diploma = form.save(commit=False)
            diploma.created_by = request.user
            diploma.save()
            messages.success(request, _("Diploma created."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaForm()
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Create diploma"),
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs() + [{"label": _("New diploma")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_edit(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaForm(request.POST, instance=diploma)
        if form.is_valid():
            form.save()
            messages.success(request, _("Diploma updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaForm(instance=diploma)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Edit diploma"),
            "diploma": diploma,
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_diploma_delete(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    next_url = safe_next_url(request)
    try:
        delete_diploma_record(diploma=diploma, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        redirect_url = reverse("academics:admin_diploma_detail", kwargs={"diploma_id": diploma.id})
    else:
        messages.success(request, _("Diploma deleted."))
        redirect_url = next_url or reverse("academics:admin_diploma_list")
    response = HttpResponse()
    response["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# Admin: Semester management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_semester_create(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    next_url = safe_next_url(request)
    if diploma.semesters.count() >= diploma.total_semesters:
        messages.info(
            request,
            _("All %(total)s semester(s) already created.") % {"total": diploma.total_semesters},
        )
        return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    if request.method == "POST":
        form = SemesterForm(request.POST, diploma=diploma)
        if form.is_valid():
            semester = form.save(commit=False)
            semester.diploma = diploma
            semester.save()
            messages.success(request, _("Semester created."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = SemesterForm(initial={"order": diploma.semesters.count() + 1}, diploma=diploma)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Create semester"),
            "diploma": diploma,
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("New semester")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_semester_edit(request, diploma_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    next_url = safe_next_url(request)

    diploma_rule = DiplomaProgressionRule.objects.filter(diploma=diploma, is_active=True).first()
    semester_rule = SemesterProgressionRule.objects.filter(
        semester=semester, diploma_rule__diploma=diploma, is_active=True,
    ).first()

    if request.method == "POST":
        form = SemesterForm(request.POST, instance=semester, diploma=diploma)
        pass_percent_raw = request.POST.get("semester_pass_percent_override", "").strip()
        pass_percent = None
        if pass_percent_raw:
            try:
                parsed = Decimal(pass_percent_raw)
            except (ValueError, TypeError):
                messages.error(request, _("Invalid semester pass percent override."))
            else:
                if not parsed.is_finite() or parsed < 0 or parsed > 100:
                    messages.error(request, _("Semester pass percent must be between 0 and 100."))
                else:
                    pass_percent = parsed
        require_all = request.POST.get("semester_require_all_required") == "1"
        allow_missing = request.POST.get("semester_allow_missing_optional") == "1"
        if form.is_valid() and pass_percent is not None:
            form.save()
            if diploma_rule:
                if pass_percent:
                    from apps.progression.services import save_progression_rule

                    semester_rule, _created = SemesterProgressionRule.objects.update_or_create(
                        semester=semester,
                        diploma_rule=diploma_rule,
                        defaults={
                            "pass_percent": pass_percent,
                            "require_all_required_assessments": require_all,
                            "allow_missing_optional_assessments": allow_missing,
                            "is_active": True,
                        },
                    )
                    try:
                        save_progression_rule(semester_rule, request.user)
                    except (ValueError, ValidationError) as exc:
                        messages.error(request, str(exc))
                        return render(
                            request,
                            "ui/pages/admin/academic_form.html",
                            {
                                "form": form,
                                "title": _("Edit semester"),
                                "diploma": diploma,
                                "semester_pass_percent_override": str(pass_percent),
                                "semester_require_all_required": require_all,
                                "semester_allow_missing_optional": allow_missing,
                                "next": next_url,
                                "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit semester")}],
                            },
                        )
                elif semester_rule:
                    from apps.progression.services import save_progression_rule

                    semester_rule.is_active = False
                    try:
                        save_progression_rule(semester_rule, request.user)
                    except (ValueError, ValidationError) as exc:
                        messages.error(request, str(exc))
                        return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
                    semester_rule = None
            messages.success(request, _("Semester updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = SemesterForm(instance=semester, diploma=diploma)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Edit semester"),
            "diploma": diploma,
            "semester_pass_percent_override": str(semester_rule.pass_percent) if semester_rule else "",
            "semester_require_all_required": semester_rule.require_all_required_assessments if semester_rule else True,
            "semester_allow_missing_optional": semester_rule.allow_missing_optional_assessments if semester_rule else False,
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit semester")}],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Semester management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_semester_delete(request, diploma_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    next_url = safe_next_url(request)
    try:
        delete_semester_record(semester=semester, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        redirect_url = reverse("academics:admin_diploma_detail", kwargs={"diploma_id": diploma.id})
    else:
        messages.success(request, _("Semester deleted."))
        redirect_url = next_url or reverse("academics:admin_diploma_detail", kwargs={"diploma_id": diploma.id})
    response = HttpResponse()
    response["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# Admin: Course management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_list(request):
    q = request.GET.get("q", "")
    is_active_filter = request.GET.get("is_active", "")
    courses = Course.objects.select_related("created_by").annotate(
        version_count=Count("versions", distinct=True),
        lecture_count=Count("versions__lectures", distinct=True),
        enrollment_count=Count("versions__enrollments", distinct=True),
    ).order_by("title")
    if q:
        courses = courses.filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )
    if is_active_filter:
        courses = courses.filter(is_active=is_active_filter == "1")
    filters = []
    filters.append({
        "name": "is_active",
        "label": _("Status"),
        "value": is_active_filter,
        "options": [
            {"value": "1", "label": _("Active"), "selected": is_active_filter == "1"},
            {"value": "0", "label": _("Inactive"), "selected": is_active_filter == "0"},
        ],
    })
    page_obj, pagination_qs = paginate(request, courses)
    ctx = {"page_obj": page_obj, "search_value": q, "filters": filters, "pagination_qs": pagination_qs}
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_course_table.html", ctx)
    return render(request, "ui/pages/admin/course_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    versions = course.versions.prefetch_related("lectures").all()
    lectures = Lecture.objects.filter(course_version__course=course).order_by("course_version", "order")
    enrollments = CourseEnrollment.objects.filter(course_version__course=course).select_related("student", "course_version")
    return render(
        request,
        "ui/pages/admin/course_detail.html",
        {
            "course": course,
            "versions": versions,
            "lectures": lectures,
            "enrollments": enrollments,
            "breadcrumb_items": [
                {"label": _("Courses"), "href": reverse("academics:admin_course_list")},
                {"label": course.title},
            ],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_create(request):
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save(commit=False)
            course.created_by = request.user
            course.save()
            messages.success(request, _("Course created."))
            return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseForm()
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Create course"), "next": next_url, "diploma": None, "semesters": [], "diplomas": None},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_semester_options(request):
    diploma_id = request.GET.get("diploma_id")
    semesters = Diploma.objects.get(id=diploma_id).semesters.all() if diploma_id else []
    return render(request, "ui/fragments/admin/_semester_options.html", {"semesters": semesters})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_edit(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, _("Course updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseForm(instance=course)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Edit course"), "course": course, "next": next_url, "semester_courses": [], "diploma": None, "semesters": []},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_course_delete(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    next_url = safe_next_url(request)
    try:
        delete_course_record(course=course, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        redirect_url = reverse("academics:admin_course_detail", kwargs={"course_id": course.id})
    else:
        messages.success(request, _("Course deleted."))
        redirect_url = next_url or reverse("academics:admin_course_list")
    response = HttpResponse()
    response["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# Admin: Cohort management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_cohort_create(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = CohortForm(request.POST)
        if form.is_valid():
            create_cohort(
                diploma=diploma,
                form_data=form.cleaned_data,
                actor=request.user,
            )
            messages.success(request, _("Cohort created."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = CohortForm(initial={"diploma": diploma.id})
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Create cohort"),
            "diploma": diploma,
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("New cohort")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_cohort_edit(request, diploma_id, cohort_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = CohortForm(request.POST, instance=cohort)
        if form.is_valid():
            form.save()
            messages.success(request, _("Cohort updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = CohortForm(instance=cohort)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {
            "form": form,
            "title": _("Edit cohort"),
            "diploma": diploma,
            "next": next_url,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit cohort")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_cohort_delete(request, diploma_id, cohort_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    next_url = safe_next_url(request)
    try:
        delete_cohort_record(cohort=cohort, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        redirect_url = reverse("academics:admin_diploma_detail", kwargs={"diploma_id": diploma.id})
    else:
        messages.success(request, _("Cohort deleted."))
        redirect_url = next_url or reverse("academics:admin_diploma_detail", kwargs={"diploma_id": diploma.id})
    response = HttpResponse()
    response["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# Admin: Course version management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_version_create(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = CourseVersionForm(request.POST)
        if form.is_valid():
            version = form.save(commit=False)
            version.course = course
            version.created_by = request.user
            version.save()
            messages.success(request, _("Course version created."))
            return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseVersionForm(initial={"course": course.id})
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Create course version"), "course": course, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_version_detail(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lectures = version.lectures.all()
    intake_forms = IntakeForm.objects.filter(course_version=version).annotate(
        responses_count=Count("responses"),
    )
    return render(
        request,
        "ui/pages/admin/academic_detail.html",
        {
            "course": course, "version": version, "lectures": lectures, "intake_forms": intake_forms,
            "title": version.label, "object": version,
            "breadcrumb_items": [
                {"label": _("Academics"), "href": reverse("academics:admin_course_list")},
                {"label": course.title, "href": reverse("academics:admin_course_detail", args=[course.id])},
                {"label": version.label},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Lecture management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_create(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    if not is_admin(request.user):
        raise PermissionDenied
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.course_version = version
            lecture.save()
            _process_lecture_audio(request, lecture, request.FILES)
            enqueue_youtube_metadata_refresh(lecture)
            messages.success(request, _("Lecture created."))
            return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(initial={"order": version.lectures.count() + 1})
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Create lecture"), "course": course, "version": version, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_edit(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not is_admin(request.user):
        raise PermissionDenied
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES, instance=lecture)
        if form.is_valid():
            previous_pending_key = lecture.audio_pending_storage_key
            previous_pending_prefix = (
                previous_pending_key.rsplit("/", 1)[0]
                if previous_pending_key.startswith(f"lectures/{lecture.id}/audio/")
                else ""
            )
            form.save()
            pending_replaced = _process_lecture_audio(request, lecture, request.FILES)
            enqueue_youtube_metadata_refresh(lecture)
            if pending_replaced and previous_pending_prefix:
                _cleanup_audio_after_commit([], [previous_pending_prefix])
            messages.success(request, _("Lecture updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(instance=lecture)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Edit lecture"), "course": course, "version": version, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_lecture_delete(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not is_admin(request.user):
        raise PermissionDenied
    next_url = safe_next_url(request)
    try:
        delete_lecture_record(lecture=lecture, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    messages.success(request, _("Lecture deleted."))
    return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_version_delete(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    next_url = safe_next_url(request)
    try:
        delete_course_version_record(version=version, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        redirect_url = reverse("academics:admin_course_detail", kwargs={"course_id": course.id})
    else:
        messages.success(request, _("Course version deleted."))
        redirect_url = next_url or reverse("academics:admin_course_detail", kwargs={"course_id": course.id})
    response = HttpResponse()
    response["HX-Redirect"] = redirect_url
    return response


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_version_toggle_active(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    version.is_active = not version.is_active
    version.save(update_fields=["is_active", "updated_at"])
    messages.success(request, _("Course version status updated."))
    return redirect("academics:admin_course_detail", course_id=course.id)


def _process_lecture_audio(request, lecture, files, key_prefix=None):
    """Validate and upload an audio file, then enqueue the HLS transcode.

    The FFmpeg work runs in a bounded Celery task (S2-09); the web request
    only validates size/type server-side and uploads the source through the
    storage service. The lecture carries a visible processing state.
    """
    audio_file = files.get("audio_file")
    if not audio_file:
        return False
    base_prefix = key_prefix or f"lectures/{lecture.id}/audio"
    generation_prefix = f"{base_prefix}/{uuid.uuid4().hex}"
    uploaded_key = ""
    source_persisted = False
    try:
        filename = str(audio_file)
        content_type = audio_file.content_type or "audio/mpeg"
        validate_audio_file(filename, content_type)
        max_size = settings.AUDIO_MAX_UPLOAD_MB * 1024 * 1024
        if getattr(audio_file, "size", 0) > max_size:
            raise ValueError(
                _("Audio file exceeds the maximum allowed size of %(size)s MB.")
                % {"size": settings.AUDIO_MAX_UPLOAD_MB}
            )
        data = audio_file.read(max_size + 1)
        result = storage_service.upload_file(
            data,
            key_prefix=generation_prefix,
            content_type=content_type,
            max_size=max_size,
            allowed_types=[".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"],
            original_filename=filename,
        )
        uploaded_key = result.key
        # Keep the currently published source/HLS generation addressable while
        # the replacement is processed. Only the worker publishes these
        # metadata fields after every HLS object exists.
        lecture.audio_pending_storage_key = result.key
        lecture.audio_processing_status = AUDIO_PROCESSING_PENDING
        lecture.audio_processing_error = ""
        lecture.save(update_fields=[
            "audio_pending_storage_key", "audio_processing_status",
            "audio_processing_error", "updated_at",
        ])
        source_persisted = True
        from apps.academics.tasks import transcode_lecture_audio_task

        transcode_lecture_audio_task.delay(
            lecture_id=lecture.id,
            storage_key=result.key,
            original_filename=filename,
            content_type=result.content_type,
            byte_size=result.byte_size,
            checksum=result.checksum,
            storage_prefix=generation_prefix,
            diploma=isinstance(lecture, DiplomaLecture),
        )
        messages.success(request, _("Audio uploaded. HLS processing started."))
    except Exception as exc:
        logger.exception("Audio upload/processing failed for lecture %s", lecture.id)
        if uploaded_key and not source_persisted:
            try:
                if not storage_service.delete_prefix(generation_prefix):
                    record_deferred_storage_deletion(prefix=generation_prefix, error=str(exc))
            except Exception as cleanup_exc:
                record_deferred_storage_deletion(prefix=generation_prefix, error=str(cleanup_exc))
        elif source_persisted or not lecture.audio_storage_key:
            lecture.audio_processing_status = AUDIO_PROCESSING_FAILED
            lecture.audio_processing_error = str(exc)[:2000]
            lecture.save(update_fields=["audio_processing_status", "audio_processing_error", "updated_at"])
        messages.error(request, _("Audio upload failed: %(error)s") % {"error": str(exc)})
    return source_persisted


_AUDIO_SEGMENT_RE = re.compile(r"^[a-f0-9]{32}/(?:index\.m3u8|seg_\d{3}\.ts)$")


def _is_valid_audio_file_path(file_path: str) -> bool:
    """Reject any path that is not a single generated HLS audio filename (S2-22).

    Only ``<name>.m3u8`` / ``<name>.ts`` segments with safe characters are
    allowed; separators, dot segments, and empty paths return False so the
    route boundary returns 404 instead of building a storage key.
    """
    if not file_path:
        return False
    if "\\" in file_path or "\x00" in file_path:
        return False
    if file_path in {".", ".."}:
        return False
    return bool(_AUDIO_SEGMENT_RE.fullmatch(file_path))


def _cleanup_audio_after_commit(old_keys, old_prefixes):
    """Delete replaced audio objects after the request's transaction commits.

    S2-33: DB rows always point at the new objects; the old physical objects
    are removed only post-commit, and failures are logged, never fatal.
    """
    def _cleanup():
        for key in old_keys:
            if not key:
                continue
            try:
                if not storage_service.delete_file(key):
                    record_deferred_storage_deletion(key=key, error="Delete returned false")
            except Exception as exc:  # noqa: BLE001 - post-commit reconciliation
                logger.exception("Storage cleanup failed for key %s", key)
                record_deferred_storage_deletion(key=key, error=str(exc))
        for prefix in old_prefixes:
            if not prefix:
                continue
            try:
                if not storage_service.delete_prefix(prefix):
                    record_deferred_storage_deletion(prefix=prefix, error="Delete returned false")
            except Exception as exc:  # noqa: BLE001 - post-commit reconciliation
                logger.exception("Storage cleanup failed for prefix %s", prefix)
                record_deferred_storage_deletion(prefix=prefix, error=str(exc))

    transaction.on_commit(_cleanup)


# ---------------------------------------------------------------------------
# Student: content access
# ---------------------------------------------------------------------------


def _assessment_due_work(user, *, course_version_id=None, cohort_id=None):
    from types import SimpleNamespace
    filters = Q(student=user, status__in=[ATTEMPT_STATUS_ASSIGNED, ATTEMPT_STATUS_IN_PROGRESS])
    scope = Q()
    if course_version_id:
        scope |= Q(assessment__course_version_id=course_version_id)
    if cohort_id:
        scope |= Q(assessment__cohort_id=cohort_id)
    if not scope:
        return []
    attempts = list(
        AssessmentAttempt.objects.filter(filters & scope, assessment__close_at__gte=timezone.now())
        .select_related("assessment")
        .order_by("assessment__close_at")[:5]
    )
    attempted_ids = {a.assessment_id for a in attempts}
    open_assessments = [a for a in get_student_assessments(user) if (course_version_id and a.course_version_id == course_version_id or cohort_id and a.cohort_id == cohort_id) and a.id not in attempted_ids]
    for a in open_assessments:
        attempts.append(SimpleNamespace(assessment=a, assessment_id=a.id))
    return attempts[:5]


def _learning_assessments(user, *, course_version_id=None, cohort_id=None):
    """Return active student-visible assessments for one learning target."""
    return [
        assessment
        for assessment in get_student_assessments(user)
        if (
            course_version_id and assessment.course_version_id == course_version_id
        ) or (
            cohort_id and assessment.cohort_id == cohort_id
        )
    ]


def _lecture_navigation(lectures, lecture, *, version=None, enrollment=None, semester=None, unlock_map=None):
    ordered = list(lectures)
    index = ordered.index(lecture)
    route = "academics:student_lecture_view" if version else "academics:student_diploma_lecture_view"
    def url(item):
        return reverse(route, args=[version.id, item.id]) if version else reverse(
            route, args=[enrollment.id, semester.id, item.id]
        )
    next_item = ordered[index + 1] if index + 1 < len(ordered) else None
    # Course lectures unlock sequentially; never offer a next link into a
    # locked lecture. Diploma lectures have no completion locking.
    if version and next_item is not None and unlock_map is not None:
        if not unlock_map.get(next_item.id, False):
            next_item = None
    return {
        "previous_url": url(ordered[index - 1]) if index else None,
        "next_url": url(next_item) if next_item else None,
    }


@student_required
def student_course_detail(request, version_id):
    """Show a student the lectures for a course version they can access."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id)
    if not can_access_course(profile, version):
        raise PermissionDenied(_("You do not have access to this course."))
    lectures = get_visible_lectures(profile, version)
    course_enrollment = CourseEnrollment.objects.filter(
        student=profile, course_version=version, status__in=["active", "pending"]
    ).first()
    course_attendance_code = None
    course_attendance_qr = ""
    if course_enrollment:
        course_attendance_code = AttendanceCode.objects.filter(
            course_enrollment=course_enrollment, active=True
        ).first()
        if course_attendance_code:
            payload = build_attendance_qr_payload(course_attendance_code)
            img = qrcode.make(payload, box_size=6)
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            course_attendance_qr = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    progress = course_progress_for_enrollment(profile, version)
    lecture_unlock = _course_unlock_map(profile, course_enrollment, lectures) if course_enrollment else {}
    completed_lecture_ids = [
        lecture_id
        for lecture_id, done in _course_lecture_completion_map(
            profile, course_enrollment, lectures,
        ).items()
        if done
    ] if course_enrollment else []
    due_work = _assessment_due_work(request.user, course_version_id=version.id)
    learning_assessments = _learning_assessments(request.user, course_version_id=version.id)
    return render(
        request,
        "ui/pages/student/content.html",
        {
            "version": version,
            "course_version": version,
            "lectures": lectures,
            "lecture_unlock": lecture_unlock,
            "course_attendance_code": course_attendance_code,
            "course_attendance_qr": course_attendance_qr,
            "progress": progress,
            "video_tracking_enabled": version.course.video_tracking_enabled,
            "completed_lecture_ids": completed_lecture_ids,
            "due_work": due_work,
            "learning_assessments": learning_assessments,
            "access_state": course_enrollment.get_status_display() if course_enrollment else _("No active enrollment"),
            "back_url": reverse("students:diploma_course"),
            "back_label": _("Back to learning"),
            "breadcrumb_items": [
                {"label": _("Learning"), "href": reverse("students:learning")},
                {"label": version.course.title},
            ],
        },
    )


@student_required
def student_lecture_view(request, version_id, lecture_id):
    """Show a student a single lecture (YouTube embed, authenticated only)."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_access_lecture(profile, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))
    visible = get_visible_lectures(profile, version)
    enrollment = CourseEnrollment.objects.filter(
        student=profile, course_version=version, status__in=["active", "pending"]
    ).first()
    unlock_map = _course_unlock_map(profile, enrollment, visible) if enrollment else {}
    navigation = _lecture_navigation(visible, lecture, version=version, unlock_map=unlock_map)
    watch_progress = LectureWatchProgress.objects.filter(student=profile, lecture=lecture, course_enrollment__isnull=False).first()
    audio_progress = LectureAudioProgress.objects.filter(student=profile, lecture=lecture).first()
    return render(
        request,
        "ui/pages/student/content.html",
        {
            "version": version,
            "course_version": version,
            "lecture": lecture,
            "youtube_video_id": lecture.youtube_video_id or extract_youtube_video_id(lecture.youtube_url),
            "video_tracking_enabled": version.course.video_tracking_enabled,
            "navigation": navigation,
            "watch_progress": watch_progress,
            "audio_progress": audio_progress,
            "back_url": reverse("academics:student_course_detail", args=[version.id]),
            "back_label": _("Back to course"),
        },
    )


@student_required
def student_lecture_audio(request, version_id, lecture_id, file_path=""):
    """Serve lecture audio file (HLS playlist/segment) with permission check."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_access_lecture(profile, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))
    if not _is_valid_audio_file_path(file_path):
        raise Http404
    expected_prefix = f"lectures/{lecture.id}/audio/{file_path.split('/', 1)[0]}"
    if lecture.audio_hls_segment_prefix != expected_prefix:
        raise Http404
    storage_key = f"lectures/{lecture.id}/audio/{file_path}"
    try:
        file_data = storage_service.download_file(storage_key)
    except Exception:
        raise Http404 from None
    content_type = (
        "application/vnd.apple.mpegurl"
        if file_path.endswith(".m3u8")
        else "video/MP2T" if file_path.endswith(".ts")
        else "audio/mpeg"
    )
    return HttpResponse(file_data, content_type=content_type)


@student_required
def student_lecture_audio_download(request, version_id, lecture_id):
    """Serve original uploaded audio file with permission check."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_access_lecture(profile, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))
    if not lecture.audio_storage_key:
        raise Http404
    url = storage_service.get_protected_download_url(
        lecture.audio_storage_key,
        user=request.user,
        permission_fn=lambda u: can_access_lecture(profile, lecture),
    )
    return redirect(url)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_preview(request, course_id, version_id, lecture_id):
    """Render the student-facing lecture page for admin preview (no enrollment/release checks).

    The version is resolved with BOTH its own id and the route's course parent
    (S2-13), and the breadcrumb is built from the resolved object.
    """
    version = get_object_or_404(CourseVersion, id=version_id, course_id=course_id)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    return render(
        request,
        "ui/pages/admin/course_lecture_preview.html",
        {
            "version": version,
            "lecture": lecture,
            "is_preview": True,
            "youtube_video_id": lecture.youtube_video_id or extract_youtube_video_id(lecture.youtube_url),
            "breadcrumb_items": [
                {"label": _("Academics"), "href": reverse("academics:admin_course_list")},
                {"label": version.course.title, "href": reverse("academics:admin_course_detail", args=[course_id])},
                {"label": lecture.title},
            ],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Diploma lecture management (Phase 6 direct-lecture topology)
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_lecture_list(request, diploma_id, cohort_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lectures = DiplomaLecture.objects.filter(cohort=cohort, semester=semester).order_by("order")
    return render(
        request,
        "ui/pages/admin/diploma_lecture_list.html",
        {
            "diploma": diploma, "cohort": cohort, "semester": semester, "lectures": lectures,
            "breadcrumb_items": [
                {"label": _("Academics"), "href": reverse("academics:admin_diploma_list")},
                {"label": diploma.title, "href": reverse("academics:admin_diploma_detail", args=[diploma.id])},
                {"label": cohort.name}, {"label": semester.title},
            ],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_lecture_create(request, diploma_id, cohort_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaLectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.cohort = cohort
            lecture.semester = semester
            lecture.save()
            _process_lecture_audio(request, lecture, request.FILES, key_prefix=f"diploma-lectures/{lecture.id}/audio")
            enqueue_youtube_metadata_refresh(lecture, diploma=True)
            messages.success(request, _("Diploma lecture created."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        initial = {"order": DiplomaLecture.objects.filter(cohort=cohort, semester=semester).count() + 1}
        form = DiplomaLectureForm(initial=initial)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Create diploma lecture"), "diploma": diploma, "cohort": cohort, "semester": semester, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_lecture_edit(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = DiplomaLectureForm(request.POST, request.FILES, instance=lecture)
        if form.is_valid():
            previous_pending_key = lecture.audio_pending_storage_key
            previous_pending_prefix = (
                previous_pending_key.rsplit("/", 1)[0]
                if previous_pending_key.startswith(f"diploma-lectures/{lecture.id}/audio/")
                else ""
            )
            form.save()
            pending_replaced = _process_lecture_audio(
                request, lecture, request.FILES,
                key_prefix=f"diploma-lectures/{lecture.id}/audio",
            )
            enqueue_youtube_metadata_refresh(lecture, diploma=True)
            if pending_replaced and previous_pending_prefix:
                _cleanup_audio_after_commit([], [previous_pending_prefix])
            messages.success(request, _("Diploma lecture updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaLectureForm(instance=lecture)
    return render(
        request,
        "ui/pages/admin/academic_form.html",
        {"form": form, "title": _("Edit diploma lecture"), "diploma": diploma, "cohort": cohort, "semester": semester, "lecture": lecture, "next": next_url},
    )


@login_required
@require_POST
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_lecture_delete(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    next_url = safe_next_url(request)
    try:
        delete_diploma_lecture_record(lecture=lecture, actor=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    messages.success(request, _("Diploma lecture deleted."))
    return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_lecture_preview(request, diploma_id, cohort_id, semester_id, lecture_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=cohort, semester=semester)
    return render(
        request,
        "ui/pages/admin/lecture_preview.html",
        {
            "lecture": lecture,
            "version": None,
            "is_preview": True,
            "diploma": diploma,
            "cohort": cohort,
            "semester": semester,
            "youtube_video_id": lecture.youtube_video_id or extract_youtube_video_id(lecture.youtube_url),
            "breadcrumb_items": [
                {"label": _("Academics"), "href": reverse("academics:admin_diploma_list")},
                {"label": diploma.title, "href": reverse("academics:admin_diploma_detail", args=[diploma.id])},
                {"label": semester.title}, {"label": lecture.title},
            ],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_enrollment_list(request, course_id):
    """Show who is enrolled in each version of a course."""
    course = get_object_or_404(Course, id=course_id)
    q = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    version_filter = request.GET.get("version", "")
    enrollments = CourseEnrollment.objects.filter(
        course_version__course=course
    ).select_related(
        "student", "student__user", "course_version"
    ).order_by("-created_at")
    enrollments = enrollments.filter(enrollment_scope_filter(request.user, "course"))
    if status_filter:
        enrollments = enrollments.filter(status=status_filter)
    if version_filter:
        enrollments = enrollments.filter(course_version_id=version_filter)
    if q:
        enrollments = enrollments.filter(
            Q(student__user__full_name__icontains=q)
            | Q(student__user__email__icontains=q)
        )
    paginator = Paginator(enrollments, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    versions = CourseVersion.objects.filter(course=course)
    status_choices = [
        {"value": code, "label": str(label), "selected": status_filter == code}
        for code, label in ENROLLMENT_STATUS_CHOICES
    ]
    version_choices = [
        {"value": str(v.id), "label": str(v), "selected": version_filter == str(v.id)}
        for v in versions
    ]
    filters = [
        {"name": "status", "label": _("Enrollment Status"), "value": status_filter, "options": status_choices},
        {"name": "version", "label": _("Version"), "value": version_filter, "options": version_choices},
    ]
    pagination_qs = request.GET.copy()
    if "page" in pagination_qs:
        del pagination_qs["page"]
    pagination_qs = pagination_qs.urlencode()
    ctx = {
        "page_obj": page_obj,
        "search_value": q,
        "filters": filters,
        "pagination_qs": pagination_qs,
        "course": course,
        "enrollment_type": "course",
        "versions": versions,
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_enrollment_table.html", ctx)
    return render(request, "ui/pages/admin/enrollment_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_course_enrollment_migrate(request, course_id, enrollment_id):
    course = get_object_or_404(Course, id=course_id)
    enrollment = get_object_or_404(
        CourseEnrollment,
        id=enrollment_id,
        course_version__course=course,
    )
    target = get_object_or_404(
        CourseVersion,
        id=request.POST.get("version_id"),
        course=course,
        is_active=True,
    )
    if target.id != enrollment.course_version_id and CourseEnrollment.objects.filter(
        student=enrollment.student, course_version=target,
    ).exists():
        messages.error(request, _("The student already has an enrollment for that version."))
    else:
        enrollment.course_version = target
        enrollment.save(update_fields=["course_version", "updated_at"])
        messages.success(request, _("Student migrated to the selected course version."))
    return redirect("academics:admin_course_enrollment_list", course_id=course.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_enrollment_list(request, diploma_id):
    """Show who is enrolled in a diploma, optionally filtered by cohort."""
    diploma = get_object_or_404(Diploma, id=diploma_id)
    q = request.GET.get("q", "")
    status_filter = request.GET.get("status", "")
    cohort_filter = request.GET.get("cohort", "")
    enrollments = DiplomaEnrollment.objects.filter(
        cohort__diploma=diploma
    ).select_related(
        "student", "student__user", "cohort"
    ).order_by("-created_at")
    enrollments = enrollments.filter(enrollment_scope_filter(request.user, "diploma"))
    if status_filter:
        enrollments = enrollments.filter(status=status_filter)
    if cohort_filter:
        enrollments = enrollments.filter(cohort_id=cohort_filter)
    if q:
        enrollments = enrollments.filter(
            Q(student__user__full_name__icontains=q)
            | Q(student__user__email__icontains=q)
        )
    paginator = Paginator(enrollments, 25)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    cohorts = diploma.cohorts.all()
    status_choices = [
        {"value": code, "label": str(label), "selected": status_filter == code}
        for code, label in ENROLLMENT_STATUS_CHOICES
    ]
    cohort_choices = [
        {"value": str(c.id), "label": c.name, "selected": cohort_filter == str(c.id)}
        for c in cohorts
    ]
    filters = [
        {"name": "status", "label": _("Enrollment Status"), "value": status_filter, "options": status_choices},
        {"name": "cohort", "label": _("Cohort"), "value": cohort_filter, "options": cohort_choices},
    ]
    pagination_qs = request.GET.copy()
    if "page" in pagination_qs:
        del pagination_qs["page"]
    pagination_qs = pagination_qs.urlencode()
    ctx = {
        "page_obj": page_obj,
        "search_value": q,
        "filters": filters,
        "pagination_qs": pagination_qs,
        "diploma": diploma,
        "enrollment_type": "diploma",
        "cohorts": cohorts,
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_enrollment_table.html", ctx)
    return render(request, "ui/pages/admin/enrollment_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_diploma_enrollment_update(request, diploma_id, enrollment_id):
    """Update a diploma enrollment's status or cohort."""
    diploma = get_object_or_404(Diploma, id=diploma_id)
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id, cohort__diploma=diploma)
    new_status = request.POST.get("status")
    new_cohort_id = request.POST.get("cohort_id")
    if new_status and new_status in dict(ENROLLMENT_STATUS_CHOICES):
        enrollment.status = new_status
        if new_status == "active" and not enrollment.activated_at:
            enrollment.activated_at = timezone.now()
        enrollment.save(update_fields=["status", "activated_at", "updated_at"])
    if new_cohort_id:
        new_cohort = get_object_or_404(Cohort, id=new_cohort_id, diploma=diploma)
        if new_cohort_id != str(enrollment.cohort_id):
            enrollment.cohort = new_cohort
            enrollment.save(update_fields=["cohort", "updated_at"])
    msg = _("Enrollment updated.")
    messages.success(request, msg)
    if is_htmx(request):
        cohorts = diploma.cohorts.all()
        enrollment.refresh_from_db()
        response = render(request, "ui/fragments/admin/_enrollment_row.html", {
            "e": enrollment, "diploma": diploma, "cohorts": cohorts, "enrollment_type": "diploma",
        })
        response['HX-Trigger'] = json.dumps({'show-toast': {'message': msg, 'type': 'success'}})
        return response
    return redirect("academics:admin_diploma_enrollment_list", diploma_id=diploma.id)


# --- Watch API ----------------------------------------------------------------


@require_POST
@login_required
def api_watch_start(request, version_id, lecture_id):
    """POST start watch session. Returns session_id, resume_position, duration, interval, threshold."""
    try:
        version = CourseVersion.objects.get(id=version_id)
        lecture = Lecture.objects.get(id=lecture_id, course_version=version)
    except (CourseVersion.DoesNotExist, Lecture.DoesNotExist):
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    try:
        result = start_lecture_watch(student=student, lecture=lecture)
        return JsonResponse(result)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=403)


@require_POST
@login_required
def api_watch_checkpoint(request, version_id, lecture_id):
    """POST checkpoint. Returns progress, status, violation."""
    try:
        version = CourseVersion.objects.get(id=version_id)
        lecture = Lecture.objects.get(id=lecture_id, course_version=version)
    except (CourseVersion.DoesNotExist, Lecture.DoesNotExist):
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    result = record_lecture_checkpoint(student=student, lecture=lecture, payload=payload)
    http_status = result.pop("status", 200)
    if not isinstance(http_status, int):
        http_status = 200
    return JsonResponse(result, status=http_status)


@require_POST
@login_required
def api_audio_start(request, version_id, lecture_id):
    """POST start audio session. Returns session_id, resume_position, duration, interval, threshold."""
    try:
        version = CourseVersion.objects.get(id=version_id)
        lecture = Lecture.objects.get(id=lecture_id, course_version=version)
    except (CourseVersion.DoesNotExist, Lecture.DoesNotExist):
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    try:
        result = start_lecture_audio(student=student, lecture=lecture)
        return JsonResponse(result)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=403)


@require_POST
@login_required
def api_audio_checkpoint(request, version_id, lecture_id):
    """POST audio checkpoint. Returns progress, status, violation."""
    try:
        version = CourseVersion.objects.get(id=version_id)
        lecture = Lecture.objects.get(id=lecture_id, course_version=version)
    except (CourseVersion.DoesNotExist, Lecture.DoesNotExist):
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    result = record_lecture_audio_checkpoint(student=student, lecture=lecture, payload=payload)
    http_status = result.pop("status", 200)
    if not isinstance(http_status, int):
        http_status = 200
    return JsonResponse(result, status=http_status)


# --- Student: Diploma content views (Phase 6) ---------------------------------


@login_required
def student_diploma_detail(request, enrollment_id):
    """Show a student the semesters and lectures for their diploma enrollment."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if enrollment.student != profile and not request.user.is_superuser:
        raise PermissionDenied
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise PermissionDenied(_("Enrollment is not active."))
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        raise PermissionDenied(_("Enrollment has expired."))

    diploma = enrollment.cohort.diploma
    cohort = enrollment.cohort
    access = get_student_diploma_access(enrollment)

    semester_lectures = []
    all_lectures = []
    for semester in access["visible_semesters"]:
        lectures = get_visible_diploma_lectures(profile, enrollment, semester=semester)
        all_lectures.extend(lectures)
        semester_lectures.append({
            "semester": semester,
            "lectures": lectures,
        })

    attendance_code = AttendanceCode.objects.filter(
        diploma_enrollment=enrollment, active=True
    ).first()
    attendance_qr = ""
    if attendance_code:
        payload = build_attendance_qr_payload(attendance_code)
        img = qrcode.make(payload, box_size=6)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        attendance_qr = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    progress = diploma_progress_for_enrollment(profile, enrollment)
    attendance_summary = (
        get_attendance_summary(enrollment, access.get("current_semester"))
        if access.get("current_semester")
        else None
    )

    return render(request, "ui/pages/student/content.html", {
        "enrollment": enrollment,
        "diploma": diploma,
        "cohort": cohort,
        "semester_lectures": semester_lectures,
        "diploma_access": access,
        "attendance_code": attendance_code,
        "attendance_qr": attendance_qr,
        "progress": progress,
        "video_tracking_enabled": diploma.video_tracking_enabled,
        "attendance_summary": attendance_summary,
        "due_work": _assessment_due_work(request.user, cohort_id=cohort.id),
        "learning_assessments": _learning_assessments(request.user, cohort_id=cohort.id),
        "back_url": reverse("students:diploma_course"),
        "back_label": _("Back to learning"),
        "access_state": _("Active until %(date)s") % {"date": enrollment.expires_at.strftime("%x") if enrollment.expires_at else _("unlimited")},
    })


@login_required
def student_diploma_semester_detail(request, enrollment_id, semester_id):
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if enrollment.student != profile and not request.user.is_superuser:
        raise PermissionDenied
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise PermissionDenied(_("Enrollment is not active."))
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        raise PermissionDenied(_("Enrollment has expired."))
    semester = get_object_or_404(Semester, id=semester_id, diploma=enrollment.cohort.diploma)
    access = get_student_diploma_access(enrollment)
    if semester.id not in {item.id for item in access["visible_semesters"]}:
        raise PermissionDenied(_("This semester is not available yet."))
    lectures = list(get_visible_diploma_lectures(profile, enrollment, semester=semester))
    watch_rows = DiplomaLectureWatchProgress.objects.filter(student=profile, diploma_enrollment=enrollment, lecture__semester=semester)
    lecture_watch = {row.lecture_id: row for row in watch_rows}
    for lecture in lectures:
        lwp = lecture_watch.get(lecture.id)
        if lecture.youtube_duration_seconds:
            if lwp and lwp.validated_contiguous_seconds:
                pct = min(float(lwp.validated_contiguous_seconds) / float(lecture.youtube_duration_seconds) * 100, 100)
                lecture.watch_pct = round(pct)
                lecture.watch_status = lwp.status
            else:
                lecture.watch_pct = 0
                lecture.watch_status = "not_started"
        else:
            lecture.watch_pct = 0
            lecture.watch_status = None
    return render(request, "ui/pages/student/content.html", {
        "enrollment": enrollment,
        "diploma": enrollment.cohort.diploma,
        "cohort": enrollment.cohort,
        "semester": semester,
        "lectures": lectures,
        "access": access,
        "progress": diploma_progress_for_enrollment(profile, enrollment),
        "due_work": _assessment_due_work(request.user, cohort_id=enrollment.cohort_id),
        "learning_assessments": _learning_assessments(request.user, cohort_id=enrollment.cohort_id),
        "back_url": reverse("academics:student_diploma_detail", args=[enrollment.id]),
        "back_label": _("Back to diploma"),
        "video_tracking_enabled": enrollment.cohort.diploma.video_tracking_enabled,
    })


@login_required
def student_diploma_lecture_view(request, enrollment_id, semester_id, lecture_id):
    """Show a student a single diploma lecture."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if enrollment.student != profile and not request.user.is_superuser:
        raise PermissionDenied
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise PermissionDenied(_("Enrollment is not active."))
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        raise PermissionDenied(_("Enrollment has expired."))

    semester = get_object_or_404(Semester, id=semester_id, diploma=enrollment.cohort.diploma)
    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=enrollment.cohort, semester=semester)

    if not can_access_diploma_lecture(profile, enrollment, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))

    diploma = enrollment.cohort.diploma
    navigation = _lecture_navigation(
        get_visible_diploma_lectures(profile, enrollment, semester=semester), lecture,
        enrollment=enrollment, semester=semester,
    )
    watch_progress = DiplomaLectureWatchProgress.objects.filter(student=profile, lecture=lecture, diploma_enrollment=enrollment).first()
    return render(request, "ui/pages/student/content.html", {
        "lecture": lecture,
        "enrollment": enrollment,
        "semester": semester,
        "diploma": diploma,
        "version": None,
        "youtube_video_id": lecture.youtube_video_id or extract_youtube_video_id(lecture.youtube_url),
        "video_tracking_enabled": diploma.video_tracking_enabled,
        "navigation": navigation,
        "watch_progress": watch_progress,
        "back_url": reverse("academics:student_diploma_semester_detail", args=[enrollment.id, semester.id]),
        "back_label": _("Back to semester"),
    })


@login_required
def student_diploma_lecture_audio(request, enrollment_id, semester_id, lecture_id, file_path=""):
    """Serve diploma lecture audio file with permission check."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if enrollment.student != profile and not request.user.is_superuser:
        raise PermissionDenied
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise PermissionDenied(_("Enrollment is not active."))
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        raise PermissionDenied(_("Enrollment has expired."))

    lecture = get_object_or_404(
        DiplomaLecture,
        id=lecture_id,
        cohort=enrollment.cohort,
        semester_id=semester_id,
    )
    if not can_access_diploma_lecture(profile, enrollment, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))

    if not _is_valid_audio_file_path(file_path):
        raise Http404
    expected_prefix = f"diploma-lectures/{lecture.id}/audio/{file_path.split('/', 1)[0]}"
    if lecture.audio_hls_segment_prefix != expected_prefix:
        raise Http404
    storage_key = f"diploma-lectures/{lecture.id}/audio/{file_path}"
    try:
        file_data = storage_service.download_file(storage_key)
    except Exception:
        raise Http404 from None
    content_type = (
        "application/vnd.apple.mpegurl"
        if file_path.endswith(".m3u8")
        else "video/MP2T" if file_path.endswith(".ts")
        else "audio/mpeg"
    )
    return HttpResponse(file_data, content_type=content_type)


@login_required
def student_diploma_lecture_audio_download(request, enrollment_id, semester_id, lecture_id):
    """Serve original uploaded audio file for a diploma lecture."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None

    enrollment = get_object_or_404(DiplomaEnrollment, id=enrollment_id)
    if enrollment.student != profile and not request.user.is_superuser:
        raise PermissionDenied
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        raise PermissionDenied(_("Enrollment is not active."))
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        raise PermissionDenied(_("Enrollment has expired."))

    lecture = get_object_or_404(
        DiplomaLecture,
        id=lecture_id,
        cohort=enrollment.cohort,
        semester_id=semester_id,
    )
    if not can_access_diploma_lecture(profile, enrollment, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))
    if not lecture.audio_storage_key:
        raise Http404
    url = storage_service.get_protected_download_url(
        lecture.audio_storage_key,
        user=request.user,
        permission_fn=lambda u: can_access_diploma_lecture(profile, enrollment, lecture),
    )
    return redirect(url)


@require_POST
@login_required
def api_diploma_watch_start(request, enrollment_id, semester_id, lecture_id):
    """POST start watch session for a diploma lecture."""
    try:
        enrollment = DiplomaEnrollment.objects.get(id=enrollment_id)
    except DiplomaEnrollment.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    if enrollment.student_id != student.id and not request.user.is_superuser:
        return JsonResponse({"error": "forbidden"}, status=403)
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return JsonResponse({"error": "enrollment_not_active"}, status=403)
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        return JsonResponse({"error": "enrollment_expired"}, status=403)

    try:
        lecture = DiplomaLecture.objects.get(id=lecture_id, cohort=enrollment.cohort_id, semester_id=semester_id)
    except DiplomaLecture.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    if not can_access_diploma_lecture(student, enrollment, lecture):
        return JsonResponse({"error": "lecture_not_available"}, status=403)

    try:
        result = start_diploma_lecture_watch(student=student, enrollment=enrollment, lecture=lecture)
        return JsonResponse(result)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=403)


@require_POST
@login_required
def api_diploma_watch_checkpoint(request, enrollment_id, semester_id, lecture_id):
    """POST checkpoint for a diploma lecture."""
    try:
        enrollment = DiplomaEnrollment.objects.get(id=enrollment_id)
    except DiplomaEnrollment.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        student = StudentProfile.objects.get(user=request.user)
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_student_profile"}, status=400)

    if enrollment.student_id != student.id and not request.user.is_superuser:
        return JsonResponse({"error": "forbidden"}, status=403)
    if enrollment.status != ENROLLMENT_STATUS_ACTIVE:
        return JsonResponse({"error": "enrollment_not_active"}, status=403)
    if enrollment.expires_at and timezone.now() >= enrollment.expires_at:
        return JsonResponse({"error": "enrollment_expired"}, status=403)

    try:
        lecture = DiplomaLecture.objects.get(id=lecture_id, cohort=enrollment.cohort_id, semester_id=semester_id)
    except DiplomaLecture.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid_json"}, status=400)

    result = record_diploma_lecture_checkpoint(student=student, enrollment=enrollment, lecture=lecture, payload=payload)
    http_status = result.pop("status", 200)
    if not isinstance(http_status, int):
        http_status = 200
    return JsonResponse(result, status=http_status)
