"""Views for academic structure management and student content access."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
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
    SemesterCourse,
)


def _auto_create_cohort_versions(course, semester, user):
    """Create or update a CourseVersion per cohort. Semester goes in defaults so
    changing semesters updates existing rows instead of violating the unique constraint."""
    for cohort in semester.diploma.cohorts.all():
        cv, created = CourseVersion.objects.get_or_create(
            course=course,
            cohort=cohort,
            label="v1",
            defaults={"semester": semester, "created_by": user, "is_active": True},
        )
        if not created and cv.semester_id != semester.id:
            cv.semester = semester
            cv.save(update_fields=["semester", "updated_at"])
from apps.academics.services import (
    can_access_course,
    can_access_lecture,
    get_student_course_versions,
    get_visible_lectures,
)
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_EXAM_STAFF, is_student, role_required
from apps.core.views import safe_next_url
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
    sc_qs = SemesterCourse.objects.filter(semester__diploma=diploma).select_related("course", "semester")
    cv_qs = CourseVersion.objects.filter(semester__diploma=diploma).select_related("course", "semester", "cohort").annotate(lecture_count=Count("lectures"))
    cv_by_course_semester = {}
    for cv in cv_qs:
        cv_by_course_semester.setdefault((cv.course_id, cv.semester_id), []).append(cv)
    semester_data = []
    for semester in semesters:
        courses = []
        for sc in [s for s in sc_qs if s.semester_id == semester.id]:
            key = (sc.course_id, sc.semester_id)
            courses.append({"course": sc.course, "sc_id": sc.id, "versions": cv_by_course_semester.get(key, [])})
        semester_data.append({"semester": semester, "courses": courses})
    return render(
        request,
        "academics/admin_diploma_detail.html",
        {
            "diploma": diploma,
            "semesters": semesters,
            "cohorts": cohorts,
            "semester_data": semester_data,
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
    course_count = CourseVersion.objects.filter(semester=semester).count()
    if course_count:
        messages.warning(
            request,
            _("Cannot delete semester with %(count)d course version(s). Remove them first.") % {"count": course_count},
        )
    else:
        semester.delete()
        messages.success(request, _("Semester deleted."))
    return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma.id)


@login_required
@role_required(*ROLE_EXAM_STAFF)
@require_POST
def admin_semester_course_delete(request, sc_id):
    sc = get_object_or_404(SemesterCourse, id=sc_id)
    diploma_id = sc.semester.diploma_id
    next_url = safe_next_url(request)
    version_count = CourseVersion.objects.filter(
        course=sc.course, semester=sc.semester,
    ).count()
    if version_count:
        messages.warning(
            request,
            _("Cannot remove course: %(count)d version(s) are linked to it in this semester.") % {"count": version_count},
        )
    else:
        sc.delete()
        messages.success(request, _("Course removed from semester."))
    return redirect(next_url) if next_url else redirect("academics:admin_diploma_detail", diploma_id=diploma_id)


# ---------------------------------------------------------------------------
# Admin: Course management
# ---------------------------------------------------------------------------


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_list(request):
    q = request.GET.get("q", "")
    is_active_filter = request.GET.get("is_active", "")
    is_standalone_filter = request.GET.get("is_standalone", "")
    courses = Course.objects.select_related("created_by").all()
    if q:
        courses = courses.filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )
    if is_active_filter:
        courses = courses.filter(is_active=is_active_filter == "1")
    if is_standalone_filter:
        courses = courses.filter(is_standalone=is_standalone_filter == "1")
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
    filters.append({
        "name": "is_standalone",
        "label": _("Type"),
        "value": is_standalone_filter,
        "options": [
            {"value": "1", "label": _("Standalone"), "selected": is_standalone_filter == "1"},
            {"value": "0", "label": _("Diploma"), "selected": is_standalone_filter == "0"},
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
    versions = course.versions.select_related("cohort", "semester").prefetch_related("lectures").all()
    semester_courses = course.semester_courses.select_related("semester__diploma").all()
    return render(
        request,
        "academics/admin_course_detail.html",
        {"course": course, "versions": versions, "semester_courses": semester_courses},
    )


@login_required
@role_required(*ROLE_EXAM_STAFF)
def admin_course_create(request):
    next_url = safe_next_url(request)
    diploma_id = request.GET.get("diploma_id") or request.POST.get("diploma_id")
    semester_id = request.GET.get("semester_id") or request.POST.get("semester_id")
    diploma = None
    if diploma_id:
        diploma = get_object_or_404(Diploma, id=diploma_id)
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save(commit=False)
            course.created_by = request.user
            course.save()
            try:
                linked_semester = None
                if semester_id and diploma:
                    sc, _created = SemesterCourse.objects.get_or_create(course=course, semester_id=semester_id)
                    linked_semester = sc.semester
                elif diploma_id and not semester_id:
                    semester = diploma.semesters.first()
                    if semester:
                        SemesterCourse.objects.get_or_create(course=course, semester=semester)
                        linked_semester = semester
                if linked_semester:
                    _auto_create_cohort_versions(course, linked_semester, request.user)
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, 'message') else str(e))
                return redirect("academics:admin_course_edit", course_id=course.id)
            messages.success(request, _("Course created."))
            return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)
    else:
        standalone = request.GET.get("standalone") == "1"
        initial = {"is_standalone": standalone} if standalone else {}
        if diploma:
            semesters = diploma.semesters.all()
        else:
            semesters = []
        form = CourseForm(initial=initial)
    diplomas = Diploma.objects.all() if not diploma_id else None
    return render(
        request,
        "academics/admin_course_form.html",
        {"form": form, "title": _("Create course"), "next": next_url, "diploma": diploma, "semesters": semesters, "diplomas": diplomas},
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
    semester_courses = course.semester_courses.select_related("semester__diploma").all()
    diploma = semester_courses[0].semester.diploma if semester_courses else None
    semesters = diploma.semesters.all() if diploma else []
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            course = form.save()
            new_semester_id = request.POST.get("semester_id")
            if new_semester_id and semester_courses:
                sc = semester_courses[0]
                if str(sc.semester_id) != new_semester_id:
                    try:
                        sc.semester_id = new_semester_id
                        sc.save()
                        _auto_create_cohort_versions(course, sc.semester, request.user)
                    except ValidationError as e:
                        messages.error(request, e.message if hasattr(e, 'message') else str(e))
                        return redirect("academics:admin_course_detail", course_id=course.id)
            messages.success(request, _("Course updated."))
            return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)
    else:
        form = CourseForm(instance=course)
    return render(
        request,
        "academics/admin_course_form.html",
        {"form": form, "title": _("Edit course"), "course": course, "next": next_url, "semester_courses": semester_courses, "diploma": diploma, "semesters": semesters},
    )


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
            cohort = form.save(commit=False)
            cohort.diploma = diploma
            cohort.created_by = request.user
            cohort.save()
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
    # Lectures and course enrollments cascade-delete via FK on_delete=CASCADE.
    version.delete()
    messages.success(request, _("Course version deleted."))
    return redirect(next_url) if next_url else redirect("academics:admin_course_detail", course_id=course.id)


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
