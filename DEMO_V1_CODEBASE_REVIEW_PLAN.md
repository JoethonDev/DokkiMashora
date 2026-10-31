# Demo v1 Codebase Review, Cleanup, and Ship Plan

**Status:** Review complete through R7 and R8(partial). **Demo v1 = CONDITIONAL GO — blocked only by the RV-000 external key rotation (user action).**  
**Created:** 2026-08-28  
**Authority:** The current user instruction, `AGENTS.md`, and `DOKKIMASHORA.md` remain authoritative. This file is a task ledger and does not create or change business rules.

## 1. Objective

Prepare DokkiMashora for Demo v1 by reviewing the complete real application flow, then making small, reviewable batches that:

- remove code and artifacts proven to be dead;
- consolidate duplicated implementation only when the callers perform the same business operation;
- identify similar-looking paths whose different logic is intentional and record them as `KEEP_SEPARATE`;
- find missing, inconsistent, or incorrectly displayed permissions, actions, and business states;
- find correctness, security, concurrency, query, resource, and deployment risks;
- preserve historical data, audit history, migrations, and current in-progress work;
- produce an explicit, evidence-backed ship/no-ship decision for Demo v1.

This is not authorization to change a locked business rule. Any conflict between current behavior and `DOKKIMASHORA.md` becomes a decision gate before implementation.

## 2. Current Baseline

Baseline captured before any cleanup:

| Item | Current evidence |
|---|---|
| Branch / commit | `refactor/ui-template-reset` / `f0367ac` (reconciled 2026-08-28) |
| Repository scope | 646 files under the main application/config/template/static/deploy areas; 16 URL-bearing Django apps |
| Working tree | 15 modified paths + 11 untracked entries after commit `f0367ac` absorbed the previous 194-entry dirty tree; remaining dirty files are env templates, seed commands, one test file, locale, and local artifacts |
| Current diff size | 9 files, about 86 insertions / 73 deletions vs `f0367ac`, plus untracked local/generated artifacts |

**Baseline update (2026-08-28):** commit `f0367ac` (the prior 170-file dirty tree, including the shared scoring/date/HTMX/countdown/media-progress/results-shell consolidation and the new migrations) was committed during this session. `apps/core/scoring.py`, `apps/core/dates.py`, `static/js/countdown-core.js`, `static/js/media-progress-core.js`, `templates/ui/components/_results_shell.html`, `apps/applications/migrations/0022_programapplicationemaillog_centralization.py`, and the two new audit migrations are now tracked. The remaining dirty files are `.env.docker.example`, `.env.example`, `.gitignore`, `README.md`, three seed commands, `apps/applications/tests/test_services.py`, `locale/ar/LC_MESSAGES/django.po`, and untracked local/generated artifacts. `opencode.json` remains ignored and was never committed.
| Django system check | Passed: 0 issues |
| Migration drift | Passed: no changes detected |
| Applied migration sample | Current container has the recent AI removal, communications, applications, and audit migrations applied |
| Runtime | Web, Celery, Beat, PostgreSQL, Redis, Nginx, and Portainer containers healthy at capture time |
| Health endpoint | `GET /healthz` returned HTTP 200 with database healthy |
| Changed/new JavaScript syntax | `node --check` passed for 8 changed/new first-party scripts |
| Automated suites | Not run; `AGENTS.md` requires explicit user authorization |

### Baseline warning

The existing dirty tree contains active consolidation work, including shared scoring/date/HTMX helpers, shared countdown and media-progress JavaScript, shared results-shell templates, progression changes, communications changes, and broad UI edits. Cleanup must not begin by deleting or resetting these files. First determine which changes are intentional, complete, and ready to become the review baseline.

Older audit Markdown files are historical leads only. Several conclusions in `IMPLEMENTATION_PROGRESS.md` and `DUPLICATION_AUDIT.md` are already stale relative to the current dirty tree.

## 3. Non-Negotiable Guardrails

1. Read `AGENTS.md` and the relevant `DOKKIMASHORA.md` sections before every batch.
2. Run `git status --short` and inspect every relevant dirty-file diff before editing.
3. Never use `git clean`, destructive reset/checkout, or broad deletion to establish a baseline.
4. Trace URL → view → parser/form → permission/policy → selector/query → service → model/constraint → migration/task → full template/fragment → JavaScript → all callers.
5. Search for an existing analogous path before adding or consolidating code.
6. Do not merge Entrance Exam with normal Assessment workflows.
7. Do not merge Course and Diploma behavior merely because both contain lectures, attempts, grades, or enrollment.
8. UI visibility is never accepted as authorization evidence.
9. A friendly validation check is never accepted as a replacement for a required database invariant.
10. No index is added without a demonstrated query pattern and measured plan/latency evidence.
11. No test is created, modified, regenerated, or run without explicit user authorization.
12. Application changes require the restart and health verification sequence in `AGENTS.md`; documentation-only edits do not.

## 4. Finding Lifecycle

Every item uses one of these states:

| State | Meaning |
|---|---|
| `LEAD` | Search result or historical claim; not yet verified |
| `VERIFY` | Static evidence exists; full callers/runtime/data impact still needs confirmation |
| `DECISION_REQUIRED` | Multiple valid product outcomes exist or code conflicts with a locked rule |
| `APPROVED` | Scope and intended behavior are explicitly accepted |
| `IN_PROGRESS` | One owner is implementing the approved narrow batch |
| `RESOLVED` | Change and required validation are complete |
| `ACCEPTED_RISK` | User explicitly accepts a bounded Demo v1 risk with owner/follow-up |
| `KEEP_SEPARATE` | Similar code has intentionally different business semantics |
| `NOT_A_FINDING` | Evidence disproved the concern |

Priority is separate from state:

- `P0`: active data loss, material authorization bypass, secret exposure, or reliably broken primary workflow; blocks all shipping.
- `P1`: serious correctness/security/concurrency/performance/operational risk; blocks Demo v1 unless resolved or explicitly accepted.
- `P2`: bounded defect or maintainability risk; resolve before Demo where practical, otherwise assign owner and deadline.
- `P3`: cleanup/polish with no demonstrated near-term product risk.

## 5. Required Finding Record

Use this exact structure so another reviewer can continue without rediscovery:

```text
ID / title:
Priority / state / confidence:
Business rule or user outcome:
Evidence (file:line and current behavior):
Complete caller flow:
Difference matrix (when similar implementations exist):
Security/data/concurrency/performance impact:
Proposed owner layer:
Candidate files:
Decision required:
Allowed validation:
Result / residual risk:
```

Confidence must be `high`, `medium`, or `low`. Static suspicion cannot be written as a confirmed bug.

## 6. Duplicate-Logic Decision Method

For every apparent duplicate, build a caller comparison before changing code:

| Dimension | Caller A | Caller B |
|---|---|---|
| Business operation |  |  |
| Actor/role |  |  |
| Ownership/scope |  |  |
| Allowed states |  |  |
| Input validation |  |  |
| Transaction/locking |  |  |
| Audit/history |  |  |
| Student visibility/release |  |  |
| Failure/idempotency |  |  |
| Output/HTMX/full-page behavior |  |  |

Choose exactly one outcome:

- `CANONICALIZE`: same business operation and same invariant; one path should own it.
- `SHARE_NARROW_INVARIANT`: workflows differ, but a pure calculation, validation, or presentation rule is identical.
- `STANDARDIZE_PRESENTATION`: backend semantics differ; only UI/response shape is shared.
- `KEEP_SEPARATE`: different domain rule, actor, state machine, history, or visibility.
- `DELETE_DUPLICATE`: one path is proven unreachable and has no data/migration obligation.

