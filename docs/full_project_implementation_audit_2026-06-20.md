# Full Project Implementation Audit - 2026-06-20

Reviewer mode: read-only implementation audit. No implementation code, migrations, or tests were edited. Only this report file was created.

## Executive Verdict

Overall status: **partial / not safe to call full implementation complete**.

The project has a real Django foundation, a broad test suite, and many modules implemented through Phase 8. `python manage.py check`, migration checks, and the full pytest suite pass. However, several mandatory business and architecture guardrails are still violated. The largest risks are identity/application modeling, entrance exam separation, Gemini usage, assessment access scoping, staff assignment permissions, and production deployment hardening.

Safe to proceed as "nearly complete": **No**. Safe to continue with targeted fix work: **Yes**, starting with the P0 items below.

Top blockers:

- Root application/person identity is modeled as multiple `Application` rows instead of one root identity plus child program requests.
- Entrance exams are implemented inside `apps.exams` using an `exam_type` flag, not a separate product/app boundary.
- Gemini is wired to essay/written grading, directly violating the "MCQ entrance exam only, never written grading" rule.
- Normal assessment access treats assessments without `course_version` as globally accessible.
- Teacher/moderator grading queues are not scoped to explicit assignments.
- Entrance exam token access can create an assignment for any authenticated user with the link, and there is no durable one-attempt-per-student/cohort constraint.

## Validation Summary

Commands run:

| Command | Result | Notes |
|---|---:|---|
| `python manage.py check` | Pass | System check identified no issues. |
| `python manage.py makemigrations --check --dry-run` | Pass | No changes detected. |
| `python -m pytest -q` | Pass | 561 passed. Warning from `pytest_asyncio` config only. |
| `python -m ruff check .` | Fail | Import ordering/top-of-file violations in `apps/audit/views.py` and test import issues in `apps/payments/tests/test_services.py`. |

Important scan commands used:

- `rg --files`
- `git status --short`
- `rg -n "TODO|placeholder|fake|pass|except Exception|csrf_exempt|is_staff|is_superuser|objects\.all\(|\.count\(|datetime\.now|hx-push-url|hx-boost|hx-get|hx-post|HX-Redirect|safe" apps templates config static docs`
- `rg -n "GEMINI|Gemini|gemini|boto|S3|R2|storage|signed|FileField|upload_to|csv|Email|send_mail" apps config requirements.txt .github docs`
- Template scans for raw dates, one-off styling, HTMX URL behavior, and UI guide violations.

## Severity Findings

### P0-01 - Root Application Identity Model Violates Project Guardrails

Evidence:

- `apps/applications/models.py:21-36` defines `Application` as "A registration request" and links it directly to a `StudentProfile` and optional `cohort`.
- `apps/applications/models.py:22` explicitly says a student may have multiple applications.
- `apps/students/services.py:72-104` creates a new `Application` row for reapply.

Why this is wrong:

`docs/LLM_IMPLEMENTATION_GUARDRAILS.md` says the Application/account is 1:1 with a person and is created exactly once at first successful public registration. Returning program interest must be a child record such as `ProgramApplication` or `EnrollmentRequest`, never a second root `Application`.

Risk:

Identity history, duplicate handling, payment matching, audit history, and student profile history can all drift because a root person record is being reused as both identity and program-interest workflow.

Recommended fix:

Keep exactly one root `Application` or rename the root concept to a person/admission profile, then create a separate child model for cohort/course requests.

Key solution shape:

```python
# Example only.
class ProgramApplication(TimeStampedModel):
    root_application = models.ForeignKey(Application, on_delete=models.PROTECT)
    student = models.ForeignKey(StudentProfile, on_delete=models.PROTECT)
    cohort = models.ForeignKey("academics.Cohort", on_delete=models.PROTECT)
    status = models.CharField(choices=PROGRAM_APPLICATION_STATUS_CHOICES)
```

Required tests:

- Public registration creates exactly one root application per person.
- Reapply creates a child program request, not a second root application.
- Duplicate public registration is rejected and audited.
- Concurrent/pending duplicate program requests for the same cohort/course are blocked.

### P0-02 - Entrance Exams Are Not Separated From Normal Exam Product Boundary

Evidence:

- `config/settings/base.py:76-88` has `apps.exams` and `apps.assessments`, but no `apps.entrance_exams`.
- `config/urls.py:33` includes entrance and exam routes under `exams/`.
- `apps/exams/models.py:20-29` uses a generic `Exam` with `exam_type`.
- `apps/exams/views.py:95-100` forbids entrance exam access by ID as a workaround, but the app, models, admin screens, URLs, and services are still shared.

Why this is wrong:

The proposal and guardrails require entrance exams and normal assessments to be separate at product boundary: URL namespaces, entry points, reporting language, permission rules, and tests.

Risk:

Future fixes to normal exams can accidentally change admissions exams. Reporting and permissions are also harder to reason about because "entrance" is a field value, not an owner boundary.

Recommended fix:

Move entrance exam workflow to `apps/entrance_exams` with its own models/URLs/templates/services. Keep reusable lower-level scoring/timer/question utilities only if they are model-agnostic.

Required tests:

- Entrance exam URLs remain separate from normal assessment URLs.
- Normal assessment views cannot import or depend on entrance-exam workflow views.
- Entrance reports and student entry points remain isolated.

### P0-03 - Gemini Is Used For Essay/Written Grading

Evidence:

- `apps/exams/gemini.py:21-38` defines `grade_essay()`.
- `apps/exams/views.py:533-557` exposes `admin_grade_with_gemini()` for essay answers.
- `apps/exams/views.py:549-553` sends written answer text to Gemini.
- There is no Gemini request/response persistence model; the only audit record stores a summary at `apps/exams/views.py:558-564`.

Why this is wrong:

Guardrails state Gemini is used exclusively to assist MCQ scoring for the entrance exam and is never used for written-answer grading. Every Gemini request/response must be logged without API keys, and failures must not block submission.

Risk:

The implementation violates the product decision, can expose written student content to AI without approval, and can produce a 500 if the Gemini call raises because `grade_essay()` does not catch API exceptions.

Recommended fix:

Remove Gemini written grading actions. Add a dedicated entrance-exam MCQ assistance service/client with a configurable prompt, timeout/failure handling, and durable request/response log.

Required tests:

