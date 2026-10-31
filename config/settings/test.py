"""Lightweight settings for CI checks and browser QA."""

from .base import *  # noqa: F401,F403

DEBUG = False

# The test server uses HTTP; keep browser/session cookies usable in E2E tests.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# Use a fast password hasher in tests to keep them quick.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Emit test emails to stdout so browser QA can inspect verification and
# activation links without contacting an external mail provider.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Disable migrations at test time unless explicitly required.
# We keep them enabled so the test suite exercises real migrations.
