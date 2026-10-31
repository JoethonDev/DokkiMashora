# DokkiMashora LMS

A Django-based Learning Management System (LMS) for **DokkiMashora**, an Arabic-first platform
that handles registration, entrance exams, learning content, assessments, grading, and
certificates.

> **Informational setup guide.** This README is operational/setup documentation only.
> `AGENTS.md` (engineering contract) and `DOKKIMASHORA.md` (product/business contract) are the
> repository's authoritative documents and win over any statement here.

## Tech stack

- **Django 5.1** with a custom email-based user model from day one
- **Python 3.11**
- **PostgreSQL** in production, **SQLite** in dev/test
- **Tailwind CSS** (compiled via `npm run build:css`) + **HTMX** + **Alpine.js**
- **Cloudflare R2** for media (S3-compatible via `django-storages`)
- **WhiteNoise** for static files, **Gunicorn** as the application server
- **GitHub Actions** for CI and deployment

## Project structure

```
D:\DokkiMashora
├── apps/
│   ├── core/        # base models, permissions, error handlers, landing page
│   └── accounts/    # custom user model, login, dashboards
├── config/          # Django project (settings/urls/wsgi/asgi)
├── docs/            # project plan, handovers, runbooks
├── locale/          # translation files
├── scripts/         # provisioning and ops scripts
├── static/          # local static assets
├── templates/       # Django templates
├── .github/         # CI / CD workflows
├── manage.py
├── requirements.txt
└── ruff.toml
```

## Quick start

```powershell
# 1. Copy environment variables.
Copy-Item .env.example .env

# 2. Install dependencies.
pip install -r requirements.txt

# 3. Apply migrations and seed default role groups.
python manage.py migrate
python manage.py seed_roles
# Create a superuser from DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_PASSWORD.
$env:DJANGO_SUPERUSER_USERNAME="admin@dokkimashora.test"; $env:DJANGO_SUPERUSER_PASSWORD="ChangeMe123!"; python manage.py createsuperuser --noinput

# 4. Run the dev server.
python manage.py runserver
```

Visit `http://127.0.0.1:8000/` for the landing page and `http://127.0.0.1:8000/admin/` for the Django admin.

Demo accounts are created by `seed_roles` only when `DJANGO_SEED_DEMO=true`.
Set `DJANGO_SEED_DEMO_PASSWORD` to an explicit local password, or the command
generates a random password and prints it once. Never enable demo accounts or
use demo passwords in production.

> The Teacher role is **deferred / not a current product role** (see `DOKKIMASHORA.md` §5);
> existing seed data may still contain a Teacher account.

## Internationalization

- Default language: **Arabic (ar)**, with **English (en)** available.
- All user-facing strings use `gettext_lazy` / `{% trans %}`.
- The layout direction follows the active language (`dir="rtl"` for Arabic, `dir="ltr"` for English).

## Time zone

- `TIME_ZONE = "Africa/Cairo"`, `USE_TZ = True`. All datetimes are timezone-aware.

## Container logs (Dozzle)

Nginx, Django, and Celery write single-line key/value logs to stdout so Dozzle
can search them directly. Every request carries the same `X-Request-ID`
through nginx, Django, the audit trail, and Celery logs.

Useful searches:

```text
req=<correlation-id>          # Django/audit/Celery lines for one request
request_id=<correlation-id>   # matching nginx access line
request_server_error          # HTTP 5xx
request_client_error          # HTTP 4xx (403/404 etc.)
request_slow                  # requests above SLOW_REQUEST_LOG_MS (default 2000)
celery_task_failed            # background job failures
celery_task_retry             # background job retries
email_delivery_failed         # SMTP delivery failures
smtp_connection_failed        # SMTP test/connection failures
status=403                    # permission denials
task_id=<celery-task-id>      # one background job
```

Django log fields include `req`, `user`, `method`, `path`, `status`,
`dur_ms`, `ip`, `task`, and `task_id`. Activation/document/intake token path
segments are redacted before they reach logs or audit metadata.

## Tooling

```powershell
# Lint
ruff check .
ruff format --check .

# Django checks
python manage.py check
python manage.py makemigrations --check --dry-run
```

The repository does not ship an automated test suite.

## Deployment

The CI workflow runs lint and Django checks on every push and PR. The deployment workflow
(`.github/workflows/deploy.yml`) is intentionally a skeleton: it expects the
following GitHub repository secrets to be added before it can run:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY`
- `DEPLOY_PATH`

Operational deployment guidance (backup/restore, secrets, rollback, day-2 checklist)
lives in `docs/DEPLOYMENT.md`.
