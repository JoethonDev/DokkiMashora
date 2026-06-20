# LLM Implementation Guardrails

Use this prompt/instruction file for any LLM or contributor implementing fixes or new features in this codebase. These rules are mandatory.

## Prime Directive

Do not optimize for "feature exists". Optimize for "the business rule cannot be bypassed accidentally". A workflow is not complete until the model, service, form, view, template, permissions, tests, and documentation agree.

## Work Sequence For Every Task

1. Read the related docs and current code before editing.
2. Identify the domain owner:
   - data shape and durable constraints: model/migration
   - business rule: service, policy, selector, validator, or model clean method
   - request orchestration: view
   - display only: template
   - reusable UI: shared partial
3. Write or update tests for the intended rule.
4. Implement the smallest scoped change.
5. Run checks:
   - `python manage.py check`
   - `python manage.py makemigrations --check --dry-run`
   - `python -m pytest -q`
   - `python -m ruff check .`
6. Update docs when behavior or architecture changes.

## Validation Rules

Validation must be layered. Never rely on only the template, only JavaScript, or only the view.

- Use database constraints for durable uniqueness and relationship rules where possible.
- Use model `clean()` or dedicated validators for semantic rules that belong to data.
- Use services/policies for workflow rules that depend on state, time, role, or multiple rows.
- Use forms for user-friendly field and non-field errors.
- Use templates only to display errors and guide the user.
- Repeat critical validation inside transactions so non-form callers cannot bypass it.

Required examples:

- A unique value in the database must be checked before save and protected by a database constraint.
- A public registration must reject duplicate email, normalized phone, and national ID before creation.
- Public registration must never update an existing applicant unless a documented product decision changes this.
- MCQ questions must have at least two choices and exactly one correct choice.
- Essay questions must have no choices. Converting MCQ to essay must delete stale choices.
- Exam start and submit must validate active status, window, assignment ownership, assignment status, and timer expiry on the server.
- Course content access must require active, non-expired enrollment. Pending is not active.

## Translation And Display Rules

- All user-facing Python strings must use `gettext_lazy` or `gettext`.
- All user-facing template strings must use `{% trans %}` or `{% blocktrans %}`.
- Do not hardcode Arabic or English labels in business logic.
- Database values that are choice/status values must be displayed through translated labels, not raw stored codes.
- User-entered database content stays as entered. Do not translate user-generated names, essay answers, notes, titles, or descriptions.
- System-generated database content, seed data, and fixed email template names must be translated or seeded per language where needed.
- Validation messages must be translatable and field-specific when possible.

## Permission And Security Rules

- UI hiding is not security.
- Every protected view must check permissions server-side.
- Every state-changing action must be POST-only with CSRF.
- Never mutate state on GET.
- Students can only access their own records.
- Teachers and moderators can only access records explicitly permitted by role or assignment.
- Admin-only design or operations pages must be gated by Admin/Super Admin permissions.
- Do not log secrets, raw API keys, or unnecessary sensitive student data.
- File uploads must validate extension, MIME where possible, size, and storage destination.

## HTMX And URL Rules

- Do not set global `hx-push-url="true"`.
- A partial endpoint must never become the browser's stable refresh URL unless it can render a complete page.
- Inline edit/create/delete interactions must use `hx-push-url="false"` unless there is a deliberate full-page URL.
- If an HTMX action completes and the canonical page should reload, return `HX-Redirect` to the parent URL.
- Non-HTMX GET requests must return a full page that extends `base.html`.
- HTMX partials must return predictable partial templates only when `HX-Request` is present.
- Every HTMX form must display validation errors without losing user-entered data.

## UI/UX Rules

- This LMS is for simple users/admins aged roughly 30-65. Prefer clarity over novelty.
- No fancy animations, no decorative motion, no bouncy transitions, no complex hover effects.
- Keep navigation shallow. Prefer clear list-detail pages, tabs only when they reduce page depth, and visible back links.
- Use calm colors, readable contrast, generous line height, and predictable placement.
- Use shared partials for buttons, fields, badges, cards, tables, breadcrumbs, messages, empty states, and stats.
- Do not create one-off button/card/input styles in templates.
- Do not put cards inside cards.
- Do not make marketing-style landing sections for operational tools.
- Do not show Login/Register to authenticated users.
- Do not show admin links to non-admins.
- Do not show student-only links to admins unless the link is meaningful for that account.
- Test light mode first, then dark mode.
- Test Arabic RTL and English LTR for every touched screen.

## Import And Structure Rules

- Imports must be at the beginning of Python files.
- Allowed exceptions must be documented before use. Current candidates:
  - `AppConfig.ready()` signal import
  - optional debug-toolbar import
  - ASGI/WSGI environment setup before application creation