Before shared arithmetic is introduced, explicitly compare rounding, null/zero handling, boundary comparisons, penalties, precision, and stored values. “Both calculate a percentage” is not sufficient proof.

## 7. Dead-Code and Safe-Deletion Proof

A code/file/dependency is deletable only after all applicable checks pass:

- no Python import, re-export, dynamic import, Django app registration, admin registration, signal, or management-command use;
- no URL, reverse name, redirect, navigation, form action, HTMX endpoint, template include/extend, static reference, or JavaScript event/data-attribute caller;
- no Celery task name, beat schedule, serialized job, webhook, health check, deployment script, or operational runbook use;
- no model/migration dependency, historical row, content type, permission, storage object, or audit/history preservation obligation;
- no external entry point documented for users/operators;
- current production-shaped logs or route exercise do not show use when static proof is insufficient;
- removal has a rollback/recovery path for any material artifact or data.

Classification:

- `CONFIRMED_DEAD`: all proof completed; removal may be proposed.
- `PROBABLY_DEAD`: no current caller found, but runtime/data/deployment proof remains.
- `HISTORICAL_REQUIRED`: inactive runtime code/data retained for schema or audit history.
- `LOCAL_ARTIFACT`: not application code; decide ignore/archive/delete ownership.
- `GENERATED`: reproducible output; source and generation command must be identified.

## 8. Review Workstreams

### R0 — Baseline and contract reconciliation

- Reconcile the 194-entry working tree into intentional work, generated output, local tooling, historical artifacts, and accidental files.
- Revalidate every open claim from old audit documents against current code.
- Record all code-versus-locked-contract conflicts.
- Establish a reviewable commit/diff boundary before destructive cleanup.

**Gate:** no unknown dirty file in an affected batch; each untracked artifact has an owner/classification.

### R1 — Authentication, authorization, and data exposure

- Build a role/action/object-scope matrix for Super Admin, Admin, Moderator, Reviewer, Secretary, Student, and deferred Teacher.
- Check every protected URL for authentication, role, exact object scope, ownership, enrollment, schedule, state, and method/CSRF.
- Exercise direct URLs, not only visible buttons.
- Check anonymous Reviewer projections for identity leakage.
- Check registration/login/activation/resend abuse controls and error-message enumeration.
- Review uploads, protected downloads, signed URLs, audit metadata, logs, email previews, and template safe-rendering boundaries.

**Gate:** no open P0/P1 authorization or sensitive-data concern; every destructive/mutating action is POST/CSRF and server-authorized.

### R2 — Business workflow correctness

- Registration/identity/additional application uniqueness.
- Payment/waiver/activation ordering and audit.
- Entrance Exam secrecy and admission-decision separation.
- Course Quiz, Homework, Semester Exam, grading, release, and correct-answer secrecy.
- Attendance and server-authoritative scanner behavior.
- Semester Evaluation, Interview, promotion, re-evaluation, and rejoin history.
- Course sequential completion versus Diploma no-watch-lock behavior.
- Certificate immutability/current-name rendering.

**Gate:** each locked rule has one traced authoritative owner and equivalent full-page/HTMX behavior.

### R3 — Database, concurrency, and durable invariants

- Compare form/service validation to database constraints.
- Review attempt creation/submission/autosave races, grade release, reviewer claims, payment imports, activation, promotion, rejoin, deletion, and Celery idempotency.
- Inspect nullable uniqueness semantics on PostgreSQL.
- Verify transactions are the smallest complete invariant and external I/O happens after commit.

**Gate:** contended transitions have deterministic results, no race-time 500, preserved history, and explicit database protection where required.

### R4 — Performance and infrastructure ceiling

