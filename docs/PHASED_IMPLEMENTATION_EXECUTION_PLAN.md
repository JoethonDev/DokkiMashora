# LMS Phased Implementation Execution Plan

## Purpose

This plan converts the project requirements and implementation decisions into detailed phases with tasks, outputs, acceptance criteria, validations, engineering rules, and implementation constraints.

The plan is for the full project, while keeping the first 2-day stakeholder target focused on a working entrance exam workflow.

## Confirmed Decisions

- The project will use Django from the start.
- The project will use a custom Django user model from the start.
- The main application language is Arabic.
- English must be supported through Django i18n.
- All date/time behavior must use Cairo/Egypt time.
- Entrance exams must be separated from normal assessments and exams.
- Entrance exams and normal assessments may share internal logic through services, validators, scoring utilities, question models, or abstract/base classes where appropriate.
- Email provider is not decided yet, but the app will send through SMTP.
- Gemini integration is required as a real API call in the 2-day MVP.
- R2 will be wired through environment variables first.
- Certificate templates are not available yet.
- Dedicated server is not available yet, but deployment automation and provisioning structure should start from Phase 1.
- The first stakeholder-visible workflow after 2 days should be entrance exam implementation.
- **Registration identity is 1:1 with a person.** The main registration creates the person record. Applying for a course or diploma is optional during first registration. Dashboard applications create child program request records, never duplicate root registrations. (Per Stakeholder Decision Addendum 2026-06-20.)
- **Gemini is an assistance/suggestion layer**, not hidden final authority. It may assist entrance exam scoring and normal assessment review. Staff final decision is always recorded. (Per Stakeholder Decision Addendum 2026-06-20, superseding earlier exclusivity rules.)
- **Two global UI themes** are required: Operational LMS and Landing Heritage. Theme choice is client-side and must not affect backend behavior. (Per docs/UI_UX_DUAL_THEME_EXTENSION.md.)

## Global Restricted Rules

These rules apply to every phase.

### Architecture And Maintainability

- Use clear separation of concerns.
- Keep views thin.
- Put business rules in services, selectors, validators, policies, or model methods only when they truly belong there.
- Do not put complex business logic directly inside templates.
- Do not duplicate scoring, timing, access, permission, or email rendering logic.
- Shared functionality must be extracted into small, well-named modules.
- Prefer explicit service functions over hidden side effects.
- Keep models focused on data shape, relationships, constraints, and simple domain helpers.
- Avoid creating overly large models that become business-logic dumping grounds.
- Use database constraints where they protect important rules.
- Keep imports at the top of every Python file.
- Avoid circular imports by using service boundaries, app-level selectors, and lazy model references where appropriate.
- Do not add broad abstractions before there is repeated real usage.
- Do not break existing URLs, template context keys, forms, or model fields without updating all dependent code in the same phase.

### Frontend And Backend Sync

- Backend forms, view context, template fields, and HTMX partials must be changed together.
- Every form field shown in templates must map to a real form/model/service field.
- Every submit button/action in a template must have a backend route, permission check, validation path, and success/error response.
- Template context names should be stable and documented in the related view or selector.
- HTMX endpoints must return predictable partials.
- Do not leave dead buttons, fake links, or UI actions that imply a workflow exists when the backend does not support it.
- Use feature placeholders only when clearly marked as unavailable to users.
- Keep Arabic labels and English translation keys synchronized.
- If a model field changes, update:
  - forms
  - admin
  - selectors
  - services
  - templates
  - tests
  - seed data

### Language And Internationalization

- Arabic is the default language.
- English is supported using Django i18n.
- All user-facing strings must use translation functions:
  - Python: `gettext_lazy` or `gettext`
  - Templates: `{% trans %}` and `{% blocktrans %}`
- Use right-to-left layout for Arabic.
- Support left-to-right layout for English.
- Keep layout direction controlled by active language.
- Avoid hardcoded Arabic or English strings in business logic.
- Store user-generated content as entered.
- Use translation files for application labels and fixed UI text.

### Timezone And Dates

- Django timezone must be enabled.
- Application timezone must be `Africa/Cairo`.
- Store datetimes as timezone-aware values.
- Display dates/times in Cairo time.
- Validate exam opening, closing, timers, expiry, and release schedules using timezone-aware logic.
- Avoid naive `datetime.now()`.
- Use Django timezone utilities.
- Tests must include timezone-aware date/time cases.

### Security And Permissions

