# DokkiMashora — Agent Engineering Contract

**Status:** Authoritative engineering contract  
**Applies to:** Every human or LLM contributor working in this repository  
**Stack:** Python 3.11, Django, Django Templates, PostgreSQL, Tailwind CSS, HTMX, Alpine.js, JavaScript, Celery/Redis, Nginx  
**Infrastructure ceiling:** 2 vCPU / 4 GB RAM / 30 GB SSD

## 1. Authority

Use this order:

1. The user's current explicit instruction.
2. This `AGENTS.md`.
3. `DOKKIMASHORA.md` for locked product/business/architecture decisions and the compact codebase map.
4. Current executable code, migrations, settings, templates, and current Git diff as implementation evidence.

Other Markdown files are historical or task-specific unless the user explicitly promotes one.

### Business conflict rule

If current code conflicts with a **LOCKED** business rule in `DOKKIMASHORA.md`:

1. Trace the complete affected flow.
2. Determine whether the conflict is stale documentation, incomplete implementation, regression, migration/schema mismatch, or a genuine architecture conflict.
3. Explain the conflict and impact.
4. **Ask the user to lock the decision.**
5. Do not silently change the business behavior before that decision is locked.

Do not choose documentation over production behavior—or production behavior over a locked decision—without investigation.

## 2. Required Start Before Editing

For every implementation, fix, refactor, or debugging task:

1. Read this file.
2. Read the relevant sections of `DOKKIMASHORA.md`, including its Codebase Index.
3. Read the user's requested outcome and boundaries.
4. Run `git status --short`.
5. Inspect the current diff of every relevant dirty file before editing it.
6. Preserve unrelated and incomplete user work.
7. Use repository search first (`rg`, `rg --files`) rather than random file browsing.
8. Trace the complete real flow before selecting a solution:
   - URL
   - view
   - form / request parser
   - permission / policy
   - selector / queryset
   - service / workflow
   - model / constraint
   - migration
   - task if relevant
   - template
   - HTMX fragment
   - Alpine / JavaScript
   - every caller affected
9. Search for at least one existing analogous feature/page before creating new code.
10. State a small change map: root cause, files to change, code to reuse, affected callers, security/data risks, performance risks, and allowed validation.

Do not start with implementation because a symptom "looks obvious."

## 3. Implementation Philosophy

Use this order and stop at the first option that fully solves the demonstrated requirement:

1. Do nothing if the behavior already exists.
2. Reuse an existing path.
3. Extend an existing cohesive path.
4. Share the narrow common part when at least two real callers have the same semantic behavior.
5. Add the minimum new code necessary.

### No speculative abstraction

Do not create abstractions for possible future reuse.

Forbidden without demonstrated need:

- generic service frameworks;
- one-use interfaces;
- factories with one concrete implementation;
- wrappers that only rename a call;
- configuration for values that do not vary;
- base classes that exist only to remove a handful of obvious lines;
- shared components with many boolean flags that force unrelated workflows together;
- "future-proof" repositories or utility layers.

A one-use helper may still be appropriate when it establishes one canonical security or business invariant.

### Reuse requires semantic coherence

Before sharing code, ask:

> Are these two callers actually performing the same business operation?

If **yes**, share the narrow invariant or presentation.

If they only look visually similar but have different business rules, share presentation only where safe and keep domain workflows separate.

Never merge Entrance Exam and normal Assessment workflows merely because both contain questions, attempts, timers, or grades.

## 4. Correct Placement of Logic

Prefer the established codebase pattern. Do not create a new architectural layer solely to satisfy this list.

### Models / database
Own durable data shape, explicit relationships, choices/enums, database constraints, durable uniqueness, and invariants that must survive every caller.

### Forms
Own request-data parsing, field validation, cross-field validation, and user-friendly validation errors. Use explicit fields.

### Services
Own workflow/state transitions, multi-row mutations, transactional business operations, immutable audit side effects, and external-effect orchestration.

### Selectors / query helpers
Own reusable non-trivial reads and annotated projections. Do not create a selector for one trivial `.filter()`.

### Permission/policy helpers
Own reusable authorization decisions and object scope.

### Views
Views authenticate, authorize, parse, call domain code, and choose a response. Keep scoring, progression, enrollment, payment, grading, and access logic out of views.

### Templates
Templates render prepared state. They may receive `can_*`, `available_actions`, readiness, and display labels, but must never be the real authorization boundary.

### Celery tasks
Use Celery for genuinely asynchronous or slow work, not ordinary CRUD. Tasks call the same named services as synchronous paths where possible.

