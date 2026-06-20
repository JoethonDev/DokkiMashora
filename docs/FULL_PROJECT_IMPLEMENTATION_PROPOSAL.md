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
