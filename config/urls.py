"""Top-level URL configuration for DokkiMashora LMS."""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core import health
from apps.core import views as core_views
from apps.deployment import views as deployment_views

# try:
#     import debug_toolbar  # type: ignore[import-untyped]  # noqa: F401
# except ImportError:
#     debug_toolbar = None

# S2-12: /healthz (apps.core.health) stays the canonical liveness probe and
# keeps its {"status","database"} contract. The richer db/cache/storage health
# view from apps.deployment is wired at /deployment/health/ as a documented
# secondary probe for operators who need the deeper checks.
urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/setlang/", core_views.set_language, name="set_language"),
    path("healthz", health.healthz, name="healthz"),
    path("deployment/health/", deployment_views.health_view, name="deployment_health"),
    path("storage/", include("apps.storage.urls")),
]

# Optional: Django debug toolbar (dev only).
# if settings.DEBUG and debug_toolbar is not None:
#     urlpatterns.insert(0, path("__debug__/", include("debug_toolbar.urls")))

urlpatterns += i18n_patterns(
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("applications/", include("apps.applications.urls")),
    path("entrance-exams/", include("apps.entrance_exams.urls")),

    path("academics/", include("apps.academics.urls")),
    path("assessments/", include("apps.assessments.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("progression/", include("apps.progression.urls")),
    path("certificates/", include("apps.certificates.urls")),
    path("payments/", include("apps.payments.urls")),
    path("communications/", include("apps.communications.urls")),
    path("audit/", include("apps.audit.urls")),
    path("intake/", include("apps.intake_forms.urls")),
    path("profile/", include("apps.students.urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    # Private storage is NEVER served from MEDIA_ROOT (S2-21): local files are
    # served through the signed-URL protected_download endpoint instead.
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