- Do not add circular imports. Use service boundaries, selectors, policies, or lazy model references.
- Keep views thin. Do not put scoring, timing, access, registration matching, email rendering, or promotion rules directly in templates or views.
- Do not duplicate business logic across apps.
- Do not introduce broad abstractions before there is repeated real usage.
- Do not leave fake links, dead buttons, or placeholder actions visible to users.

## Architecture Rules

- Entrance exams and normal learning assessments must remain separate at the product boundary.
- Separate means distinct URL namespaces, entry points, policies, reporting language, permission rules, and tests.
- Shared question/scoring/timer utilities are allowed only below the workflow layer.
- AI/Gemini integration belongs behind a service/client boundary, not embedded in view logic.
- Academic access must be mediated by policies/selectors, not ad-hoc query fragments in templates.
- Enrollment lifecycle must be explicit: pending, active, expired, restricted/revoked.

## Testing Rules

- Every new business rule gets a test.
- Every fixed bug gets a regression test.
- Test the forbidden path, not only the happy path.
- Add tests for form validation and service validation when both layers are used.
- Add permission tests for anonymous, wrong-role, right-role, and ownership boundaries.
- Add HTMX tests for partial responses and normal GET fallback when endpoints support both.
- Add timezone-aware tests for exam windows, timers, release schedules, and expiry.

## Do Not Do

- Do not silence errors by broad `except Exception` unless the failure is truly non-critical and logged safely.
- Do not use raw `.objects.create()` in workflow code when a service enforces required rules.
- Do not save invalid intermediate rows and "fix them later" unless the transaction cannot commit invalid state.
- Do not add UI that implies a backend action exists before implementing the backend route, permission check, validation, success response, and tests.
- Do not continue to later phases while P0 review tasks remain unresolved.

---

# LMS Project-Specific Rules

These rules apply specifically to this LMS project (registration, admissions, cohort/course management, assessments, grading, promotion, certificates, payments). They layer on top of every rule above and resolve domain behavior confirmed directly by stakeholders. Where a rule here adds precision to a rule above, both stand — read this section as the authoritative detail for this project's specific behavior.

## Registration, Duplicate Detection, and Account Identity

**Update 2026-06-20: This section supersedes prior wording per the Stakeholder Decision Addendum. Registration may create a person record without a program application; first registration may optionally include one course or one diploma application.**

### Root Identity

- A main registration creates the user/person/profile identity. A person never has more than one root registration/identity record in the system.
- All personal registration fields are mandatory.
- First registration may optionally create **exactly one** program request — either one course application **or** one diploma/cohort application, never both.
- If no program is selected during registration, only the user/person/profile/root registration is created. The person must not appear as an active applicant in admin application queues.
- Dashboard applications (reapply or new program interest) create child program request records linked to the single root identity — they never create a second root registration.

### Duplicate Detection

- Duplicate detection on public registration checks identifiers in this exact order: national ID, then email, then phone number.
- If the submitted national ID, email, or phone number matches **any** existing person on **any** of those three fields, the public registration submission is rejected outright. Do not create a new record. Do not merge, update, or silently reuse the existing record. Show the submitter a message directing them to log in, or to use the "apply to another program" flow instead.
- The public registration form must never update an existing person/student record, under any circumstance. Only Admin, through the admin dashboard, may edit an existing applicant's data.
- A returning, already-identified person who wants to apply to an additional diploma/course does so through a separate authenticated/account-linked action, not the public registration form. This action creates a new **child** record (e.g., `ProgramApplication` / `EnrollmentRequest`) linked to their single existing identity — it never creates a second root identity row.
- Before creating a new program-interest record, check whether the person already has an active or pending request for that exact diploma/course. If so, block the request and show "you are already enrolled/applied" instead of creating a duplicate.
- Account-matching attempts, including rejected duplicate registration attempts, are logged to an admin-only audit trail (version-control style), not shown to the student/applicant. This is distinct from the student's own application/enrollment/grade/certificate history, which **does** remain visible to the student per the Student Profile requirements — do not hide a student's own legitimate history from them.

## Cohort and Course-Version Scoping

- Every cohort is its own content, assessment, and grading boundary. Every course version is its own content, assessment, and grading boundary. This is the most important business rule in the system; treat any code path that risks crossing this boundary as a P0 review item.
- A student's active cohort (for a diploma) or active version (for a course) is determined exclusively by their own current Enrollment record for that diploma/course — specifically the latest/active enrollment row, since a student may have older rows from a prior failed attempt. Never infer the active cohort/version from a global "current cohort" setting, a session value, a URL parameter, or a request parameter.
- Any selector/service function that returns content, assessments, grades, or progress for a student must take the student's Enrollment record as the scoping input, not a bare `cohort_id`/`version_id` passed in from the view or template.
- Never trust a client-supplied cohort/version identifier for an access decision. The server must always re-derive the correct cohort/version from the authenticated student's own enrollment data.
- New cohorts/versions, and content/assessment changes made to them, must never alter what an existing cohort/version's students see. Test this explicitly: creating cohort N+1 content must leave cohort N students' visible content unchanged.