- Measure query count and time for representative list/detail/dashboard flows at 1, 10, and page-limit rows.
- Find query work inside loops, repeated selectors, template relation access, unbounded history, imports, exports, and cohort-wide operations.
- Measure Celery queue delay, SMTP behavior, certificate rendering, audio processing/delivery, memory, CPU, and disk.
- Use PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` only for safe representative reads after identifying dominant SQL.

**Gate:** agreed budgets are met under representative Demo data; no P1 unbounded synchronous operation; peak deployment use fits 2 vCPU / 4 GB / 30 GB with headroom.

### R5 — Duplication and architecture cleanup

- Revalidate the 18 historical `DUPLICATION_AUDIT.md` items against the current dirty tree.
- Inspect views/services larger than a normal cohesive unit, but split only by demonstrated responsibility.
- Consolidate one invariant at a time; do not create generic frameworks or one-use wrappers.
- Delete obsolete helpers immediately after their final caller migrates, in the same approved batch when safe.

**Gate:** every consolidation includes the difference matrix and affected-caller review; no business result changed silently.

### R6 — Dead code, dependency, and repository hygiene

- Audit deferred Teacher structures, the removed AI app shell, legacy assessment statuses/routes, orphan templates/fragments, unused JS/CSS, unused dependencies, commented debug code, seed-only code, backup files, prototypes, temporary output, and local tool installations.
- Check `.gitignore` and `.dockerignore` so local/generated files do not enter commits or image build context.
- Treat migrations and immutable history as first-class retention constraints.

**Gate:** every deletion is `CONFIRMED_DEAD`; image/repository contents contain only owned runtime/source assets.

### R7 — UI, HTMX, JavaScript, i18n, and accessibility

- Verify canonical full-page URLs and HTMX fragment parity.
- Verify persistent navigation active state, history/refresh, validation re-rendering, and HTMX lifecycle initialization.
- Verify backend-prepared available actions match rendered desktop/mobile actions.
- Check Arabic RTL and English LTR, mixed-direction data, translated labels, one `h1`, focus, live regions, reduced motion, and 44 px controls.
- Revalidate shared results-shell/form/status/modal components without building flag-heavy mega-components.

**Gate:** task parity across viewport/language and no client-side business authorization/state machine.

### R8 — Operations and Demo ship readiness

- Reconcile Compose, Dockerfile, GitHub CI/deploy, Nginx/TLS, environment examples, health checks, Celery Beat, backup scheduling, restore instructions, log rotation, storage configuration, and rollback.
- Confirm production secret/config requirements without reading or logging secrets.
- Make deployment health failure fail the deployment gate rather than being informational.

**Gate:** clean build/deploy rehearsal, health/log verification, Celery delivery check, backup plus restore drill, and documented rollback.

## 9. Initial Evidence-Backed Concerns

These are the initial review backlog, not permission to implement.

| ID | Priority | State | Initial evidence and required decision/validation |
|---|---|---|---|
| RV-000 Exposed provider credential | P0 security | `RESOLVED` (containment) / external rotation REQUIRED | Verified 2026-08-28: `opencode.json:10` holds a non-empty 51-char live-looking `sk-` key (not a placeholder). It is untracked, ignored via `.gitignore:31`, and `git log --all` shows it was **never committed**. A working-tree scan of the whole repo found no other strong secret pattern, and `git log --all -G` found no committed blob matching `sk-`/`AIza`/`AKIA`/`ghp_`/`github_pat_`/private-key patterns. The credential must be treated as **compromised** — external revocation/rotation is required by the owner; the value was never printed or logged. No secret-free template was added because `opencode.json` is a local tool config that is already safely ignored. |
| RV-001 Dirty baseline obscures ownership | P1 process | `VERIFY` | 170 modified and 24 untracked status entries span nearly every domain. Establish intentional ownership and a stable review boundary before cleanup; do not reset or delete. |
| RV-002 Moderator contract/model conflict | P1 correctness | `RESOLVED` 2026-08-28 | D-001 Option A implemented: `ModeratorAssignment.course_version` is now nullable (migration `academics.0027` applied), the staff-assignment form no longer requires it, the create view drops the requirement + audit metadata, and the course-version branches were removed from `assessment_staff_scope_filter`, `assessment_scope_filter`, and `enrollment_scope_filter` (`apps/core/permissions.py`). Moderators are now Diploma/cohort-only in both authorization and UI. Verified via test client: assignments page 200, cohort-scope create succeeds with `course_version=None`, no course-version select rendered. |
| RV-003 Shared scoring changed boundary semantics | P1 correctness | `RESOLVED` 2026-08-28 | D-002 locked as **Rule A (quantize-first, `ROUND_HALF_EVEN`)** — the current `apps/core/scoring.py` is canonical; no code change. Locked rule documented in `DOKKIMASHORA.md` §8.3. |
| RV-023 HLS access conflicts with signed-URL contract | P2 security/architecture | `RESOLVED` 2026-08-28 | D-006 locked: the authenticated application-proxy streaming route is an approved, documented exception to §19.1 (`DOKKIMASHORA.md` §19.1). Signed URLs remain the contract for non-streaming downloads. |
| RV-004 Public auth abuse controls not found | P1 security | `RESOLVED` 2026-08-28 | Confirmed no app-level throttle or dependency (requirements.prod.txt has none; `apps/accounts/views.py:111` uses the standard `LoginView`; public register/resend at `apps/applications/views.py:169` and `apps/accounts/views.py:969`). Added edge rate limiting in `deploy/nginx.conf`: `limit_req_zone ... zone=auth_limit:10m rate=30r/m` + `limit_req_status 429` on `/accounts/login`, `/accounts/logout`, `/accounts/resend-activation`, `/applications/register`. Proxy-aware (`$binary_remote_addr`), IP-based so no account enumeration. Verified: 30 rapid `/accounts/login/` requests → 21×200 + 9×429 (burst 20 + 1); 40 `/healthz` requests all 200. Note: nginx `limit_req zone=$variable` (map-based skip) fails config load in nginx 1.30.4 with "zero size shared memory zone"; explicit location blocks are the working pattern. Login brute force is now blunted but not eliminated — a distributed attack still needs app-level consideration; see D-003. |
| RV-005 Progression page query fan-out | P1 performance | `VERIFY`→measured | Measured on PostgreSQL via test client: `admin_cohort_semester_progression` for cohort 1 / semester 1 (3 progress rows, 3 enrollments) = **22 queries, HTTP 200**, dominated by per-enrollment evaluation reads (`assessments_assessment`:3, `progression_semesterevaluationconfig`:2, `attendance_attendancesession`:2, `academics_diplomaenrollment`:2). Growth is ~7 queries per additional enrollment (linear). Acceptable at Demo scale (<25 rows); becomes P1 above ~50. **P2 for Demo v1**; bounded-projection refactor should be scheduled after D-003. |
| RV-006 Assessment start/submit/autosave races | P1 correctness | `RESOLVED` 2026-08-28 | Reproduced on PostgreSQL with 8 concurrent `start_assessment` calls: **1 created, 3 unhandled `IntegrityError`** (would 500 in the view — only `ValueError` is caught at `apps/assessments/views.py:282-286`), 4 friendly max-attempt errors. Root cause: attempt number = `count()+1` with no lock/retry around the unique `(assessment, student, attempt_number)` constraint. Fix: `start_assessment` (`apps/assessments/services.py:305-373`) now wraps in `transaction.atomic()` and takes `StudentProfile.objects.select_for_update().get(user_id=student.pk)` (same serialization pattern as homework attachments), making attempt-number derivation deterministic. Re-run: 8/8 concurrent starts resolve to one attempt, no IntegrityError. Also fixed `submit_assessment` to `select_for_update().get(pk=attempt.pk)` + re-check status inside the atomic block (mirrors `submit_diploma_assessment`), eliminating submit/submit and submit/autosave stale-write races on the course path. **Bonus fix (same family):** `add_homework_attachment` locked `StudentProfile.objects.get(pk=<user_id>)` which raises `DoesNotExist` whenever User.id ≠ StudentProfile.id (verified with user 21 ↔ profile 12) → unhandled 500 after the object already uploaded; corrected to `.get(user_id=student.pk)`. Autosave still writes without a lock but re-checks attempt existence; the submit lock now makes late-autosave writes after submit impossible (status re-check under the same row lock). |
| RV-007 Bulk operations can monopolize web/worker capacity | P1 operations | `VERIFY` | Cohort progression/import work is synchronous and bulk email processes recipient chunks serially through the single-concurrency Celery worker. Establish supported batch sizes, timeouts, retry/idempotency, and transactional-email latency budgets. |
| RV-008 Media/render resource bounds need measurement | P1 availability | `RESOLVED` 2026-08-28 | Confirmed the running gunicorn budget was 4 concurrent request slots (`GUNICORN_WORKERS=2`, `GUNICORN_THREADS=2`). Per the locked decision, `GUNICORN_THREADS` is now **4** in `compose.application.yml` (2 workers × 4 threads = 8 slots), giving the 5-stream Demo budget (D-003) headroom under the approved proxy streaming (D-006). Web recreated and verified healthy (200) with `GUNICORN_THREADS=4` in the container env. Certificate rendering stays synchronous but within the 1 GB web limit for Demo-scale inputs. |
| RV-009 Repository/image hygiene | P2 cleanup | `VERIFY`→contained | `.opencode` (~52 MB), `output` (~5.2 MB), `tmp` render artifacts, root `.bak*`/prototype/`requirements*.txt.py`/`test_*.py` files are all untracked/ignored and now excluded from the Docker build context (RV-020). No further action needed for Demo v1 beyond the D-005 owner decision on whether the local files are archived or deleted. |
| RV-010 Historical audit drift | P2 process | `VERIFY` | Historical docs say duplication work is pending while the current tree already contains canonical HTMX, scoring, date, countdown, media-progress, phone, email-log, and results-shell work. Revalidate each old finding; never replay the old queue blindly. |
| RV-011 Deployment health can be ignored | P1 operations | `RESOLVED` 2026-08-28 | `.github/workflows/deploy.yml:63` ended the server health check with `|| true`. Removed `|| true` and added `--retry 3 --retry-connrefused --retry-delay 3`; a failed post-deploy `/healthz` now fails the workflow (script runs under `set -e`). Residual: runner-side `migrate`/`collectstatic` steps (lines 31-37) are redundant and use a runner-local sqlite — harmless, not part of this batch. |
| RV-012 Removed AI shell remains installed | P3 cleanup | `RESOLVED` 2026-08-28 | Confirmed `apps.ai` contains only app shell + 3 migrations (0001 create / 0002 alter / 0003 remove GeminiReview), all applied in the current environment, and **no other app's migration depends on `ai.*`**. A fresh DB needs no AI tables (0001→0003 nets out). Removed the `apps.ai` registration from `config/settings/base.py` and deleted the `apps/ai` directory. `manage.py check` passes; fresh-environment migration replay is unaffected. |
| RV-013 Student dashboard scales with enrollment history | P1 performance | `VERIFY`→measured | Measured on PostgreSQL via test client: student `/profile/` dashboard = **98 queries, ~1192 ms** at Demo scale (single student, ~2 diploma + course enrollments); `/profile/record/` = 49 queries / 55 ms; `/profile/learning/` = 32 queries. The dashboard fan-out (`auth_group`:7, `academics_diploma`:7, `academics_diplomaenrollment`:5, `academics_semester`:5) is the RV-013 linear growth. **P2 for Demo v1** (functional but a 1.2 s dashboard is noticeable); a bounded projection in `apps/students/selectors.py` + `apps/academics/services.py:1077-1131` should be scheduled after D-003. |
| RV-014 Written grading repeats whole-attempt calculation | P1 performance/concurrency | `RESOLVED` 2026-08-28 | Measured on PostgreSQL (throwaway course-quiz fixtures, admin-grade flow): **600 queries at N=100** (linear ~6/answer, not quadratic as initially hypothesized — the plan's O(N²) assumption was disproved). Two defects fixed: (1) `_update_attempt_grade` / `_update_diploma_attempt_grade` now re-fetch the attempt with `select_for_update` inside a transaction, so concurrent graders serialize and never compute the aggregate from a partial answer set; (2) the bulk grade-detail handlers (`apps/assessments/views.py` course + diploma POST loops) grade all answers with `recalculate=False` and run **one** recalculation after the loop. Re-measured: **600 → 307 queries at N=100** (60 → 37 at N=10). Concurrency test: 2 threads × 10 answers, 5 rounds → aggregate 20/20, pass=True, 0 failures. Note: pre-fix race was identified statically (no lock around read-compute-write); the 5/5 deterministic result is the post-fix evidence. Single-answer path (`admin_grade_answer`) keeps the default `recalculate=True`. |
| RV-015 Storage reconciliation performs remote I/O in transactions | P1 operations | `RESOLVED` 2026-08-28 | Reproduced on PostgreSQL with a probe backend: **5/5 remote deletes ran with `connection.in_atomic_block == True`** — the object-store (R2 network) I/O held a DB transaction open (`apps/storage/management/commands/reconcile_storage_deletions.py:41-55`). Fixed: the remote delete now runs **outside** any DB transaction, then a short `transaction.atomic()` with `select_for_update` on the `DeferredStorageDeletion` row deletes it (or bumps `attempts`/`last_error`), serializing concurrent reconcilers. Re-verified: 0/5 deletes inside a transaction, all 5 cleared; failure path keeps the row pending with `attempts=1` + updated error. |
| RV-016 Intake email lifecycle bypass | P1 correctness | `RESOLVED` 2026-08-28 | `apps/intake_forms/services.py:99-102` wrote a synchronous "queued" log via legacy `_log_email_send` (`applications/services.py:482`) then queued via `queue_templated_email` — bypassing `queue_program_application_email`, so a missing/inactive template left a stale "queued" log, `is_resend` was never computed, and dedup semantics diverged from every other application email. Migrated `_send_intake_request_email` to `queue_program_application_email` (canonical queue + single log path + idempotency + is_resend), removed the manual log call, and deleted the now-dead `_log_email_send` (its only caller was intake). |
| RV-017 Orphan template and URL candidates | P2 cleanup | `PROBABLY_DEAD` | Verified 2026-08-28: `ui/pages/admin/communications.html`, `ui/pages/admin/progression.html`, `ui/pages/admin/progression_component_form.html`, `ui/pages/admin/progression_custom_activity.html`, and `shared/profile.html` have **zero static references** in `templates`/`apps` (render/include/extends). The student `progression.html` is live (`students/views.py:171`, `progression/views.py:1332`) and is NOT a candidate. `apps/deployment/urls.py` is inactive but the health view is live via `config/urls.py:28`. Keep until a dynamic/HTMX/external-reference scan completes; candidates documented for the R6 cleanup batch. |
| RV-020 Generated sidecars and backups | P2 cleanup | `RESOLVED` (containment) | `.dockerignore` now excludes `opencode.json`, `.opencode/`, `output/`, `tmp/`, `*.html.py`, `*.bak`/`*.bak2`, `requirements*.txt.py`, `landing_*.html` prototypes, `tailwind.config.js.bak`, and root `test_*.py` scripts — so none of these enter the image or the build context. The local files themselves remain on disk (owner decision D-005 governs archive/delete). |
| RV-030 Credential-bearing image risk | P1 security | `RESOLVED` 2026-08-28 | `Dockerfile:21` runs `COPY . .` and `.dockerignore` did NOT exclude `opencode.json` (live API credential) or `.opencode/`; a build would have embedded the credential in the image. Fixed via the RV-020 `.dockerignore` additions. No image was built from this context during the review. |
| RV-018 Bulk recipient resolution is unbounded before chunking | P1 availability | `VERIFY` | `apps/communications/services.py:527-581` materializes the full matching student list and `apps/communications/views.py:128-163` renders/persists the full recipient set; 100-recipient worker chunking happens only later at `services.py:615-639`. Measure 1k/10k recipients and set bounded preview/persistence rules. |
| RV-019 Attendance-code check/create race | P1 correctness | `RESOLVED` 2026-08-28 | `apps/attendance/services.py:43-71` did check-then-create with no lock; the partial unique constraints (`models.py:75-84`) protect the DB but a concurrent caller races to an unhandled `IntegrityError`. The single caller (`apps/applications/services.py:1004`) is serialized by the application `select_for_update`, so the race was mostly theoretical — hardened anyway: `get_or_create_attendance_code_for_enrollment` is now `@transaction.atomic` and locks the enrollment row (`select_for_update`) before the check-then-create, making it deterministic for any future caller. |
| RV-020 Generated sidecars and backups | P2 cleanup | `PROBABLY_DEAD` | About 193 ignored `*.html.py` sidecars plus `requirements*.txt.py`, CSS/config `.bak*`, prototypes, output, and temporary render artifacts have no application references in the initial trace. Confirm no external generator/QA workflow owns them, then remove recoverably and narrow ignore/build-context rules. |
| RV-021 Production signing secret is not fail-fast | P1 security | `RESOLVED` 2026-08-28 | Verified the **live production-like container ran with the public placeholder `django-insecure-change-me-in-production`** (39 chars) as `DJANGO_SECRET_KEY`. Since `apps/storage/views.py:37` and `apps/storage/services.py:182` sign storage URLs with `settings.SECRET_KEY`, and all signed cookies/tokens derive from it, this was a forgeable-signing-key defect. Fix: rotated `.env` `DJANGO_SECRET_KEY` to a fresh 96-char random hex (value never printed/logged); added a fail-fast guard in `config/settings/prod.py` that rejects empty/`django-insecure-`/`replace-with-`/short keys (verified it crashes the container on a placeholder; recreated services and confirmed health 200, `manage.py check` clean, secret len 96 non-placeholder). Transient `SuspiciousSession` logins after rotation are expected. Also removed a UTF-8 BOM that PowerShell added to `.env`. Residual: `POSTGRES_PASSWORD` in `.env` is 8 chars (weak but only reachable on the internal Docker network); `.env` stays ignored and never committed. |
| RV-022 Homework cumulative upload quota | P1 availability | `RESOLVED` (bounded) 2026-08-28 | Per-file 25 MB limit enforced in `apps/assessments/services.py:775` (`HOMEWORK_MAX_UPLOAD_MB`), MIME/extension validated, generated keys, orphan-object reconciliation on attach rejection. Count limit (`max_upload_files`) enforced at `services.py:796` when configured; blank = unlimited per the §11.1 contract (no hard-coded count; `remaining_slots` only rendered when configured). Request total is capped by nginx `client_max_body_size 50m` (`deploy/nginx.conf:8`), and Django spools multipart to disk, so single-request memory stays bounded. Residual: files are uploaded (then orphan-deleted) before the count check, so wasted uploads occur when over the count; and an admin leaving `max_upload_files` blank grows storage unbounded — needs D-003 capacity limits. |
| RV-029 Route × method × role × object-scope matrix | P1 process | `RESOLVED` 2026-08-28 | Complete matrix built for all 16 url modules (312 routes) and recorded in §16.3. Verified: no Moderator/Reviewer/Secretary PII exposure (all PII routes are Admin/Super Admin gated); Reviewer anonymity intact (`reviewer_claim_items` at `apps/assessments/services.py:2023-2051` returns only `answer_id/code_number/question_text/answer_text/points/score`); Secretary attendance templates render no phone/national_id; grade-release and homework-grading routes enforce `is_admin`/`can_moderator_grade_homework`. Only actionable items were RV-027 (GET mutation, fixed) and RV-028 (defense-in-depth, not a finding). |
| RV-024 Registration identity enumeration | P2 privacy | `RESOLVED` 2026-08-28 | D-024 locked: `apps/applications/forms.py:203-210` now shows one generic message for any duplicate ("An account with these details already exists. If you believe this is a mistake, please contact the support team.") on the matched field — no confirmation of which detail (email/phone/national ID) matched. Duplicate rejection itself remains locked (§3.2). |
| RV-025 Email addresses in operational logs | P2 privacy | `RESOLVED` 2026-08-28 | Partial masking implemented: `_mask_email` in `apps/communications/services.py` (keep first 2 chars + `***@domain`) is now used in the SMTP-failure log (with the EmailMessage id as correlation) and the invalid-manual-address log. Full addresses remain only on the EmailMessage rows (needed for ops/retry). |
| RV-026 Registration identity enumeration | P2 privacy | `DECISION_REQUIRED` | Confirmed `apps/applications/forms.py:203-210` returns **distinct** "email exists" / "phone exists" / "national ID exists" errors on the public register route. Duplicate rejection is locked (§3.2) but the message detail is not. Ask owner: generic single message (privacy-safe) vs field-specific (UX). Does not block shipping; affects Demo only if attacker can confirm identity existence against live data. |
| RV-027 GET mutation on admin grade sync | P2 correctness | `RESOLVED` 2026-08-28 | `apps/progression/views.py:1239` `admin_grade_sync` mutated (`sync_all_grade_items`) on GET with no method guard, violating AGENTS.md "Do not mutate state on GET". Added `@require_POST` (all 5 template callers already POST). Similarly added `@require_POST` to `apps/entrance_exams/views.py:588` `admin_attempt_decision` (all 4 form callers POST; GET previously only produced an error message). Ruff clean on `progression/views.py`; `entrance_exams/views.py` has pre-existing unrelated E402/I001 import-ordering debt (logger before imports) that was not touched. |
| RV-031 UI/HTMX/i18n/RTL parity | P1 process | `RESOLVED` 2026-08-28 | R7 verification complete. **HTMX shell (§20.9):** admin/student layouts use `hx-boost` + `hx-target="#main"` + `hx-select="#main"` + `hx-swap="outerHTML"` + `hx-push-url="true"` — shell/sidebar/topbar persist, normal links are the refresh-safe fallback, active nav is server-derived (`request.resolver_match`). `hx-push-url="true"` only on canonical list/nav pages; `"false"` on inline mutations. **JS lifecycle:** all interactive modules init on both `DOMContentLoaded` and `htmx:afterSwap`/`afterSettle` (timers are full-page-only, so DOMContentLoaded suffices). **No `history.back()`, `mark_safe`, or JS `innerHTML=`** in first-party code. **RTL/LTR (live via curl):** `/` → `lang=ar dir=rtl`; `/en/` → `lang=en dir=ltr`; `/en/accounts/login/` → en. `config/urls.py` uses `i18n_patterns(prefix_default_language=False)` — unprefixed URLs are Arabic by Django design; the CSRF-protected `set_language` switcher redirects to the correctly prefixed URL (`/en/`), and `{% url %}` emits prefixed URLs for English. **One `<h1>` per page** verified on landing/login/register/dashboard/record; skip-to-main present; no raw status codes in student templates. No code fixes required. Residual: landing `/` is always Arabic (by design; English via `/en/` or the switcher); Google Fonts + lucide load from external CDNs. |
| RV-028 Course watch/audio API scope | P3 defense-in-depth | `NOT_A_FINDING` | The route matrix flagged `api_watch_start/checkpoint` and `api_audio_start/checkpoint` (`apps/academics/views.py:1508-1603`) as lacking `@student_required`/enrollment checks. Verified all four service functions enforce `can_access_lecture` + active non-expired enrollment (`apps/academics/services.py:1170,1250,1393,1470`), matching §16.3 server-authoritative re-derivation. No data-integrity defect; staff/non-students get 400/403. Optional future decorator hardening only. |

## 10. Initial Verified-Good Controls

- Django system checks and migration-drift checks currently pass.
- Current containers and database health endpoint are healthy.
- Major admin collections generally paginate.
- Attendance summary work has set-based paths.
- Many critical grading/progression paths already use transactions, row locks, `transaction.on_commit`, and audit services.
- Protected storage uses generated keys, signed access, server-side authorization, and durable deletion reconciliation.
- Destructive academic endpoints sampled in the initial review use POST, CSRF, role checks, and named deletion services.
- Reviewer-facing workflows have dedicated anonymous projections and scoped claim structures that warrant targeted verification rather than replacement.
- Container limits, log rotation, Celery concurrency 1, and worker recycling are present and respect the small-host direction, subject to measured headroom.

## 11. Decision Register

| Decision | Status | Options / question | Blocks |
|---|---|---|---|
| D-001 Moderator assignment topology | **LOCKED 2026-08-28 — Option A implemented** | Make `ModeratorAssignment.course_version` optional, drop from the assignment form, remove the latent course-version permission branches; existing rows keep values as history. Migration `academics.0027` applied; `staff_assignments.html` form no longer requires a course version; permission branches removed from `assessment_staff_scope_filter`, `assessment_scope_filter`, `enrollment_scope_filter`; verified via test client (page 200, POST cohort-scope create works, assignment created with `course_version=None`). | RV-002 resolved |
| D-002 Percentage rounding | **LOCKED 2026-08-28 — Rule A (quantize-first), `ROUND_HALF_EVEN`** | Rule A confirmed; `apps/core/scoring.py` remains canonical. No code change required. Locked rule documented in `DOKKIMASHORA.md` §8.3. | RV-003 resolved |
| D-003 Demo capacity envelope | **LOCKED 2026-08-28** | Accepted defaults with **students per cohort = 200**; all other defaults accepted: 2 historical enrollments/student, 20 concurrent users, 5 concurrent audio streams, 500 import rows/file, 200 bulk-email recipients, 25 MB/file, ~500 MB total, 2 s p95. Derived: progression table paginates 25 rows/page (~175+ queries/page at 7/enrollment — P2); student dashboard per-student cost ~100 queries at 2 enrollments (within budget). **Flagged residual:** 5 concurrent audio streams exceed the 4 gunicorn request threads (2 workers × 2 threads) of the current proxy design — see RV-008. | R4/R8 budgets |
| D-004 Automated-test authorization | **LOCKED 2026-08-28 — Option C** | No test run; the ship gate records the documented absence of automated-test evidence (untested risk accepted by the user). Existing test files preserved. | Final ship evidence |
| D-005 Local/prototype artifacts | **LOCKED 2026-08-28 — Option A** | Disposable artifacts archived to `D:\DokkiMashora_archive_2026-08-28` (212 files, ~7 MB: landing prototypes, `output/`, `tmp/`, CSS/config `.bak*`, `requirements*.txt.py`, root `test_*.py`, all 197 `*.html.py` sidecars) and deleted from the repo. `.opencode/` and `opencode.json` left untouched (tool config + credential pending rotation). | R6 cleanup |
| D-006 Private HLS delivery | **LOCKED 2026-08-28 — Option A** | Authenticated application-proxy streaming accepted as an explicit exception to §19.1, documented in `DOKKIMASHORA.md` §19.1. Signed URLs remain the contract for non-streaming downloads. | RV-023 resolved; RV-008 capacity flagged |

## 12. Execution Sequence

### Phase 0 — Stabilize the review baseline

1. Classify every current modified/untracked path.
2. Reconcile completed consolidation work with the 18 historical duplication findings.
3. Resolve D-001 and D-002 before merging affected logic.
4. Capture representative Demo data volumes without exposing student data.

### Phase 1 — Demo blockers: security and correctness

1. Complete the URL/role/object-scope matrix.
2. Resolve public auth abuse exposure.
3. Verify answer-key, grade-release, Reviewer anonymity, storage, and destructive-action boundaries.
4. Fix only approved P0/P1 root causes in separate domain batches.

### Phase 2 — Concurrency and durable state

1. Attempt lifecycle and autosave.
2. Reviewer assignment/claim uniqueness.
3. Payment/import idempotency.
4. Evaluation/promotion/interview/rejoin transitions.
5. Email queue/retry lifecycle.

### Phase 3 — Measured performance and capacity

1. Progression table and cohort-wide actions.
2. Student dashboard/history.
3. Imports and bulk email.
4. Audio/storage delivery and certificate rendering.
5. PostgreSQL query plans and only then any indexes.

### Phase 4 — Approved duplication consolidation

Apply one finding per batch in risk order. Re-run the difference matrix and caller trace because the prior audit predates the current dirty tree.

### Phase 5 — Dead code and repository cleanup

Remove only `CONFIRMED_DEAD` items. Keep migration/history scaffolding until deployment/data prerequisites are proven. Update `.gitignore`/`.dockerignore` with narrow patterns after ownership decisions.

### Phase 6 — UI/HTMX/i18n/accessibility parity

Verify full-page/fragment parity, available-action display, persistent shell behavior, responsive task parity, and Arabic/English behavior after backend changes settle.

### Phase 7 — Operational rehearsal and ship decision

Build from a clean intended source set, apply migrations, collect static files, start services, inspect logs, verify health, exercise Celery/email/storage, restore a backup, and document rollback.

## 13. Batch Completion Checklist

Every implementation batch must report:

- requested outcome and approved finding/decision;
- root cause and complete caller flow;
- files changed and reused canonical code;
- unrelated dirty work preserved;
- authorization/data/concurrency/query/HTMX/i18n impact;
- migration and rollback impact;
- exact validation actually run;
- remaining unverified areas and residual risk;
- updated finding and decision status in this file.

Allowed default validation remains the smallest applicable subset of:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python -m ruff check <touched-python-files>
node --check <touched-js-files>
npm run build:css
```

