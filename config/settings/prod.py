"""Production settings."""

from .base import *  # noqa: F401,F403
from .base import env  # noqa: F401

DEBUG = False

# Enforce secure cookies in production.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)

# WhiteNoise serves static files in production too.
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