- All protected actions must be checked server-side.
- UI hiding is not a security control.
- Students can only access their own records.
- Teachers and moderators can only access assigned or permitted records.
- Admin actions must be permission checked.
- File uploads must validate type and size.
- Secrets must only come from environment variables.
- API keys must never be committed.
- Do not log raw secrets, full API keys, or sensitive student data.

### Testing And Validation

- Every phase must include tests for new business rules.
- Every important workflow must have at least one integration test.
- Critical user workflows should receive E2E coverage.
- External integrations must have mocked tests plus a real configuration path.
- CI must run linting and tests before deployment.
- Every migration must be committed with the model change that requires it.
- Every phase must include a manual validation checklist.

### Optimization

- Optimize for the agreed VPS constraints: 2 vCPU, 4 GB RAM, 30 GB SSD.
- Avoid storing large files on the server.
- Use Cloudflare R2 for audio and uploaded files.
- Use database indexes for frequent filters:
  - application status
  - student identifiers
  - cohort
  - enrollment
  - assessment/exam availability
  - grading status
- Avoid heavy background workers unless required.
- Prefer simple synchronous operations at first, then move heavy tasks to a queue only when needed.
- Keep admin list queries efficient with `select_related` and `prefetch_related`.
- Avoid N+1 queries in dashboards and student profile pages.

## Proposed App Structure

### Core Apps

- `config`
  - settings
  - URLs
  - ASGI/WSGI
  - environment configuration

- `core`
  - shared utilities
  - base models
  - timezone helpers
  - translation helpers
  - common validators
  - common test utilities

- `accounts`
  - custom user model
  - roles
  - authentication
  - permission helpers

- `students`
  - student profiles
  - student history aggregation
  - student portal views

- `applications`
  - registration
  - account matching
  - admin review
  - approval/rejection

- `entrance_exams`
  - entrance exam definitions
  - entrance exam dates/windows
  - entrance exam attempts
  - entrance exam submissions
  - entrance exam scoring/review

- `assessments`
  - normal quizzes
  - semester exams
  - final exams
  - homework assessments
  - assessment attempts
  - grading workflows

- `questions`
  - shared question models or utilities
  - MCQ logic
  - written answer logic
  - scoring helpers
  - penalty helpers

- `programs`
  - diplomas
  - semesters
  - cohorts
  - standalone courses
  - course versions

- `content`
  - lectures
  - audio files
  - YouTube embeds
  - release scheduling
  - content access policies

- `communications`
  - SMTP email backend wrapper
  - email templates
  - template preview
  - email send history

- `ai`
  - Gemini client
  - AI scoring services
  - request/response records
  - AI error handling

- `storage`
  - R2 configuration
  - file upload services
  - signed URL helpers if needed

- `payments`
  - payment CSV upload
  - matching results
  - upload history

- `certificates`
  - certificate templates
  - PDF generation
  - certificate records

- `audit`
  - admin action history
  - access changes
  - grade overrides
  - promotion decisions

## Shared Logic Boundaries

Entrance exams and normal assessments should be separate at the product and app level, but they may reuse lower-level logic.

### Must Be Separate

- URL namespaces.
- Admin screens.
- Student entry points.
- Attempt models or attempt type boundaries.
- Business policies.
- Reporting/status language.
- Permission rules where they differ.
- Acceptance criteria.

### Can Be Shared

- Question data structures if they fit both domains.
- MCQ scoring utility.
- Written answer review helpers.
- Timer/window validation.
- Attempt state constants.
- Grade visibility utilities.
- Gemini scoring client.
- Common form widgets.
- Common template partials for question rendering.

### Recommended Pattern

- `entrance_exams` owns entrance-specific workflows.
- `assessments` owns course/diploma learning assessments.
- `questions` owns reusable question/scoring pieces.
- `ai` owns Gemini integration.
- Shared logic must not force entrance exams and normal assessments into the same user workflow.

## Phase 1: Foundation

### Goal

Create a maintainable Django foundation with Arabic-first i18n, Cairo timezone, custom user model, placeholders, CI/CD skeleton, and deployment structure.

### Tasks

- Initialize Django project.
- Add custom user model.
- Configure environment-based settings.
- Configure PostgreSQL support.
- Configure `LANGUAGE_CODE = "ar"`.
- Configure available languages:
  - Arabic
  - English