## Timezone Rules

- Django `TIME_ZONE` must be `Africa/Cairo` and `USE_TZ` must be `True`, in every environment.
- All datetime fields are stored timezone-aware. Never use naive `datetime.now()`; use `django.utils.timezone.now()`.
- Exam open/close windows, timers, content release schedules, access expiry, and certificate dates must all be validated and displayed in Cairo time.
- Every business-rule test involving dates/times must include at least one timezone-aware edge case (e.g., a window boundary crossing midnight Cairo time relative to UTC).

## Internationalization Specifics

- Arabic (`ar`) is `LANGUAGE_CODE` and the default rendered language.
- English is available through Django i18n as a secondary language.
- Layout direction (RTL/LTR) follows the active language automatically; do not hardcode a single direction in base templates.
- All translation requirements stated earlier in this document apply across both languages without exception.

## Entrance Exam vs. Normal Assessment Boundary

- Entrance exams and normal learning assessments (quizzes, semester exams, final exams, homework) remain fully separate at the product boundary: separate URL namespaces, separate admin screens, separate student entry points, separate attempt/result models, separate permission rules, separate reporting language, separate acceptance tests.
- The following may be shared as lower-level utilities only, never as a shared user-facing workflow: question data structures, MCQ scoring utility, written-answer review helpers, timer/window validation, attempt-state constants, grade-visibility utilities, the Gemini scoring client, common form widgets, common question-rendering template partials.
- Shared code must be model-agnostic. Do not let a shared utility import or depend on entrance-exam-specific or normal-assessment-specific models.

## Gemini AI Integration Scope

**Update 2026-06-20: This section supersedes the prior "exclusive entrance MCQ" restriction per the Stakeholder Decision Addendum.**

Gemini is an **assistance/suggestion layer**, not a hidden final authority. It may assist with review and scoring suggestions across multiple contexts.

### Where Gemini May Be Used

- **Entrance exam MCQ scoring** — weighted scoring via rubric/model-answer/instruction-based prompts.
- **Entrance exam written answer review** — when enabled, provides assistance suggestions for staff, never as final authority.
- **Normal exam/assessment review** — available for prompt evaluation and fine-tuning with staff final decision always required.

### Mandatory Audit and Authority Rules

- Every Gemini request and response must be logged:
  - prompt version and model name
  - input reference (question/exam context, never raw API keys)
  - response summary or full safe response
  - score suggestion and confidence if available
  - timestamp and success/failure state
  - final staff decision (accepted, adjusted, or rejected)
  - reviewer identity and timestamp
- Never log API keys, full secrets, or unnecessary sensitive student data.
- Staff final grade/decision must remain explicit and recorded regardless of Gemini suggestion.
- Staff can always override the Gemini-assisted score.
- Gemini failures or timeouts must never block or fail the student's exam submission.
  - On failure, mark the affected portion "Pending AI Review" rather than scoring zero or auto-passing it.
  - Allow staff to manually trigger a rescore or enter an override score directly.
- The scoring prompt is a configurable system-prompt template (stored in code/config, not hardcoded inline per request) that encodes the predefined scoring rules for that exam context.

## Video Completion Tracking