- No Gemini button/action appears for essay/written answers.
- Entrance MCQ scoring logs request reference, response, timestamp, and success/failure.
- Gemini timeout/failure leaves submission successful and marks the MCQ portion pending manual/AI review.

### P0-04 - Entrance Exam Assignment Access Is Too Broad And Not Cohort-Safe

Evidence:

- `apps/exams/models.py:136-180` defines `Assignment` with `exam`, `student`, and `attempt_number`, but no cohort/application link and no unique constraint.
- `apps/exams/views.py:356-360` lets any authenticated user with the token get or create an assignment.
- `apps/exams/views.py:75-92` creates assignments if none exist.
- `apps/exams/services.py:154-162` only checks completed/graded attempts, not cohort-level one-submission rules.

Why this is wrong:

Phase 3 requires one submission per student per cohort and an entrance assessment linked to diploma cohort. Guardrails require server-derived cohort/version boundaries, not client/token-only access.

Risk:

Any logged-in user with the token can enter the exam. Duplicate assignment rows can also be created through admin, shell, race conditions, or service calls.

Recommended fix:

Link entrance attempts/assignments to application/program request and cohort. Add a durable unique constraint such as `(student, entrance_exam, cohort)` or the chosen child application boundary. Do not auto-create assignments from token alone unless eligibility is proven.

Required tests:

- User with token but no invited application/cohort assignment is denied.
- Duplicate assignment creation fails at service and database levels.
- Retake or future cohort creates a separate cohort-scoped attempt without touching prior cohort data.

### P0-05 - Assessment Access Is Global When `course_version` Is Null

Evidence:

- `apps/assessments/models.py:33-56` allows `course_version`, `semester`, and `cohort` to be nullable.
- `apps/assessments/services.py:68-72` returns `True` from `has_active_enrollment()` if `assessment.course_version` is missing.
- `apps/assessments/services.py:395-417` includes `Q(course_version__isnull=True)` in student-visible assessments.

Why this is wrong:

Assessment access must be scoped by the student's own active enrollment. The current logic makes any active assessment without a course version visible/accesssible to all students.

Risk:

Admins can accidentally create a cohort/semester assessment with no course version and expose it to every student.

Recommended fix:

Make assessment scope explicit and mandatory. If an assessment is cohort-scoped, check active `DiplomaEnrollment`; if course-version scoped, check `CourseEnrollment` or the matching diploma cohort enrollment. Remove the global `course_version__isnull=True` path unless there is a documented public assessment type.

Required tests:

- Assessment with cohort but no course version is visible only to active cohort students.
- Assessment with no valid scope is not visible/startable.
- Expired/restricted enrollments cannot start assessments.

### P0-06 - Teacher/Moderator Permissions Are Not Assignment-Scoped

Evidence:

- `apps/core/permissions.py:77-84` defines broad staff access for moderators/teachers.
- `apps/assessments/services.py:438-448` returns all pending grading to moderators and teachers.
- `apps/assessments/views.py:576-584`, `apps/assessments/views.py:601-608`, and `apps/assessments/views.py:632-740` let any exam staff role access arbitrary grading objects by ID.
- `apps/exams/views.py:479-573` has the same broad `ROLE_EXAM_STAFF` grading access.

Why this is wrong:

The canonical permissions table says moderators and teachers can only view/grade assigned active students or assigned cohorts/courses.

Risk:

Horizontal privilege escalation across classes/cohorts: a teacher or moderator can grade or view submissions outside their assignment.

Recommended fix:

Add explicit teacher/moderator assignment models for cohorts/course versions/assessment queues, then route every staff object lookup through policy helpers such as `can_grade_attempt(user, attempt)`.

Required tests:

- Teacher cannot see or grade unassigned attempts/homework.
- Moderator cannot see unassigned students.
- Admin/super admin retain full operational access.
- Direct URL access by wrong staff role returns 403/404.

### P0-07 - Storage Access Can Produce Unsigned/Public URLs

Evidence:

- `apps/storage/services.py:112-113` returns `/media/storage/{key}` for local downloads.
- `apps/assessments/views.py:157-160` and `apps/assessments/views.py:608` expose download URLs from storage service.
- `config/urls.py:46-47` serves media/static only in `DEBUG`, but the storage service itself has no environment guard around public local URLs.

Why this is wrong:

Guardrails require no R2 object to be served via public/unsigned URL and all access to go through signed, time-limited URLs or permission-gated serving.

Risk:

If production or staging is run without R2, homework files can become predictable media URLs. The service API does not force permission context before generating a URL.

Recommended fix:

Make `get_download_url()` require a permission-checked caller context or use a signed local download route in non-R2 environments. Production should fail closed if `USE_R2_STORAGE=False` for protected uploads.

Required tests:

- Download URL generation requires a permitted user/object context.
- Local fallback download route denies wrong student/role.
- Production settings check fails when protected upload storage is local.

### P1-01 - Registration Is Missing Program/Cohort Selection And Invitation Status

Evidence:

- `apps/applications/forms.py:18-34` contains personal registration fields but no program/cohort field.
- `apps/applications/constants.py:7-18` lacks the documented "Invited to Entrance Assessment" status.
- `apps/applications/views.py:155-185` activation enables the user account but does not create diploma/course enrollment.

Why this matters:

Phase 2 requires program selection, invitation status, and admin enrollment activation into a selected diploma/course.

Recommended fix:

After fixing the root/child application model, add program/cohort selection to the child request and wire approval/activation to create the correct pending/active enrollment.

Required tests:

- Public registration captures selected program/cohort.
- Admin can move request to invited/accepted/rejected/activated.
- Activation creates the expected enrollment row.

### P1-02 - Payment Upload Is Not Atomic On Malformed CSV Creation

Evidence:

- `apps/payments/services.py:297-316` creates `PaymentUpload` before `validate_csv_content(content)` and row parsing.
- `apps/payments/views.py:94-98` catches parsing errors and shows an error, but the upload row can already exist.

Why this matters:

Payment CSV upload is documented as atomic per file. Failed files should not leave ambiguous pending artifacts unless explicitly modeled as rejected upload history.

Recommended fix:

Wrap `create_upload()` in `transaction.atomic()` and parse/validate before creating durable upload state, or create a failed upload record intentionally with complete row/error metadata.

Required tests:

- Missing headers do not create a pending upload.
- Invalid rows produce exact row errors and no payment records.
- Confirm is idempotent or safely rejected on repeat.

