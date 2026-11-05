#!/usr/bin/env bash
# GitHub Codespaces development-only bootstrap; never deploys to production.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${CODESPACE_NAME:-}" ]]; then
  echo "This entrypoint requires GitHub Codespaces (CODESPACE_NAME is unset)." >&2
  exit 1
fi

# No auto-generation and no alternate dotenv file.
if [[ ! -s .env || ! -r .env ]]; then
  echo "ERROR: Missing, empty or unreadable .env. Create it manually before startup." >&2
  exit 1
fi
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
  docker compose --project-name dokkimashora-trial --env-file .env
  -f compose.application.yml -f compose.infrastructure.yml -f compose.codespaces.yml
)

"${compose[@]}" config --quiet

# Run the configuration validator in the Python Docker image, not on the
# Codespaces host (the base devcontainer does not provide python3).
# No secrets are logged; Compose JSON is only piped to this local container.
web_image="$("${compose[@]}" --profile tunnel config --format json |
  docker run --rm -i --network none --read-only \
    -e CODESPACE_NAME="$CODESPACE_NAME" \
    python:3.11-slim python -c '
import json, os, sys
services = json.load(sys.stdin)["services"]
env = services["web"]["environment"]
required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
            "DJANGO_SECRET_KEY", "DJANGO_SETTINGS_MODULE", "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS", "SITE_URL", "DATABASE_URL", "CACHE_URL",
            "CELERY_BROKER_URL", "DJANGO_SECURE_COOKIES", "DJANGO_SECURE_SSL_REDIRECT",
            "APP_IMAGE_TAG", "PORT", "GUNICORN_WORKERS", "GUNICORN_THREADS")
missing = [k for k in required if not str(env.get(k, "")).strip()]
if missing:
    sys.exit("ERROR: Missing required settings in .env: " + ", ".join(missing))
codespace = os.environ.get("CODESPACE_NAME", "")
forwarded = codespace + "-80.app.github.dev"
hosts = set(env["DJANGO_ALLOWED_HOSTS"].split(","))
origins = set(env["DJANGO_CSRF_TRUSTED_ORIGINS"].split(","))
if not {"localhost", "127.0.0.1", "demo.joethon.dev", forwarded} <= hosts:
    sys.exit("ERROR: .env DJANGO_ALLOWED_HOSTS needs the demo and forwarded hosts")
if not {"https://demo.joethon.dev", "https://" + forwarded} <= origins:
    sys.exit("ERROR: .env DJANGO_CSRF_TRUSTED_ORIGINS needs both HTTPS origins")
if env["DJANGO_SETTINGS_MODULE"] != "config.settings.prod" or env.get("DJANGO_DEBUG", "false").lower() not in ("false", "0"):
    sys.exit("ERROR: Use production Django settings with DJANGO_DEBUG=false in demo .env")
if env["SITE_URL"] != "https://demo.joethon.dev":
    sys.exit("ERROR: .env SITE_URL must be https://demo.joethon.dev")
if env["DJANGO_SECURE_COOKIES"].lower() != "true" or env["DJANGO_SECURE_SSL_REDIRECT"].lower() != "false":
    sys.exit("ERROR: .env secure cookie or SSL redirect settings are incorrect")
if len(env["DJANGO_SECRET_KEY"]) < 50 or env["DJANGO_SECRET_KEY"].startswith(("django-insecure-","replace-with-")):
    sys.exit("ERROR: .env needs a unique DJANGO_SECRET_KEY (50+ characters)")
if not env["DATABASE_URL"].startswith(("postgresql://", "postgres://")) or "@postgres:5432/" not in env["DATABASE_URL"]:
    sys.exit("ERROR: .env DATABASE_URL must point to the Docker Postgres service")
if env["CACHE_URL"] != "redis://redis:6379/1" or env["CELERY_BROKER_URL"] != "redis://redis:6379/0":
    sys.exit("ERROR: .env must point Cache and Celery to the Docker Redis service")
