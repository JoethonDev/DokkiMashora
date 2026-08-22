"""Django settings for DokkiMashora LMS project.

Settings are split into base/dev/prod to keep concerns isolated.
Environment variables are loaded from a .env file via django-environ.
"""

from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, ""),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, ["http://localhost:8000", "http://127.0.0.1:8000"]),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    CACHE_URL=(str, ""),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/0"),
    EMAIL_BACKEND=(str, "django.core.mail.backends.console.EmailBackend"),
    EMAIL_HOST=(str, ""),
    EMAIL_PORT=(int, 587),
    EMAIL_HOST_USER=(str, ""),
    EMAIL_HOST_PASSWORD=(str, ""),
    EMAIL_USE_TLS=(bool, True),
    SITE_NAME=(str, "DokkiMashora LMS"),
    SITE_NAME_AR=(str, "دكى مشورة"),
    DEFAULT_FROM_EMAIL=(str, "no-reply@dokkimashora.local"),
    R2_ACCESS_KEY_ID=(str, ""),
    R2_SECRET_ACCESS_KEY=(str, ""),
    R2_BUCKET_NAME=(str, ""),
    R2_ENDPOINT_URL=(str, ""),
    R2_PUBLIC_BASE_URL=(str, ""),
    USE_R2_STORAGE=(bool, False),
    SITE_URL=(str, "http://localhost:8000"),
)

# Read .env file if it exists.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# --- Paths -------------------------------------------------------------------
APPS_DIR = BASE_DIR / "apps"
LOCALE_DIR = BASE_DIR / "locale"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# --- Core --------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Applications ------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django.forms",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.students",
    "apps.applications",
    "apps.communications",
    "apps.entrance_exams",
    "apps.academics",
    "apps.assessments",
    "apps.storage",
    "apps.progression",
    "apps.certificates",
    "apps.payments",
    "apps.audit",
    "apps.ai",  # retained only for schema-removal migration history (DECISION 35)
    "apps.deployment",
    "apps.intake_forms",
    "apps.attendance",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# --- Middleware --------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "apps.core.admin_middleware.ForceEnglishAdminMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# --- Templates ---------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [TEMPLATES_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.site_meta",
            ],
        },
    },
]

# --- Database ----------------------------------------------------------------
DATABASES = {"default": env.db()}

# --- Cache and background jobs ----------------------------------------------
CACHE_URL = env("CACHE_URL")
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
        }
    }

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Africa/Cairo"
CELERY_ENABLE_UTC = True

# P5-04: bounded daily lifecycle normalization for expired enrollments.
# Read-path active/non-expired checks remain the authorization authority; this
# beat only keeps the persisted status consistent. Beat uses the existing
# Celery broker (no second scheduler, no worker-concurrency increase).
CELERY_BEAT_SCHEDULE = {
    "expire-stale-enrollments-daily": {
        "task": "apps.academics.tasks.expire_stale_enrollments_task",
        "schedule": 86400.0,  # every 24h
    },
    # DECISION #33: daily 30-day retention cleanup of temporary/ephemeral data
    # (completed exam snapshots, email bodies, import raw payloads). Durable
    # business history is never touched. Runs on the existing Celery broker.
    "purge-ephemeral-data-daily": {
        "task": "apps.core.tasks.purge_ephemeral_data_task",
        "schedule": 86400.0,  # every 24h
    },
}

# --- Custom user -------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# --- Password validation -----------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization ----------------------------------------------------
LANGUAGE_CODE = "ar"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

DATE_FORMAT = "j M Y"
DATETIME_FORMAT = "j M Y, H:i"
SHORT_DATETIME_FORMAT = "j M Y, H:i"

LANGUAGES = [
    ("ar", "العربية"),
    ("en", "English"),
]

LANGUAGE_COOKIE_NAME = "django_language"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
LANGUAGE_COOKIE_PATH = "/"

LOCALE_PATHS = [str(LOCALE_DIR)]

# --- Static files ------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [str(STATIC_DIR)]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# --- Media files -------------------------------------------------------------
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Default primary key field type ------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email -------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND")
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env("EMAIL_PORT")
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env("EMAIL_USE_TLS")
SITE_NAME = env("SITE_NAME")
SITE_NAME_AR = env("SITE_NAME_AR")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

# --- Cloudflare R2 -----------------------------------------------------------
USE_R2_STORAGE = env("USE_R2_STORAGE")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = env("R2_BUCKET_NAME")
R2_ENDPOINT_URL = env("R2_ENDPOINT_URL")
R2_PUBLIC_BASE_URL = env("R2_PUBLIC_BASE_URL")
SITE_URL = env("SITE_URL")

# --- File upload limits ----------------------------------------------------
HOMEWORK_MAX_UPLOAD_MB = 25
AUDIO_MAX_UPLOAD_MB = 50
APPLICATION_DOCUMENT_PERSONA_MAX_MB = 5
APPLICATION_DOCUMENT_ATTACHMENTS_MAX_MB = 25
APPLICATION_DOCUMENT_MAX_ATTACHMENT_FILES = 20
APPLICATION_DOCUMENT_IMAGE_MAX_PIXELS = 25_000_000
STORAGE_BACKEND = "r2" if USE_R2_STORAGE else "local"

if USE_R2_STORAGE and R2_BUCKET_NAME and R2_ENDPOINT_URL:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            "OPTIONS": {
                "access_key": R2_ACCESS_KEY_ID,
                "secret_key": R2_SECRET_ACCESS_KEY,
                "bucket_name": R2_BUCKET_NAME,
                "endpoint_url": R2_ENDPOINT_URL,
                "region_name": "auto",
                "default_acl": None,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# --- Security ----------------------------------------------------------------
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Login -------------------------------------------------------------------
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:post_login"
LOGOUT_REDIRECT_URL = "core:landing"

# --- Logging -----------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name} {message}",
            "style": "{",
        },
        "simple": {
            "format": "[{levelname}] {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