### P1-03 - Ruff Fails Current Codebase

Evidence:

- `python -m ruff check .` reports import block and E402 failures in `apps/audit/views.py:1-14`.
- It also reports import sorting, unused import, and `datetime.UTC` issues in `apps/payments/tests/test_services.py`.

Why this matters:

CI has `ruff check .` at `.github/workflows/ci.yml:53-54`, so the current code cannot pass CI even though tests pass.

Recommended fix:

Fix import order/top-of-file issues and test lint failures without changing behavior.

Required tests:

- Re-run `python -m ruff check .`.

### P1-04 - Deployment Workflow Does Not Meet Phase 9 Acceptance

Evidence:

- `.github/workflows/deploy.yml:31-37` collects static and migrates before copying to server.
- `.github/workflows/deploy.yml:39-63` deploys/restarts without running lint/tests/migration check in the deploy job.
- `.github/workflows/deploy.yml:63` uses `curl ... || true`, so a failed health check does not fail deployment.
- Proposal secret names include `SSH_HOST`, `SSH_PORT`, `SSH_USER`, `SSH_PRIVATE_KEY`; workflow uses `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`.

Why this matters:

Phase 9 requires hardened deployment, blocking health check, secret alignment, backups/restore, TLS/reverse proxy, and operational handover.

Recommended fix:

Make deploy depend on CI or repeat lint/test/migration checks, align secret names, make health check blocking, add rollback notes, backup/restore scripts, and server provisioning docs.

Required tests/checks:

- Staging deploy fails if health check fails.
- Production `manage.py check --deploy` is documented/run.
- Backup creation and restore are tested on non-production database.

### P1-05 - Certificate PDF Path Is Not Arabic-Verified And Uses Broad Exception Handling

Evidence:

- `apps/certificates/services.py:148-158` falls back from WeasyPrint to xhtml2pdf without verifying Arabic shaping/bidi.
- `apps/certificates/services.py:161-187` creates an English placeholder body for `language="ar"`.
- `apps/certificates/views.py:245-253` catches all exceptions when serving PDFs.

Why this matters:

Phase 6 requires Arabic PDF support and a rendering smoke test. Guardrails discourage broad exception handling unless safely logged.

Recommended fix:

Pick and document one Arabic-capable renderer, embed fonts, add a render test checking connected RTL Arabic glyphs, and log storage/render exceptions safely.

Required tests:

- Arabic certificate PDF renders readable connected Arabic.
- Failed PDF retrieval is logged and shown as a safe message.

### P1-06 - Audio Lectures Still Use URL Field, Not Storage Service

Evidence:

- `apps/academics/models.py:206-207` stores `youtube_url` and `audio_url`.
- `apps/storage/services.py` supports upload/download, but lecture audio upload/storage is not wired to academic views/models.

Why this matters:

Phase 5 requires audio lecture upload through the storage abstraction/R2, not permanent VPS disk or arbitrary URLs.

Recommended fix:

Add lecture audio metadata (`storage_key`, content type, size, checksum) and admin upload workflow through `storage_service`. Student playback must be permission-gated and signed.

Required tests:

- Audio upload validates type/size.
- Student receives signed URL only when active enrollment permits the lecture.

### P2-01 - UI Guide Is Partially Applied, Not Complete

Evidence:

- `templates/base.html:16-17` still uses Tailwind Play CDN.
- `templates/partials/_language_switcher.html:5` still uses `onchange="this.form.submit()"`.
- Raw date filters remain in many student/admin templates, for example `apps/students/templates/students/profile/applications.html:14-16`, `apps/progression/templates/progression/student_progress.html:74`, and `apps/audit/templates/audit/admin_list.html:53`.
- One-off button/input styles remain in several templates, for example `apps/communications/templates/communications/bulk_email_create.html:10-48`, `apps/certificates/templates/certificates/admin_certificate_list.html:26`, and `apps/progression/templates/progression/admin_student_detail.html:115`.

Why this matters:

The UI guide requires shared partials, standard datetime rendering, no inline old-style controls, and compiled Tailwind before production.

Recommended fix:

Run a UI cleanup pass after P0/P1 backend rules: replace raw dates with the shared datetime helper, move repeated controls to partials, remove Play CDN, and verify 360px mobile plus Arabic RTL/English LTR.

Required checks:

- Template scan for raw `|date:` and one-off button/input classes.
- Manual mobile screenshots for high-traffic flows.

### P2-02 - Broad Exceptions And Non-Top Imports Need Cleanup

Evidence:

- `apps/students/selectors.py:340-345` suppresses any payment selector failure.
- `apps/certificates/services.py:140-143` stores raw exception text in metadata.
- `apps/core/permissions.py:34` imports `datetime` inside a helper.
- `apps/audit/views.py:13-14` imports after executable code, causing Ruff E402.
- `apps/assessments/views.py:406` and `apps/assessments/views.py:474` import forms inside functions.

Why this matters:

The guardrails require imports at the top except documented exceptions and discourage broad exceptions that hide important failures.

Recommended fix:

Move imports to file top where possible. Replace broad catches with targeted exceptions and safe logging. Do not suppress payment/profile failures silently unless the fallback is explicitly documented.

Required tests:

- Ruff passes.
- Payment selector failure path is tested if fallback remains.

## Phase Coverage Matrix

