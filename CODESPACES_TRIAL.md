# DokkiMashora — 10-day GitHub Codespaces trial

This is a **development/client-acceptance demo**, not production hosting or the
approved 400-user SLA benchmark. Do not import real student records, payment
credentials, live SMTP configuration, or production R2 secrets.

## What this branch adds

- A Codespaces dev container with Docker-in-Docker and port 80 forwarding.
- A dedicated `compose.codespaces.yml` **override** over the unchanged
  `compose.application.yml` + `compose.infrastructure.yml`.
- Two-CPU-compatible container pinning and lower demo memory limits.
- A required private `.env` file. Startup fails if it is missing or invalid;
  credentials are never generated automatically.
- Automatic image build only if missing; otherwise restart existing Compose
  services after Codespace resume.
- PostgreSQL 18, Redis AOF and media bind-mounted under the persistent
  Codespace workspace. Existing application migrations and health checks are
  reused.

The actual GitHub Codespace and its initial port visibility must be created
in GitHub's UI. The GitHub connector cannot do those operations.

## One-time Codespace creation

1. Open the repository branch `chore/codespaces-trial` on GitHub.
2. **Code → Codespaces → New with options**; select this branch and a
   **2-core** Codespace (8 GB RAM), then create it.
3. Create `.env` manually in the project root before starting. The post-start
   hook fails if it is missing. Previously generated `.env.docker` values must
   be migrated manually; the old file is never read. Run:
   `bash .devcontainer/start-demo.sh` in the Codespace terminal.
4. Verify `curl -f http://localhost/healthz` succeeds.
5. In VS Code's **Ports** panel, select port `80`, set visibility to
   **Public**, and copy the forwarded HTTPS address:
   `https://<CODESPACE_NAME>-80.app.github.dev`.
6. Test from an unauthenticated browser window. If the port becomes Private
   after a restart, change it back to Public manually.

Do not enable public access until a demo user is created and outbound
integrations are verified inactive. GitHub Codespaces is not an always-on
server. It automatically stops on idle and has a continuous runtime limit.
Public forwarded ports do **not** wake a stopped Codespace.

## Mandatory .env for the demo

There is **one runtime source**: the private, gitignored project-root
`.env` file. Docker Compose, Django, Celery, and the optional tunnel use it.
`.env.example` is only a template (not loaded) and has local-development
defaults; you must provide the actual values below. Do not commit them.

```dotenv
POSTGRES_DB=dokkimashora_demo
POSTGRES_USER=dokkimashora
POSTGRES_PASSWORD=<strong-random-hex-password>
APP_IMAGE_TAG=codespaces
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<strong-unique-key-at-least-50-characters>
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,demo.joethon.dev,<CODESPACE_NAME>-80.app.github.dev
DJANGO_CSRF_TRUSTED_ORIGINS=https://demo.joethon.dev,https://<CODESPACE_NAME>-80.app.github.dev
SITE_URL=https://demo.joethon.dev
DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SECURE_COOKIES=true
DATABASE_URL=postgresql://dokkimashora:<same-hex-password>@postgres:5432/dokkimashora_demo
CACHE_URL=redis://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/0
PORT=8000
GUNICORN_WORKERS=3
GUNICORN_THREADS=4
DJANGO_SEED_DEMO=false
USE_R2_STORAGE=false
# Optional, for Cloudflare Tunnel access:
# CF_TUNNEL_TOKEN=<secret-from-Cloudflare>
```

Replace the placeholders and match the `POSTGRES_PASSWORD` value inside
`DATABASE_URL`. Obtain the Codespace name via `echo "$CODESPACE_NAME"`.
For safe hex values, run `openssl rand -hex 32` (database password) and
`openssl rand -hex 64` (Django signing key). Keep `.env` private with
`chmod 600 .env`. The startup script never overwrites, copies, or creates
it; missing/incomplete settings fail before containers start.

## Administration commands

From the Codespace repository root, define a helper in the terminal:

```bash
dc() {
  docker compose --project-name dokkimashora-trial --env-file .env \
    -f compose.application.yml -f compose.infrastructure.yml \
    -f compose.codespaces.yml "$@"
}
dc ps
dc exec web python manage.py seed_roles
dc exec web python manage.py createsuperuser
dc logs --tail=100 web celery-submit celery-autosave beat nginx
```