## 5. FBV vs CBV

Do not preserve function-based views merely because they already exist.

Use a Class-Based View immediately when it materially reduces repeated and cohesive Django orchestration, such as repeated GET/POST form handling, list pagination/filter handling, permission/context preparation, or related CRUD views sharing the same semantic behavior.

Do not convert an isolated workflow action to a CBV just for architectural fashion.

Choose the option with the clearest and smallest cohesive implementation.

## 6. Django Rules

- Use the configured custom user model.
- Relations use `settings.AUTH_USER_MODEL`; runtime access uses `get_user_model()`.
- Use timezone-aware values and `django.utils.timezone`; business timezone is `Africa/Cairo`; `USE_TZ` stays enabled.
- Arabic is default; English remains supported.
- User-facing Python/template text uses Django i18n.
- Display choice/status values through translated labels, never raw stored codes.
- Do not mutate state on GET.
- State-changing requests use proper non-GET methods and CSRF.
- Backend permission, ownership, state, schedule, enrollment, and transition validation are mandatory.
- Never trust hidden fields, HTMX headers, query parameters, IDs, browser state, or disabled controls.
- Prefer named workflow services for state changes.
- Keep remote calls, email, storage upload, media processing, PDF rendering, and slow I/O outside DB transactions.
- Use `transaction.on_commit()` when an external effect must happen only after commit.
- Do not hide primary workflows in model `save()` or signals.
- Never edit an already-applied migration to change current behavior. Add a new migration.

## 7. PostgreSQL, Security, and Performance

PostgreSQL is authoritative where DB semantics matter.

### Query correctness
- Prefer Django ORM.
- Raw SQL requires a concrete ORM limitation or measured need.
- Parameterize raw values.
- Allowlist dynamic ordering/filter field names.
- Put ownership/cohort/enrollment/assignment scope in the queryset where practical.
- Friendly validation does not replace DB constraints.
- Handle race-time `IntegrityError` where contention is possible.