| Phase | Status | Evidence | Main gaps | Next action |
|---|---|---|---|---|
| Phase 1 - Foundation | Mostly complete | `config/settings/base.py`, `.github/workflows/ci.yml`, base templates, roles/tests | Ruff currently fails; deploy skeleton incomplete; production Tailwind not compiled | Fix lint, align deployment, remove Play CDN for production |
| Phase 2 - Registration/Admin/Email | Partial | `apps/applications/*`, `apps/communications/*` | No program selection on public registration; no invited-to-exam status; activation does not create enrollment; root Application model wrong | Redesign root vs child application model, add program request workflow |
| Phase 3 - Entrance Exam/Gemini | Incorrect/partial | `apps/exams/*`, `apps/exams/gemini.py` | No separate entrance app; token creates assignments too broadly; no cohort/application scoping; Gemini grades essays instead of entrance MCQs; no request/response log | Build `entrance_exams` boundary and migrate workflow |
| Phase 4 - Academics/Content | Partial | `apps/academics/models.py`, `apps/academics/services.py` | Course access exists, but audio is URL-only; no video completion tracking; admin assignment scoping absent | Add storage-backed lecture audio and access/download policy |
| Phase 5 - Assessments/Grading/Homework/R2 | Partial | `apps/assessments/*`, `apps/storage/services.py` | Assessment with no course version is globally accessible; teacher/moderator queues unscoped; local storage URLs unsigned; audio not wired | Fix assessment scope, staff assignment policies, storage download guard |
| Phase 6 - Promotion/Certificates | Mostly complete but needs hardening | `apps/progression/*`, `apps/certificates/*` | Arabic PDF rendering not proven; placeholder certificate is English; broad exception handling; certificate immutability/regeneration needs audit review | Add Arabic render test and certificate audit/immutability tests |
| Phase 7 - Full Student Profile | Mostly complete | `apps/students/views.py`, `apps/students/selectors.py` | Payment selector fallback hides failures; raw date/UI inconsistencies; relies on current flawed Application model | Rework profile after identity model fix; clean UI/date rendering |
| Phase 8 - Missing Scope | Partial | `apps/payments/*`, `apps/audit/*`, `apps/communications/*` | Payment CSV not fully atomic; audit is present but not append-only enforced at model/service level; bulk email exists but needs recipient/permission polish | Atomic payment import, audit immutability guard, bulk email QA |
| Phase 9 - Deployment | Not complete | `.github/workflows/deploy.yml` | Non-blocking health check; no backup/restore procedure; no server provisioning/TLS docs verified; deploy does not prove lint/tests | Harden deploy and write operations handover |

## Ordered Fix Plan

### P0 Fixes - Do First

1. Redesign application identity:
   - Keep one root person/application record.
   - Add child `ProgramApplication` or `EnrollmentRequest`.
   - Migrate reapply and payment/program references to the child model.

2. Split entrance exams:
   - Create `apps/entrance_exams`.
   - Move entrance exam URLs, models, services, templates, and tests.
   - Keep shared scoring/question utilities only below workflow level.

3. Replace Gemini integration:
   - Remove essay Gemini grading.
   - Implement entrance MCQ assistance only.
   - Add durable request/response log and failure fallback.

4. Make entrance exam access cohort/application-safe:
   - Link invitation/assignment to the child program request and cohort.
   - Add database uniqueness for one active/submitted attempt per student/exam/cohort.
   - Stop auto-creating assignments from a token unless eligibility is verified.

5. Fix assessment access scoping:
   - Require explicit scope for every assessment.
   - Check `DiplomaEnrollment` for cohort/semester assessment.
   - Check `CourseEnrollment` or cohort enrollment for course-version assessment.
   - Remove global `course_version__isnull=True` visibility.

6. Add teacher/moderator assignment policies:
   - Create explicit assignment records.
   - Add `can_grade_attempt`, `can_grade_homework`, `can_view_student`.
   - Apply policy checks in list selectors and direct object views.

7. Lock down file download access:
   - Require permission context for storage download URL generation.
   - Add local signed/proxy route or disallow protected local storage outside development.

### P1 Fixes

1. Complete registration/program workflow:
   - Add program/cohort selection.
   - Add invited-to-entrance-assessment status.
   - Activation creates enrollment.

2. Make payment upload atomic:
   - Parse/validate before durable upload creation or record failed upload intentionally.
   - Keep exact row-level errors.

3. Fix Ruff:
   - Sort imports and remove unused imports.
   - Move non-exception imports to file top.

4. Certificate Arabic hardening:
   - Use a verified Arabic-capable PDF renderer/font stack.
   - Add rendering smoke tests.

5. Lecture audio storage:
   - Add storage metadata fields and admin upload.
   - Generate signed playback/download URLs only after access checks.

6. Audit immutability:
   - Prevent hard-delete/edit paths for audit/history records.
   - Capture before/after/reason consistently for overrides.

### P2/P3 Quality And Production Tasks

1. Compile Tailwind and remove Play CDN from production templates.
2. Replace remaining raw `|date:` uses with the project datetime helper.
3. Convert remaining one-off buttons/inputs to shared partials.
4. Replace language switcher full-page `onchange` with the approved pattern.
5. Add Playwright or equivalent E2E coverage for:
   - registration to approval/activation
   - entrance exam invitation/submission/review
   - content access expiry
   - assessment/homework/grading
   - payment CSV upload/confirm
   - certificate generation/download
6. Harden deployment:
   - deploy depends on passing CI
   - blocking health check
   - rollback notes
   - backups and restore test
   - TLS/reverse proxy/server provisioning docs

## Final Completion Checklist

- [ ] One root application/person record per registered person.
- [ ] Child program/cohort request model exists and powers reapply.
- [ ] Entrance exams live in a separate app/namespace.
- [ ] Gemini is MCQ entrance-only and fully logged.
- [ ] Entrance attempts are cohort/application scoped and database constrained.
- [ ] All assessment access derives from active non-expired enrollment.
- [ ] Teacher/moderator access is explicit-assignment scoped.
- [ ] Homework/audio/certificate files are storage-service-backed and permission-gated.
- [ ] Payment CSV upload is atomic per file.
- [ ] Audit/history records are append-only in practice.
- [ ] Arabic/English UI strings and date formats are consistent.
- [ ] Tailwind is compiled for production.
- [ ] Ruff, Django check, migrations, tests, and E2E smoke checks pass.
- [ ] Deployment has blocking health checks, backups, restore docs, and handover notes.

---

# Stakeholder Decision Addendum - 2026-06-20

This addendum records owner decisions received after the audit. It supersedes conflicting assumptions in the audit findings and must be reflected in implementation docs, SRS/FRD-facing docs, guardrails, tests, and code.

## Implementation Agent Instructions

Use this section as mandatory instructions for any implementation agent, including smaller/cheaper models. Do not infer different business rules from old code. When there is a conflict, follow the source-of-truth order below.

### Source-Of-Truth Order

1. This file, especially:
   - `Stakeholder Decision Addendum - 2026-06-20`
   - `Revised Recovery Phases`
   - `Updated Completion Checklist`
2. `docs/LLM_IMPLEMENTATION_GUARDRAILS.md`
   - Must be updated first where it conflicts with this addendum, especially Gemini scope and application identity.
3. `docs/UI_UX_DUAL_THEME_EXTENSION.md`
   - Source of truth for the two global UI themes.
