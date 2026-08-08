"""Production settings."""

from .base import *  # noqa: F401,F403
from .base import env  # noqa: F401

DEBUG = False
PRODUCTION_DEPLOYMENT = True

# Enforce secure cookies in production.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

# WhiteNoise serves static files in production too. The manifest storage
# appends a content hash to every asset (css/tailwind.<hash>.css), so Django
# handles cache-busting automatically instead of manual ?v= bumps.
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