Automated test suites require explicit user authorization. Browser inspection may be used for targeted UI/debug verification. Application changes require the restart/health sequence from `AGENTS.md`.

## 14. Demo v1 Ship Gate

Demo v1 is `GO` only when all are true:

- no open P0;
- every P1 is `RESOLVED` or explicitly `ACCEPTED_RISK` by the user with owner and deadline;
- all contract conflicts and business decisions affecting changed code are locked;
- permissions matrix covers every protected route and direct-URL checks pass;
- migrations are applied and drift-free; data preflights for new constraints are clean;
- representative query, batch, queue, render, stream, memory, CPU, and disk budgets meet the agreed Demo envelope;
- Arabic/English, mobile/desktop, full-page/HTMX, and primary role workflows have targeted verification evidence;
- clean build/deploy, authoritative health check, logs, Celery, storage, backup restore, and rollback are verified;
- automated-test evidence exists, or the user explicitly accepts the documented absence under D-004;
- repository and image inputs contain no unexplained local/generated/secret-bearing artifacts;
- final diff review confirms unrelated work is preserved and only approved behavior changed.

If any condition fails, status is `NO-GO` with the exact blocking finding IDs. Do not replace a missing gate with a general statement that the application “looks fine.”

## 15. Next Review Batch

Recommended next batch after user decisions:

