"""Views for academic structure management and student content access."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.academics.audio import transcode_to_hls, validate_audio_file
from apps.academics.forms import (
    CohortForm,
    CourseForm,
    CourseVersionForm,
    DiplomaForm,
    LectureForm,
    SemesterForm,
)
from apps.academics.models import (
    Cohort,
    Course,
    CourseVersion,
    Diploma,
    Lecture,
    Semester,
)
from apps.academics.services import (
    can_access_course,
    can_access_lecture,
    get_student_course_versions,
    get_visible_lectures,
)
from apps.core.permissions import ROLE_EXAM_STAFF, is_student, role_required
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
    diplomas = Diploma.objects.select_related("created_by").all()
    return render(request, "academics/admin_diploma_list.html", {"diplomas": diplomas})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_detail(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semesters = diploma.semesters.all()
    cohorts = diploma.cohorts.all()
    return render(
        request,
        "academics/admin_diploma_detail.html",
        {
            "diploma": diploma,
            "semesters": semesters,
            "cohorts": cohorts,
            "breadcrumb_items": _diploma_breadcrumbs(diploma),
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_create(request):
    if request.method == "POST":
        form = DiplomaForm(request.POST)
        if form.is_valid():
            diploma = form.save(commit=False)
            diploma.created_by = request.user
            diploma.save()
            messages.success(request, _("Diploma created."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaForm()
    return render(
        request,
        "academics/admin_diploma_form.html",
        {
            "form": form,
            "title": _("Create diploma"),
            "breadcrumb_items": _diploma_breadcrumbs() + [{"label": _("New diploma")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_diploma_edit(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    if request.method == "POST":
        form = DiplomaForm(request.POST, instance=diploma)
        if form.is_valid():
            form.save()
            messages.success(request, _("Diploma updated."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = DiplomaForm(instance=diploma)
    return render(
        request,
        "academics/admin_diploma_form.html",
        {
            "form": form,
            "title": _("Edit diploma"),
            "diploma": diploma,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit")}],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Semester management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_semester_create(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    if request.method == "POST":
        form = SemesterForm(request.POST)
        if form.is_valid():
            semester = form.save(commit=False)
            semester.diploma = diploma
            semester.save()
            messages.success(request, _("Semester created."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = SemesterForm(initial={"diploma": diploma.id, "order": diploma.semesters.count() + 1})
    return render(
        request,
        "academics/admin_semester_form.html",
        {
            "form": form,
            "title": _("Create semester"),
            "diploma": diploma,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("New semester")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_semester_edit(request, diploma_id, semester_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    semester = get_object_or_404(Semester, id=semester_id, diploma=diploma)
    if request.method == "POST":
        form = SemesterForm(request.POST, instance=semester)
        if form.is_valid():
            form.save()
            messages.success(request, _("Semester updated."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = SemesterForm(instance=semester)
    return render(
        request,
        "academics/admin_semester_form.html",
        {
            "form": form,
            "title": _("Edit semester"),
            "diploma": diploma,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit semester")}],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Course management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_list(request):
    courses = Course.objects.select_related("created_by").all()
    return render(request, "academics/admin_course_list.html", {"courses": courses})


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_detail(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    versions = course.versions.select_related("cohort", "semester").all()
    return render(
        request,
        "academics/admin_course_detail.html",
        {"course": course, "versions": versions},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_create(request):
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save(commit=False)
            course.created_by = request.user
            course.save()
            messages.success(request, _("Course created."))
            return redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseForm()
    return render(
        request,
        "academics/admin_course_form.html",
        {"form": form, "title": _("Create course")},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_edit(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, _("Course updated."))
            return redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseForm(instance=course)
    return render(
        request,
        "academics/admin_course_form.html",
        {"form": form, "title": _("Edit course"), "course": course},
    )


# ---------------------------------------------------------------------------
# Admin: Cohort management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_cohort_create(request, diploma_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    if request.method == "POST":
        form = CohortForm(request.POST)
        if form.is_valid():
            cohort = form.save(commit=False)
            cohort.diploma = diploma
            cohort.created_by = request.user
            cohort.save()
            messages.success(request, _("Cohort created."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = CohortForm(initial={"diploma": diploma.id})
    return render(
        request,
        "academics/admin_cohort_form.html",
        {
            "form": form,
            "title": _("Create cohort"),
            "diploma": diploma,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("New cohort")}],
        },
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_cohort_edit(request, diploma_id, cohort_id):
    diploma = get_object_or_404(Diploma, id=diploma_id)
    cohort = get_object_or_404(Cohort, id=cohort_id, diploma=diploma)
    if request.method == "POST":
        form = CohortForm(request.POST, instance=cohort)
        if form.is_valid():
            form.save()
            messages.success(request, _("Cohort updated."))
            return redirect("academics:admin_diploma_detail", diploma_id=diploma.id)
    else:
        form = CohortForm(instance=cohort)
    return render(
        request,
        "academics/admin_cohort_form.html",
        {
            "form": form,
            "title": _("Edit cohort"),
            "diploma": diploma,
            "breadcrumb_items": _diploma_breadcrumbs(diploma) + [{"label": _("Edit cohort")}],
        },
    )


# ---------------------------------------------------------------------------
# Admin: Course version management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_version_create(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == "POST":
        form = CourseVersionForm(request.POST)
        if form.is_valid():
            version = form.save(commit=False)
            version.course = course
            version.created_by = request.user
            version.save()
            messages.success(request, _("Course version created."))
            return redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseVersionForm(initial={"course": course.id})
    return render(
        request,
        "academics/admin_version_form.html",
        {"form": form, "title": _("Create course version"), "course": course},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_version_detail(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lectures = version.lectures.all()
    return render(
        request,
        "academics/admin_version_detail.html",
        {"course": course, "version": version, "lectures": lectures},
    )


# ---------------------------------------------------------------------------
# Admin: Lecture management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_create(request, course_id, version_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES)
        if form.is_valid():
            lecture = form.save(commit=False)
            lecture.course_version = version
            lecture.save()
            _process_lecture_audio(request, lecture, request.FILES)
            messages.success(request, _("Lecture created."))
            return redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(initial={"order": version.lectures.count() + 1})
    return render(
        request,
        "academics/admin_lecture_form.html",
        {"form": form, "title": _("Create lecture"), "course": course, "version": version},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_lecture_edit(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if request.method == "POST":
        form = LectureForm(request.POST, request.FILES, instance=lecture)
        if form.is_valid():
            form.save()
            _process_lecture_audio(request, lecture, request.FILES)
            messages.success(request, _("Lecture updated."))
            return redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)
    else:
        form = LectureForm(instance=lecture)
    return render(
        request,
        "academics/admin_lecture_form.html",
        {"form": form, "title": _("Edit lecture"), "course": course, "version": version},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_lecture_delete(request, course_id, version_id, lecture_id):
    course = get_object_or_404(Course, id=course_id)
    version = get_object_or_404(CourseVersion, id=version_id, course=course)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    lecture.delete()
    messages.success(request, _("Lecture deleted."))
    return redirect("academics:admin_version_detail", course_id=course.id, version_id=version.id)


def _process_lecture_audio(request, lecture, files):
    audio_file = files.get("audio_file")
    if not audio_file:
        return
    try:
        data = audio_file.read()
        filename = str(audio_file)
        content_type = audio_file.content_type or "audio/mpeg"
        validate_audio_file(filename, content_type)
        result = storage_service.upload_file(
            data,
            key_prefix=f"lectures/{lecture.id}/audio",
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
        import threading
        threading.Thread(target=transcode_to_hls, args=(lecture, data, filename, content_type), daemon=True).start()
    except Exception as exc:
        logger.exception("Audio upload/processing failed for lecture %s", lecture.id)
        messages.error(request, _("Audio processing failed: %(error)s") % {"error": str(exc)})


# ---------------------------------------------------------------------------
# Student: content access
# ---------------------------------------------------------------------------


@login_required
def student_course_list(request):
    """Show the student their enrolled courses."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        return render(request, "academics/student_course_list.html", {"course_versions": []})
    course_versions = get_student_course_versions(profile)
    return render(
        request,
        "academics/student_course_list.html",
        {"course_versions": course_versions},
    )


