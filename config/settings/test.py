"""Test settings used by pytest."""

from .base import *  # noqa: F401,F403

DEBUG = False

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

# Avoid external network calls.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Disable migrations at test time unless explicitly required.
# We keep them enabled so the test suite exercises real migrations.
