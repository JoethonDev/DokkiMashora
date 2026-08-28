"""Core views: landing page, error handlers, language switcher."""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.academics.models import Cohort, Course, CourseVersion, Diploma, Lecture
from apps.accounts.models import User
from apps.applications.models import ProgramApplication
from apps.core.forms import LandingPageSettingsForm
from apps.core.models import LandingAnnouncement, LandingPageSettings
from apps.core.htmx import is_htmx
from apps.core.navigation import workspace_navigation
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, role_required
from apps.students.services import get_open_apply_targets


def safe_next_url(request, default=""):
    url = request.POST.get("next", "") or request.GET.get("next", "")
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}):
        return url
    return default


def landing(request: HttpRequest) -> HttpResponse:
    """Public landing page."""
    open_diploma_targets = get_open_apply_targets("diploma", page_size=100)["items"]
    open_course_targets = get_open_apply_targets("course", page_size=100)["items"]
    program_items = []
    for target in open_diploma_targets:
        program_items.append({
            "kind": "diploma",
            "title": target.diploma.title,
            "description": target.diploma.description or "",
            "target_label": target.name,
            "apply_url": reverse("students:apply") + f"?program_type=diploma&target_id={target.id}",
        })
    open_course_ids = {c.pk for c in open_course_targets}
    courses_qs = list(Course.objects.filter(is_active=True, pk__in=open_course_ids).order_by("title")[:6])
    for c in courses_qs:
        program_items.append({
            "kind": "course",
            "title": c.title,
            "description": c.description or "",
            "target_label": "",
            "apply_url": reverse("students:apply") + f"?program_type=course&target_id={c.id}",
        })
    settings_page = LandingPageSettings.load()
    landing_content = settings_page.localized_content(request.LANGUAGE_CODE)
    announcement = LandingAnnouncement.current()
    if announcement and announcement.title == "Registration open":
        announcement.title = landing_content["announcement_title"]
        announcement.message = landing_content["announcement_body"]
    context = {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_NAME_AR": settings.SITE_NAME_AR,
        "landing_announcement": announcement,
        "active_diplomas": [i for i in program_items if i["kind"] == "diploma"],
        "courses": [i for i in program_items if i["kind"] == "course"],
        "program_items": program_items,
        "landing_settings": settings_page,
        "landing_content": landing_content,
    }
    return render(request, "ui/pages/public/landing.html", context)


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "POST"])
def landing_settings(request: HttpRequest) -> HttpResponse:
    settings_page = LandingPageSettings.load()
    if request.method == "POST" and request.POST.get("action") == "reset":
        settings_page.reset_to_defaults()
        messages.success(request, _("Landing page restored to the default preset."))
        return redirect("core:landing_settings")
    form = LandingPageSettingsForm(request.POST or None, request.FILES or None, instance=settings_page)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Landing page updated."))
        return redirect("core:landing_settings")
    return render(
        request,
        "ui/pages/admin/landing_settings.html",
        {
            "form": form,
            "course_versions": CourseVersion.objects.select_related("course").order_by("course__title", "label"),
            "diploma_cohorts": Cohort.objects.select_related("diploma").order_by("diploma__title", "name"),
        },
    )