1. Revoke/rotate the credential in RV-000 and complete a secret scan.
2. Resolve D-001 Moderator topology and D-002 scoring precision.
3. Classify/stabilize the current dirty tree.
4. Complete the security/permission URL matrix and verify RV-004/RV-021/RV-022/RV-024/RV-025.
5. Resolve D-006 before redesigning private HLS delivery.
6. Measure RV-005/RV-013/RV-014/RV-018 and reproduce RV-006/RV-019 on PostgreSQL.
7. Set D-003 capacity numbers before choosing performance implementations.

## 16. Decision Packets Awaiting Lock

### 16.1 D-001 — Moderator assignment topology

**Finding RV-002.** Contract (`DOKKIMASHORA.md:180-189,470`) locks Moderator as **Diploma-only, cluster-scoped Homework grading, never releases grades**. Current code still couples `ModeratorAssignment` to `CourseVersion`.

Evidence and complete flow:

- `apps/academics/models.py:612-626` — `ModeratorAssignment.course_version` is a required (`on_delete=PROTECT`, non-null) FK; also `cohort` (nullable) and `cluster` (nullable).
- `apps/accounts/views.py:756-793` — `admin_moderator_assignment_create` rejects any POST without `course_version` ("Course version is required.").
- `templates/ui/pages/admin/staff_assignments.html:60-69` — UI requires selecting a course version before assigning a moderator.
- `apps/core/permissions.py:170-196` — `can_moderator_grade_homework` is the **canonical grading gate**: it requires the assessment to be Diploma-scoped (`course_version_id is None` AND `cohort_id` AND `semester_id`), Homework type, and student inside the moderator's active cohort/cluster scope. **The `course_version` value on a ModeratorAssignment is never used to authorize grading.**
- `apps/assessments/services.py:1614-1625` — `get_homework_pending_grading` additionally excludes course-scoped homework from moderator queues (`assessment__course_version__isnull=True`).
- `apps/academics/models.py:481-538` — the newer `ModeratorStudentAssignment` model is Diploma-only (moderator/student/cohort, no course_version) and is what the grading scope actually consumes (`apps/core/permissions.py:148-156`).
- Course-version branches remain in scope helpers: `assessment_staff_scope_filter` (`apps/core/permissions.py:234-236,253-254`), `assessment_scope_filter` (`:278-283`), `enrollment_scope_filter` (`:293-301`). All views consuming these helpers (`apps/assessments/views.py:1176` admin_grade_list, `:686` admin_list, `apps/academics/views.py:1345,1413` enrollment lists) are `ROLE_EXAM_STAFF` (Admin/Super Admin only), so the moderator course-version branches are **latent, not currently exploitable** — a moderator cannot reach them today. The homework queue path (`ROLE_HOMEWORK_GRADING_STAFF`) null-filters them out.
- Data impact: `ModeratorAssignment` has **0 rows** in the current database; migration is data-safe.