- Configure `TIME_ZONE = "Africa/Cairo"`.
- Enable `USE_TZ = True`.
- Add i18n middleware and locale paths.
- Add RTL/LTR layout support in base template.
- Add Tailwind CSS and HTMX.
- Create base templates:
  - Arabic default shell
  - admin shell
  - student shell
  - form layout partials
- Add role groups:
  - Super Admin
  - Admin
  - Moderator
  - Teacher
  - Student
- Add placeholder dashboards.
- Add global permission helpers.
- Add base timestamped model using timezone-aware fields.
- Add project-level logging setup.
- Add `.env.example`.
- Add GitHub Actions CI:
  - lint
  - tests
  - migration check
- Add GitHub Actions deployment skeleton over SSH.
- Add server provisioning notes because server is not available yet.

### Output

- Running Django project.
- Custom user model.
- Arabic-first UI shell.
- English translation support structure.
- Cairo timezone configuration.
- Placeholder dashboards.
- CI pipeline.
- Deployment workflow skeleton.

### Acceptance Criteria

- App starts locally.
- Admin user can log in.
- Student user can log in.
- Arabic is the default language.
- English can be activated.
- Layout direction changes based on active language.
- Datetimes display in Cairo time.
- CI runs lint and tests.
- Deployment workflow exists but can be disabled until server secrets are added.

### Validations And Checks

- Run Django system check.
- Run migrations.
- Run test suite.
- Verify no missing migration changes.
- Verify all new Python imports are at top of file.
- Verify no hardcoded user-facing strings in Python views/forms.
- Verify base templates load in Arabic and English.
- Verify no protected dashboard is reachable anonymously.

## Phase 2: Registration, Admin Approval, And Email Sending

### Goal

Implement registration (with duplicate rejection), application review, approval/rejection, enrollment activation, and SMTP-ready email templates.

**Updated per Stakeholder Decision Addendum 2026-06-20:** Registration creates a person identity. Applying for a course or diploma is optional during first registration. A person with no program application does not appear in admin application queues. Dashboard applications create child program request records.

### Tasks

- Create student profile model.
- Create root registration/person identity model (1:1 with user).
- Create child program application/request models for course and diploma/cohort applications.
- Add approved registration fields:
  - الاسم رباعي
  - تاريخ الميلاد
  - السن
  - النوع
  - العنوان
  - رقم الموبايل
  - البريد الالكتروني
  - المؤهل الدراسي
  - التخصص
  - جهة العمل
  - الوظيفة الحالية
  - الكنيسة التابع لها
  - اسم الأب الكاهن
  - التواصل مع الأب الكاهن
  - الرقم القومي
- Create public registration form with optional program selection (course XOR diploma/cohort, never both).
- Registration without program selection creates user/person/profile only.
- Implement duplicate account matching by:
  - national ID
  - email
  - phone
- Create application/program-request statuses including "Invited to Entrance Assessment".
- Add admin application list showing only program requests requiring action.
- Add admin application detail.
- Add admin edit registration data.
- Add approve/reject actions with statuses.
- Add activation/enrollment placeholder if programs are not fully implemented yet.
- Add SMTP configuration through environment variables.
- Add email template model.
- Add email variable rendering.
- Add email preview.
- Add send utility.
- Add send history.
- Create initial email templates:
  - registration received
  - entrance exam invitation
  - acceptance
  - rejection
  - activation

### Output

- Working Arabic registration form.
- Application records.
- Duplicate rejection behavior (never merge or update existing records).
- Admin approval workflow.
- SMTP-ready email utility.
- Email templates and preview.

### Acceptance Criteria

- Student can submit registration.
- Duplicate application is rejected when any identifier (national ID, email, phone) matches an existing record.
- Admin can review, edit, approve, or reject.
- Registration received email can be sent through SMTP configuration.
- Admin can preview email content before sending.
- Email history is saved.
- All labels and messages are translatable.

### Validations And Checks

- Test duplicate matching.
- Test application status transitions.
- Test registration form validation.
- Test email template rendering.
- Test SMTP send with configured backend or local console backend.
- Test admin-only access.
- Check Arabic layout on registration and admin review pages.
- Check backend form fields match frontend template fields.

## Phase 3: Entrance Exam, Handover, And Gemini Integration

### Goal

Deliver the first stakeholder-visible workflow: a real entrance exam system with timing, submissions, MCQ/written questions, Gemini-assisted MCQ scoring (with rubric/model-answer weighted suggestions), manual review, and handover notes.

