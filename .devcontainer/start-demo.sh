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

# Only this trial uses the Cloudflare TLS-terminating gateway. Preserve the
# original Nginx config and adjust the trusted upstream scheme in a generated
# ignored file, rather than modifying the production Nginx configuration.
sed 's/proxy_set_header X-Forwarded-Proto $scheme;/proxy_set_header X-Forwarded-Proto https;/g' \
  deploy/nginx.conf > .docker-data/nginx.codespaces.conf

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

echo "Reloading the web and Nginx containers with the latest Codespaces settings..."
# 'up' alone keeps existing containers (and their old environment) unchanged.
# Recreate only these stateless services; never reset Postgres/Redis volumes.
"${compose[@]}" up -d --no-build --force-recreate web nginx

echo "Starting Docker Compose services..."
# Cloudflare Tunnel is opt-in. Its secret lives only in the ignored env file;
# Docker Compose never starts the tunnel with a missing/blank token.
if grep -Eq '^CF_TUNNEL_TOKEN=[^[:space:]]+' .env.docker; then
  echo "Cloudflare Tunnel token configured; starting the tunnel sidecar."
  "${compose[@]}" --profile tunnel up -d --no-build
else
  echo "Tunnel disabled until CF_TUNNEL_TOKEN is added to .env.docker."
  "${compose[@]}" up -d --no-build
fi

if ! curl --fail --silent --show-error --retry 20 --retry-delay 3 \
  --retry-connrefused http://127.0.0.1/healthz >/dev/null; then
  "${compose[@]}" ps
  "${compose[@]}" logs --tail=80 web nginx
  exit 1
fi

# Verify the running container, not merely the Compose YAML, has the correct
# public hosts and can complete the CSRF-protected login round trip.
"${compose[@]}" exec -T web python - <<'PY'
import os

import django

django.setup()
from django.conf import settings
from django.test import Client

expected = {
    "https://demo.joethon.dev",
    f"https://{os.environ['CODESPACE_NAME']}-80.app.github.dev",
}
assert expected <= set(settings.CSRF_TRUSTED_ORIGINS), settings.CSRF_TRUSTED_ORIGINS
assert "demo.joethon.dev" in settings.ALLOWED_HOSTS
assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
assert settings.CSRF_COOKIE_SECURE and settings.SESSION_COOKIE_SECURE
assert not settings.SECURE_SSL_REDIRECT
assert settings.DEBUG is False

client = Client(enforce_csrf_checks=True, HTTP_HOST="demo.joethon.dev",
                HTTP_X_FORWARDED_PROTO="https")
page = client.get("/accounts/login/")
assert page.status_code == 200, f"Login GET: HTTP {page.status_code}"
csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
assert csrf, "Login form did not set a CSRF cookie"
response = client.post("/accounts/login/", {
    "username": "invalid-demo-user@example.invalid",
    "password": "intentionally-incorrect",
}, HTTP_ORIGIN="https://demo.joethon.dev")
assert response.status_code != 403, (
    f"Valid-origin login form failed CSRF: HTTP {response.status_code}"
)
print("Codespaces demo: host, HTTPS cookies and CSRF login checks passed.")
PY

echo "Demo healthy. Forward port 80 and set its visibility to Public only when ready."
echo "Use https://demo.joethon.dev after configuring the Cloudflare gateway and tunnel token."
