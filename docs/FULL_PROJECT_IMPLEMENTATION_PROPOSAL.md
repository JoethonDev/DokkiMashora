# LMS Full Project Implementation Proposal

## Purpose

This proposal translates the stakeholder FRD/SRS and the requested implementation order into a full-project delivery plan. It is intentionally broader than the 2-day MVP target so that the MVP can later be selected from a clear, complete roadmap without losing architectural direction.

## Source Basis

- Document reviewed: `docs/LMS_FRD_SRS_Stakeholder_Document.docx`
- Requested implementation order:
  1. Foundation
  2. Registration, Admin Approval, and Email Sending
  3. Minimal Assessment, Handover, and Gemini Integration
  4. Diplomas, Cohorts, Courses, and Content
  5. Assessments, Grading, Homework, and R2 Integration
  6. Promotion and Certificates
  7. Full Student Profile
  8. Complete Missing Scope
  9. Complete Deployment

## Target Stack

- Backend: Django
- Frontend: Django templates, HTMX, Tailwind CSS, targeted Ajax where needed
- Database: PostgreSQL
- Object storage: Cloudflare R2
- AI integration: Gemini API
- Email: SMTP or transactional email provider abstraction
- Background work: start synchronous where acceptable, then add a lightweight queue only if needed
- Deployment: dedicated server over SSH with GitHub Actions from Phase 1
- Testing:
  - Unit tests with Django test framework or pytest-django
  - Integration tests for workflows and permissions
  - E2E tests with Playwright
  - Linting and formatting in CI

## Delivery Principles

- Build the core domain model early, even when screens are placeholders.
- Keep cohort and course version boundaries from the beginning because this is the most important business rule.
- Add deployment automation from Phase 1 so every phase can be tested on the target-style environment.
- Add tests gradually, not at the end.
- Preserve historical records instead of overwriting student/application/enrollment data.
- Keep the admin workflow practical and operational before adding advanced UX polish.
- Defer heavy reporting, payment gateway, full DRM, mobile app, forums, parent accounts, and proctoring unless scope changes.
- **Registration identity is 1:1 with a person.** First registration may optionally include one course or one diploma application. Dashboard applications create child program request records. (Per Stakeholder Decision Addendum 2026-06-20.)
- **Gemini is an assistance/suggestion layer** available for entrance exams and normal assessment review, never hidden final authority. (Per Stakeholder Decision Addendum 2026-06-20.)

## Suggested Architecture

### Django Apps

- `accounts`
  - Custom user model or user profile extension
  - Roles: Super Admin, Admin, Moderator, Teacher, Student
  - Permissions and role-based access helpers

- `students`
  - Student profile
  - Full registration fields
  - Student lifecycle history

- `applications`
  - Registration/application submissions
  - Duplicate account matching
  - Admission statuses
  - Admin approval/rejection workflow

- `programs`
  - Diplomas
  - Semesters
  - Cohorts
  - Standalone courses
  - Course versions

- `content`
  - Lectures
  - Audio files
  - YouTube embeds
  - Weekly release scheduling
  - Content access rules

- `assessments`
  - Entrance assessments
  - Quizzes, exams, final exams
  - MCQ and written questions
  - Attempts, answers, scores, feedback
  - Homework uploads

- `grading`
  - Manual review queues
  - Moderator/teacher grading
  - Score overrides
  - Gemini-assisted MCQ scoring records

- `communications`
  - Email templates
  - Template variables
  - Email preview
  - Bulk send records

- `payments`
  - CSV upload
  - Matching by national ID, email, or phone
  - Matched/unmatched result records

- `certificates`
  - Certificate templates
  - Generated PDF records
  - Student certificate access

- `audit`
  - Important admin actions
  - Access changes
  - Promotion/demotion/completion decisions
  - Email send history

- `deployment`
  - Health checks
  - Operational settings
  - Backup scripts or management commands

## Phase 1: Foundation

### Goal

Create the base project structure, permissions foundation, deployment pipeline, and placeholder surfaces so later phases can attach real workflows without rework.

### Scope

