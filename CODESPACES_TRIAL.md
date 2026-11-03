# DokkiMashora — 10-day GitHub Codespaces trial

This is a **development/client-acceptance demo**, not production hosting or the
approved 400-user SLA benchmark. Do not import real student records, payment
credentials, live SMTP configuration, or production R2 secrets.

## What this branch adds

- A Codespaces dev container with Docker-in-Docker and port 80 forwarding.
- A dedicated `compose.codespaces.yml` **override** over the unchanged
  `compose.application.yml` + `compose.infrastructure.yml`.
- Two-CPU-compatible container pinning and lower demo memory limits.
- Automatic initial generation of **random local credentials** in the
  gitignored `.env.docker` (never displayed or committed).
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
3. The post-start hook creates the gitignored local demo environment and
   starts the existing Compose stack automatically. Initial image building,
   migrations and static collection may take time. If needed, run:
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

## Administration commands

From the Codespace repository root, define a helper in the terminal:

```bash
export APP_ENV_FILE=.env.docker APP_IMAGE_TAG=codespaces
dc() {
  docker compose --project-name dokkimashora-trial --env-file .env.docker \
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
The auto-generated `.env.docker` has `DJANGO_SEED_DEMO=false`; introduce
synthetic demo data only through an approved, existing seed command.

The app uses the Codespaces HTTPS host for Django allowed hosts, CSRF origins
and canonical site URL. This override disables Django's **HTTP-to-HTTPS
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

The public GitHub-forwarded URL is **not** a custom-domain hosting origin.
No custom DNS, Cloudflare Worker, wake-on-request proxy, or automatic port
visibility change is part of this first phase. A stable custom domain and
wake-on-request flow would require an external gateway and must be checked
against GitHub's terms before being used for unsupervised public hosting.

A 2-core/8-GB Codespace is **not** the approved 2-vCPU/4-GB/30-GB benchmark
environment. Do not run the 400-concurrent-user load test through a public
Codespaces forwarded port. Run the exact 400-user synchronized barrier,
autosave, submit, and mixed workload simulations on a separate isolated,
resource-matched benchmark machine. The hard p99 acceptance remains **<=4s**;
the engineering target remains **<=3s**.

Configure GitHub Codespaces billing and spending limits before the demo.
A 2-core Codespace consumes two core-hours per running hour. Never assume
an unattended 10-day always-on website fits the free allowance.

## Cleanup

After the tutorial, download and verify the final backup, stop the Codespace,
then delete it when the data is no longer needed. Review repository and
Cloudflare resources before removing anything unrelated.