**Updated per Stakeholder Decision Addendum 2026-06-20:** Gemini is an assistance/suggestion layer for entrance MCQ and written review. Entrance MCQ uses rubric-based weighted scoring with Gemini suggestions. Written answers wait manual review with optional Gemini assistance. Final entrance result (pass/fail) drives admission email targeting. Access requires authenticated user with matching invited application.

### Tasks

- Create `entrance_exams` app.
- Create entrance exam models:
  - exam (linked to diploma, cohort)
  - exam cohort link
  - exam date/window
  - question (MCQ + written)
  - MCQ option (no correct/incorrect flag — answer options only)
  - scoring rubric/model answer per question
  - attempt linked to program application + cohort
  - answer (with Gemini suggestion fields)
  - review record (staff decision, Gemini accepted/adjusted/rejected)
- Keep entrance exam URLs separate from normal assessments.
- Add shared question/scoring utilities where useful.
- Implement opening time, closing time, and timer duration using Cairo-aware time.
- Enforce one submission per student per cohort with database constraint.
- Add student entrance exam screen.
- Add exam timer behavior.
- Add MCQ answer submission.
- Add written answer submission.
- Add automatic MCQ scoring rules.
- Add Gemini assistance/suggestion layer:
  - environment variable for API key
  - request timeout
  - response parsing
  - failure handling
  - request/response audit record (prompt version, suggestion, staff decision)
  - entrance MCQ weighted suggestion via rubric/model-answer prompt
  - entrance written assistance if enabled (suggestion only, not final)
  - staff can accept/adjust/reject Gemini suggestion
- Add admin/manual review screen for written answers.
- Add admin override for AI/scoring results.
- Add result status:
  - submitted
  - under review
  - passed
  - failed
- Add entrance exam invitation email connection.
- Entrance exam access requires authenticated user + matching invited program application.
- Token alone is not sufficient for access.
- Add initial handover document:
  - setup
  - environment variables
  - Gemini configuration
  - how to create entrance exam
  - how to review submissions

### Output

- Complete entrance exam workflow.
- Gemini suggestion layer (MCQ weighted + optional written assistance).
- Manual review and override.
- Cohort/application-scoped access.
- Handover notes.

### Acceptance Criteria

- Admin can create an entrance exam with rubric/model-answer scoring.
- Admin can configure exam window and timer.
- Student can open exam only during allowed window.
- Student can submit once per cohort (database-constrained).
- MCQ questions are scored with weighted rubric.
- Written answers are marked under review until reviewed.
- Gemini API is called when configured and stored as suggestion.
- Gemini failure does not break the exam submission.
- Gemini suggestion can be accepted/adjusted/rejected by staff.
- Entrance access with token but no matching application is denied.
- Student/application result can be updated from entrance exam result.
- Pass/fail drives admission email targeting.

### Validations And Checks

- Test Cairo-aware exam open/close logic.
- Test timer expiration behavior.
- Test one submission per cohort (database constraint).
- Test MCQ weighted scoring with rubric.
- Test written answer under-review status.
- Test mocked Gemini success with suggestion stored.
- Test mocked Gemini failure (submission not blocked).
- Test token access denial without matching application.
- Manually validate real Gemini call with API key in local environment.
- E2E test:
  - admin creates exam
  - student submits exam
  - admin reviews submission with Gemini suggestion
- Verify entrance exam code is separate from normal assessments.
- Verify shared scoring code does not depend on entrance-specific models.

## Phase 4: Diplomas, Cohorts, Courses, And Content

### Goal

Implement academic structure, cohort boundaries, course versions, content management, and student content access.

### Tasks

- Create diploma model.
- Create semester model.
- Create cohort model.
- Create standalone course model.
- Create course version model.
- Create enrollment models:
  - diploma cohort enrollment
  - course version enrollment
- Add admin management screens.
- Add cohort-as-version boundary.
- Add course versioning boundary.
- Add lecture model.
- Add YouTube embed support.
- Add audio field placeholder using storage abstraction.
- Add weekly release scheduling.
- Add content access policy.
- Add student content list.
- Add expired/restricted access behavior.

### Output

- Admin can configure diplomas, cohorts, courses, and content.
- Students can see only approved content.
- Cohort and course version boundaries are enforced.

### Acceptance Criteria

- Existing cohort students are not affected by new cohort content.
- Existing course version students are not affected by new course version content.
- Student cannot access content without enrollment.
- Student cannot access expired or restricted content.
- Weekly release schedule controls visibility.
- YouTube embeds are only shown on authenticated pages.