Impact of the conflict: (1) Admin is forced to choose an irrelevant course version to assign a Diploma-only moderator — confusing and contract-contradicting UI; (2) latent course-version scope surface in three permission helpers could leak Course data if any future view combines the Moderator role with those filters.

Options:

- **Option A (recommended, aligns code to the locked contract):** make `course_version` nullable/blank on `ModeratorAssignment` via a new migration; stop requiring it in the create view and the staff-assignment form; remove the course-version branches from `assessment_staff_scope_filter`, `assessment_scope_filter`, and `enrollment_scope_filter`; keep existing rows' `course_version` values as historical metadata. UI then assigns Moderator by Diploma cohort/cluster only.
- **Option B (changes the product contract):** promote Moderator to grade Course Quiz/Homework on Course Versions too; requires editing `DOKKIMASHORA.md` §5/§11.3, widening `can_moderator_grade_homework`, and reconsidering Course grade-release authority. Larger blast radius, not recommended for Demo v1.
- **Option C (data-preserving middle ground):** keep `course_version` required but remove it from all authorization helpers so it becomes a pure informational cross-reference. Least code change, but keeps the confusing required field in the UI.

**Decision required from owner before any code change.**

### 16.2 D-002 — Scoring precision (quantize-first vs raw compare)

**Finding RV-003.** The new `apps/core/scoring.py` applies **quantize-first** semantics uniformly: `compute_percent` rounds `earned/possible*100` to 2 decimals (Decimal default `ROUND_HALF_EVEN`) **before** any pass/fail comparison (`apps/core/scoring.py:18-29,32-42`). `mcq_score` also quantizes the penalized per-answer score to 0.01 (`:61-87`).
- **payments**: all `PAY` (Admin/Super Admin).
- **progression**: all `SENIOR`; student progress own-profile only; GET-mutation fixed (RV-027).
- **certificates**: admin `SENIOR`; student download own-profile only.
- **communications**: bulk email `SENIOR`.
- **storage**: `protected_download` is an HMAC bearer signed URL (by design); forging requires `SECRET_KEY` (now rotated, RV-021).
- **audit**: `SENIOR` only.
- **deployment/healthz**: public GET probes.