@login_required
def student_course_detail(request, version_id):
    """Show a student the lectures for a course version they can access."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id, is_active=True)
    if not can_access_course(profile, version):
        raise PermissionDenied(_("You do not have access to this course."))
    lectures = get_visible_lectures(profile, version)
    return render(
        request,
        "academics/student_course_detail.html",
        {"version": version, "lectures": lectures},
    )


@login_required
def student_lecture_view(request, version_id, lecture_id):
    """Show a student a single lecture (YouTube embed, authenticated only)."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id, is_active=True)
    lecture = get_object_or_404(Lecture, id=lecture_id, course_version=version)
    if not can_access_lecture(profile, lecture):
        raise PermissionDenied(_("This lecture is not available yet."))
    return render(
        request,
        "academics/student_lecture_view.html",
        {"version": version, "lecture": lecture},
    )


@login_required
def student_lecture_audio(request, version_id, lecture_id, file_path=""):
    """Serve lecture audio file (HLS playlist/segment) with permission check."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id, is_active=True)
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


@login_required
def student_lecture_audio_download(request, version_id, lecture_id):
    """Serve original uploaded audio file with permission check."""
    if not is_student(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    try:
        profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        raise Http404 from None
    version = get_object_or_404(CourseVersion, id=version_id, is_active=True)
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