4. `docs/UI_UX_TEMPLATE_GUIDE.md`
   - Source of truth for templates, partials, HTMX safety, accessibility, RTL/LTR, and production UI checks.
5. `docs/FULL_PROJECT_IMPLEMENTATION_PROPOSAL.md`
6. `docs/PHASED_IMPLEMENTATION_EXECUTION_PLAN.md`
7. Phase-specific plans/reviews under `docs/`
8. Current codebase

If current code conflicts with the docs above, treat the code as needing refactor. Do not silently preserve wrong behavior because tests currently pass.

### Non-Negotiable Working Rules

- Do not start implementation before reading the relevant source-of-truth docs for the phase.
- Do not optimize for "feature exists"; optimize for "the business rule cannot be bypassed accidentally."
- Every workflow change must update model/service/form/view/template/tests together.
- Put business rules in services, policies, selectors, validators, or model constraints. Do not hide them in templates.
- Use database constraints for duplicate prevention and one-attempt/one-active-application rules where possible.
- Every protected action must be checked server-side.
- Every state-changing action must be POST-only with CSRF.
- Do not mutate state on GET.
- Students can access only their own data.
- Moderators and teachers must be restricted by the assignment model defined in this addendum.
- Do not rely on UI hiding for permissions.
- Do not add fake buttons, dead links, placeholder actions, or template-only workflows.
- Do not add broad `except Exception` unless it is logged safely and explicitly justified.
- Do not leave imports inside functions unless there is a documented reason.
- Lint/import cleanup may be the last step in a recovery phase, but it is mandatory before that phase is complete.

### Required Validation Commands