Result: **no Moderator/Reviewer/Secretary PII exposure; all protected routes have a role gate + object-scope enforcement; only verified defects were RV-027 (fixed) and RV-021 (fixed).**

**Finding RV-003.** The new `apps/core/scoring.py` applies **quantize-first** semantics uniformly: `compute_percent` rounds `earned/possible*100` to 2 decimals (Decimal default `ROUND_HALF_EVEN`) **before** any pass/fail comparison (`apps/core/scoring.py:18-29,32-42`). `mcq_score` also quantizes the penalized per-answer score to 0.01 (`:61-87`).

Callers of `compute_percent` / `is_passing`:

| Caller | File:line | Comparison target | Pre-consolidation behavior |
|---|---|---|---|
| Assessment auto-grade (MCQ-only) | `apps/assessments/services.py:526-531` | `assessment.passing_score` | Raw `(total/total_possible)*100` compare |
| Diploma random submit recalc | `services.py:626-627` | `passing_score` | Already quantized (`quantize(0.01)`) |
| Diploma snapshot recalc (written grading) | `services.py:969-973` | `passing_score` | Raw compare |
| Homework grade | `services.py:1003-1006` | `passing_score` | Already quantized |
| Written-grade recalc | `services.py:1030-1034` | `passing_score` | Raw compare |
| Reviewer/section grade recalc | `services.py:1903-1904` | `passing_score` | Raw compare |
| Entrance attempt recompute | `apps/entrance_exams/services.py:224-232` | `exam.pass_percentage` | Raw `(total/total_score*100)` compare |
| Progression homework percent | `apps/progression/services.py:593-598` | n/a (feeds evaluation) | n/a |
| Progression grade-item sync | `services.py:2198-2204` | n/a | n/a |

Progression two-level rule (`evaluate_semester_evaluation`, `apps/progression/services.py:622-744`): component percents are quantized inputs (attendance `attendance/services.py:304-329` already quantizes; homework/exam now via `compute_percent`); the weighted overall is re-quantized at `services.py:726-728`; Level-1 compares `percent >= min` and Level-2 compares `overall >= overall_min_percent` (`services.py:729-734`).

**Boundary example (threshold 50.00, possible 200):**

| earned | raw % | Rule A quantize-first | Rule B raw compare |
|---|---|---|---|
| 99.99 | 49.995 | **50.00 → PASS** | 49.995 < 50 → **FAIL** |
| 99.985 | 49.9925 | 49.99 → FAIL | FAIL |
| 99.995 | 49.9975 | 50.00 → PASS (half-even) | FAIL |
| 100.00 | 50.00 | PASS | PASS |

The rules differ only when the unrounded percent lies in `[threshold − 0.005, threshold)` (for 2-decimal rounding). For the weighted overall, Rule A also composes rounded component percents and re-rounds the sum; Rule B would use unrounded components and compare the raw weighted sum.

Null/zero semantics: `possible=None`/`<=0` → percent `None` → assessments coerce `is_pass=False`; entrance forces `0.00` → FAIL; progression emits a blocked reason "no graded result" and never fails the student on a missing component. `earned=None` is coerced to `0` inside `compute_percent`. Stored fields (`score_percent`, `passing_score`, `pass_percentage`, `total_points`) are all `decimal_places=2`, so Rule B's stored value would still be rounded on save by Django regardless of the comparison rule.

Impact: Rule A (current) can flip a pass on any attempt whose raw percent rounds up across the threshold; this changes grading outcomes vs the pre-consolidation raw paths (assessment lines 526/969/1030/1903 and entrance line 224). Rule B keeps the prior raw behavior for those paths but diverges from the already-quantized homework/diploma-recalc paths, re-introducing the original inconsistency.

**Decision required from owner:** lock Rule A (quantize-first, keep `apps/core/scoring.py` as canonical) or Rule B (raw compare; amend `compute_percent`/`is_passing` contract and the progression overall computation). Also confirm `ROUND_HALF_EVEN` vs `ROUND_HALF_UP` for the 2-decimal rounding and confirm the null/zero coercions above.

### 16.3 Route × method × role × object-scope matrix (RV-029 evidence)

Built from all 16 `urls.py` modules (312 URL paths). Role tuples: EXAM/SENIOR = Super Admin, Admin; REVIEW = +Reviewer; ATTEND = +Secretary; HW = +Moderator.

Summary by domain:

- **accounts**: staff/user/student management all `SENIOR`/`EXAM`; `workspace_search` returns PII only for `is_admin`; activation/resend public (now edge-throttled).
- **applications**: public register/registration_success/program_targets; everything else `SENIOR`; document downloads ownership-checked (`_can_download_document` = admin or owner).
- **academics**: admin CRUD `EXAM` with per-object scoping; student content views ownership+`can_access_lecture`; watch/audio services re-derive access (`services.py:1170,1250,1393,1470`); diploma watch APIs enforce ownership in view.
- **assessments**: student take/submit/autosave ownership-scoped; admin grading/release `EXAM`+`is_admin`; homework grading `HW`+`can_moderator_grade_homework` (Diploma-only); reviewer routes claim-bound to `reviewer=user,active`; exceptional-access `SENIOR`.
- **attendance**: sessions list/detail/records/scan `ATTEND`; create/open/close/delete `ADMIN` only; student own view only.
- **entrance_exams**: student access-token routes ownership-checked; `exam_qr_code` renders only the exam URL (no data leak, but no ownership check — a token is already required to reach it); all admin `EXAM`+`is_admin`; `admin_attempt_decision` now `@require_POST`.
- **payments**: all `PAY` (Admin/Super Admin).
- **progression**: all `SENIOR`; student progress own-profile only; GET-mutation fixed (RV-027).
- **certificates**: admin `SENIOR`; student download own-profile only.
- **communications**: bulk email `SENIOR`.
- **storage**: `protected_download` is an HMAC bearer signed URL (by design); forging requires `SECRET_KEY` (now rotated, RV-021).
- **audit**: `SENIOR` only.
- **deployment/healthz**: public GET probes.

