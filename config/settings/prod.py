"""Production settings."""

from django.core.exceptions import ImproperlyConfigured  # noqa: F401

from .base import *  # noqa: F401,F403
from .base import (
    DATABASES,  # noqa: F401
    SECRET_KEY,  # noqa: F401
    env,  # noqa: F401
)

DEBUG = False
PRODUCTION_DEPLOYMENT = True

# Fail fast on a missing, example, or weak signing key (RV-021). The Django
# placeholder and this repo's example values must never reach production,
# because the storage signed URLs and all signed tokens derive from it.
if (
    not SECRET_KEY
    or SECRET_KEY.startswith("django-insecure-")
    or SECRET_KEY.startswith("replace-with-")
    or len(SECRET_KEY) < 50
):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be a strong, unique random value in production "
        "(at least 50 characters, not a known example)."
    )

# Enforce secure cookies in production. The flags are env-overridable so an
# internal HTTP-only load test (e.g. hitting nginx on :80 inside a trusted
# network) can authenticate without TLS. Production keeps them True.
_SECURE_COOKIES = env.bool("DJANGO_SECURE_COOKIES", default=True)
SESSION_COOKIE_SECURE = _SECURE_COOKIES
CSRF_COOKIE_SECURE = _SECURE_COOKIES
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True) and _SECURE_COOKIES
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

# WhiteNoise serves static files in production too. The manifest storage
# appends a content hash to every asset (css/tailwind.<hash>.css), so Django
# handles cache-busting automatically instead of manual ?v= bumps.
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Keep database connections across requests; the autosave endpoint is latency
# sensitive and PostgreSQL remains the authoritative store.
CONN_MAX_AGE = env.int("DJANGO_CONN_MAX_AGE", default=300)
DATABASES["default"]["CONN_MAX_AGE"] = CONN_MAX_AGE
ASSESSMENT_AUTOSAVE_WRITE_BEHIND = env.bool(
    "ASSESSMENT_AUTOSAVE_WRITE_BEHIND", default=False,
)
ASSESSMENT_SUBMISSION_GRADING_DELAY = env.float(
    "ASSESSMENT_SUBMISSION_GRADING_DELAY", default=0.0,
)