### Validations And Checks

- Test cohort isolation.
- Test course version isolation.
- Test access duration.
- Test release schedule.
- Test unauthenticated access denial.
- Check admin templates match backend forms.
- Check student content page has no dead actions.

## Phase 5: Normal Assessments, Grading, Homework, And R2 Integration

### Goal

Implement normal learning assessments separately from entrance exams while reusing shared question, scoring, AI, and validation logic where appropriate.

### Tasks

- Create normal assessment models:
  - quiz
  - semester exam
  - final exam
  - homework assessment
  - attempt
  - answer
  - grade/review
- Keep normal assessment URLs and admin screens separate from entrance exams.
- Reuse shared question utilities where useful.
- Add MCQ/written/mixed assessments.
- Add question-level points.
- Add optional penalty rules.
- Add empty answer handling.
- Add show/hide correct answer toggle.
- Add show/hide written feedback toggle.
- Add maximum attempts.
- Add grade visibility states.
- Add teacher/moderator grading queues.
- Add homework upload:
  - PDF
  - DOCX
  - max file size config
- Add R2 environment variable configuration.
- Add storage service wrapper.
- Add audio upload to R2.
- Add homework upload to R2.
- Add file validation and error handling.

### Output

- Full normal assessment system.
- Homework upload workflow.
- R2-backed storage integration.
- Staff grading queues.

### Acceptance Criteria

- Student can take allowed quiz/exam.
- Student cannot exceed maximum attempts.
- MCQ score is calculated correctly.
- Written answers require manual review.
- Homework upload accepts only allowed files.
- Files are stored through the storage service, ready for R2.
- Staff can grade assigned work.
- Students see corrected or under-review status correctly.

### Validations And Checks

- Test scoring.
- Test penalties.
- Test attempt limits.
- Test grade visibility.
- Test homework file validation.
- Test mocked R2 upload.
- Test permission boundaries for teacher/moderator grading.
- Verify no normal assessment route depends on entrance exam views.

## Phase 6: Promotion And Certificates

### Goal

Complete academic lifecycle rules for promotion, demotion, completion, graduation, and certificate generation.

### Tasks

- Add diploma passing configuration.
- Add semester passing rules.
- Add promotion service.
- Add automatic promotion when configured.
- Add manual admin actions:
  - promote
  - demote
  - override
  - lock
  - retake handling
- Add final exam completion requirement.
- Add admin manual graduation/completion.
- Add certificate template model.
- Add placeholder until real templates are available.
- Add PDF generation service.
- Add Arabic PDF support considerations.
- Add generated certificate record.
- Add student certificate access.

### Output

- Promotion lifecycle.
- Manual override workflow.
- Completion/graduation workflow.
- Certificate generation path.

### Acceptance Criteria

- Passing rules promote students correctly.
- Admin can override promotion/demotion.
- Final exam is required before completion.
- Admin manually marks graduation.
- Certificate can be generated after completion.
- Student can access generated certificate.

### Validations And Checks

- Test promotion rules.
- Test demotion and lock behavior.
- Test final exam prerequisite.
- Test certificate generation smoke path.
- Test Arabic text rendering strategy for certificate output.
- Test audit records for manual actions.

## Phase 7: Full Student Profile

### Goal

Provide a complete Arabic-first student profile and history area.

### Tasks

- Add student profile dashboard.
- Add application history.
- Add diploma enrollment history.
- Add course enrollment history.
- Add active access list.
- Add expired/restricted access list.
- Add entrance exam results.
- Add normal assessment attempts.
- Add grades and feedback.
- Add homework submissions.
- Add promotion history.
- Add certificate list.
- Add payment status.
- Add email/notification history if appropriate.
- Add reapply flow.
- Add clear access labels:
  - active
  - expired
  - restricted
  - under review
  - completed

### Output

- Complete student profile.
- Student can understand current and historical status.

### Acceptance Criteria

- Student sees only their own data.
- Expired student can view history but not expired content.
- Student can view grades, progress, and certificates.
- Student can see all applications and enrollments.
- Profile works in Arabic and English.

### Validations And Checks

- Test student-only data access.
- Test history aggregation.
- Test expired access behavior.
- Test template/backend context sync.
- E2E test student profile navigation.

## Phase 8: Complete Missing Scope

### Goal

Close all remaining documented MVP requirements and operational gaps.

### Tasks

- Implement payment CSV upload.
- Match payment rows by:
  - national ID
  - email
  - phone
