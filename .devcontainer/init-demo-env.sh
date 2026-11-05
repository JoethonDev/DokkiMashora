#!/usr/bin/env bash
# Explicit one-time setup for a fresh Codespaces demo; never runs automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${CODESPACE_NAME:-}" ]]; then
  echo "ERROR: This demo setup must run inside GitHub Codespaces." >&2
  exit 1
fi
if [[ -e .env ]]; then
  echo "ERROR: .env already exists; it was not changed." >&2
  echo "Edit your existing .env, then run: bash .devcontainer/start-demo.sh" >&2
  exit 1
fi
# Never create new database credentials when an older demo DB may still exist.
if [[ -d .docker-data/postgres ]] && [[ -n "$(find .docker-data/postgres -mindepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "ERROR: Existing PostgreSQL data detected, but .env is missing." >&2
  echo "Restore your original .env (or migrate the legacy .env.docker);" >&2
  echo "generating new database credentials would prevent reconnecting." >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "ERROR: openssl is required for secure demo credentials." >&2; exit 1; }

db_password="$(openssl rand -hex 32)"
django_key="$(openssl rand -hex 64)"
forwarded="${CODESPACE_NAME}-80.app.github.dev"

# A single gitignored, private file is the source of runtime settings.
umask 077
set -C
cat > .env <<EOF
# Generated explicitly for this Codespaces trial. Never commit this file.
POSTGRES_DB=dokkimashora_demo
POSTGRES_USER=dokkimashora
POSTGRES_PASSWORD=${db_password}
DATABASE_URL=postgresql://dokkimashora:${db_password}@postgres:5432/dokkimashora_demo

DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=${django_key}
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,demo.joethon.dev,${forwarded}
DJANGO_CSRF_TRUSTED_ORIGINS=https://demo.joethon.dev,https://${forwarded}
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SECURE_COOKIES=true
SITE_URL=https://demo.joethon.dev

CACHE_URL=redis://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/0
APP_IMAGE_TAG=codespaces
PORT=8000
GUNICORN_WORKERS=3
GUNICORN_THREADS=4
DJANGO_SEED_DEMO=false
USE_R2_STORAGE=false
DJANGO_SUPERUSER_USERNAME=demo-admin@dokkimashora.test
EMAIL_HOST=
EOF
set +C
chmod 600 .env
unset db_password django_key

echo "Created .env with secure, unique credentials (permissions: 600)."
echo "It will not be created or overwritten by the startup script."
echo "For access via demo.joethon.dev, add CF_TUNNEL_TOKEN to .env privately."
echo "Next: bash .devcontainer/start-demo.sh"
