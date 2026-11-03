#!/usr/bin/env bash
# GitHub Codespaces development-only bootstrap; never deploys to production.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${CODESPACE_NAME:-}" ]]; then
  echo "This entrypoint requires GitHub Codespaces (CODESPACE_NAME is unset)." >&2
  exit 1
fi

if [[ ! -f .env.docker ]]; then
  command -v openssl >/dev/null || { echo "openssl is required to generate trial credentials" >&2; exit 1; }
  umask 077
  {
    printf '%s\n' 'POSTGRES_DB=dokkimashora_demo'
    printf '%s\n' 'POSTGRES_USER=dokkimashora'
    printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)"
    printf 'DJANGO_SECRET_KEY=%s\n' "$(openssl rand -hex 64)"
    printf '%s\n' 'DJANGO_DEBUG=false'
    printf '%s\n' 'DJANGO_SEED_DEMO=false'
    printf '%s\n' 'USE_R2_STORAGE=false'
    printf '%s\n' 'EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend'
    printf '%s\n' 'EMAIL_HOST='
    printf '%s\n' 'DJANGO_SUPERUSER_USERNAME=demo-admin@dokkimashora.test'
  } > .env.docker
  echo "Created private .env.docker with unique database and Django secrets."
fi

export APP_ENV_FILE=.env.docker
export APP_IMAGE_TAG=codespaces
# The entire workspace is bind-mounted read-only at /app. The web container
# mounts a writable named volume at /app/staticfiles; its mountpoint must
# already exist in the host workspace or runc cannot create it under /app:ro.
# Only create the empty directory: collectstatic writes to the named volume.
mkdir -p .docker-data/postgres .docker-data/redis media staticfiles .demo-backups

for attempt in $(seq 1 45); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" -eq 45 ]]; then
    echo "Docker daemon did not become ready." >&2
    exit 1
  fi
  sleep 2
done

compose=(
  docker compose --project-name dokkimashora-trial --env-file .env.docker
  -f compose.application.yml -f compose.infrastructure.yml -f compose.codespaces.yml
)

"${compose[@]}" config --quiet

if ! docker image inspect dokkimashora-app:codespaces >/dev/null 2>&1; then
  echo "First run: building the shared Django/Celery image..."
  "${compose[@]}" build web
fi

# Dockerfile runs the application as the nonroot 'app' user. Give that user
# ownership of the bind-mounted demo media folder without changing the source.
docker run --rm --user root --entrypoint sh \
  -v "$PWD/media:/demo-media" dokkimashora-app:codespaces \
  -c 'chown app:app /demo-media'

echo "Starting Docker Compose services..."
"${compose[@]}" up -d --no-build

if ! curl --fail --silent --show-error --retry 20 --retry-delay 3 \
  --retry-connrefused http://127.0.0.1/healthz >/dev/null; then
  "${compose[@]}" ps
  "${compose[@]}" logs --tail=80 web nginx
  exit 1
fi

echo "Demo healthy. Forward port 80 and set its visibility to Public only when ready."
echo "URL: https://${CODESPACE_NAME}-80.app.github.dev"