The first startup intentionally does **not** create an administrator with a
default password. The `createsuperuser` command prompts for credentials.
Set `DJANGO_SEED_DEMO=false` in your `.env`; introduce
synthetic demo data only through an approved, existing seed command.

The app accepts both the Codespaces forwarded HTTPS host and the Cloudflare
`demo.joethon.dev` hostname for Django allowed hosts/CSRF. The Cloudflare
hostname is the canonical site URL. This override disables Django's **HTTP-to-HTTPS
redirect** because GitHub terminates HTTPS before reaching the internal
HTTP Nginx, but it retains secure browser cookies.

Django production settings select the database-managed email backend. The
fresh demo database should have no active SMTP configuration; confirm this
before any bulk notifications or email workflows. Likewise, local media is
used here rather than production R2; do not claim that R2-specific functionality
is validated by this demo.

## Database and 10-day retention

- PostgreSQL 18 data: `.docker-data/postgres` (volume at
  `/var/lib/postgresql`, matching the existing PostgreSQL 18 layout).
- Redis AOF: `.docker-data/redis`.
- Media uploads: `media/`.
- The existing `static_data` named volume is **regenerable** only.
- The startup script creates an empty ignored `staticfiles/` directory in the
  host workspace so Docker can mount the named volume below the read-only
  `/app` source mount. Actual collected assets live in `static_data`, not in
  the host directory; never mount host `staticfiles/` over that volume.
- Keep the **same** Codespace for the full 10 days. A normal stop/start should
  preserve workspace data. Deleting a Codespace or removing its workspace
  destroys the local data. Rebuilding the development container can recreate
  Docker's own state, so preserve data with backups.
- Ensure your GitHub Codespaces retention setting covers the full trial.
- Never run `docker compose down -v` unless you intend to remove volumes.

After defining the `dc` helper above, create a private database backup:

```bash
mkdir -p .demo-backups
dc exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > ".demo-backups/db-$(date +%Y%m%d-%H%M%S).dump"
tar -czf ".demo-backups/media-$(date +%Y%m%d-%H%M%S).tar.gz" -C media .
```

Download backups to private storage **outside** Codespaces; a backup on
the same Codespace does not protect against its deletion. Verify a restore
with a separate disposable database before the tutorial. Database backups
may contain personal data; use synthetic records and do not commit them.

## Celery and scheduled tasks

`celery-submit`, `celery-autosave` and `beat` are brought up with the same
Compose stack. While the Codespace is stopped, no worker or periodic job runs.
In-flight tasks can be interrupted; correctness depends on the existing
durable submission/recovery and idempotency logic. Inspect queues and logs
after resuming. Redis AOF does not guarantee delivery of every unacknowledged
task across arbitrary shutdowns.

## Scope and limits

The optional Cloudflare gateway described below is only for a controlled
10-day development/demo session. It requires manual secret provisioning.
GitHub explicitly restricts production-facing hosting and disproportionate
burden on Codespaces. No unapproved public production service or
400-concurrent-user load generation is allowed through this Codespace.

A 2-core/8-GB Codespace is **not** the approved 2-vCPU/4-GB/30-GB benchmark
environment. Do not run the 400-concurrent-user load test through a public
Codespaces forwarded port. Run the exact 400-user synchronized barrier,
autosave, submit, and mixed workload simulations on a separate isolated,
resource-matched benchmark machine. The hard p99 acceptance remains **<=4s**;
the engineering target remains **<=3s**.

Configure GitHub Codespaces billing and spending limits before the demo.
A 2-core Codespace consumes two core-hours per running hour. Never assume
an unattended 10-day always-on website fits the free allowance.


## Cloudflare on-demand activation (optional controlled demo)

This trial has dedicated, already-created Cloudflare resources:

- `https://demo.joethon.dev` — Cloudflare Worker named
  `dokkimashora-demo-gateway`; always presents the activation screen when
  the upstream tunnel is unavailable.
- `dokkimashora-origin.joethon.dev` — proxied DNS for the Cloudflare Tunnel
  `dokkimashora-codespaces-demo`. The tunnel forwards to `nginx:80`.
- The original GitHub Codespaces forwarded port can remain **private**.
  GitHub reverts public ports to private on restart, which is why the
  independent Cloudflare Tunnel is used.

### 1. Install the tunnel token inside the existing Codespace