### Query performance
- N+1 queries are defects.
- No queries inside loops when set-based alternatives exist.
- Use `select_related()` / `prefetch_related()` appropriately.
- Fetch only relations actually used.
- Use `exists()`, `count()`, annotations, and aggregates intentionally.
- Keep querysets lazy until evaluation is intentional.
- Paginate user-facing collections.
- Batch maintenance operations with bounded chunks.
- Do not load unbounded rows/files/task payloads into memory.
- Add indexes only for demonstrated patterns.
- Do not add speculative indexes.
- Use PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` only on safe representative reads when needed.

### Concurrency
- Keep `transaction.atomic()` around the smallest complete invariant.
- Use `select_for_update()` for contended transitions when appropriate.
- Prefer `F()` expressions, conditional updates, constraints, and idempotency.
- Retriable Celery tasks must be idempotent or durably protected.

### Infrastructure ceiling
The complete deployment must fit 2 vCPU / 4 GB RAM / 30 GB SSD, including Django/Gunicorn, PostgreSQL, Nginx, Redis/Celery, and backup work.

Do not increase workers, pools, task concurrency, cache size, or DB memory settings without considering aggregate worst-case use.

### Security
- Least privilege.
- Object authorization at every protected entry point.
- UI hiding is not security.
- Never commit/log credentials, tokens, session values, secret URLs, or unnecessary student data.
- Keep template auto-escaping.
- Avoid `mark_safe` without proven trust/sanitization.
- Uploads require server-side authorization, size/type/extension validation, generated storage names, and safe destinations.
- External calls require timeouts and bounded payloads.
- Destructive actions require target resolution, authorization, POST/CSRF, confirmation, domain handling, and immutable audit where required.

Authentication, permissions, payments, certificates, uploads, destructive changes, migrations, concurrency, and raw SQL require a focused correctness/security review.

## 8. HTMX Rules

- A normal page has a canonical refresh-safe full-page URL.
- Normal non-HTMX GET returns a full page.
- HTMX may return a fragment when intentionally supported.
- Full-page and HTMX paths expose equivalent business state/actions.
- A fragment must not become a stable browser URL unless refresh-safe.
- Use `hx-push-url` deliberately, never globally.
- Inline mutation normally uses `hx-push-url="false"` unless navigation is intentional.
- Redirect to canonical pages when appropriate.
- Every fragment has a predictable target/frame contract.
- HTMX validation preserves entered values and renders errors.
- Security/authorization are identical for HTMX and normal requests.

## 9. Alpine.js and JavaScript Rules

Alpine owns small local interaction state such as disclosure, tabs, menus, step/wizard visibility, local modal/drawer state, and presentation-only toggles.

Backend owns authorization, readiness, business state, enrollment state, grading state, and available transitions.

Do not recreate backend state machines in Alpine.

Avoid global Alpine stores unless independent components genuinely need shared client state.

Reusable JS belongs in modules rather than inline handlers.

Code used by HTMX-inserted content must initialize on appropriate HTMX lifecycle events, not only `DOMContentLoaded`.

Do not place untrusted text into `innerHTML`.

## 10. UI Implementation Rules

The final detailed UI/UX architecture will be locked in `DOKKIMASHORA.md`.

Already locked:

- preserve the brand;
- no dark-mode requirement;
- Arabic RTL and English LTR are first-class;
- admin and student must both be fully responsive on mobile and desktop;
- breakpoints follow the approved design;
- one design system serves admin and student;
- same semantic component uses the same visual language across roles;
- inputs, selects, textareas, checkboxes, buttons, statuses, spacing, and sizing stay consistent;
- expose only actions valid in the current business state;
- do not flood pages with every possible transition.

A UI/UX proposal may request a backend change if it materially simplifies UX. Do not implement the changed business behavior until the user locks it.

## 11. Debugging and Fixing

1. Understand/reproduce the symptom from available evidence.
2. Trace the complete flow.
3. Identify root cause.
4. Search all callers and analogous flows.
5. Locate the correct owner: model/form/service/selector/permission/view/template/HTMX/JS.
6. Fix the narrowest shared root.
7. Inspect every affected caller.
8. Re-check security/data boundaries.
9. Perform allowed validation.
10. Review the final diff.

Do not apply a template-only workaround before checking whether backend/context/service/permission prepared the wrong state.

Do not fix backend behavior without checking templates, HTMX fragments, and JavaScript callers.

## 12. Delegation

Use delegation for separate features/workstreams that can progress in parallel with shared context.

Each delegated agent receives:

- `AGENTS.md`;
- relevant `DOKKIMASHORA.md` sections;
- exact objective and acceptance conditions;
- owned files/domain;
- forbidden overlap;
- relevant current diff/context;
- security/data/performance constraints;
- allowed validation;
- required handoff format.

Parallel agents must not own overlapping files or tightly coupled transitions.

Sub-agents do not redefine business decisions. The main agent owns integration and final correctness.

## 13. Testing and Validation Policy

### Automated tests
The repository no longer ships an automated test suite. Do **not** recreate, restore, or introduce unit, integration, Playwright, browser-E2E, or similar test suites unless the user explicitly requests testing.

### Allowed sanity validation
Use the smallest applicable set:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python -m ruff check <touched-python-files>
python -m ruff format --check <touched-python-files>
node --check <touched-js-files>
npm run build:css
```

Browser interaction may be used when necessary to reproduce/debug/inspect UI behavior. It is not a mandatory completion gate unless the user asks.

Never claim validation that was not actually performed.

## 14. Restart After Application Changes

After modifying Python, templates, static assets, settings, migrations, forms, or application behavior:

```powershell
docker compose -p dokkimashora -f compose.application.yml -f compose.infrastructure.yml restart web celery-submit celery-autosave beat nginx
```

Then verify:

```powershell
docker ps --filter name=dokkimashora --format "{{.Names}} {{.Status}}"
docker logs dokkimashora-web-1 2>&1 | Select-String -Pattern "migrate|Migration|staticfiles|collectstatic|Starting gunicorn"
Invoke-WebRequest -Uri http://127.0.0.1/healthz -UseBasicParsing -TimeoutSec 15
```

Do not restart PostgreSQL, Redis, or Portainer for ordinary source changes.

## 15. Completion Review

Before declaring completion:

1. Re-read the requested outcome.
2. Inspect final diff.
3. Confirm unrelated dirty work was preserved.
4. Re-trace changed callers.
5. Compare analogous implementation paths.
6. Check business contract alignment.
7. Check authorization/ownership/state transitions.
8. Check query/transaction behavior.
9. Check HTMX/full-page parity where relevant.
10. Check Arabic/English and responsive implications for UI work.
11. Run applicable validation.
12. Report outcome, changed files, reuse, risks, validation, and unverified areas.

Do not create a chronological documentation entry for every task. Update `DOKKIMASHORA.md` only when a durable business rule, architecture decision, codebase index location, or implementation status changes.
