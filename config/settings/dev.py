"""Development settings."""

from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS, MIDDLEWARE  # noqa: F401

DEBUG = True

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

# In dev we always use the console email backend unless overridden.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