- Show matched, unmatched, updated, and failed rows.
- Save payment upload history.
- Complete bulk email preview and recipient confirmation.
- Improve admin audit history.
- Add search and filters across admin screens.
- Add dashboard query optimization.
- Improve Arabic and English translations.
- Improve responsive UI.
- Add seed/demo data.
- Add admin documentation.
- Add acceptance checklist.
- Add known limitations page/document.

### Output

- Full documented MVP coverage.
- Operational admin workflows.
- Improved production readiness.

### Acceptance Criteria

- Payment CSV upload works and reports results.
- Bulk email preview/send works.
- Admin can audit key operational actions.
- Core admin lists are searchable/filterable.
- Translation coverage is complete for user-facing strings.
- Acceptance checklist maps to stakeholder document.

### Validations And Checks

- Test payment CSV matching.
- Test bulk email preview and send.
- Test audit records.
- Run full regression suite.
- Run E2E workflow suite.
- Run query count checks for key dashboards where needed.

## Phase 9: Complete Deployment

### Goal

Deploy the app completely to a dedicated server and establish production operations.

### Tasks

- Provision dedicated server when available.
- Install required server packages.
- Configure PostgreSQL.
- Configure application service.
- Configure reverse proxy.
- Configure HTTPS.
- Configure environment variables.
- Configure static files.
- Configure R2 credentials.
- Configure SMTP credentials.
- Configure Gemini API key.
- Finalize GitHub Actions deployment over SSH.
- Add post-deploy health check.
- Add rollback notes.
- Add database backup script.
- Add scheduled backups.
- Add restore procedure.
- Add logging and monitoring plan.
- Add production security checklist.

### Output

- Production deployment.
- CI/CD deployment path.
- Backup and restore process.
- Final handover package.

### Acceptance Criteria

- Production site is reachable over HTTPS.
- Deployment runs from GitHub Actions.
- Migrations run during deployment.
- Static files are served correctly.
- App service restarts successfully.
- Health check passes after deploy.
- Backups run on schedule.
- Restore procedure is documented and tested on a non-production database.

### Validations And Checks

- Run production Django checks.
- Verify secure cookie settings.
- Verify allowed hosts and CSRF trusted origins.
- Verify admin access is protected.
- Verify logs are available.
- Verify backup file creation.
- Verify deployment secrets are not printed in logs.

## 2-Day Stakeholder MVP Focus

The full project should follow the phases above, but the 2-day target should be narrowed to the first visible entrance exam workflow.

### Day 1 Target

- Django foundation.
- Custom user model.
- Arabic-first base templates.
- Cairo timezone.
- Placeholder dashboards.
- Basic application/student setup.
- Entrance exam models.
- Admin create/edit entrance exam.
- Student entrance exam access screen.
- CI skeleton.

### Day 2 Target

- MCQ/written entrance exam submission.
- One submission per student/cohort.
- Cairo-aware open/close/timer validation.
- MCQ scoring.
- Gemini real API call.
- Manual review screen.
- Basic result status.
- Handover notes.
- Minimum tests for entrance exam flow.

### 2-Day MVP Acceptance Criteria

- Admin can create an entrance exam.
- Student can access the exam only during the valid window.
- Student can submit MCQ and written answers.
- Student cannot submit twice for the same cohort.
- MCQ score is calculated.
- Gemini is called using the configured API key.
- Gemini failure is handled gracefully.
- Admin can review written answers.
- Admin can update result status.
- Arabic UI is usable.
- Cairo timezone behavior is correct.

## Definition Of Done For Every Phase

- All new code has clear ownership by app/module.
- All imports are at the top of Python files.
- No known broken buttons or dead workflow links.
- Backend routes, forms, templates, and tests are synchronized.
- User-facing strings are translatable.
- Date/time logic is timezone-aware and Cairo-aligned.
- Permission checks exist server-side.
- Tests for new business rules pass.
- CI passes.
- Migrations are included.
- Documentation is updated for new setup or operations.

## Immediate Open Items

- Choose exact SMTP provider or SMTP host.
- Decide if the first 2-day demo needs registration before entrance exam, or if demo users can be seeded.
- Confirm whether stakeholders need Arabic-only demo first, with English translations added progressively.
- Confirm entrance exam result rules:
  - pass/fail threshold
  - manual review required before final result
  - whether Gemini contributes directly to score or only assists
- Confirm whether entrance exam should require login only, or support a secure invitation link.
