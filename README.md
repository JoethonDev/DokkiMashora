# DokkiMashora LMS

A Django-based Learning Management System (LMS) for **DokkiMashora**, an Arabic-first platform
that handles registration, entrance exams, learning content, assessments, grading, and
certificates.

## Tech stack

- **Django 5.1** with a custom email-based user model from day one
- **Python 3.11**
- **PostgreSQL** in production, **SQLite** in dev/test
- **Tailwind CSS** (Play CDN during dev) + **HTMX** + **Alpine.js**
- **Cloudflare R2** for media (S3-compatible via `django-storages`)
- **Gemini** (Google GenAI) for entrance-exam written answer scoring
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
├── ruff.toml
└── pytest.ini
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
DJANGO_SUPERUSER_USERNAME=admin@dokkimashora.test DJANGO_SUPERUSER_PASSWORD=ChangeMe123! python manage.py createsuperuser --noinput

# 4. Run the dev server.
python manage.py runserver
```

Visit `http://127.0.0.1:8000/` for the landing page and `http://127.0.0.1:8000/admin/` for the Django admin.

Demo accounts (created by `seed_roles`):

| Role      | Email                              | Password       |
|-----------|------------------------------------|----------------|
| Admin     | `admin@dokkimashora.test`          | `ChangeMe123!` |
| Moderator | `moderator@dokkimashora.test`      | `ChangeMe123!` |
| Teacher   | `teacher@dokkimashora.test`        | `ChangeMe123!` |
| Student   | `student@dokkimashora.test`        | `ChangeMe123!` |

## Internationalization

- Default language: **Arabic (ar)**, with **English (en)** available.
- All user-facing strings use `gettext_lazy` / `{% trans %}`.
- The layout direction follows the active language (`dir="rtl"` for Arabic, `dir="ltr"` for English).

## Time zone

- `TIME_ZONE = "Africa/Cairo"`, `USE_TZ = True`. All datetimes are timezone-aware.

## Tooling

```powershell
# Lint
ruff check .
ruff format --check .

# Tests
pytest

# Coverage
coverage run -m pytest
coverage report
```

## Deployment

The CI workflow runs lint + tests on every push and PR. The deployment workflow
(`.github/workflows/deploy.yml`) is intentionally a skeleton: it expects the
following GitHub repository secrets to be added before it can run:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY`
- `DEPLOY_PATH`

Server provisioning notes live in `docs/` (to be expanded in Phase 9).
