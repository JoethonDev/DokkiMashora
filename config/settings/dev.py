"""Development settings."""

import sys

from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS, MIDDLEWARE  # noqa: F401

DEBUG = True

# Local QA runs over plain HTTP; secure cookies would never be returned by the
# browser and would make login/CSRF flows appear broken.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

INTERNAL_IPS = ["127.0.0.1", "localhost"]

# # Django debug toolbar is optional; only enable when installed.
# try:  # pragma: no cover - optional dependency
#     import debug_toolbar  # type: ignore[import-untyped]  # noqa: F401

#     INSTALLED_APPS = list(INSTALLED_APPS) + ["debug_toolbar"]
#     MIDDLEWARE = list(MIDDLEWARE) + ["debug_toolbar.middleware.DebugToolbarMiddleware"]
#     DEBUG_TOOLBAR_CONFIG = {
#         "SHOW_TOOLBAR_CALLBACK": lambda request: DEBUG,
#     }
# except ImportError:  # pragma: no cover - optional dependency
#     pass

# Static files in dev: serve raw files without hashed manifest.
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

# In dev we always use the console email backend unless overridden.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Django's console backend writes message bodies to stdout. Windows may expose
# a cp1252 stream, which cannot emit Arabic templates; keep QA mail capture
# lossless and readable.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