if env["PORT"] != "8000":
    sys.exit("ERROR: .env PORT must be 8000 for the existing Nginx upstream")
if services["web"]["image"] != "dokkimashora-app:" + env["APP_IMAGE_TAG"]:
    sys.exit("ERROR: Docker image tag differs from the value in .env")
postgres = services["postgres"]["environment"]
for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
    if postgres[key] != env[key]:
        sys.exit("ERROR: Postgres is overriding " + key + " instead of using .env")
tunnel_env = services["cloudflared"]["environment"]
if tunnel_env.get("TUNNEL_TOKEN", "") != env.get("CF_TUNNEL_TOKEN", ""):
    sys.exit("ERROR: Tunnel token differs from the one configured in .env")
for service in ("web", "celery-submit", "celery-autosave", "beat"):
    e = services[service]["environment"]
    if any(e.get(k) != env.get(k) for k in required):
        sys.exit("ERROR: " + service + " has settings that differ from .env")
print(services["web"]["image"])
')"
echo "Codespaces .env configuration validated."


if ! docker image inspect "$web_image" >/dev/null 2>&1; then
  echo "First run: building the shared Django/Celery image..."
  "${compose[@]}" build web
fi

# Dockerfile runs the application as the nonroot 'app' user. Give that user
# ownership of the bind-mounted demo media folder without changing the source.
docker run --rm --user root --entrypoint sh \
  -v "$PWD/media:/demo-media" "$web_image" \
  -c 'chown app:app /demo-media'

echo "Reloading the web and Nginx containers with the latest Codespaces settings..."
# 'up' alone keeps existing containers (and their old environment) unchanged.
# Recreate only these stateless services; never reset Postgres/Redis volumes.
"${compose[@]}" up -d --no-build --force-recreate web nginx

echo "Starting Docker Compose services..."
# Cloudflare Tunnel is opt-in. Its secret lives only in the ignored env file;
# Docker Compose never starts the tunnel with a missing/blank token.
if grep -Eq '^CF_TUNNEL_TOKEN=[^[:space:]]+' .env; then
  echo "Cloudflare Tunnel token configured; starting the tunnel sidecar."
  "${compose[@]}" --profile tunnel up -d --no-build
else
  echo "Tunnel disabled until CF_TUNNEL_TOKEN is added to .env."
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
"${compose[@]}" exec -T -e CODESPACE_NAME="${CODESPACE_NAME}" web python - <<'PY'
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

for origin in expected:
    host = origin.removeprefix("https://")
    client = Client(enforce_csrf_checks=True, HTTP_HOST=host,
                    HTTP_X_FORWARDED_PROTO="https")
    page = client.get("/accounts/login/")
    assert page.status_code == 200, f"{host} login GET: HTTP {page.status_code}"
    csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert csrf, f"{host} login form did not set a CSRF cookie"
    response = client.post("/accounts/login/", {
        "username": "invalid-demo-user@example.invalid",
        "password": "intentionally-incorrect",
    }, HTTP_ORIGIN=origin)
    assert response.status_code != 403, (
        f"{host} valid-origin login form failed CSRF: HTTP {response.status_code}"
    )
print("Codespaces demo: both public hosts, HTTPS cookies and CSRF checks passed.")
PY

echo "Local demo healthy. Use the Codespaces forwarded HTTPS port 80 for direct access."
if grep -Eq '^CF_TUNNEL_TOKEN=[^[:space:]]+' .env; then
  echo "Tunnel sidecar configured. Use https://demo.joethon.dev when it connects."
  echo "If the public domain is offline, inspect: docker compose --project-name dokkimashora-trial --env-file .env -f compose.application.yml -f compose.infrastructure.yml -f compose.codespaces.yml --profile tunnel logs --tail=40 cloudflared"
else
  echo "Public demo is OFFLINE: add CF_TUNNEL_TOKEN to the private .env and rerun this script."
  echo "Get the token from Cloudflare Zero Trust > Networks > Tunnels > dokkimashora-codespaces-demo."
fi