- Initialize Django project and core settings.
- Add environment-based settings for local, staging, and production.
- Configure PostgreSQL.
- Add Tailwind/HTMX frontend base.
- Create base templates and navigation shell.
- Add authentication.
- Add role model or permission groups:
  - Super Admin
  - Admin
  - Moderator
  - Teacher
  - Student
- Add placeholder dashboards:
  - Admin dashboard
  - Moderator dashboard
  - Teacher dashboard
  - Student portal
- Add base models that should exist early:
  - Student profile
  - Application
  - Diploma
  - Cohort
  - Course
  - Enrollment
  - Assessment shell
  - Email template shell
- Add audit/history base tables or reusable mixins.
- Add global access-control decorators/mixins/helpers.
- Add GitHub repository workflow expectations.
- Add GitHub Actions deployment over SSH.
- Add linting, formatting, and test commands.

### Deployment From Phase 1

GitHub Actions should support:

- Running lint checks.
- Running unit tests.
- Building static assets.
- Connecting to the dedicated server over SSH.
- Pulling latest code or uploading release artifact.
- Installing dependencies.
- Running migrations.
- Collecting static files.
- Restarting the app service.
- Running a post-deploy health check.

Required GitHub secrets:

- `SSH_HOST`
- `SSH_PORT`
- `SSH_USER`
- `SSH_PRIVATE_KEY`
- `DEPLOY_PATH`
- `DJANGO_SECRET_KEY`
- `DATABASE_URL`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`

### Testing

- Unit tests for user creation and role assignment.
- Permission smoke tests for each dashboard.
- CI check for migrations.
- Basic Playwright smoke test:
  - Login page loads.
  - Admin dashboard loads for admin.
  - Student portal loads for student.

### Deliverables

- Running Django project.
- Role-aware dashboards, even if most pages are placeholders.
- First automated deploy workflow.
- CI running lint and tests.
- Basic project README with local setup and deployment notes.

## Phase 2: Registration, Admin Approval, and Email Sending

### Goal

Implement the first real business workflow: student registration (with duplicate rejection), application review, approval/rejection, enrollment activation, and email sending with editable templates.

### Scope

- Public registration/application form with approved fields:
  - Full name
  - Date of birth
  - Age
  - Gender
  - Address
  - Mobile number
  - Email
  - Educational qualification
  - Specialization
  - Workplace
  - Current job
  - Church affiliation
  - Priest name
  - Priest contact
  - National ID
- Program selection during application.
- Duplicate detection and rejection (never merge or update) using:
  - National ID
  - Email
  - Phone number
- Application statuses:
  - Submitted
  - Under Review
  - Invited to Entrance Assessment
  - Accepted
  - Rejected
  - Activated/Enrolled
- Admin application list, filters, detail page, and edit capability.
- Admin approve/reject actions.
- Admin enrollment activation into selected diploma/course.
- Email utility:
  - Template model
  - Template variables
  - Render preview
  - Send single email
  - Send bulk email
  - Record sent email history
- Initial email templates:
  - Registration received
  - Entrance assessment invitation
  - Acceptance
  - Rejection
  - Account activation

### Testing

- Unit tests for duplicate matching.
- Unit tests for email template rendering.
- Permission tests for admin-only application review.
- Integration tests for registration to application creation.
- E2E test:
  - Student submits application.
  - Admin reviews and approves.
  - Student can log in or receive activation path.

### Deliverables

- Complete registration flow.
- Admin approval workflow.
- Email rendering and sending utility.
- Email history records.

## Phase 3: Minimal Assessment, Handover, and Gemini Integration

### Goal

Implement a lean but real assessment path early so admissions and AI-assisted scoring can be validated before the larger assessment system is expanded.

### Scope

- Entrance assessment model linked to diploma cohort.
- Assessment open time, close time, and timer duration.
- One submission per student per cohort.
- MCQ question support.
- Written question support.
- Question-level points.
- Empty answers score zero.
- Basic student assessment-taking screen.
- Basic submission locking.
- Admin/teacher review screen for written answers.
- Gemini integration for configured MCQ assistance:
  - API client abstraction
  - Prompt template stored in code or configuration
  - Request/response logging
  - Error handling and fallback to manual review
  - Admin override of AI-assisted result
- Minimal handover documentation:
  - How to run locally
  - How to deploy
  - How to configure Gemini
  - How to create assessment data

### Testing

- Unit tests for assessment timing.
- Unit tests for one-submission-per-cohort rule.
- Unit tests for scoring.
- Mocked Gemini API tests.
- Integration test for assessment submission.
- E2E test:
  - Admin opens assessment.
  - Student submits.
  - Admin reviews result.

### Deliverables

- Working entrance assessment flow.
- Basic Gemini-assisted MCQ path.
- Manual review and override path.
- Initial handover notes.

## Phase 4: Diplomas, Cohorts, Courses, and Content

### Goal

Build the core academic structure and content access model, including cohort/version separation.

### Scope

- Diploma management.
- Dynamic semesters per diploma.
- Cohort creation per diploma.
- Cohort-as-version boundaries for:
  - Students
  - Lectures
  - Assessments
  - Grades
  - Progress
- Standalone course management.
- Course versioning.
- Course access duration in days after activation.
- Enrollment management:
  - Diploma cohort enrollment
  - Course version enrollment
  - Activation/deactivation/restriction
- Content organization:
  - Diploma/course
  - Cohort/version
  - Semester/module
  - Lecture order
- YouTube embed support.
- Audio link placeholder or temporary local storage until R2 is completed.
- Weekly lecture release scheduling.
- Student content page showing only allowed content.
- Expired access behavior:
  - Student can log in.
  - Student can see profile/progress/certificates.
  - Student cannot access expired materials.

### Testing

- Unit tests for cohort isolation.
- Unit tests for course access expiry.
- Permission tests for content visibility.
- E2E test:
  - Admin creates diploma/cohort/content.
  - Admin enrolls student.
  - Student sees assigned content only.

### Deliverables

- Real academic structure.
- Cohort-safe content model.
- Student access to approved content.
- Course expiry enforcement.

## Phase 5: Assessments, Grading, Homework, and R2 Integration

### Goal

Expand the minimal assessment system into the full MVP assessment and grading module, then move file/media handling to Cloudflare R2.

### Scope

- Assessment types:
  - Entrance assessment
  - Quiz
  - Semester exam
  - Additional assessment/homework
  - Final exam
- Question types:
  - MCQ
  - Written
  - Mixed
- MCQ options and correct answers.
- Optional MCQ penalty rules, disabled by default.
- Toggle showing correct answers.
- Toggle showing written feedback.
- Maximum attempts, default one.
- Grade visibility states:
  - Corrected
  - Under Review
  - Hidden until released
- Manual grading queues for teachers/moderators.
- Admin score override.
- Homework uploads:
  - PDF/DOCX
  - Configurable max file size, recommended 10 MB or 25 MB
  - Staff review and grading
- Cloudflare R2 integration:
  - Audio lecture upload
  - Homework upload storage
  - Private/signed access where needed
  - File validation
  - Storage error handling
- Media handling:
  - Do not store large files permanently on VPS disk.
  - Keep metadata in database.
  - Store files in R2.

### Testing

- Unit tests for scoring, penalties, attempts, and visibility.
- Unit tests for homework validation.
- Mocked R2 storage tests.
- Integration tests for upload and download permissions.
- E2E test:
  - Student takes quiz.
  - Student uploads homework.
  - Teacher grades written/homework.
  - Student sees final status.

### Deliverables

- Full assessment system.
- Staff grading workflows.
- R2-backed audio and homework files.
- Correct student grade visibility.

## Phase 6: Promotion and Certificates

### Goal

Complete the academic lifecycle from semester progress to final completion and certificate generation.

### Scope

- Diploma-level passing configuration.
- Semester promotion rules based on:
  - Exams
  - Additional assessments
  - Configured passing percentages
- Automatic promotion where configured.
- Admin manual actions:
  - Promote
  - Demote
  - Override
  - Temporarily lock student
  - Retake handling
- Final exam flow after passing all semesters.
- Manual graduation/completion by admin after final exam review.
- Certificate template management.
- PDF certificate generation.
- Arabic text support for generated PDFs.
- Student certificate access.
- Certificate generation history.

### Testing

- Unit tests for promotion rules.
- Unit tests for manual overrides.
- Unit tests for final exam completion prerequisites.
- PDF generation smoke tests.
- Integration test for full lifecycle:
  - Enrolled
  - Passed semester
  - Final exam
  - Completed
  - Certificate generated

### Deliverables

- Promotion lifecycle.
- Completion/graduation workflow.
- Generated certificate PDFs.

## Phase 7: Full Student Profile

### Goal

Make the student portal complete, useful, and historically accurate.

### Scope

- Profile details and editable allowed fields.
- Applications history.
- Diploma enrollments.
- Course enrollments.
- Active access list.
- Expired access list.
- Assessment attempts.
- Grades and feedback.
- Homework submissions.
- Promotion history.
- Certificates.
- Payment status where applicable.
- Email/notification history where appropriate.
- Reapply flow for future cohorts or new programs.
- Clear access messaging:
  - Active
  - Expired
  - Restricted
  - Under review
  - Completed

### Testing

- Permission tests ensuring students only see their own data.
- Unit tests for profile history aggregation.
- E2E test for student portal navigation.

### Deliverables

- Complete student-facing profile and history area.
- Accurate visibility after access expiry.

## Phase 8: Complete Missing Scope

### Goal

Close remaining MVP gaps, improve operational completeness, and prepare for production acceptance.

### Scope

- Payment CSV upload:
  - Match by national ID
  - Fallback to email or phone
  - Matched/unmatched rows
  - Failed rows
  - Upload history
- Bulk email finalization:
  - Recipient preview
  - Template preview
  - Bulk send confirmation
  - Send history
- Admin audit improvements:
  - Access changes
  - Grading changes
  - Promotion/demotion
  - Email sending
  - Payment uploads
- Dashboard refinements.
- Search and filters across admin screens.
- Better form validation and user messages.
- Responsive polish for common mobile browsers.
- Data import helpers if needed.
- Documentation completion:
  - Admin guide
  - Deployment guide
  - Backup/restore notes
  - Known limitations
- Acceptance criteria review against stakeholder document.

### Testing

- Regression test suite.
- Permission matrix tests.
- E2E tests for core workflows:
  - Registration to approval
  - Assessment and grading
  - Content access
  - Promotion and certificate
  - Payment CSV upload
  - Bulk email preview/send
- Load-sensitive smoke checks for dashboard queries.

### Deliverables

- Complete documented MVP scope.
- Production readiness checklist.
- Stakeholder acceptance checklist.

## Phase 9: Complete Deployment

### Goal

Harden the production deployment, monitoring, backup, and release process.

### Scope

- Dedicated server provisioning.
- Production stack:
  - Linux server
  - PostgreSQL
  - App service
  - Reverse proxy
  - TLS certificate
  - Static file serving
  - Media via R2
- GitHub Actions production deployment:
  - Lint
  - Tests
  - Build assets
  - Deploy over SSH
  - Migrate database
  - Collect static
  - Restart service
  - Health check
  - Rollback notes or previous-release strategy
- Environment variable management.
- Database backup procedure:
  - Scheduled backups
  - Retention policy
  - Restore test
- Application logging.
- Error reporting plan.
- Security hardening:
  - HTTPS
  - Secure cookies
  - CSRF settings
  - Allowed hosts
  - Admin URL considerations
  - Server firewall
  - Least-privilege deploy user
- Operational handover:
  - Admin credentials process
  - Deployment process
  - Backup restore process
  - R2 credentials process
  - Email provider setup
  - Gemini key setup

### Testing

- Production health check after deploy.
- Smoke E2E tests against staging.
- Backup restore test on non-production database.
- Security configuration checklist.

### Deliverables

- Production deployment.
- Automated CI/CD.
- Backup and restore procedure.
- Final handover documentation.

## Cross-Cutting Requirements

### Permissions

Every protected action must be checked server-side. UI hiding is not enough.

Role expectations:

- Super Admin: full access.
- Admin: operational access to students, applications, content, payments, emails, and access control.
- Moderator: assigned students, grading, homework review, progress support.
- Teacher: assigned assessment/written answer review.
- Student: own applications, content, assessments, progress, grades, and certificates.

### History and Audit

The system should preserve:

- Applications.
- Account reuse mapping.
- Cohort and course enrollments.
- Assessment attempts.
- MCQ and written grades.
- Staff grading changes.
- Admin overrides.
- Homework uploads and grades.
- Promotion, demotion, lock, graduation.
- Certificates.
- Access activation/deactivation/expiry/restriction.
- Email sends.
- Payment CSV uploads.

### Access Expiry

Expired students can:

- Log in.
- View profile.
- View progress.
- View grades.
- View certificates.
- Apply again.

Expired students cannot:

- Access expired lectures.
- Take expired quizzes/exams.
- Access expired course/diploma materials.

### Testing Strategy

Testing should be introduced from the first phase and expanded as modules become real.

Recommended test layers:

- Unit tests for model rules and service functions.
- Integration tests for workflow behavior.
- Permission tests for protected views.
- Mocked external-service tests for Gemini, R2, and email.
- E2E tests for user-critical paths.
- Migration checks in CI.

Minimum recurring CI checks:

- Code formatting.
- Linting.
- Unit tests.
- Migration check.
- Static asset build.
- Playwright smoke tests where environment allows.

### Linting and Quality

Recommended tooling:

- Python formatting: Black or Ruff formatter.
- Python linting: Ruff.
- Django checks: `python manage.py check`.
- Template linting: optional, added after templates stabilize.
- JavaScript linting: only if custom JS grows beyond small HTMX helpers.

### External Integrations

#### Email

Use an internal email service wrapper so provider changes do not affect business logic.

Required capabilities:

- Render template.
- Preview rendered output.
- Send one email.
- Send bulk emails.
- Record delivery attempt.
- Record failures.

#### Gemini

Use Gemini through a dedicated service wrapper.

Required capabilities:

- Configurable API key.
- Prompt template for scoring rules.
- Request/response logging.
- Timeout and failure handling.
- Manual override.
- No dependency on Gemini for written answer grading.

#### Cloudflare R2

Use a storage abstraction and keep R2 credentials outside source control.

Required capabilities:

- Upload audio.
- Upload homework files.
- Validate file type and size.
- Generate protected access URLs if needed.
- Avoid permanent VPS media storage.

## Suggested Full-Project Timeline

The stakeholder document estimates:

- Fast MVP: 7 weeks
- Standard MVP: 8-9 weeks
- Extended MVP: 10+ weeks

Using the requested implementation order and adding CI/CD, tests, R2, Gemini, certificates, and production hardening, a realistic full-project plan is:

| Phase | Estimated Duration |
|---|---:|
| Phase 1: Foundation | 4-6 days |
| Phase 2: Registration/Admin/Email | 5-7 days |
| Phase 3: Minimal Assessment/Gemini/Handover | 5-7 days |
| Phase 4: Programs/Content | 7-10 days |
| Phase 5: Full Assessments/Grading/Homework/R2 | 10-14 days |
| Phase 6: Promotion/Certificates | 5-8 days |
| Phase 7: Full Student Profile | 4-6 days |
| Phase 8: Missing Scope/Acceptance | 5-8 days |
| Phase 9: Complete Deployment | 3-5 days |

Expected total: about 8-11 weeks depending on feedback speed, content readiness, certificate template complexity, and deployment environment readiness.

## Proposed 2-Day MVP Slice To Discuss Later

The following is not the full project. It is only a candidate slice we can refine after this proposal is approved.

### Day 1

- Django project setup.
- Base models.
- Auth and roles.
- Admin/student dashboard placeholders.
- GitHub Actions skeleton.
- Registration form.
- Application creation.
- Admin application list/detail.
- Email template utility skeleton.

### Day 2

- Admin approve/reject/enroll.
- Basic entrance assessment model and screen.
- Simple MCQ/written submission.
- Manual review placeholder.
- Gemini service wrapper with mocked or real configurable call.
- Minimal student portal showing application/enrollment/assessment status.
- Basic tests and handover notes.

## Open Decisions Needed

- Will the project use a custom Django user model from the start?
- Is the dedicated server already available, or should Phase 1 include provisioning notes only?
- Which email provider or SMTP service will be used?
- Will Gemini be required in the 2-day MVP as a real API call or as a mocked integration with configuration ready?
- Should R2 be configured with real credentials during development or only wired through environment variables first?
- Are certificate templates already available?
- What is the exact first workflow that stakeholders must see after 2 days?

## Recommended Next Step

Use this full-project plan as the master roadmap, then mark each item as one of:

- `2-day MVP`
- `Post-MVP but required`
- `Full-project completion`
- `Deferred`

That will let us protect the 2-day deadline while still building the foundation in the right direction.

---

## Fix Order — Violations V001–V200

### Block 1 — Deployment & hardening (P0)
- [X] V001: `healthcheck` management command exists
- [X] V002: `/healthz/` HTTP endpoint
- [X] V003: backup/restore CLI commands
- [X] V004: `LOGGING` dict in `prod.py`
- [X] V065: `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`
- [X] V066: `SECURE_PROXY_SSL_HEADER`
- [X] V067: `SECURE_SSL_REDIRECT`
- [X] V068: `ADMIN_URL` env var
- [X] V069: Deploy workflow secret renames
- [X] V070: `needs: test` gate on deploy
- [X] V071: Removed `continue-on-error: true` from `check --deploy`
- [X] V072: Server-side `collectstatic` in deploy workflow
- [X] V073: `docs/DEPLOYMENT.md` with backup/restore/rollback/handover
- [X] V074: `docs/DEPLOYMENT.md` handover notes section
- [X] V190: CI `makemessages --no-obsolete` step
- [X] V191: CI `compilemessages` step
- [X] V192: Deploy workflow SSH secret renamed
- [X] V198: Health check `|| true` removed
- [X] V199: `check --deploy` `continue-on-error` removed
- [X] V200: Server collectstatic added

### Block 2 — Entrance exams (P0)
- [X] V021: `EntranceAttempt.cohort` FK field
- [X] V022: `unique_attempt_per_student_exam_cohort` constraint
- [X] V023: Token expiry (`valid_until`) and revocation (`revoked_at`)
- [X] V024: `admin_bulk_send_results` view
- [X] V100: `gemini_prompt_version` CharField on `EntranceExam`/`Assessment`

### Block 3 — Assessment scope (P0)
- [X] V034: Removed `course_version__isnull=True` escape hatch
- [X] V035: Two `CheckConstraint`s on assessment models
- [X] V036: Migration `0004`

### Block 4 — Staff assignment (P0)
- [X] V037: `TeacherCohortAssignment` model
- [X] V038: `ModeratorStudentAssignment` model
- [X] V089: Active-only unique constraints on assignments
- [X] V090: Merge-add `assign_moderator_to_students` service
- [X] V091: `TeacherCohortAssignment` active constraint
- [X] V092: `ModeratorStudentAssignment` active constraint
- [X] V093: Merge-add behavior tests

### Block 5 — Storage & permissions (P0)
- [X] V039: `get_download_url(*, user, obj)` enforces permission
- [X] V040: `R2_SIGNED_URL_TTL=600` setting
- [X] V045: Assessment views pass `request.user` to signed URLs

### Block 6 — Audio & HLS (P1)
- [X] V094: Admin fieldsets for `Lecture` audio fields
- [X] V095: `get_lecture_playback_url()` service
- [X] V096: Returns signed R2 URL
- [X] V097: Lecture audio tests

### Block 7 — Hardening & misc (P1)
- [X] V041: Assessment scope proper tests
- [X] V042: Grade/release/penalty logic tests
- [X] V043: Attempt status transition validators
- [X] V044: File blocklist (`.exe` rejected)
- [X] V046: Max-attempts default enforcement
- [X] V050: Certificate `__str__` methods
- [X] V051: Certificate progression tests
- [X] V052: Payment `__str__` methods
- [X] V053: Payment upload atomicity
- [X] V054: Payment normalization (`normalize_national_id`, `normalize_phone`, `parse_date`)
- [X] V055: `CertificateCounter.__str__`
- [X] V056: `CertificateTemplate.__str__`
- [X] V057: `GeneratedCertificate.__str__`
- [X] V058: `regenerate_certificate` service with reason
- [X] V061: `PaymentUpload.__str__`, `PaymentUploadRow.__str__`, `PaymentRecord.__str__`
- [X] V062: Progression model `__str__` methods + `clean()` validators
- [X] V063: `StudentDiplomaCompletion.clean()` score-range validation
- [X] V078: Ruff `I001` import ordering (auto-fixed)
- [X] V079: Ruff `UP015` unnecessary mode (auto-fixed)
- [X] V080: Ruff `F401` unused imports (auto-fixed)
- [X] V081: Ruff `F821` undefined names (entrance_exams views imports fixed)
- [X] V082: Ruff `I001` in test files (auto-fixed)
- [X] V083: `unique_active_course_enrollment` partial unique constraint
- [X] V084: `unique_active_diploma_enrollment` partial unique constraint
- [X] V085: Enrollment constraint tests
- [X] V086: Code cleanup — unused imports in progression tests
- [X] V087: Code cleanup — unused imports in certificates views
- [X] V088: Code cleanup — unused imports in assessment views

### Block 8 — i18n & Arabic (P0)
- [X] V171: `landing.html` bilingual branches → `{% trans %}`/`{% blocktrans %}`
- [X] V172: `assessments/services.py` raises wrapped in `_()`
- [X] V173: `progression/services.py` raises wrapped in `_()`
- [X] V174: `applications/services.py` raises wrapped in `_()`
- [X] V175: `certificates/services.py` raises wrapped in `_()`
- [X] V176: `payments/services.py` raises wrapped in `_()`
- [X] V177: `storage/services.py` raises wrapped in `_()`
- [X] V178: `audit/models.py` raises wrapped in `_()`
- [X] V179: `accounts/managers.py` raises wrapped in `_()`
- [X] V180: `ai/providers/gemini.py` raises wrapped in `_()`
- [X] V181: `students/views.py` raises wrapped in `_()`
- [X] V182: `applications/views.py` raises wrapped in `_()`
- [X] V183: App `verbose_name` in all `apps/*/apps.py`
- [X] V184: Locale headers fixed (`Language: ar`, `PO-Revision-Date: 2026`)
- [X] V185: CI `makemessages --no-obsolete` + `compilemessages` step
- [X] V186: Full Arabic translation coverage — **100%** (0 empty)
- [X] V187: `compilemessages` succeeds
- [X] V188: Arabic catalog compiles without warnings
- [X] V189: `SITE_NAME` vs `SITE_NAME_AR` false positive cleared

### Block 9 — UI & design system (P1)
- [X] V101: `_button.html` focus ring
- [X] V102: `_button.html` `dm-*` tokens
- [X] V103: `_field.html` `aria-describedby` IDs
- [X] V104: `_messages.html` `aria-label`
- [X] V105: `_badge.html` `dm-*` token usage
- [X] V106: Dashboard tile literal tokens → `dm-*` (`dm-brand-text`, `dm-border`, `dm-surface`)
- [X] V107: Hand-rolled buttons → `_button.html`
- [X] V108: Manual tables → `_data_table.html`
- [X] V109: Manual breadcrumbs → `_breadcrumb.html`
- [X] V110: Manual empty states → `_empty_state.html`
- [X] V111: Manual page headers → `_page.html`
- [X] V112: Manual stats → `_stat.html`
- [X] V113: Manual fields → `_field.html`
- [X] V114: RTL `text-right` → `text-end`
- [X] V115: `.flip-on-rtl` on arrow spans (~17)
- [X] V116: Status pills → `_badge.html`
- [X] V117: Search forms → `_field.html`
- [X] V118: Checkbox lists → `_field.html`
- [X] V119: Filter forms → `_field.html`
- [X] V120: `a` styled as buttons → `_button.html`
- [X] V121: `button` tags → `_button.html`
- [X] V122: One-off empty state divs → `_empty_state.html`
- [X] V123: `table` elements → `_data_table.html`
- [X] V124: Page `<h1>` + description → `_page.html`
- [X] V125: Stats cards → `_stat.html`
- [X] V126: Breadcrumb navs → `_breadcrumb.html`
- [X] V127: Form fields → `_field.html`
- [X] V128: Landing heritage nav buttons → `_button.html`
- [X] V129: Landing heritage cards → card partial if exists
- [X] V130: Landing heritage stats → `_stat.html` if applicable
- [X] V131: Entrance exam tables → `_data_table.html`
- [X] V132: Entrance exam filter fields → `_field.html`
- [X] V133: Entrance exam status badges → `_badge.html`
- [X] V134: Assessment tables → `_data_table.html`
- [X] V135: Assessment status badges → `_badge.html`
- [X] V136: Assessment breadcrumbs → `_breadcrumb.html`
- [X] V137: Progression tables → `_data_table.html`
- [X] V138: Progression breadcrumbs → `_breadcrumb.html`
- [X] V139: Progression status badges → `_badge.html`
- [X] V140: Certificate tables → `_data_table.html`
- [X] V141: Certificate breadcrumbs → `_breadcrumb.html`
- [X] V142: Certificate status badges → `_badge.html`
- [X] V143: Payment tables → `_data_table.html`
- [X] V144: Payment breadcrumbs → `_breadcrumb.html`
- [X] V145: Payment status badges → `_badge.html`
- [X] V146: Student dashboard tiles → `_stat.html`
- [X] V147: Admin dashboard tiles → `_stat.html`
- [X] V148: `text-right` → `text-end` global sweep (0 remaining)
- [X] V149: `hx-boost="true"` on `<body>` in `base.html`
- [X] V150: Alpine init after HTMX swap (`Alpine.initTree()` in `app.js`)
- [X] V151: HTMX view branching — shared `is_htmx()` utility
- [X] V152: Filter forms → `hx-get` with `hx-push-url`
- [X] V153: Review/action forms → `hx-post` with fragment targets
- [X] V154: HTMX branching in assessment views (import shared `is_htmx`)
- [X] V155: HTMX branching in progression/certificate/exams views
- [X] V156: OOB swap targets on review actions
- [X] V157: Loading states on submit buttons
- [X] V158: HTMX progress bar (`base.html`)
- [X] V159: `hx-target` on filter forms
- [X] V160: `hx-select-oob` on partial responses
- [X] V161: HTMX error handling
- [X] V162: `{% dt %}` shared template tag with `fmt` parameter
- [X] V163: `coverage.py` date format migrated to `{% dt %}`
- [X] V164: Remaining `|date:` filters → `{% dt %}`
- [X] V165: `{% dt %}` used consistently across templates
- [X] V166: Raw `datetime.*` in templates → `{% dt %}`
- [X] V167: Entrance exam Alpine tab `x-data` scope fix
- [X] V168: Mobile `grid-cols-1` fallback (8 grids)
- [X] V169: `_page.html` padding/spacing consistency
- [X] V170: `partials/_blank.html` for missing values

### Block 10 — Final validation
- [X] V193: All 36 changed files reviewed
- [X] V194: 727 tests pass (up from 561)
- [X] V195: Stale Google Fonts comment removed
- [X] V196: Emoji SVG favicon in `base.html`
- [X] V197: `python manage.py check` — clean
- [X] V198: Ruff — all checks passed (0 errors)
- [X] V199: Arabic coverage — 100% (0 empty)
- [X] V200: 17/17 partials OK, 77/77 app templates OK

---

## Validation Pass — 2026-06-23

| Check | Result |
|---|---|
| `python manage.py check` | ✅ Pass |
| `python manage.py makemigrations --check --dry-run` | ✅ Pass |
| `python -m pytest -q --ignore=tests/e2e` | ✅ **727 passed** |
| `python -m ruff check .` | ✅ **All checks passed** (0 errors) |
| `python coverage.py` | ✅ **100% Arabic** (0 empty) |
| `python manage.py compilemessages` | ✅ Pass |
| 17 shared partials present | ✅ Confirmed |
| `text-right` → `text-end` (0 remaining) | ✅ Confirmed |
| `apps.ai` in INSTALLED_APPS | ✅ Fixed |
| Imports fixed (`entrance_exams/views.py`) | ✅ Fixed |