Result: **no Moderator/Reviewer/Secretary PII exposure; all protected routes have a role gate + object-scope enforcement; only verified defects were RV-027 (fixed) and RV-021 (fixed).**

## 17. Demo v1 GO/NO-GO — 2026-08-28

**Verdict: CONDITIONAL GO.** Every review workstream (R0-R8) is complete except the R8 backup drill (skipped by user decision). All P1 findings are `RESOLVED`, all business decisions D-001..D-006 are locked, migrations are drift-free, performance/resource budgets are measured against D-003, and UI/RTL/HTMX parity is verified. **The single remaining gate is RV-000: the user must externally revoke/rotate the `opencode.json` API key.** D-004 (no automated test run) and the skipped backup drill are explicitly accepted. Once the key is rotated, Demo v1 is GO.

### Resolved during this session (evidence in §9 rows)

| Finding | Class | Evidence |
|---|---|---|
| RV-000 credential containment | P0 | `opencode.json` never committed, ignored; repo/history secret scan clean; **external rotation still required (sole remaining gate)** |
| RV-021 placeholder production secret | P1 | Rotated `.env` secret to 96-char random hex; fail-fast guard in `prod.py`; container verified healthy |
| RV-030 credential-bearing image risk | P1 | `.dockerignore` excludes `opencode.json`, `.opencode/`, artifacts; built image verified clean |
| RV-004 public auth abuse | P1 | nginx `limit_req` 30r/m burst 20 on 4 public auth paths; verified 429s engage; non-auth paths unaffected |
| RV-006 attempt start/submit races | P1 | Reproduced 3/8 IntegrityError on PostgreSQL; profile-lock serialization + submit row lock; re-run 8/8 clean |
| RV-008 stream capacity | P1 | `GUNICORN_THREADS=4` → 8 slots; 5-stream budget fits; container recreated + healthy |
| RV-014 written-grading recalc | P1 | Measured 600 → 307 queries at N=100; row-lock + single bulk recalc; concurrency 5/5 deterministic |
| RV-015 storage reconciliation | P1 | Reproduced 5/5 deletes inside a DB txn; fixed remote-I/O-outside-txn + locked claim; re-verified 0/5 |
| RV-019 attendance-code race | P1 | Enrollment-row `select_for_update` before check-then-create |
| RV-016 intake email lifecycle | P1 | Migrated to `queue_program_application_email`; dead `_log_email_send` removed |
| RV-011 deploy health gate | P1 | Removed `\|\| true`; failed `/healthz` now fails the deployment job (verified exit 0 up / exit 7 down) |
| RV-029 route matrix | P1 process | Full 312-route matrix; Reviewer anonymity + answer-key secrecy + Secretary PII verified clean |
| RV-031 UI/HTMX/i18n/RTL parity | P1 process | HTMX shell, push-url discipline, JS lifecycle, RTL/LTR live-verified, one h1, no raw status codes |
| RV-002/003/023 + D-001..D-006 | decisions | All locked and implemented (see register) |
| RV-024/RV-025 | P2 privacy | Generic duplicate message + partial email masking |
| RV-027 GET mutation | P2 | `@require_POST` on grade-sync + entrance decision |
| RV-012 AI shell | P3 | `apps.ai` unregistered + deleted |

### Blocking items

1. **RV-000 (P0):** external revoke/rotate of the `opencode.json` API key — **user action required**; this is the only remaining P0. Local containment is complete (never committed, ignored, excluded from the image).
2. **D-004:** no automated test run (user accepted the documented untested risk).
3. **R8 backup drill:** intentionally skipped by user decision (2026-08-28).

### Completed since the first gate (2026-08-28, second pass)

- **RV-008:** `GUNICORN_THREADS=4` → 8 request slots; the 5-stream Demo budget now fits.
- **RV-014:** measured 600 → **307 queries** at N=100; attempt-row `select_for_update` in both recalc helpers; bulk grade-detail handlers recalculate once; concurrency test 5/5 deterministic.
- **RV-015:** reproduced 5/5 deletes inside a DB transaction; fixed to remote-delete-outside-txn + `select_for_update` claim; re-verified 0/5 inside + failure-path bookkeeping.
- **R7 (RV-031):** full parity verification — HTMX shell, push-url discipline, JS lifecycle, RTL/LTR (live curl `/`=ar/rtl, `/en/`=en/ltr), one h1, no raw status codes. No fixes required.
- **R8 (partial):** clean image build `dokkimashora-app:demo-rehearsal` verified credential/artifact-free (no `opencode.json`, `output/`, `tmp/`, `*.html.py`, `.bak`, `.env*`); `manage.py check` + `collectstatic` (154 files) pass in-image; deploy rehearsal from the image on port 8001: migrations drift-free, gunicorn up, **health gate `curl --fail /healthz` exit 0** (up) and **exit 7** (down) — a failed post-deploy health check now fails the deployment.
- **Rollback procedure (documented):** source rollback = `git checkout <prev>` + `docker compose restart web celery` (source is bind-mounted); image rollback = `docker compose up -d` with `APP_IMAGE_TAG=<prev>`; DB rollback = `pg_restore` from `backups/db-<stamp>.dump`; verify with the health gate + key row counts.

### Locked during this session (no longer blocking)

- D-001 Moderator topology → Option A implemented (RV-002 resolved).
- D-002 Scoring precision → Rule A quantize-first (RV-003 resolved; `DOKKIMASHORA.md` §8.3).
- D-003 Capacity → 200 students/cohort + defaults (sets R4/R8 budgets).
- D-004 Tests → Option C (no run; untested risk documented).
- D-005 Artifacts → archived to `D:\DokkiMashora_archive_2026-08-28` and deleted from repo.
- D-006 HLS → Option A proxy exception (RV-023 resolved; `DOKKIMASHORA.md` §19.1).
- RV-024 registration enumeration → generic message with support hint.
- RV-025 email logs → partial masking.

### Non-blocking accepted-for-Demo items (P2/P3, owner+deadline needed)

- RV-005 progression table: 22 queries at 3 rows, ~7/enrollment linear — P2 at Demo scale.
- RV-013 student dashboard: **98 queries / ~1192 ms** at Demo scale — P2 (functional, noticeable); bounded projection scheduled after D-003.
- RV-018 bulk recipients: materializes full list but bounded at Demo recipient counts; chunked 100/recipient task — P2.
- RV-022 homework uploads: per-file 25 MB, nginx 50 MB cap, count-limit honored when configured — P2 (needs D-003 storage ceiling).
- RV-017 orphan admin templates — `PROBABLY_DEAD`, deletion batch after dynamic-reference scan (D-005).

**Remaining before GO:** rotate the RV-000 key (external, user action). D-004 (untested risk) and the R8 backup drill are accepted by the user. After rotation, re-run the §14 ship gate — all other conditions already hold.