- Video completion validation (max 1x playback speed, no skipping, at least 80% watched) is configurable both per individual video and per whole course — the configuration model must support both granularities.
- Treat this as best-effort compliance tracking, not proctoring. It cannot fully prevent a determined student from bypassing it, and that limitation stays documented, not silently promised away.
- Never trust a client-reported "completed" state alone. The client reports periodic playback-position checkpoints (e.g., via the YouTube IFrame API's postMessage events relayed through HTMX/Ajax), and the server validates that consecutive checkpoints are consistent with real-time, ≤1x playback, with no implausible forward jumps.
- On a detected violation (skip or over-speed), stop tracking, surface a clear message to the student, and require them to reload/restart according to the configured rule — never silently fail and never silently pass.

## Cloudflare R2 / File Storage Rules

- All audio lecture files and homework uploads go through a single storage-service abstraction; no view or template ever calls the R2 SDK directly.
- No R2 object is ever served via a public/unsigned URL. All access goes through signed, time-limited URLs generated by the storage service.
- File uploads are validated for extension, MIME type, and size before upload, both client- and server-side, with the server-side check being authoritative.
- Homework upload size limits and accepted extensions (PDF/DOCX) are configurable, recommended at 10–25 MB. Confirm an equivalent limit before implementing audio uploads if none has been set yet.
- Storage failures (upload errors, signing errors) must be caught and surfaced as a clear user-facing error — never a silent failure or a broken link in the UI.

## Certificate Generation Rules

- A generated certificate is an immutable historical record: the fact that a certificate was issued, on what date, for what completion, under what template, is never edited or deleted.
  - `[ASSUMPTION — confirm]`: the PDF rendering is recomputed at view/download time, combining the immutable certificate record (issue date, program, certificate number, template) with the student's **current** profile name — so a later legal-name correction is reflected without altering the historical record. If the name should instead be frozen exactly as it was at issuance, say so and this rule is reversed.
- Recommended approach (not mandated, since no library is chosen yet): generate Arabic-correct PDFs with a tool that supports proper Arabic shaping and bidi rendering — e.g., WeasyPrint with HTML/CSS templates and embedded Arabic fonts (simplest to maintain), or ReportLab combined with `arabic-reshaper` + `python-bidi` (more control, more setup). Either choice must be validated with an automated rendering test confirming Arabic glyphs render connected and right-to-left, not as isolated/disconnected characters.
- Certificate templates are admin-provided. Until real templates are supplied, build against a placeholder template so the generation pipeline can be tested end-to-end without blocking on stakeholder assets.

## Payment CSV Matching Rules

- Matching uses national ID, email, or phone number. Since all three are guaranteed unique per student record, there is no priority order — any single match is sufficient.
- CSV upload is atomic per file: if any row fails to match an existing student or fails validation, reject the **entire** file. Do not partially apply a CSV.
- On rejection, the response must identify the exact row number(s) and the specific reason for each failure (e.g., "row 14: no student found matching national ID ..."), so the file can be corrected and re-uploaded.
- `[NOTE]`: this is stricter than the FRD's original "matched/unmatched/updated/failed rows" wording, which implied partial success. This rule supersedes that wording per the explicit project decision recorded here.

## Promotion, Demotion, and Reapplication Rules

- A student who fails or does not complete a cohort, and later applies to a future cohort for the same diploma, is not blocked from reapplying — but is treated as a fresh applicant for that new cohort: a new program-interest/enrollment-request record is created for the new cohort, while their prior cohort's enrollment, grades, and attempts remain untouched and fully preserved.
- Never copy, migrate, or merge data from a prior failed cohort enrollment into a new cohort enrollment. They are fully independent rows linked only by the shared Application/person identity.
- Before allowing a reapplication, check the student does not already have an active or pending request for that same diploma/course; if they do, block with "already enrolled/applied" rather than creating a second concurrent request.

## Audit and History Immutability

- Audit/history rows (admin actions, access changes, grading overrides, promotion/demotion decisions, account-matching attempts, email sends, payment uploads, certificate generation) are append-only. They are never edited in place and never hard-deleted.
- Every override of a prior value (grade change, promotion override, access change) writes a new audit row capturing: the actor, a timestamp, the before value, the after value, and a reason where applicable.
- The account-matching/duplicate-detection audit trail is admin-only and is a separate concept from the student-facing profile history (applications, enrollments, grades, certificates), which the student is allowed to see per the Student Profile requirements.

## Performance and VPS Optimization

The hosting constraint is fixed at 2 vCPU / 4 GB RAM / 30 GB SSD. Optimize accordingly:

- Use database indexes for frequent filters: application status, student identifiers, cohort, enrollment, assessment/exam availability, grading status.
- Avoid heavy background workers; prefer simple synchronous operations first, and move only genuinely heavy tasks to a queue when there's a demonstrated need.
- Keep admin list queries efficient with `select_related` and `prefetch_related`.
- Avoid N+1 queries in dashboards and the student profile page — these are the highest-traffic, most relationship-heavy views in the system.
- Do not store large files (audio, homework) on the VPS disk; they belong in R2.

## Roles and Permissions Canonical Table

This table is the single source of truth for role permissions. Any permission check in code must trace back to a row here; do not invent new permission boundaries ad hoc in a view.

| Role | Scope |
|---|---|
| Super Admin | Full access to all modules, settings, users, programs, cohorts, and records. |
| Admin | Manage registrations, students, diplomas, courses, cohorts, assessments, lectures, email templates, payment CSV uploads, and access control. |
| Moderator | View assigned active students, review written answers, grade homework, follow student progress, support assigned groups only. |
| Teacher | Review and grade assigned written answers/assessments for active students in assigned cohorts/courses only. |
| Student | Apply to programs, access their own approved content, take their own assessments, upload their own homework, view their own progress/grades/feedback/certificates. |

- Permission checks must go through named helper functions (e.g., `is_assigned_moderator_for(user, student)`, `is_teacher_of(user, cohort)`) rather than inline role-string comparisons scattered across views.
- "Assigned" for Moderator/Teacher means an explicit assignment record exists (cohort assignment, course assignment, or similar) — never an implicit "all moderators see all students" shortcut.