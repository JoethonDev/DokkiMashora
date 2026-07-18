"""Views for academic structure management and student content access."""

import base64
import io as _io
import json
import logging
import threading

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.audio import transcode_to_hls, validate_audio_file
from apps.academics.constants import ENROLLMENT_STATUS_ACTIVE, ENROLLMENT_STATUS_CHOICES
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
    Lecture,
    Semester,
)
from apps.academics.services import (
    can_access_course,
    can_access_diploma_lecture,
    can_access_lecture,
    create_cohort,
    delete_cohort_delivery,
    get_student_course_versions,
    get_student_diploma_access,
    get_visible_diploma_lectures,
    get_visible_lectures,
    record_diploma_lecture_checkpoint,
    record_lecture_checkpoint,
    start_diploma_lecture_watch,
    start_lecture_watch,
)
from apps.attendance.models import AttendanceCode
from apps.attendance.services import build_attendance_qr_payload
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import (
    ROLE_EXAM_STAFF,
    can_manage_course_version,
    enrollment_scope_filter,
    is_student,
    role_required,
    student_required,
)
from apps.core.views import safe_next_url
from apps.intake_forms.models import IntakeForm
from apps.storage.services import storage_service
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
    diplomas = Diploma.objects.select_related("created_by").all()
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
        return render(request, "academics/partials/_diploma_list_table.html", ctx)
    return render(request, "academics/admin_diploma_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_detail(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semesters = diploma.semesters.all()
    cohorts = diploma.cohorts.all()
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
    # Build semester + cohort lecture data
    semester_cohort_data = []
    for semester in semesters:
        cohort_lectures = []
        for cohort in cohorts:
            l_count = count_lookup.get((cohort.id, semester.id), 0)
            cohort_lectures.append({"cohort": cohort, "lecture_count": l_count})
        semester_cohort_data.append({"semester": semester, "cohort_lectures": cohort_lectures})
    return render(
        request,
        "academics/admin_diploma_detail.html",
        {
            "diploma": diploma,
            "semesters": semesters,
            "cohorts": cohorts,
            "semester_cohort_data": semester_cohort_data,
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
        "academics/admin_diploma_form.html",
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
        "academics/admin_diploma_form.html",
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
    diploma.delete()
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
        "academics/admin_semester_form.html",
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
    if request.method == "POST":
        form = SemesterForm(request.POST, instance=semester, diploma=diploma)
        if form.is_valid():
            form.save()
            messages.success(request, _("Semester updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = SemesterForm(instance=semester, diploma=diploma)
    return render(
        request,
        "academics/admin_semester_form.html",
        {
            "form": form,
            "title": _("Edit semester"),
            "diploma": diploma,
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
    semester.delete()
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
    courses = Course.objects.select_related("created_by").all()
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
        return render(request, "academics/partials/_course_list_table.html", ctx)
    return render(request, "academics/admin_course_list.html", ctx)


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    versions = course.versions.prefetch_related("lectures").all()
    return render(
        request,
        "academics/admin_course_detail.html",
        {"course": course, "versions": versions},
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
        "academics/admin_course_form.html",
        {"form": form, "title": _("Create course"), "next": next_url, "diploma": None, "semesters": [], "diplomas": None},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_semester_options(request):
    diploma_id = request.GET.get("diploma_id")
    semesters = Diploma.objects.get(id=diploma_id).semesters.all() if diploma_id else []
    return render(request, "academics/partials/_semester_options.html", {"semesters": semesters})


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
        "academics/admin_course_form.html",
        {"form": form, "title": _("Edit course"), "course": course, "next": next_url, "semester_courses": [], "diploma": None, "semesters": []},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_course_delete(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    next_url = safe_next_url(request)
    course.delete()
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
        "academics/admin_cohort_form.html",
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
        "academics/admin_cohort_form.html",
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
    delete_cohort_delivery(cohort=cohort)
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
        "academics/admin_version_form.html",
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
        "academics/admin_version_detail.html",
        {"course": course, "version": version, "lectures": lectures, "intake_forms": intake_forms},
    )


# ---------------------------------------------------------------------------
# Admin: Lecture management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_create(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    if not can_manage_course_version(request.user, version):
        raise PermissionDenied
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.course_version = version
            lecture.save()
            _process_lecture_audio(request, lecture, request.FILES)
            messages.success(request, _("Lecture created."))
            return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(initial={"order": version.lectures.count() + 1})
    return render(
        request,
        "academics/admin_lecture_form.html",
        {"form": form, "title": _("Create lecture"), "course": course, "version": version, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_edit(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_manage_course_version(request.user, version):
        raise PermissionDenied
    next_url = safe_next_url(request)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES, instance=lecture)
        if form.is_valid():
            form.save()
            _process_lecture_audio(request, lecture, request.FILES)
            messages.success(request, _("Lecture updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(instance=lecture)
    return render(
        request,
        "academics/admin_lecture_form.html",
        {"form": form, "title": _("Edit lecture"), "course": course, "version": version, "next": next_url},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_lecture_delete(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_manage_course_version(request.user, version):
        raise PermissionDenied
    next_url = safe_next_url(request)
    lecture.delete()
    messages.success(request, _("Lecture deleted."))
    return redirect(next_url) if next_url else redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_version_delete(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    next_url = safe_next_url(request)
    version.delete()
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
    audio_file = files.get("audio_file")
    if not audio_file:
        return
    prefix = key_prefix or f"lectures/{lecture.id}/audio"
    try:
        data = audio_file.read()
        filename = str(audio_file)
        content_type = audio_file.content_type or "audio/mpeg"
        validate_audio_file(filename, content_type)
        result = storage_service.upload_file(
            data,
            key_prefix=prefix,
            content_type=content_type,
            max_size=200 * 1024 * 1024,
            allowed_types=[".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"],
            original_filename=filename,
        )
        lecture.audio_storage_key = result.key
        lecture.audio_original_filename = filename
        lecture.audio_content_type = result.content_type
        lecture.audio_byte_size = result.byte_size
        lecture.audio_checksum = result.checksum
        lecture.save(update_fields=[
            "audio_storage_key", "audio_original_filename",
            "audio_content_type", "audio_byte_size", "audio_checksum",
        ])
        # ponytail: sync FFmpeg blocks the request. Switch to Celery/Task queue if uploads >50MB.
        threading.Thread(target=transcode_to_hls, args=(lecture, data, filename, content_type), daemon=True).start()
    except Exception as exc:
        logger.exception("Audio upload/processing failed for lecture %s", lecture.id)
        messages.error(request, _("Audio processing failed: %(error)s") % {"error": str(exc)})


# ---------------------------------------------------------------------------
# Student: content access
# ---------------------------------------------------------------------------


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
    return render(
        request,
        "academics/student_course_detail.html",
        {
            "version": version,
            "lectures": lectures,
            "course_attendance_code": course_attendance_code,
            "course_attendance_qr": course_attendance_qr,
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
    return render(
        request,
        "academics/student_lecture_view.html",
        {"version": version, "lecture": lecture},
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
    """Render the student-facing lecture page for admin preview (no enrollment/release checks)."""
    version = get_object_or_404(CourseVersion, id=version_id)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    return render(
        request,
        "academics/student_lecture_view.html",
        {"version": version, "lecture": lecture, "is_preview": True},
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
        "academics/admin_diploma_lecture_list.html",
        {"diploma": diploma, "cohort": cohort, "semester": semester, "lectures": lectures},
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
            messages.success(request, _("Diploma lecture created."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        initial = {"order": DiplomaLecture.objects.filter(cohort=cohort, semester=semester).count() + 1}
        form = DiplomaLectureForm(initial=initial)
    return render(
        request,
        "academics/admin_lecture_form.html",
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
            form.save()
            _process_lecture_audio(request, lecture, request.FILES, key_prefix=f"diploma-lectures/{lecture.id}/audio")
            messages.success(request, _("Diploma lecture updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaLectureForm(instance=lecture)
    return render(
        request,
        "academics/admin_lecture_form.html",
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
    lecture.delete()
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
        "academics/student_lecture_view.html",
        {
            "lecture": lecture,
            "version": None,
            "is_preview": True,
            "diploma": diploma,
            "cohort": cohort,
            "semester": semester,
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
        return render(request, "academics/partials/_enrollment_table.html", ctx)
    return render(request, "academics/admin_enrollment_list.html", ctx)


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
        return render(request, "academics/partials/_enrollment_table.html", ctx)
    return render(request, "academics/admin_enrollment_list.html", ctx)


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
    if is_htmx(request):
        cohorts = diploma.cohorts.all()
        enrollment.refresh_from_db()
        return render(request, "academics/partials/_enrollment_row.html", {
            "e": enrollment, "diploma": diploma, "cohorts": cohorts, "enrollment_type": "diploma",
        })
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
    for semester in access["visible_semesters"]:
        lectures = get_visible_diploma_lectures(profile, enrollment, semester=semester)
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

    return render(request, "academics/student_diploma_detail.html", {
        "enrollment": enrollment,
        "diploma": diploma,
        "cohort": cohort,
        "semester_lectures": semester_lectures,
        "diploma_access": access,
        "attendance_code": attendance_code,
        "attendance_qr": attendance_qr,
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
    return render(request, "academics/student_diploma_semester_detail.html", {
        "enrollment": enrollment,
        "diploma": enrollment.cohort.diploma,
        "cohort": enrollment.cohort,
        "semester": semester,
        "lectures": get_visible_diploma_lectures(profile, enrollment, semester=semester),
        "access": access,
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
    return render(request, "academics/student_lecture_view.html", {
        "lecture": lecture,
        "enrollment": enrollment,
        "semester": semester,
        "diploma": diploma,
        "version": None,
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

    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=enrollment.cohort)
    if not can_access_diploma_lecture(profile, enrollment, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))

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

    lecture = get_object_or_404(DiplomaLecture, id=lecture_id, cohort=enrollment.cohort)
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
