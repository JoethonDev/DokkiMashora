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

    if request.headers.get("HX-Request") != "true":
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
                    "icon": item["icon"],
                })
        if nav_results:
            results.append({"name": "navigation", "label": _("Navigation"), "items": nav_results[:5]})

        if len(q) >= 2 and can_search_all:
            # Students
            students = User.objects.filter(
                Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(national_id__icontains=q),
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
    mock_system = {
        "themes": [
            {
                "id": "calm-slate",
                "name": {"en": "Calm Slate + Emerald", "ar": "سكني هادئ + زمردي"},
                "note": {"en": "Default LMS look", "ar": "الواجهة الافتراضية"},
                "bg": "#f8fafc",
                "surface": "#ffffff",
                "panel": "#eef2f7",
                "border": "#dbe3ee",
                "text": "#0f172a",
                "muted": "#475569",
                "accent": "#059669",
                "accent_soft": "#dcfce8",
            },
            {
                "id": "warm-paper",
                "name": {"en": "Warm Paper + Emerald", "ar": "ورقي دافئ + زمردي"},
                "note": {"en": "Soft reading comfort", "ar": "راحة بصرية للقراءة"},
                "bg": "#fdfaf3",
                "surface": "#fffdf8",
                "panel": "#f3ece0",
                "border": "#e5dac7",
                "text": "#1f2937",
                "muted": "#52606d",
                "accent": "#059669",
                "accent_soft": "#d1fae5",
            },
            {
                "id": "high-contrast-calm",
                "name": {"en": "High-Contrast Calm", "ar": "تباين واضح وهادئ"},
                "note": {"en": "Sharper for admin review", "ar": "أوضح للمراجعة الإدارية"},
                "bg": "#ffffff",
                "surface": "#ffffff",
                "panel": "#f8fafc",
                "border": "#cbd5e1",
                "text": "#0f172a",
                "muted": "#334155",
                "accent": "#0f766e",
                "accent_soft": "#ccfbf1",
            },
            {
                "id": "quiet-dark",
                "name": {"en": "Quiet Dark", "ar": "داكن هادئ"},
                "note": {"en": "Readable dark preview", "ar": "معاينة داكنة مقروءة"},
                "bg": "#0f172a",
                "surface": "#111827",
                "panel": "#1e293b",
                "border": "#334155",
                "text": "#e2e8f0",
                "muted": "#94a3b8",
                "accent": "#34d399",
                "accent_soft": "#064e3b",
            },
            {
                "id": "coptic-family",
                "name": {"en": "Coptic Crimson + Gold", "ar": "قبطي قرمزي + ذهبي"},
                "note": {"en": "Public institute landing page", "ar": "صفحة المعهد العامة"},
                "bg": "#faf9f6",
                "surface": "#ffffff",
                "panel": "#f6efe7",
                "border": "#e7d9c7",
                "text": "#2d0505",
                "muted": "#665f59",
                "accent": "#8c1d1d",
                "accent_soft": "#fbeee6",
            },
        ],
        "designs": [
            {
                "id": "operations-classic",
                "name": {"en": "Operations Classic", "ar": "العمليات الكلاسيكية"},
                "note": {"en": "Admin/moderator workspace", "ar": "مساحة المشرف والإدارة"},
                "role": {"en": "Admin / Moderator", "ar": "إداري / مشرف"},
                "shell": "operations",
                "density": "High",
                "default_page": "dashboard",
                "nav": [
                    {"en": "Applications", "ar": "الطلبات"},
                    {"en": "Academics", "ar": "الأكاديميات"},
                    {"en": "Courses", "ar": "المقررات"},
                    {"en": "Exams", "ar": "الاختبارات"},
                ],
                "focus": [
                    {"en": "Queue-first", "ar": "قائمة أولاً"},
                    {"en": "Fast review", "ar": "مراجعة سريعة"},
                    {"en": "Shallow navigation", "ar": "تنقل قصير"},
                ],
            },
            {
                "id": "learning-console",
                "name": {"en": "Learning Console", "ar": "وحدة التعلم"},
                "note": {"en": "Student/teacher workspace", "ar": "مساحة الطالب والمعلم"},
                "role": {"en": "Student / Teacher", "ar": "طالب / معلم"},
                "shell": "learning",
                "density": "Medium",
                "default_page": "courses",
                "nav": [
                    {"en": "Dashboard", "ar": "لوحة التحكم"},
                    {"en": "My Courses", "ar": "مقرراتي"},
                    {"en": "My Exams", "ar": "اختباراتي"},
                    {"en": "Profile", "ar": "الملف الشخصي"},
                ],
                "focus": [
                    {"en": "Progress first", "ar": "التقدم أولاً"},
                    {"en": "Reading comfort", "ar": "راحة القراءة"},
                    {"en": "Simple next step", "ar": "خطوة تالية واضحة"},
                ],
            },
            {
                "id": "review-desk",
                "name": {"en": "Review Desk", "ar": "منصة المراجعة"},
                "note": {"en": "Grading and approvals", "ar": "التقييم والاعتماد"},
                "role": {"en": "Admin / Teacher", "ar": "إداري / معلم"},
                "shell": "review",
                "density": "Medium-high",
                "default_page": "applications",
                "nav": [
                    {"en": "Queue", "ar": "القائمة"},
                    {"en": "Details", "ar": "التفاصيل"},
                    {"en": "Decision", "ar": "القرار"},
                    {"en": "History", "ar": "السجل"},
                ],
                "focus": [
                    {"en": "One item at a time", "ar": "عنصر واحد كل مرة"},
                    {"en": "Clear decision path", "ar": "مسار قرار واضح"},
                    {"en": "Less clutter", "ar": "بدون ازدحام"},
                ],
            },
            {
                "id": "reading-workspace",
                "name": {"en": "Reading Workspace", "ar": "مساحة القراءة"},
                "note": {"en": "Content-heavy pages", "ar": "الصفحات النصية"},
                "role": {"en": "Student / Staff", "ar": "طالب / موظف"},
                "shell": "reading",
                "density": "Low-medium",
                "default_page": "courses",
                "nav": [
                    {"en": "Overview", "ar": "نظرة عامة"},
                    {"en": "Lessons", "ar": "الدروس"},
                    {"en": "Resources", "ar": "المراجع"},
                    {"en": "Notes", "ar": "الملاحظات"},
                ],
                "focus": [
                    {"en": "Wide reading column", "ar": "عمود قراءة واسع"},
                    {"en": "Minimal chrome", "ar": "إطار بسيط"},
                    {"en": "Eye comfort", "ar": "راحة العين"},
                ],
            },
            {
                "id": "coptic-institute-landing",
                "name": {"en": "Coptic Family Institute", "ar": "معهد الأسرة القبطي"},
                "note": {
                    "en": "Crimson/gold public recruitment page",
                    "ar": "صفحة عامة قرمزية وذهبية للتقديم",
                },
                "role": {"en": "Public visitors", "ar": "زوار الموقع"},
                "shell": "reading",
                "density": "Medium",
                "default_page": "courses",
                "nav": [
                    {"en": "Vision", "ar": "الرؤية"},
                    {"en": "Programs", "ar": "البرامج"},
                    {"en": "Priority finder", "ar": "اختبر أولوياتك"},
                    {"en": "Apply", "ar": "التقديم"},
                ],
                "focus": [
                    {"en": "Pastoral trust", "ar": "ثقة رعوية"},
                    {"en": "Live cohorts", "ar": "دفعات فعلية"},
                    {"en": "Clear application path", "ar": "مسار تقديم واضح"},
                ],
            },
        ],
        "pages": [
            {
                "id": "dashboard",
                "nav": {"en": "Dashboard", "ar": "لوحة التحكم"},
                "eyebrow": {"en": "Overview", "ar": "نظرة عامة"},
                "title": {"en": "Operations dashboard", "ar": "لوحة العمليات"},
                "summary": {
                    "en": "A calm overview with clear priorities and quick actions.",
                    "ar": "عرض هادئ مع أولويات واضحة وإجراءات سريعة.",
                },
                "stats": [
                    {
                        "label": {"en": "Pending applications", "ar": "الطلبات المعلقة"},
                        "value": "18",
                        "note": {"en": "Need review today", "ar": "تحتاج مراجعة اليوم"},
                    },
                    {
                        "label": {"en": "Active courses", "ar": "المقررات النشطة"},
                        "value": "42",
                        "note": {"en": "Published and visible", "ar": "منشورة ومرئية"},
                    },
                    {
                        "label": {"en": "Open exams", "ar": "الاختبارات المفتوحة"},
                        "value": "6",
                        "note": {"en": "Running this week", "ar": "تعمل هذا الأسبوع"},
                    },
                    {
                        "label": {"en": "Certificates", "ar": "الشهادات"},
                        "value": "124",
                        "note": {"en": "Completed records", "ar": "السجلات المكتملة"},
                    },
                ],
                "actions": [
                    {"label": {"en": "Applications", "ar": "الطلبات"}},
                    {"label": {"en": "Courses", "ar": "المقررات"}},
                    {"label": {"en": "Exams", "ar": "الاختبارات"}},
                    {"label": {"en": "Designs", "ar": "التصاميم"}},
                ],
                "activity": [
                    {
                        "title": {"en": "New registration received", "ar": "تم استلام تسجيل جديد"},
                        "meta": {"en": "2 minutes ago", "ar": "منذ دقيقتين"},
                    },
                    {
                        "title": {"en": "Exam schedule updated", "ar": "تم تحديث جدول الاختبارات"},
                        "meta": {"en": "15 minutes ago", "ar": "منذ 15 دقيقة"},
                    },
                    {
                        "title": {"en": "Course content published", "ar": "تم نشر محتوى مقرر"},
                        "meta": {"en": "Today", "ar": "اليوم"},
                    },
                ],
            },
            {
                "id": "applications",
                "nav": {"en": "Applications", "ar": "الطلبات"},
                "eyebrow": {"en": "Queue", "ar": "قائمة المراجعة"},
                "title": {"en": "Application review", "ar": "مراجعة الطلبات"},
                "summary": {
                    "en": "A split list-detail workspace for fast review without deep navigation.",
                    "ar": "واجهة قائمة وتفاصيل للمراجعة السريعة دون عمق تنقل كبير.",
                },
                "queue": [
                    {
                        "name": {"en": "Ahmed Ali", "ar": "أحمد علي"},
                        "status": {"en": "Pending", "ar": "معلق"},
                        "note": {"en": "ID document missing", "ar": "مستند الهوية غير موجود"},
                    },
                    {
                        "name": {"en": "Mona Hassan", "ar": "منى حسن"},
                        "status": {"en": "Needs check", "ar": "بحاجة إلى مراجعة"},
                        "note": {"en": "Payment proof attached", "ar": "إثبات الدفع مرفق"},
                    },
                    {
                        "name": {"en": "Omar Nabil", "ar": "عمر نبيل"},
                        "status": {"en": "Waiting", "ar": "بانتظار"},
                        "note": {"en": "Interview slot requested", "ar": "تم طلب موعد مقابلة"},
                    },
                ],
                "detail": {
                    "title": {"en": "Selected application", "ar": "الطلب المحدد"},
                    "fields": [
                        {
                            "label": {"en": "Applicant", "ar": "المتقدم"},
                            "value": {"en": "Ahmed Ali", "ar": "أحمد علي"},
                        },
                        {
                            "label": {"en": "Program", "ar": "البرنامج"},
                            "value": {"en": "Grade 10 Track", "ar": "مسار الصف العاشر"},
                        },
                        {
                            "label": {"en": "Status", "ar": "الحالة"},
                            "value": {"en": "Pending review", "ar": "بانتظار المراجعة"},
                        },
                        {
                            "label": {"en": "Next action", "ar": "الإجراء التالي"},
                            "value": {"en": "Approve or request update", "ar": "اعتماد أو طلب تعديل"},
                        },
                    ],
                },
            },
            {
                "id": "courses",
                "nav": {"en": "My Courses", "ar": "مقرراتي"},
                "eyebrow": {"en": "Learning", "ar": "التعلم"},
                "title": {"en": "Course workspace", "ar": "مساحة المقرر"},
                "summary": {
                    "en": "Simple course cards with a clear progress panel and next lesson.",
                    "ar": "بطاقات مقررات بسيطة مع لوحة تقدم واضحة والدرس التالي.",
                },
                "courses": [
                    {
                        "name": {"en": "Mathematics", "ar": "الرياضيات"},
                        "progress": "68%",
                        "note": {"en": "Next: Fractions", "ar": "التالي: الكسور"},
                    },
                    {
                        "name": {"en": "Science", "ar": "العلوم"},
                        "progress": "41%",
                        "note": {"en": "Next: Lab notes", "ar": "التالي: ملاحظات المختبر"},
                    },
                    {
                        "name": {"en": "English", "ar": "اللغة الإنجليزية"},
                        "progress": "85%",
                        "note": {"en": "Next: Revision", "ar": "التالي: مراجعة"},
                    },
                ],
                "lesson": {
                    "title": {"en": "Current lesson", "ar": "الدرس الحالي"},
                    "points": [
                        {"en": "Watch lecture", "ar": "مشاهدة المحاضرة"},
                        {"en": "Open worksheet", "ar": "فتح ورقة العمل"},
                        {"en": "Submit review", "ar": "إرسال المراجعة"},
                    ],
                },
            },
            {
                "id": "exams",
                "nav": {"en": "My Exams", "ar": "اختباراتي"},
                "eyebrow": {"en": "Assessment", "ar": "التقييم"},
                "title": {"en": "Exam workspace", "ar": "مساحة الاختبار"},
                "summary": {
                    "en": "A quiet exam preview with timing, progress, and short task labels.",
                    "ar": "معاينة اختبار هادئة مع وقت وتقدم وعناوين قصيرة للمهام.",
                },
                "schedule": [
                    {
                        "name": {"en": "Entrance exam", "ar": "اختبار القبول"},
                        "time": {"en": "Today 10:00", "ar": "اليوم 10:00"},
                        "status": {"en": "Open", "ar": "مفتوح"},
                    },
                    {
                        "name": {"en": "Midterm review", "ar": "مراجعة منتصف الفصل"},
                        "time": {"en": "Fri 13:30", "ar": "الجمعة 13:30"},
                        "status": {"en": "Scheduled", "ar": "مجدول"},
                    },
                    {
                        "name": {"en": "Quiz practice", "ar": "تدريب قصير"},
                        "time": {"en": "Self-paced", "ar": "حسب التقدم"},
                        "status": {"en": "Available", "ar": "متاح"},
                    },
                ],
                "runner": {
                    "timer": "01:25:10",
                    "progress": "18 / 40",
                    "status": {"en": "Stable and readable", "ar": "مستقر وسهل القراءة"},
                },
            },
            {
                "id": "profile",
                "nav": {"en": "Profile", "ar": "الملف الشخصي"},
                "eyebrow": {"en": "Account", "ar": "الحساب"},
                "title": {"en": "Profile workspace", "ar": "مساحة الملف الشخصي"},
                "summary": {
                    "en": "Straightforward settings and personal details without visual clutter.",
                    "ar": "إعدادات وبيانات شخصية واضحة دون ازدحام بصري.",
                },
                "fields": [
                    {
                        "label": {"en": "Full name", "ar": "الاسم الكامل"},
                        "value": {"en": "Maha Hassan", "ar": "ماها حسن"},
                    },
                    {
                        "label": {"en": "Email", "ar": "البريد الإلكتروني"},
                        "value": {"en": "maha@example.com", "ar": "maha@example.com"},
                    },
                    {"label": {"en": "Role", "ar": "الدور"}, "value": {"en": "Teacher", "ar": "معلمة"}},
                    {
                        "label": {"en": "Language", "ar": "اللغة"},
                        "value": {"en": "Arabic / English", "ar": "العربية / الإنجليزية"},
                    },
                ],
                "preferences": [
                    {
                        "label": {"en": "Notifications", "ar": "الإشعارات"},
                        "value": {"en": "Enabled", "ar": "مفعلة"},
                    },
                    {
                        "label": {"en": "Dark mode", "ar": "الوضع الداكن"},
                        "value": {"en": "Optional", "ar": "اختياري"},
                    },
                ],
            },
        ],
    }
    return render(request, "ui/pages/admin/dashboard.html", {"mock_system": mock_system, "title": _("Design showcase")})


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