At the end of each implementation phase, run:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest -q
python -m ruff check .
```

When frontend/theme behavior changes, also perform:

- 360px mobile check.
- Arabic RTL check.
- English LTR check.
- Both UI themes check.
- HTMX direct-refresh check for any partial endpoint touched.

### Model And Workflow Ownership Rules

Registration/person identity:

- A main registration creates the user/person/profile identity.
- All personal registration fields are mandatory.
- First registration may optionally create one program request.
- First registration may choose either one course or one diploma/cohort, never both.
- A registration with no selected course/diploma must not appear as an active admin application.
- Dashboard applications are child program requests, not duplicate root registrations.

Program applications:

- Use separate child records for course/diploma/cohort application requests.
- Block duplicate pending application or active enrollment for the same target.
- Preserve all historical records.

Entrance exams:

- Entrance exams must be separated from normal assessments/exams.
- Entrance exams attach to diploma cohort application flow.
- A token/email link is not sufficient access; the authenticated user must have the matching invited application.
- One student cannot submit duplicate attempts for the same entrance exam/application/cohort.
- Entrance final result pass/fail controls admission email targeting.

Gemini:

- Gemini is an assistance/suggestion layer, not hidden final authority.
- Gemini can assist entrance exam MCQ/written review and normal exam/assessment review when enabled.
- Staff final grade/decision must remain explicit.
- Store prompt version, model, request/response metadata, suggestion, final staff decision, and failure state.
- Never log API keys or unnecessary sensitive data.

Assessments:

- No normal assessment may be globally accessible because a scope field is null.
- Every active assessment must have explicit scope.
- Access must derive from active, non-expired enrollment.

Moderators and teachers:

- Moderators are assigned to students per cohort.
- Adding moderator assignments merges with existing assignments and must not delete existing assignment rows.
- Teachers are assigned to cohorts.
- Direct object URLs must enforce these scopes.

Storage:

- Protected file access requires permission checks before URL generation.
- Protected files must not be exposed as public/unsigned URLs.
- Audio lectures require original storage plus FFmpeg-generated HLS `.m3u8` and segments for CDN/R2 delivery.

UI themes:

- `docs/UI_UX_DUAL_THEME_EXTENSION.md` defines the two approved global themes:
  - Operational LMS
  - Landing Heritage
- Theme choice is client-side at first and must not affect permissions or backend behavior.
- Dark/light mode remains separate from design theme.
- Admin/student pages must remain operational and readable in both themes.
- Shared partials remain mandatory.

### Required Implementation Pattern For Each Recovery Phase

For each phase:

1. Read the source docs listed in this section.
2. Write/update tests for the intended business rules.
3. Add or update database constraints/migrations where the rule must be durable.
4. Implement service/policy/selector logic.
5. Update forms/views/templates.
6. Update docs/SRS notes if behavior changed.
7. Run targeted tests.
8. Run the full validation commands.
9. Do final Ruff/import cleanup.
10. Record any remaining known limitation in docs.

Do not skip tests because existing tests pass. Current tests pass while several documented business rules are still wrong.

## Decision Summary

### Registration, Identity, And Applications

Approved direction:

- There is one mandatory main registration form.
- All personal registration fields are mandatory.
- During first registration, applying for a course or diploma is optional.
- A first-time registrant may apply for either one course or one diploma, but not both in the same registration submission.
- If the user registers without selecting a course or diploma, they become a registered user/person only and should not appear as an active applicant in admin application queues.
- After registration, the user can apply from the dashboard to any number of courses or diplomas over time.
- A user cannot apply to a course/diploma when they already have a pending application or active enrollment for that same course/diploma.
- Diploma admissions are cohort-based.
- Admin sends entrance exam emails by diploma cohort.
- Entrance exam is attached to the diploma cohort application flow.

Implementation consequence:

- Keep a single person/registration identity.
- Separate program-interest/application records from the root registration identity.
- Admin applicant queues should show program/course/diploma applications, not plain users who registered without applying.

### Entrance Exams

Approved direction:

- Entrance exams are separate from normal exams and assessments.
- Entrance exam grading is not related to diploma course grades or learning assessment grades.
- Entrance exam exists to approve or reject a candidate for a diploma cohort.
- Entrance exam attaches to a diploma cohort.
- Entrance MCQ choices are not simple correct/incorrect choices.
- Entrance exam MCQ grading uses model answer/instructions/rubric sent to Gemini.
- Gemini returns a result per MCQ answer/question, and weighted scoring is stored in the entrance answer/scoring flow.
- Written entrance answers wait for manual review.
- Final entrance grade is saved after review/scoring.
- Entrance exam final result is pass/fail based on the required score.
- When admin sends cohort emails after entrance review, the system checks passed/failed candidates and sends success/failure templates accordingly.

Implementation consequence:

- Entrance MCQ model must support rubric/model-answer/instruction-based weighted scoring.
- Entrance answer records need fields for Gemini suggestion/result, staff decision, final score, and audit metadata.
- Entrance exam completion must feed admission/application decision state.

### Gemini AI Scope

Approved direction:

- Gemini may assist with review/correction suggestions for entrance exam answers.
- For entrance exams, Gemini review can help Admin/Moderator without automatically being the only authority for final grading.
- Gemini assistance should also be extended to normal exams and assessments so the team can evaluate performance and tune prompts.
- The system must support prompt tuning and comparison between Gemini suggestions and staff final grades.

Important update required:

- `docs/LLM_IMPLEMENTATION_GUARDRAILS.md` currently says Gemini is exclusively for entrance MCQ and never for written grading. That rule is now superseded by this stakeholder decision and must be rewritten before implementation starts.

Implementation consequence:

- Gemini should be modeled as an assistance/suggestion layer, not as hidden final authority.
- Staff final action should record whether Gemini was accepted, adjusted, or ignored.
- Store prompt version, model name, input reference, response summary/full safe response, score suggestion, confidence if available, reviewer decision, timestamps, and failure state.
- Avoid sending unnecessary sensitive data. Do not log API keys.

### Entrance Exam Access

Approved direction:

- The audit finding is confirmed.
- Entrance exam access must be cohort-safe and application-safe.

Implementation consequence:

- A token or email link alone is not enough.
- The authenticated user must have a matching diploma cohort application/invitation.
- Duplicate attempts must be blocked by service validation and database constraints.

### Assessment Access

Approved direction:

- The audit finding is confirmed.
- Assessments must not become globally accessible when `course_version` is null.

Implementation consequence:

- Every normal assessment must have an explicit scope.
- Scope can be cohort/diploma semester/course version depending on the assessment type.
- Student access is always derived from active, non-expired enrollment.

### Moderator And Teacher Assignment Scope

Approved direction:

- Moderators are assigned to groups of students per cohort.
- A moderator has view/grading/support scope over all assigned students across any cohort where those student assignments exist.
- Teachers are assigned to cohorts, not individual students.
- A teacher has full view/grading scope over their assigned cohort.
- Admin can assign moderators to students.
- Adding a moderator assignment merges with existing assignments; it must not delete or replace previous assignments unless an explicit remove action is used.

Implementation consequence:

- Add explicit assignment models.
- Add merge/add behavior for moderator assignment.
- Add separate remove/revoke behavior with audit trail.
- Enforce direct object access checks for all grading/student/progress views.

### Storage And File Access

Approved direction:

- The audit finding is confirmed.
- Protected files must not be served by public/unsigned URLs.

Implementation consequence:

- File access must go through permission checks before signed URL generation.
- Local fallback should be development-only or use a signed/proxy route.

### Payment Upload Atomicity

Approved direction:

- The audit finding is confirmed.

Implementation consequence:

- Payment CSV upload must be atomic per file.
- Malformed CSVs must not leave ambiguous pending upload artifacts unless deliberately saved as rejected/failed upload history with row-level errors.

### Deployment

Approved direction:

- Phase 9 is pending and has not started.

Implementation consequence:

- Keep deployment hardening in the plan, but do not block current domain repair work on Phase 9.
- Deployment tasks remain mandatory before final production readiness.

### Certificates

Approved direction:

- The audit finding is confirmed.

Implementation consequence:

- Arabic PDF generation must be verified.
- Broad exceptions must be replaced or logged safely.
- Placeholder certificate language and fonts must support Arabic.

### Audio Lectures

Approved direction:

- The audit finding is confirmed.
- Audio lectures should use FFmpeg.
- Store the original uploaded audio file for authorized download.
- Generate compressed HLS output without unnecessary quality loss.
- Split audio into `.m3u8` playlist and segments for fast CDN/R2 delivery.
- Serve HLS from Cloudflare R2/CDN through permission-gated signed access.

Implementation consequence:

- Add an audio processing pipeline.
- Store original and HLS derivative metadata.
- FFmpeg processing can start synchronous for small files only if acceptable, but should be queue-ready.
- Do not expose raw R2 keys to students.

### UI Design System

Approved direction:

- The current operational design remains one global design.
- The landing page design/theme becomes a second global design.
- The user/admin should be able to switch/apply one of the two global designs.
- The selected design is saved client-side until a final choice is made.
- Progressive rendering/loading behavior should remain.

Implementation consequence:

- Create a new UI rules document for the dual-theme system.
- Do not replace operational LMS clarity with marketing-style layouts in admin/student workflows.
- The landing-derived design can supply colors, typography mood, surfaces, and public-facing polish, but admin/student screens must remain usable and dense enough for repeated work.

### Linting And Cleanup

Approved direction:

- Ruff/lint/import cleanup is confirmed.
- Linting and similar mechanical cleanup should be planned as the last step in a phase/fix cycle, but must not be omitted.

Implementation consequence:

- Domain correctness comes first.
- Final quality gate still requires Ruff, Django check, migration check, pytest, and relevant UI/E2E checks to pass.

## Revised Recovery Phases

### Recovery Phase 0 - Documentation And SRS Alignment

Goal:

Align the written project rules with the stakeholder decisions before code changes begin.

Tasks:

1. Update `docs/LLM_IMPLEMENTATION_GUARDRAILS.md`:
   - Replace old Gemini exclusivity rule.
   - Add Gemini assistance/suggestion model for entrance exams and learning assessments.
   - Clarify final grade authority and audit requirements.
   - Clarify registration identity versus program application.

2. Update `docs/FULL_PROJECT_IMPLEMENTATION_PROPOSAL.md` and `docs/PHASED_IMPLEMENTATION_EXECUTION_PLAN.md`:
   - Registration may create user/person without program application.
   - First registration may optionally include one course or one diploma application.
   - Dashboard applications create child program/course requests.
   - Entrance exams are attached to diploma cohort applications.

3. Add/update SRS addendum:
   - Define root registration/person record.
   - Define course application and diploma cohort application.
   - Define entrance exam invitation, pass/fail, and email outcomes.
   - Define teacher/moderator assignment boundaries.
   - Define audio HLS/R2 delivery.
   - Define dual UI theme requirement.

4. Add acceptance criteria for every revised rule.

Exit criteria:

- All docs agree on identity/application model.
- All docs agree on Gemini assistance scope.
- No document still says Gemini is forbidden for all non-entrance contexts without noting supersession.

### Recovery Phase 1 - Identity, Registration, And Program Applications

Goal:

Implement the correct person/registration and program application model.

Tasks:

1. Data model:
   - Keep or create a root registration/person record tied 1:1 to `StudentProfile`/user.
   - Add separate program application/request records for:
     - diploma cohort application
     - course application
   - Add status fields for program requests.
   - Add uniqueness/constraints to block duplicate pending/active requests for the same course/diploma/cohort.

2. Registration form:
   - Keep all personal fields mandatory.
   - Add optional application section.
   - Allow selecting either one course or one diploma/cohort, not both.
   - If no program is selected, create only the user/person/profile/root registration.

3. Dashboard apply flow:
   - Allow logged-in users to apply for courses/diplomas.
   - Enforce no pending application or active enrollment for the same target.
   - Show clear student-facing status.

4. Admin application queues:
   - Show only program applications/requests requiring action.
   - Do not show plain registrations as active applicants unless they applied.

5. Migration/data repair:
   - Map existing `Application` rows into root registration plus child requests.
   - Preserve historical statuses and audit.

6. Tests:
   - Register without program creates no admin application queue item.
   - Register with course creates one course request.
   - Register with diploma/cohort creates one diploma cohort request.
   - Register with both course and diploma is rejected.
   - Dashboard duplicate pending/active target is blocked.
   - Historical records remain visible.

7. Final quality gate:
   - Run targeted tests.
   - Run full tests after migration is stable.
   - Run Ruff as the last cleanup step for this phase.

### Recovery Phase 2 - Entrance Exam Boundary And Cohort Admissions

Goal:

Make entrance exams a separate, cohort-safe admissions workflow.

Tasks:

1. Create or refactor to `apps/entrance_exams`.
2. Model entrance exam ownership:
   - diploma
   - cohort
   - program application/request
   - invitation
   - attempt
   - answer
   - Gemini review/suggestion
   - manual review
   - final pass/fail result

3. Entrance MCQ behavior:
   - MCQ choices are answer options, not correct/incorrect flags.
   - Store scoring rubric/model answer/instructions per exam/question.
   - Send structured prompt to Gemini for weighted scoring suggestion.
   - Store suggestion separately from final staff-approved score.

4. Written entrance behavior:
   - Written answers remain pending manual review.
   - Gemini may provide assistance only if enabled and recorded as suggestion, not hidden final authority.

5. Access/invitation:
   - Admin sends invitation emails by diploma cohort.
   - Link must require authenticated user and matching invited application.
   - Token cannot create assignment for unrelated users.

6. Result workflow:
   - Final score computed after required reviews.
   - Pass/fail based on configured score.
   - Application status updates based on pass/fail/admin decision.
   - Admin can bulk send pass/fail templates after review.

7. Tests:
   - User with copied token but no invitation is denied.
   - Same user cannot submit twice for the same application/cohort.
   - Future cohort application has independent entrance attempt history.
   - Gemini failure does not block submission.
   - Staff can accept/adjust/reject Gemini suggestion.
   - Bulk success/failure email recipients match pass/fail results.

8. Final quality gate:
   - Targeted entrance tests.
   - Full tests.
   - Ruff cleanup last.

### Recovery Phase 3 - Gemini Assistance Platform

Goal:

Build a reusable, auditable Gemini assistance layer for entrance exams and learning assessments.

Tasks:

1. Add AI app/service boundary:
   - Gemini client
   - prompt templates and versions
   - request log
   - response log
   - failure handling
   - safe redaction

2. Add prompt management:
   - Entrance MCQ prompt.
   - Entrance written review assistance prompt if enabled.
   - Assessment/exam grading assistance prompt.
   - Prompt version labels for performance comparison.

3. Add review result model:
   - target type and target id
   - prompt version
   - suggested score
   - suggested feedback
   - raw/safe response
   - final staff decision
   - accepted/adjusted/rejected state

4. Add staff UI:
   - "Get Gemini suggestion"
   - display suggestion next to manual grading fields
   - accept suggestion
   - adjust score/feedback
   - ignore suggestion

5. Add analytics foundation:
   - Compare Gemini suggested score with final staff score.
   - Track prompt version performance.

6. Tests:
   - API success/failure/timeouts.
   - Prompt version stored.
   - Suggestions do not alter final grade unless staff action applies them.
   - Secrets are never logged.

7. Final quality gate:
   - Mocked Gemini tests.
   - No external API dependency in CI.
   - Ruff cleanup last.

### Recovery Phase 4 - Assessment, Course, And Cohort Access Scoping

Goal:

Remove global access paths and enforce enrollment-derived access.

Tasks:

1. Make assessment scope explicit:
   - course version
   - diploma cohort
   - semester within cohort
   - final exam scope

2. Update models/forms:
   - Prevent creating active assessment without valid scope.
   - Use model/form/service validation.

3. Update services/selectors:
   - Remove global `course_version is null` access.
   - Derive access from active, non-expired enrollment.
   - Validate status, time window, attempts, and ownership inside service transactions.

4. Update templates:
   - Show only accessible assessments.
   - Show clear messages for expired/restricted/no enrollment.

5. Tests:
   - No-scope assessment is not startable.
   - Cohort assessment visible only to active cohort students.
   - Course assessment visible only to active course/version students.
   - Expired/restricted access cannot start or submit.

6. Final quality gate:
   - Targeted assessment tests.
   - Full tests.
   - Ruff cleanup last.

### Recovery Phase 5 - Teacher And Moderator Assignment Scoping

Goal:

Implement the approved staff permission model.

Tasks:

1. Add assignment models:
   - `TeacherCohortAssignment`
   - `ModeratorStudentAssignment`
   - Include cohort on moderator assignment.
   - Include active/revoked status and audit metadata.

2. Admin assignment UI:
   - Assign teacher to cohort.
   - Assign moderator to one or many students within cohort.
   - Add/merge behavior for moderator assignment.
   - Separate remove/revoke action.

3. Permission policies:
   - `is_teacher_of_cohort(user, cohort)`
   - `is_moderator_for_student(user, student, cohort=None)`
   - `can_grade_attempt(user, attempt)`
   - `can_view_student_progress(user, student, cohort=None)`

4. Apply policies:
   - assessment grading queue
   - homework queue
   - entrance exam review
   - student progress/support screens
   - direct detail URLs

5. Tests:
   - Moderator sees only assigned students.
   - Moderator assignments merge without deleting existing rows.
   - Teacher sees full assigned cohort.
   - Teacher cannot see unassigned cohort.
   - Direct URL bypass attempts fail.

6. Final quality gate:
   - Permission matrix tests.
   - Full tests.
   - Ruff cleanup last.

### Recovery Phase 6 - Protected Storage And Audio HLS Pipeline

Goal:

Make file storage private, signed, CDN/R2-backed, and suitable for audio streaming.

Tasks:

1. Storage service:
   - Require permission context or object context before generating protected URLs.
   - Add signed local/proxy fallback for development.
   - Fail closed in production if protected storage is local.

2. Homework/certificate downloads:
   - Generate signed URLs only after permission checks.
   - Avoid raw R2 keys in templates.

3. Audio models:
   - Store original file key.
   - Store original metadata: filename, content type, size, checksum.
   - Store HLS playlist key.
   - Store segment prefix.
   - Store processing status and errors.

4. FFmpeg processing:
   - Validate uploaded audio.
   - Store original.
   - Compress/transcode to HLS `.m3u8` and segments.
   - Upload playlist and segments to R2.
   - Keep process queue-ready.

5. Student playback:
   - Permission-check lecture access.
   - Generate signed playlist/segment access or controlled CDN strategy.
   - Provide original download only if allowed.

6. Tests:
   - Wrong user cannot get homework/certificate/audio URLs.
   - FFmpeg command is called with expected arguments in mocked tests.
   - HLS metadata saved.
   - Storage failure does not create successful lecture media state.

7. Final quality gate:
   - Storage/audio tests.
   - Full tests.
   - Ruff cleanup last.

### Recovery Phase 7 - Payments, Certificates, And Audit Hardening

Goal:

Close confirmed operational correctness gaps.

Tasks:

1. Payment CSV:
   - Make file processing atomic.
   - Preserve failed upload history only when explicitly modeled as failed.
   - Row-level exact errors.

2. Certificates:
   - Verify Arabic PDF rendering.
   - Add Arabic placeholder template.
   - Embed or configure Arabic-capable fonts.
   - Replace broad exceptions with safe logging and targeted catches.

3. Audit:
   - Make audit/history append-only in practice.
   - Add before/after/reason fields for overrides where needed.
   - Avoid secrets/raw file contents in metadata.

4. Tests:
   - Malformed payment CSV behavior.
   - Arabic certificate render smoke.
   - Audit event immutability or no-edit/no-delete application paths.

5. Final quality gate:
   - Targeted tests.
   - Full tests.
   - Ruff cleanup last.

### Recovery Phase 8 - Dual UI Theme System

Goal:

Support two global designs while keeping operational screens usable.

Tasks:

1. Create new UI rules document:
   - Operational LMS design.
   - Landing Heritage design.
   - Shared component rules.
   - Theme token mapping.
   - Client-side theme persistence.

2. Implement theme switch:
   - Client-side saved choice.
   - No backend data mutation required initially.
   - Body/html theme attribute or class.

3. Tokenize design:
   - colors
   - typography
   - surfaces
   - buttons
   - cards
   - tables
   - badges

4. Progressive rendering/loading:
   - Keep HTMX-safe URL rules.
   - Use loading states consistently.

5. UI cleanup:
   - Replace remaining raw date filters.
   - Replace one-off controls with partials.
   - Keep admin/student screens simple and readable.

6. Tests/checks:
   - Arabic RTL and English LTR.
   - 360px mobile.
   - Both themes render core screens.
   - No text overflow in buttons/tables/cards.

7. Final quality gate:
   - Template scan.
   - UI smoke screenshots if tooling is available.
   - Ruff/static checks last.

### Recovery Phase 9 - Deployment And Final Production Readiness

Goal:

Complete pending production deployment work after core domain repairs.

Tasks:

1. CI/CD:
   - Deploy depends on passing CI.
   - Blocking health check.
   - Align secret names.
   - Add rollback notes.

2. Server:
   - PostgreSQL.
   - Reverse proxy.
   - TLS.
   - App service.
   - Static files.
   - R2 credentials.
   - SMTP credentials.
   - Gemini credentials.

3. Operations:
   - Backup script.
   - Scheduled backups.
   - Restore test.
   - Logging and monitoring plan.
   - Security checklist.

4. Final quality gate:
   - `python manage.py check --deploy`.
   - `python manage.py check`.
   - `python manage.py makemigrations --check --dry-run`.
   - `python -m pytest -q`.
   - `python -m ruff check .`.
   - E2E smoke suite or documented manual acceptance run.

## Updated Completion Checklist

- [ ] Main registration creates user/person with mandatory personal fields.
- [ ] First registration optionally creates exactly one course or diploma/cohort application.
- [ ] First registration cannot apply to both course and diploma.
- [ ] Registration without program does not create active admin applicant queue item.
- [ ] Dashboard application flow blocks duplicate pending application or active enrollment for same target.
- [ ] Entrance exams are separate from normal assessments/exams.
- [ ] Entrance exams are attached to diploma cohort application flow.
- [ ] Entrance MCQs support rubric/model-answer weighted Gemini suggestion.
- [ ] Entrance written answers wait manual review, with optional recorded Gemini assistance if enabled.
- [ ] Entrance final result pass/fail drives admission email targeting.
- [ ] Gemini assistance is auditable and never hidden final authority.
- [ ] Normal assessment Gemini assistance is available for prompt evaluation/fine-tuning with staff final decision.
- [ ] Entrance exam token access is invitation/application/cohort-safe.
- [ ] Assessments without valid explicit scope are not visible/startable.
- [ ] Moderators are assigned to students per cohort with merge-add behavior.
- [ ] Teachers are assigned to cohorts.
- [ ] Staff direct URL access is policy-checked.
- [ ] Protected files use permission-gated signed access.
- [ ] Audio lecture originals and HLS derivatives are stored in R2/CDN via storage service.
- [ ] Payment CSV handling is atomic.
- [ ] Arabic certificate PDF rendering is verified.
- [ ] Dual UI theme rules are documented and implemented.
- [ ] Deployment hardening is completed in Phase 9.
- [ ] Lint/import cleanup is performed as final quality step, not skipped.