@login_required
@require_http_methods(["GET"])
def workspace_search(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q", "") or "").strip()
    user = request.user
    nav = workspace_navigation(user)
    can_search_all = is_admin(user)

    if not is_htmx(request):
        return redirect("accounts:dashboard")

    results = []
    no_results = False
    search_error = False
    nav_results = []

    try:
        # Navigation always included
        for item in nav:
            label_match = q.lower() in item["label"].lower() if q else True
            kw_match = any(q.lower() in kw.lower() for kw in item["keywords"].split()) if q else False
            if not q or label_match or kw_match:
                nav_results.append({
                    "label": item["label"],
                    "subtitle": item["section"],
                    "url": reverse(item["url_name"]),
                })
        if nav_results:
            results.append({"name": "navigation", "label": _("Navigation"), "items": nav_results[:5]})

        if len(q) >= 2 and can_search_all:
            # Students (phone is single-owner on StudentProfile.phone)
            students = User.objects.filter(
                Q(full_name__icontains=q) | Q(email__icontains=q) | Q(student_profile__phone__icontains=q) | Q(national_id__icontains=q),
                groups__name="Student",
            )[:5]
            if students:
                student_items = []
                for s in students:
                    student_items.append({
                        "label": s.full_name or s.email,
                        "subtitle": s.email,
                        "url": reverse("accounts:admin_user_detail", args=[s.pk]),
                    })
                results.append({"name": "students", "label": _("Students"), "items": student_items})

            # Applications
            apps = ProgramApplication.objects.filter(
                Q(student__user__full_name__icontains=q) | Q(student__user__email__icontains=q),
            ).select_related("student__user", "cohort__diploma", "course")[:5]
            if apps:
                app_items = []
                for a in apps:
                    app_items.append({
                        "label": a.student.user.full_name or a.student.user.email,
                        "subtitle": str(a.cohort.diploma if a.cohort else a.course or ""),
                        "url": reverse("applications:program_detail", args=[a.pk]),
                    })
                results.append({"name": "applications", "label": _("Applications"), "items": app_items})

            # Diplomas
            diplomas = Diploma.objects.filter(
                Q(title__icontains=q),
            )[:5]
            if diplomas:
                diploma_items = []
                for d in diplomas:
                    diploma_items.append({
                        "label": d.title,
                        "subtitle": _("Active") if d.is_active else _("Inactive"),
                        "url": reverse("academics:admin_diploma_detail", args=[d.pk]),
                    })
                results.append({"name": "diplomas", "label": _("Diplomas"), "items": diploma_items})

            # Courses
            courses = Course.objects.filter(
                Q(title__icontains=q),
            )[:5]
            if courses:
                course_items = []
                for c in courses:
                    course_items.append({
                        "label": c.title,
                        "subtitle": _("Active") if c.is_active else _("Inactive"),
                        "url": reverse("academics:admin_course_detail", args=[c.pk]),
                    })
                results.append({"name": "courses", "label": _("Courses"), "items": course_items})

        if not results and q:
            no_results = True

    except Exception:
        search_error = True

    return render(request, "ui/fragments/shared/_global_search_results.html", {
        "q": q,
        "results": results,
        "no_results": no_results,
        "search_error": search_error,
        "navigation": nav_results if q and not can_search_all else [],
    })


@login_required
@role_required(*ROLE_SENIOR_STAFF)
@require_http_methods(["GET", "HEAD"])
def design_showcase(request: HttpRequest) -> HttpResponse:
    """Admin-only UI layout showcase for reviewing LMS design directions."""
    return render(request, "ui/pages/admin/dashboard.html", {"title": _("Design showcase")})


@csrf_protect
@require_http_methods(["POST"])
def set_language(request: HttpRequest) -> HttpResponse:
    """Switch the active language and redirect to the same page in the new language."""
    new_language = request.POST.get("language")
    next_url = request.POST.get("next", "/")
    available_codes = {code for code, _name in settings.LANGUAGES}
    if new_language not in available_codes:
        return HttpResponseBadRequest(_("Invalid language"))
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"
    translated_url = rewrite_language_url(next_url, new_language)
    response = HttpResponseRedirect(translated_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        new_language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        secure=request.is_secure(),
        httponly=False,
        samesite="Lax",
    )
    return response


def rewrite_language_url(url: str, new_language: str) -> str:
    """Strip any known language prefix and add the new one."""
    available = {code for code, _name in settings.LANGUAGES}
    path = url
    for code in available:
        prefix = f"/{code}"
        if path == prefix or path == f"{prefix}/":
            path = "/"
            break
        if path.startswith(f"{prefix}/"):
            path = path[len(prefix) :]
            break
    if new_language != settings.LANGUAGE_CODE:
        if not path.startswith("/"):
            path = "/" + path
        path = f"/{new_language}{path}"
    return path


def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "ui/errors/400.html", status=400)


def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "ui/errors/403.html", status=403)


def page_not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "ui/errors/404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "ui/errors/500.html", status=500)
