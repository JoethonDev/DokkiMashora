"""Core views: landing page, error handlers, language switcher."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from apps.core.permissions import ROLE_SENIOR_STAFF, role_required


def landing(request: HttpRequest) -> HttpResponse:
    """Public landing page."""
    return render(request, "core/landing.html")


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
                    {"label": {"en": "Pending applications", "ar": "الطلبات المعلقة"}, "value": "18", "note": {"en": "Need review today", "ar": "تحتاج مراجعة اليوم"}},
                    {"label": {"en": "Active courses", "ar": "المقررات النشطة"}, "value": "42", "note": {"en": "Published and visible", "ar": "منشورة ومرئية"}},
                    {"label": {"en": "Open exams", "ar": "الاختبارات المفتوحة"}, "value": "6", "note": {"en": "Running this week", "ar": "تعمل هذا الأسبوع"}},
                    {"label": {"en": "Certificates", "ar": "الشهادات"}, "value": "124", "note": {"en": "Completed records", "ar": "السجلات المكتملة"}},
                ],
                "actions": [
                    {"label": {"en": "Applications", "ar": "الطلبات"}},
                    {"label": {"en": "Courses", "ar": "المقررات"}},
                    {"label": {"en": "Exams", "ar": "الاختبارات"}},
                    {"label": {"en": "Designs", "ar": "التصاميم"}},
                ],
                "activity": [
                    {"title": {"en": "New registration received", "ar": "تم استلام تسجيل جديد"}, "meta": {"en": "2 minutes ago", "ar": "منذ دقيقتين"}},
                    {"title": {"en": "Exam schedule updated", "ar": "تم تحديث جدول الاختبارات"}, "meta": {"en": "15 minutes ago", "ar": "منذ 15 دقيقة"}},
                    {"title": {"en": "Course content published", "ar": "تم نشر محتوى مقرر"}, "meta": {"en": "Today", "ar": "اليوم"}},
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
                    {"name": {"en": "Ahmed Ali", "ar": "أحمد علي"}, "status": {"en": "Pending", "ar": "معلق"}, "note": {"en": "ID document missing", "ar": "مستند الهوية غير موجود"}},
                    {"name": {"en": "Mona Hassan", "ar": "منى حسن"}, "status": {"en": "Needs check", "ar": "بحاجة إلى مراجعة"}, "note": {"en": "Payment proof attached", "ar": "إثبات الدفع مرفق"}},
                    {"name": {"en": "Omar Nabil", "ar": "عمر نبيل"}, "status": {"en": "Waiting", "ar": "بانتظار"}, "note": {"en": "Interview slot requested", "ar": "تم طلب موعد مقابلة"}},
                ],
                "detail": {
                    "title": {"en": "Selected application", "ar": "الطلب المحدد"},
                    "fields": [
                        {"label": {"en": "Applicant", "ar": "المتقدم"}, "value": {"en": "Ahmed Ali", "ar": "أحمد علي"}},
                        {"label": {"en": "Program", "ar": "البرنامج"}, "value": {"en": "Grade 10 Track", "ar": "مسار الصف العاشر"}},
                        {"label": {"en": "Status", "ar": "الحالة"}, "value": {"en": "Pending review", "ar": "بانتظار المراجعة"}},
                        {"label": {"en": "Next action", "ar": "الإجراء التالي"}, "value": {"en": "Approve or request update", "ar": "اعتماد أو طلب تعديل"}},
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
                    {"name": {"en": "Mathematics", "ar": "الرياضيات"}, "progress": "68%", "note": {"en": "Next: Fractions", "ar": "التالي: الكسور"}},
                    {"name": {"en": "Science", "ar": "العلوم"}, "progress": "41%", "note": {"en": "Next: Lab notes", "ar": "التالي: ملاحظات المختبر"}},
                    {"name": {"en": "English", "ar": "اللغة الإنجليزية"}, "progress": "85%", "note": {"en": "Next: Revision", "ar": "التالي: مراجعة"}},
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
                    {"name": {"en": "Entrance exam", "ar": "اختبار القبول"}, "time": {"en": "Today 10:00", "ar": "اليوم 10:00"}, "status": {"en": "Open", "ar": "مفتوح"}},
                    {"name": {"en": "Midterm review", "ar": "مراجعة منتصف الفصل"}, "time": {"en": "Fri 13:30", "ar": "الجمعة 13:30"}, "status": {"en": "Scheduled", "ar": "مجدول"}},
                    {"name": {"en": "Quiz practice", "ar": "تدريب قصير"}, "time": {"en": "Self-paced", "ar": "حسب التقدم"}, "status": {"en": "Available", "ar": "متاح"}},
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
                    {"label": {"en": "Full name", "ar": "الاسم الكامل"}, "value": {"en": "Maha Hassan", "ar": "ماها حسن"}},
                    {"label": {"en": "Email", "ar": "البريد الإلكتروني"}, "value": {"en": "maha@example.com", "ar": "maha@example.com"}},
                    {"label": {"en": "Role", "ar": "الدور"}, "value": {"en": "Teacher", "ar": "معلمة"}},
                    {"label": {"en": "Language", "ar": "اللغة"}, "value": {"en": "Arabic / English", "ar": "العربية / الإنجليزية"}},
                ],
                "preferences": [
                    {"label": {"en": "Notifications", "ar": "الإشعارات"}, "value": {"en": "Enabled", "ar": "مفعلة"}},
                    {"label": {"en": "Dark mode", "ar": "الوضع الداكن"}, "value": {"en": "Optional", "ar": "اختياري"}},
                ],
            },
        ],
    }
    return render(request, "core/design_showcase.html", {"mock_system": mock_system})


@csrf_protect
@require_http_methods(["POST"])
def set_language(request: HttpRequest) -> HttpResponse:
    """Switch the active language and redirect to the same page in the new language."""
    new_language = request.POST.get("language")
    next_url = request.POST.get("next", "/")
    available_codes = {code for code, _name in settings.LANGUAGES}
    if new_language not in available_codes:
        return HttpResponseBadRequest("Invalid language")
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


@require_http_methods(["GET", "HEAD"])
def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/400.html", status=400)


@require_http_methods(["GET", "HEAD"])
def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/403.html", status=403)


@require_http_methods(["GET", "HEAD"])
def page_not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/404.html", status=404)


@require_http_methods(["GET", "HEAD"])
def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "core/errors/500.html", status=500)