In Cloudflare Dashboard → **Networking → Tunnels** → select
`dokkimashora-codespaces-demo` → **Add a replica**, copy the
`eyJ...` **tunnel token** from the installation command. Do not paste it
into a chat, GitHub issue/PR, or any tracked file.

In the Codespace terminal:

```bash
cd /workspaces/DokkiMashora
git pull --ff-only origin chore/codespaces-trial
read -rs -p "Paste Cloudflare Tunnel token (hidden): " CF_TOKEN
echo
sed -i '/^CF_TUNNEL_TOKEN=/d' .env
printf 'CF_TUNNEL_TOKEN=%s\n' "$CF_TOKEN" >> .env
unset CF_TOKEN
chmod 600 .env
bash .devcontainer/start-demo.sh
```

The startup script launches the additional `cloudflared` container only if
the token is present. Normal stop/resume does not change your database; the
startup script reuses the image and restarts the stack, including the tunnel.

After it starts, verify:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -i http://localhost/healthz
```

Check `https://demo.joethon.dev/__demo/status`; it should show
`{"ready":true}` once the tunnel is connected and Django is healthy.
If not, inspect `docker logs --tail=60 dokkimashora-trial-cloudflared-1`
and verify the HTTPS site from a private browsing window.

### 2. Configure a scoped GitHub token for remote activation

In GitHub **Settings → Developer settings → Personal access tokens →
Fine-grained tokens**, create an expiration-limited token:

- Resource owner: `JoethonDev`.
- Repository access: **Only selected repositories → DokkiMashora**.
- Repository permission **Codespaces: Read** (to discover your Codespace).
- Repository permission **Codespaces lifecycle admin: Read and write**
  (to start the Codespace through GitHub's API).
- No organization administration or other repository write permissions.

In Cloudflare **Workers & Pages → dokkimashora-demo-gateway →
Settings → Variables and Secrets**, add two **Secret** bindings:

- `GITHUB_TOKEN` — the fine-grained GitHub token.
- `ACTIVATION_KEY` — your own strong random activation passphrase
  (20+ characters; e.g. generate `openssl rand -hex 24`). Keep this
  passphrase in your password manager; it is entered on the activation page,
  never in GitHub source code.

If multiple Codespaces exist for the same repository, also add a **Text**
binding named `CODESPACE_NAME` containing the target Codespace's exact name
(found with `echo "$CODESPACE_NAME"` inside the Codespace).
If you have exactly one Codespace for `JoethonDev/DokkiMashora`, the Worker
can discover it without this binding.

Never paste these tokens or the activation passphrase into ChatGPT.
No credentials are embedded in the Worker source. The Worker is safe to
deploy before the secrets exist: activation requests will respond with a
configuration-unavailable message.

### 3. Test stop/start activation

1. Verify the running site at `https://demo.joethon.dev` first.
2. In `https://github.com/codespaces`, use **... → Stop codespace**.
   Stop the existing Codespace, **do not delete it**.
3. Open `https://demo.joethon.dev` again. It should show the offline screen.
4. Enter your activation passphrase and select **Activate demo**.
5. The Worker calls the GitHub Codespaces Start API once, and the browser
   polls `/__demo/status` until the tunnel/health endpoint is available,
   then reloads into Django. GET requests **never** start the Codespace, to
   prevent anonymous visitors from consuming your Codespaces budget.

Scheduled Celery jobs cannot run while the Codespace is stopped. Allow time
for service recovery after resuming; do not start an exam mid-recovery.

### Important caveats

- The origin tunnel hostname is technically reachable directly if its
  hostname is known. Do not load real student records into this temporary
  demo; protect the Django application with accounts and add Cloudflare
  Access if you need a stronger perimeter.
- Codespaces may stop due to idle-timeout or continuous-runtime limits.
- Cloudflare does **not** keep Django, PostgreSQL, Redis or Celery running
  while the Codespace is stopped.
- Public demo links are not authorization to run production workloads in
  Codespaces. See GitHub's Codespaces Terms for permitted development usage.
- Activation has ongoing Codespaces compute consumption while running;
  review GitHub billing and spending controls.

## Cleanup

After the tutorial, download and verify the final backup, stop the Codespace,
then delete it when the data is no longer needed. Review repository and
Cloudflare resources before removing anything unrelated.
