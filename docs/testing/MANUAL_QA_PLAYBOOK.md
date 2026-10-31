# DokkiMashora Manual QA Playbook

**Scope:** route-led scenario inventory refreshed against the current working tree on 2026-09-21  
**Authority:** `AGENTS.md`, `DOKKIMASHORA.md`, then the current URLs, views, forms, services, models, templates, HTMX, and JavaScript  
**Execution type:** manual functional, authorization, usability, and scale-readiness checks  
**Excluded:** automated test execution, browser automation, load generation, seed-command changes, and application-code changes

Scenario inclusion is not evidence of execution. Record role, locale, viewport,
fixture IDs, observed result, and captured evidence when each manual case is run.

This playbook is deliberately route-led. Each scenario names a real route or
POST action from the repository. It keeps Entrance Exam separate from normal
Assessments and treats Teacher as deferred rather than inventing a Teacher
workflow.

## 1. Conventions

- `BASE` means the deployed root, for example `http://127.0.0.1`.
- Arabic is the default URL space, so a route such as `/accounts/login/` has no
  language prefix. English uses the same route under `/en/`, for example
  `/en/accounts/login/`.
- Replace `<id>` placeholders with IDs created for this QA run. Replace
  `<uuid-token>` and `<upload-token>` with the exact issued token.
- A state-changing route must be exercised from its rendered form with CSRF.
  A direct GET to a POST-only action should return `405`, or the explicitly
  coded safe redirect where noted, and must never mutate state.
- Expected status text means the translated `get_*_display()` or prepared
  business label, never the stored code such as `payment_pending`.
- Use two browser profiles when a scenario switches roles so session and cache
  state do not leak between actors.
- Capture the URL, role, locale, viewport, object IDs, time, observed result,
  and screenshots for every failure. Never include passwords, activation
  tokens, signed storage URLs, national IDs, or unnecessary student data in a
  defect report.

Priority tags:

- **P0:** security, destructive integrity, payment/activation, exam submission,
  progression, or certificate-history failure.
- **P1:** core workflow, authorization scope, grade visibility, or parity failure.
- **P2:** localization, responsive, accessibility, filtering, or presentation failure.
- **LOAD TEST REQUIRED:** record the case here, but execute it only in the later
  confirmed load test with an approved harness and isolated data.

## 2. Required environment and preconditions

### 2.1 Environment readiness

1. Open `BASE/healthz`; expect a healthy liveness response and database status.
2. Open `BASE/accounts/login/`; expect the branded login page over the intended
   scheme, with no mixed-content or missing static-asset errors.
3. Confirm Django, PostgreSQL, Redis/Celery, storage, and outbound email are
   configured for the QA environment. Use an email sink, never real recipients.
4. Record the Cairo local time and UTC time. All windows and deadlines below
   are interpreted by the server with `Africa/Cairo` business time and
   timezone-aware values.
5. Confirm the run is isolated and disposable before testing destructive
   academic deletes, payment soft deletion, rejoin revocation, or certificate
   revocation.

### 2.2 Role accounts

Create or obtain one account for each real role. Do not place a non-admin user
in multiple staff groups unless a scenario explicitly tests combined roles.

| Actor | Required state | Expected primary navigation |
|---|---|---|
| Super Admin | active, `is_superuser` | Same audited trust/capabilities as Admin |
| Admin | active Admin group | Full Workspace, Learning, Admissions, Administration |
| Moderator A | active Moderator group; assigned to Cohort A cluster A1 | Dashboard, Homework review only |
| Moderator B | active Moderator group; assigned to Cohort B cluster B1 | Dashboard, Homework review only |
| Reviewer A | active Reviewer group; assigned anonymous online and offline Semester Exam work | Dashboard, Grading assignments only |
| Secretary | active Secretary group | Dashboard, Attendance only |
| Student A1 | active Diploma enrollment in Cohort A, cluster A1, online mode | Student portal; own data only |
| Student A2 | active Diploma enrollment in Cohort A, outside cluster A1, offline mode | Student portal; own data only |
| Student B1 | active Diploma enrollment in Cohort B | Student portal; own data only |
| Course Student | active, non-expired enrollment in exact Course Version V1 | Student portal and V1 learning |
| Expired Student | expired Course or Diploma enrollment with history | History visible; active learning denied |
| Pending Student | inactive/unactivated account and pending application | Activation/application state only |
| Historical Teacher | Teacher group only, no Admin group | No active product workflow or Teacher navigation |

Teacher is **DEFERRED / NOT A CURRENT PRODUCT ROLE**. There is no Instructor
role constant or route namespace. The Teacher checks in this playbook therefore
verify absence of active capability and denial of protected staff/student
routes; they do not attempt to define a teaching workflow.

### 2.3 Academic and workflow fixtures

Prepare through current Admin UI where practical:

- Diploma D with Semester 1 and Semester 2.
- Active Cohort A and active immediately-next Cohort B for Diploma D. Give each
  its own Diploma lectures, assessments, attendance, assignments, clusters,
  enrollments, and progression data.
- Cluster A1 in Cohort A with Student A1; Student A2 remains outside A1.
- Standalone Course C with active Version V1 and at least three released
  sequential lectures: video-only, audio-only, and video+audio.
- One active Course Quiz with MCQ and written questions.
- One Diploma Homework in Cohort A + Semester 1, with manual release.
- One online Diploma Semester Exam and one offline Diploma Semester Exam in
  Cohort A + Semester 1, both with at least two grading sections.
- One active Entrance Exam for Cohort A with MCQ and written questions.
- Evaluation settings for Cohort A + Semester 1 using Attendance, Homework,
  and Semester Exam, with weights totaling 100%, component minimums, overall
  minimum, and a configured required attendance-session count.
- A PDF certificate template and a JPG/JPEG template. Keep any PSD fixture
  isolated until the open question in section 20 is resolved.
- Email templates and global footer configured for the test mail sink.

For the scale-readiness section, the target dataset is **two active cohorts
with approximately 200 active students each**. Do not create those 400 records
by hand. Population and concurrent traffic belong to the later confirmed load
test. Manual QA may use an already-provisioned equivalent dataset.

### 2.4 Boundary fixture matrix

Create distinct records for these conditions; never reuse the same student when
the previous scenario changes durable state:

- before-open, open, exactly-at-close, after-close assessment windows;
- duration not expired and duration expired;
- active, pending, restricted, completed, and expired enrollments;
- paid, unpaid, waived, waiver-revoked, and soft-deleted payment history;
- Homework with 0, 1, and maximum attachments;
- Course Quiz with automatic and manual release;
- Diploma Homework and Semester Exam with automatic and manual release;
- Semester evaluation with all components, one unconfigured component, one
  configured-but-missing result, component-minimum failure, overall failure,
  and full pass;
- progression Interview Pending after calculated PASS and after calculated FAIL;
- direct-rejoin eligible, already planned/active, and missed-window students.

## 3. Route and authorization smoke matrix

Run every row first as the allowed role, then in a second profile as each
materially disallowed role. A disallowed user should receive `403` or a safe
login redirect, must not receive protected object data, and must not mutate the
target.

| ID | Route/action | Allowed role | Exact steps | Expected result |
|---|---|---|---|---|
| AUTH-01 | `GET /accounts/dashboard/` | Every authenticated active role | Sign in as each role; open the route. | Router lands on the role-appropriate dashboard. Moderator, Reviewer, and Secretary get only their locked navigation. Student gets the student dashboard. |
| AUTH-02 | `GET /accounts/dashboard/admin/` | Admin, Super Admin | Open as Admin, then Moderator, Reviewer, Secretary, Student, Teacher-only. | Admin/Super Admin succeed. Every other role is denied without dashboard data leakage. |
| AUTH-03 | `GET /assessments/reviewer/dashboard/` | Reviewer, Admin, Super Admin | Open as Reviewer and disallowed roles. | Reviewer sees assigned anonymous queues only. Moderator/Secretary/Student/Teacher are denied. |
| AUTH-04 | `GET /assessments/manage/homework/` | Moderator, Admin, Super Admin | Open as Moderator A and other roles. | Moderator sees only authorized Diploma Homework submissions in assigned Cohort/cluster scope. Reviewer/Secretary/Student/Teacher are denied. |
| AUTH-05 | `GET /attendance/sessions/` and `GET /attendance/scan/` | Secretary, Admin, Super Admin | Open both routes as each staff role. | Secretary can list/view/scan; Moderator/Reviewer/Teacher/Student are denied. |
| AUTH-06 | `GET /attendance/sessions/create/` and POST open/close/delete actions | Admin, Super Admin | Attempt as Secretary and Admin. | Secretary cannot create/open/close/delete; Admin can. No state changes occur for denied requests. |
| AUTH-07 | Admin namespaces such as `/payments/`, `/progression/manage/`, `/certificates/manage/`, `/communications/bulk/history/`, `/audit/` | Admin, Super Admin | Open as both admin types, then all limited roles. | Admin == Super Admin for audited capabilities. Limited roles receive no page or object data. |
| AUTH-08 | Student routes `/profile/`, `/profile/learning/`, `/assessments/`, `/certificates/` | Student; intentional superuser preview where decorator allows | Open as Student A1, Admin group only, Super Admin, and Teacher-only. | Student sees own records. Superuser preview works only where coded. Admin-only and Teacher-only users do not gain Student ownership. |
| AUTH-09 | Any POST-only route, e.g. `/assessments/<id>/start/`, `/entrance-exams/manage/grading/<id>/decision/`, `/progression/manage/grade-items/<enrollment>/<semester>/sync/` | Route-specific authorized role | Record state; issue direct GET; reload state. | `405` for explicit POST-only routes and no mutation. Normal assessment submit routes that intentionally redirect on GET return to Take without mutation. |
| AUTH-10 | Any state-changing form | Authorized role with valid session | Submit normally, then repeat without CSRF using a fresh request. | Valid request follows domain rules. Missing/invalid CSRF is rejected and state remains unchanged. |
| AUTH-11 | Cross-object ID tampering on detail/download/mutation routes | Object owner or scoped staff only | Replace a valid student, cohort, enrollment, attachment, attempt, code, certificate, or assignment ID with another cohort/user's ID. | `403`/`404` or safe domain rejection; no cross-cohort or cross-student data appears and no mutation occurs. |
| AUTH-12 | Teacher-only account across `/accounts/dashboard/`, `/assessments/manage/`, `/attendance/sessions/`, `/profile/` | None as Teacher-only | Sign in as Historical Teacher and try each route directly. | No Teacher navigation or active capability is exposed. Protected admin/student routes are denied. Record any redirect loop or accidental inherited access as P0. |

## 4. Registration, identity, activation, and additional applications

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| REG-01 | `GET/POST /applications/register/` | Unique identity values | Open registration; enter all required valid identity/profile fields; leave “apply for a program” off; submit. | One User and one StudentProfile are represented in the UI; success page is `/applications/register/success/`; activation message is queued according to environment. No application is created. |
| REG-02 | Same | Open Cohort A Entrance Exam | Repeat with program application enabled, choose Diploma/Cohort A and Online delivery, submit. | Exactly one root identity plus one Diploma application is created. Application shows translated Submitted status and Online label. |
| REG-03 | Same | Active standalone Course C/V1 | Repeat with Course selected. | Exactly one identity plus one Course application; no cohort relation is attached. |
| REG-04 | Same | Existing Student A1 identity | Submit three separate registrations matching Student A1 by national ID only, email only, then phone only. | Each is rejected at the matching field with a user-friendly duplicate message. Existing identity is neither merged nor changed; no new User/Profile/Application exists. |
| REG-05 | Same | None | Test future/today date of birth, invalid email, invalid phone, invalid Egyptian and non-Egyptian identity, missing program type, missing Diploma, missing delivery mode, invalid Course target. | Form stays populated, errors are attached to the correct fields, and nothing is created. |
| REG-06 | `GET /applications/program-targets/` | Both Diploma and Course targets exist | Use the registration/apply type selector normally (HTMX), then request route directly with equivalent query. | Only open, active targets are returned. The response does not expose closed cohorts, courses without active versions, or targets blocked for the current student. |
| REG-07 | `GET/POST /accounts/activate/<uidb64>/<token>/` | Pending Student has a fresh activation link | Open link; complete the rendered activation flow; then try the same token again. | First use activates the account and advances relevant application state once. Reuse is rejected/treated as already used without duplicate email, decision, or application transition. |
| REG-08 | `GET/POST /accounts/resend-activation/` and `POST /accounts/manage/users/<id>/resend-activation/` | Pending Student | Request resend as the student/public flow, then from Admin student detail. | A new activation email is queued without exposing whether unrelated arbitrary emails exist. Admin route is unavailable once resend is not valid. |
| REG-09 | `GET/POST /profile/apply/` | Authenticated student with no blocked target | Apply to Course C, then separately to a different open Diploma. | Later applications reuse the existing identity and create distinct ProgramApplication records only. |
| REG-10 | Same | Student already has active/pending application or enrollment for exact program | Attempt the same target again, including a double-click/two-tab submit. | Duplicate is blocked by UI/service/database boundary; at most one active application for the exact target remains. A different target remains allowed. |
| REG-11 | `GET /profile/applications/portal/` and redirect `/profile/applications/` | Student has multiple applications | Open canonical portal and legacy redirect; paginate if applicable. | Student sees only own applications with translated business labels and valid current actions. Redirect lands on the canonical page. |
| REG-12 | `GET/POST /applications/programs/<application_id>/` | Admin; applications in each status | Walk Submitted → allowed review actions and inspect after each action. Also forge an action hidden in the current state. | Only backend-projected available actions are shown and accepted. Forged/stale actions produce “not allowed” without state change. Decision history records actor, old/new status, and time. |
| REG-13 | Same, action `reject` and `revert` | Reversible eligible application | Reject with blank reason, then valid reason; revert before any Entrance Attempt; try revert after an Entrance Attempt exists. | Blank rejection is blocked. Valid rejection records reason/history. Revert is only offered/accepted where legal and does not erase decision history. |
| REG-14 | Same, actions `request_documents`; `GET/POST /applications/documents/upload/<upload-token>/`; `GET/POST /applications/my/<id>/documents/upload/` | Accepted Diploma application | Admin requests documents with future expiry; Student uploads valid persona/attachments; repeat with expired token, wrong owner, invalid type, oversize, and too many attachments. | Valid upload changes document state once. Invalid/expired/cross-owner input is rejected; entered state/errors remain; private files are not exposed by public URL. |
| REG-15 | Downloads `/applications/documents/<upload_id>/persona/download/` and `/attachments/download/` | Submitted documents | Download as owner, Admin, unrelated Student, Moderator, Reviewer. | Owner/Admin receive authorized protected content. Others get denial without signed URL or file metadata leakage. |
| REG-16 | `POST /intake/admin/applications/<application_id>/request-intake/`; `GET/POST /intake/forms/fill/<upload-token>/` | Active Intake Form and requestable application | Admin requests intake; Student opens token, submits required fields; retry token; test expired/wrong-user token. | Exactly one response is recorded for form+application; application displays translated Survey Requested/Submitted lifecycle. Replay cannot duplicate responses. Invalid tokens reveal no application data. |
| REG-17 | `/applications/programs/` in Arabic and `/en/applications/programs/` in English | Admin; pending, in-progress, rejected, and activated applications | Open each locale in a fresh page; inspect the status/action filters, date labels, every visible “Action needed” value, pagination/order, and mobile card labels. | Static workflow labels follow the active locale—no English in Arabic and no frozen Arabic labels in English. Arabic student/program names may remain Arabic. Nonterminal/incomplete applications precede rejected and activated items. |

### 4.1 Admin user, role, cluster, and import controls

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ACCT-01 | `/accounts/manage/students/`, `/accounts/manage/staff/`, and their create/detail/edit routes | Admin | Create one Student and one each of Moderator, Reviewer, and Secretary; inspect lists/details; try to create an active Teacher workflow account. | Active roles use translated labels and correct navigation. Teacher may remain historical data but is not offered as a new active product workflow. Duplicate unique email/phone/national ID is rejected. |
| ACCT-02 | `/accounts/manage/staff/<user_id>/assignments/`; POST `/assignments/moderator/create/` | Moderator A; Cohorts A/B and Cluster A1 | Create Cohort A cluster assignment; repeat it; try missing Cohort/Cluster, Cluster B with Cohort A, and any crafted Course Version field. | Moderator assignment is Diploma Cohort/cluster-only. Duplicate/mismatched scope is rejected or remains one active row. Course Version is absent from authorization and current form behavior. |
| ACCT-03 | POST `/accounts/manage/cohorts/<cohort_id>/clusters/create/`, `/accounts/manage/clusters/<cluster_id>/members/add/`, and member remove route | Admin; Students A1/A2/B1 | Create A1; add A1; repeat; try B1 in A1; remove A1 twice. | Membership is exact-cohort and non-duplicated. Cross-cohort enrollment/student is rejected. Repeated remove cannot affect another membership. |
| ACCT-04 | `/accounts/manage/students/import/template/`; `GET/POST /accounts/manage/students/import/`; detail and confirm routes | Admin; valid and invalid CSVs | Download template; upload preview; correct validation issues; confirm; double-confirm. | Preview identifies row results and keeps invalid data out of durable users. Confirm creates/updates each intended identity once; repeat does not duplicate users. Sensitive values are not logged. |
| ACCT-05 | POST `/accounts/manage/users/<user_id>/delete/` | Disposable Student without history and Student with protected history | Delete disposable account, then try historical account and direct GET. | Only safely deletable Student is removed. Applications/payments/attempts/audit and other protected history block deletion. GET never mutates. |
| ACCT-06 | `GET/POST /profile/edit/` and `/profile/details/` | Student A1 | Change phone/address; attempt to inject full name, national ID, role, status, or another student's ID. | Only policy-approved editable profile fields change. Restricted identity/role/state remains authoritative and another student's profile is never touched. |

## 5. Payments, waiver, and activation

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| PAY-01 | `GET /payments/upload/template/`; `GET/POST /payments/imports/new/` | Admin and test CSV | Download template; upload a valid CSV matching one student by national ID, one by email, and one by phone. | Preview identifies every row and match method with translated labels. Any one unique identifier is sufficient; no undocumented priority is assumed. |
| PAY-02 | Same | CSV with an unmatched row, malformed amount/date, duplicate external reference, missing header, and over 1,000 rows in separate files | Upload each file. | The whole file is rejected or remains unconfirmable. Exact failing row number(s) and reasons are visible. No partial PaymentRecord is applied. |
| PAY-03 | `POST /payments/imports/<upload_id>/confirm/` | Fully valid preview | Submit once, then double-submit/two-tab submit. | File confirms atomically once. Payment records are not duplicated; second request is harmless/rejected with stable state. |
| PAY-04 | `GET/POST /applications/programs/<id>/`, action `mark_paid` | Payment-pending application | Record a valid amount/reference; repeat with negative/non-finite amount and duplicate reference. | Valid payment is audited and activation is attempted. Invalid value is rejected. Duplicate paid record/reference does not create a second active payment. |
| PAY-05 | Same, action `grant_waiver` and `/payments/waivers/` | Eligible unpaid application | Submit blank reason, then valid reason; repeat in two tabs. | Reason is required. One active waiver exists, with actor/time, target snapshot, before/after requirement state, and audit event. Duplicate grant does not create a second active waiver. |
| PAY-06 | Same, action `revoke_waiver` | Active waiver | Revoke; try again. | Waiver becomes Revoked and payment remains a separate concept. If that waiver solely caused activation, the application returns to its pre-activation payment state and its created enrollment becomes pending. A paid/unrelated activation is preserved. Second revoke cannot corrupt history. |
| PAY-07 | Same, action `activate` | Course and Diploma applications in multiple readiness states | Try unpaid/unwaived, paid but missing required intake/docs, fully ready Course, and fully ready Diploma after its admissions requirements. | Activation fails with specific reasons until ready. Ready action creates/reuses exactly one appropriate enrollment and records activation once. Payment or waiver is mandatory. |
| PAY-08 | `POST /payments/records/<record_id>/delete/` | Existing payment with historical target snapshot | Confirm delete; revisit lists/detail/audit; attempt direct GET. | Record is soft-deleted/preserved as history, no GET mutation occurs, and target snapshot/audit remain available to authorized Admin only. |
| PAY-09 | `POST /payments/enrollment/<student_id>/<course|diploma>/<enrollment_id>/toggle/` | Matching enrollment | Toggle through the rendered Admin action; tamper student/type/enrollment IDs. | Only exact matching target changes payment state. Cross-target input is rejected. Activation/access is never granted to a different enrollment. |

## 6. Academic structure, enrollment boundaries, and learning content

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ACAD-01 | `/academics/manage/diplomas/`, create/detail/edit routes | Admin | Create Diploma D; create Semesters and Cohorts A/B from Diploma detail; edit each. | Topology remains Diploma → Cohort → Semester → DiplomaLecture. Labels are translated; Cohort A/B data stay separate. |
| ACAD-02 | `/academics/manage/courses/`, `/academics/manage/courses/<course_id>/versions/create/`, and Version detail | Admin | Create standalone Course C, Version V1, and lectures. | Course has no Diploma/Cohort/Semester owner. Version state and lecture order render correctly. |
| ACAD-03 | POST academic delete routes | Disposable objects with and without history | Delete Semester, Cohort, CourseVersion, and Course from their confirmation actions; use direct GET as negative. | POST+CSRF required. Described cascade is explicit. Immutable application/payment/certificate/promotion/audit history remains with nullable academic references detached. Unrelated cohort/course data remains. |
| ACAD-04 | `/academics/manage/diplomas/<d>/cohorts/<c>/semesters/<s>/lectures/` and create/edit/preview/delete | Admin | Create a Cohort A lecture, then try IDs from Cohort B or another Diploma in URL. | Valid lecture is only in exact Cohort+Semester scope. Mismatched hierarchy is rejected and never moves/leaks content. |
| ACAD-05 | `/academics/manage/courses/<course>/versions/<version>/lectures/...` | Admin | Create video-only, audio-only, and video+audio lectures; test invalid/oversize audio. | Server validates media. Valid preview works; invalid upload shows a clear error without broken objects or public URLs. |
| ACAD-06 | `/academics/my-courses/<version_id>/` and lecture route | Course Student | Open V1 and Lecture 1; then change version/lecture IDs to another version or use Expired Student. | Exact active, non-expired Version enrollment is required. Cross-version and expired access are denied while history remains. |
| ACAD-07 | Course watch/audio start and checkpoint POST APIs | Course Student, sequential lectures | Watch continuously; replay watched range; seek forward; exceed allowed rate; refresh after violation. | Server-derived progress advances only from valid observations. Replay does not manufacture progress. Skip/speed/sequence violation pauses tracking with translated message and blocks only that session; the next session preserves validated progress and resumes from the last accepted position. |
| ACAD-08 | Course lecture page for video+audio | Course Student | Complete video only on one clean run; reset fixture; complete audio only on another. | Either medium satisfies the media requirement (`Video OR Audio`); both are never required. Next sequential lecture unlocks only when current required lecture is complete. |
| ACAD-09 | `/academics/my-diplomas/<enrollment_id>/...` | Student A1 and Student B1 | Open Cohort A lecture as A1; try A1 enrollment with Cohort B semester/lecture IDs; view Diploma lectures without completing watch progress. | Exact enrollment/cohort scoping holds. Diploma learning is not lecture-watch locked; watch progress does not leak between cohorts. |
| ACAD-10 | Audio proxy and audio-download routes | Authorized student, unrelated user, expired student | Play/seek authorized audio; request same path as unrelated/expired user; alter `file_path`. | Authorized stream/download works through protected application/storage path. Unauthorized or mismatched file path is denied; no unsigned object-store URL appears. |
| ACAD-11 | Course/Diploma enrollment list and update/migrate routes | Admin | Filter/search; update Diploma enrollment; migrate Course enrollment to another valid version; try invalid cross-course version. | Pagination/filtering retain state. Only exact valid migration/update succeeds; historical records remain attached correctly. |
| ACAD-12 | `/academics/manage/courses/<course_id>/` and `/academics/manage/courses/<course_id>/versions/<version_id>/` | Admin; a course with multiple versions, zero/nonzero lecture sets, an intake form on one version, and over 25 course enrollments | Check desktop and mobile. Compare each version's displayed lecture count with its own lectures; browse enrollment pages; open a version, follow breadcrumbs/back, inspect lecture ordering/actions, then open a version with no lectures and one with no intake form. Follow the scoped Grading link. | Course detail does not merge lectures across versions; each count matches persisted rows; enrollments are newest-first, paginated, and show the version label without repeating the course name. Version detail has one breadcrumb trail, a canonical parent link, labeled lecture columns, an empty state, and actions scoped to the selected version. Intake responses appears only when that version has a form; grading remains filtered to that exact version. |
| ACAD-13 | Diploma detail semester links and `/progression/manage/diploma/<diploma_id>/cohort/<cohort_id>/semester/<semester_id>/` | Admin and Super Admin; a Diploma with at least two cohorts and semesters | Open Diploma detail at desktop and mobile widths. Follow a semester title from each cohort, verify its cohort/semester heading and available operation scopes, then use Back and each breadcrumb. Try a mismatched diploma/cohort/semester URL. | Each title opens the exact semester workspace without adding another row action; workspace Back returns to the selected cohort's semester list, breadcrumbs are singular, and mismatched hierarchy is rejected. Admin and Super Admin see the same allowed workspace. |
| ACAD-14 | Course/Diploma lecture metadata status and scoped `youtube-metadata/retry/` POST routes | Admin; video-only plus video-and-audio lectures; one stale pending request | Disable/fail metadata queueing or use an unavailable video; inspect student and admin views; change the video URL while an earlier task is pending; retry unavailable and stale pending metadata; try GET, missing CSRF, mismatched IDs, and a non-Admin. | Pending/ready/unavailable state is truthful; Arabic labels are localized; only Admin can retry within the exact course-version or diploma/cohort/semester; stale task results cannot overwrite a changed video or newer retry. Students are told when video tracking is unavailable. Video-only sequential lessons remain blocked until fixed; a ready Course audio alternative may complete instead. No path auto-completes or skips a lesson. |
| ACAD-15 | Student Diploma landing and direct semester/lecture URLs | Student with completed/current Semester 1 and a published lecture only in locked Semester 2 | Open the Diploma landing and confirm visible lectures; request the direct Semester 2 page and lecture URL; then record a valid final promotion into Semester 2 and revisit. | The landing queries and renders lectures only from server-authorized visible semesters; locked lecture content is not returned by the list helper or direct routes. After the canonical promotion state advances, Semester 2 becomes visible. |
| ACAD-16 | Course quiz form; course completion page; course quiz start/instructions | Admin; course with published lectures and an active course quiz; Course Student | Mark one quiz as the end-course exam; try marking a second; try with lecture tracking off; open completion before the exam is passed; try starting the exam with an incomplete lecture; complete the lectures and pass the exam. | Only one end-course exam per course version. Completion readiness requires all published/released lectures, every active quiz's latest attempt passed, and the designated exam active and passed. The exam cannot start until required lecture progress is met. Passing it auto-completes the course and issues the certificate; the Admin completion page remains a manual fallback. |

## 7. Normal Assessment authoring and shared invariants

These scenarios apply only to Course Quiz, Diploma Homework, and Diploma
Semester Exam. They do **not** use Entrance Exam routes or models.

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ASM-01 | `GET/POST /assessments/manage/create/`; `/manage/scope-fields/` | Admin | Create Course Quiz, Diploma Homework, and Diploma Semester Exam through scope selector. Attempt Course Homework, Diploma Quiz, `final_exam`, and `additional_assessment`. | Only active vocabulary/scope combinations are offered and accepted. Deprecated types are absent from new UI and preserved historical rows stay quarantined/read-only. |
| ASM-02 | `/assessments/manage/<id>/questions/form|save/` | Course Quiz | Create MCQ with fewer than two choices, zero/multiple correct choices, then valid MCQ. | Invalid structures re-render the form/HTMX fragment with entered values and precise errors. Valid MCQ has at least two choices and exactly one correct choice. |
| ASM-03 | Same edit route | Existing MCQ | Convert to Written and save. | Stale choices are deleted; Written question has no answer choices. Converting back requires a new valid MCQ choice set. |
| ASM-04 | Diploma lecture-bank routes under `/assessments/manage/bank/<d>/cohorts/<c>/semesters/<s>/lectures/<l>/` | Diploma lecture | Create/edit/delete bank MCQs; tamper hierarchy IDs. | Questions remain within exact Diploma+Cohort+Semester+Lecture. Mismatches are denied. |
| ASM-05 | Section and manual-question routes under `/assessments/manage/<assessment_id>/sections/` | Semester Exam | Create two sections with bank counts and manual written questions; request more active bank MCQs than available. | Readiness explains shortage; activation/start cannot generate an invalid snapshot. Section maximum/order remains stable. |
| ASM-06 | `POST /assessments/manage/<id>/toggle/` and `/recalculate/` | Valid and invalid assessment configurations | Activate valid assessment; attempt activation with invalid questions/sections; recalculate totals. | Invalid assessment stays inactive with errors. Recalculated total equals configured question/section points. |
| ASM-07 | Student results/history routes | Released and unreleased attempts | Inspect HTML and page source before and after release. | Correct MCQ choice flags, answer keys, model answers, and question-bank solutions never appear. Only policy-allowed grade/result/feedback appears after release. |
| ASM-08 | Grade entry actions | Existing grade | Enter score below 0, above maximum, non-finite, valid first score, then change score without/with override reason. | Invalid scores are rejected. Changing an existing grade requires a reason and produces auditable before/after history where the workflow supports it. |
| ASM-09 | All assessment list/detail routes | Arabic and English | Inspect every type/status/release/attempt label. | UI uses translated choice labels (Quiz, Homework, Semester Exam, Assigned, In progress, etc.), never stored codes. |
| ASM-10 | `/assessments/manage/create/` and `/assessments/manage/<id>/edit/` | Admin | Create/edit Diploma Homework and Semester Exam assessments, then a Course Quiz. Inspect the requirement control, defaults, and save behavior; submit a crafted Course Quiz POST with the optional flag unset/false. | Diploma Homework/Exam can be Required or Optional and default to Required. Course Quiz never exposes optionality and the server persists it as Required even for a crafted request. |
| ASM-11 | Admin assessment detail; Student assessment list and instructions | Required and Optional Diploma assessments plus a Course Quiz; Arabic and English | Open each assessment as Admin and as the owning Student. | Diploma requirement labels are translated and consistent in both roles; Course Quiz has no diploma-evaluation requirement label. |
| ASM-12 | Reviewer Dashboard, online claim/grading, and assignment create/edit | One online cohort-wide Reviewer assignment spanning two semesters, one semester-scoped assignment, and a submission outside the assigned Cohort | Claim and grade a written answer in each covered semester under the cohort-wide assignment; repeat under semester scope; try a mismatched cohort/semester answer. | Cohort scope permits the matching Cohort's Semester Exams without requiring a semester ID; semester scope remains exact. Cross-cohort and wrong-semester grading is rejected, and valid grades retain the exact claim/section audit trail. |
| ASM-13 | Contextual Grading and Semester Exam review links | Admin; one Diploma cohort/semester scope, one Course Version scope, plus the global pending-work page | Open Grading from a Diploma semester workspace and follow Semester Exam review; open the same link from Course Version Grading and from global Grading. | Diploma review retains the exact Cohort/Semester; Course Version Grading does not show an unrelated Semester Exam review link; the global page continues to open the global review queue. |

## 8. Standalone Course Quiz

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| QUIZ-01 | `/assessments/<id>/instructions/` then POST `/start/` | Course Student; open active Quiz; eligible V1 enrollment | Read instructions; start once; refresh Take; try second start in another tab. | One in-progress Attempt 1 exists. Timer deadline derives from server start. Second start does not create a duplicate attempt. |
| QUIZ-02 | `/assessments/<id>/take/` and POST `/autosave/` | In-progress MCQ+written quiz | Answer, wait for autosave confirmation, refresh/reopen. | Saved answers return. Invalid JSON, unknown choice/question, expired window, wrong student, or non-active attempt is rejected without corrupting valid answers. |
| QUIZ-03 | POST `/assessments/<id>/submit/` | In-progress attempt | Submit valid answers; immediately double-submit/two-tab submit. | Exactly one terminal attempt and answer set exists. Repeat is redirected/rejected; no duplicate score or attempt. |
| QUIZ-04 | Same | Timer fixture | Start before close; submit before duration; separately wait past duration and try autosave/submit; also test assessment close. | Server, not client countdown, decides. Expired/closed mutations are rejected or finalized per current normal-assessment service; client clock changes do not extend access. |
| QUIZ-05 | `/assessments/<id>/results/` and `/profile/assessments/` | Automatic-release Quiz | Submit all objective work or complete written grading. | Once final grade commits, Automatic mode releases once. Student sees rounded two-decimal percentage and allowed feedback, never correct answers. |
| QUIZ-06 | Admin grading detail and POST `/release/`, `/hide/`, `/manage/<id>/toggle-grades/` | Manual-release Quiz or written attempt | Grade, inspect hidden state, release, repeat release, hide, repeat hide. | Hidden means no student numeric grade/feedback. Release is idempotent where service defines it; invalid hide/release is safely rejected. Audit records transitions. |
| QUIZ-07 | Exceptional access route `/assessments/manage/<id>/exceptional-access/` | Closed Quiz; exact active Course enrollment | Grant a bounded override with reason; Student starts fresh run; compare old/new attempts. | Override does not bypass enrollment or attempt timer. Prior attempt and snapshot remain historical; new run gets next attempt number and independent snapshot. |

## 9. Diploma Homework

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| HW-01 | `/assessments/<id>/take/`; POST `/homework/attachments/add/` | Student A1; open Homework | Submit with 0 files; upload one valid file; upload multiple valid files. | Zero-file submit is blocked. Files attach to one logical HomeworkSubmission, not separate gradable attempts. `remaining_slots` appears only if a limit is configured. |
| HW-02 | Same | File-limit and validation fixtures | Upload disallowed extension/content, oversize, empty selection, and more than max. | Each invalid file gets a clear error. Valid files already added remain consistent; rejected storage objects are not publicly reachable. |
| HW-03 | POST `/homework/attachments/<attachment_id>/remove/` | Draft with files | Remove own draft attachment; try another student's ID; submit Homework, then try removal/addition. | Own draft removal succeeds. Cross-owner and post-submit mutation are rejected. |
| HW-04 | POST `/assessments/<id>/submit-homework/` | Draft with 1+ files | Submit once; repeat rapidly. | One attempt and one aggregate submission become Submitted. Repeat does not duplicate either. Attachments remain a single bundle. |
| HW-05 | `/assessments/manage/homework/` and `/homework/<submission_id>/` | Moderator A | Open queue/detail for Student A1; try Student A2 and Cohort B IDs; try Course Quiz and Semester Exam grading URLs. | Moderator sees/grades only Diploma Homework for active students in assigned Cohort/cluster scope. No Course, Semester Exam, release, profile, or unrelated history access. |
| HW-06 | POST `/assessments/manage/grading/homework/<submission_id>/grade/` | Submitted Homework | Moderator enters aggregate score and feedback; try above max/negative; change existing grade where applicable. | One aggregate score/feedback covers all files. Invalid scores are rejected. Moderator never gets release authority. |
| HW-07 | POST grading release/hide routes | Graded manual Homework | As Moderator, forge release request; as Admin, release; inspect Student result; hide as Admin. | Moderator is denied. Before Admin release, student sees no score/feedback. After release, allowed grade/feedback appears; answer keys never do. Hide removes visibility without deleting grade. |
| HW-08 | Same | Automatic-release Homework configuration | Admin grades (or Moderator grades). | Release occurs only after final aggregate grade commits. It does not release a partial/failed transaction. |

## 10. Diploma Semester Exam — online path

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ONEX-01 | Instructions/start/take routes | Online Student A1; configured sections and sufficient bank | Start exam and inspect snapshot; repeat with a second student. | Each attempt contains configured random bank MCQs plus all manual written questions. Snapshots may differ, while section maxima/order stay stable. |
| ONEX-02 | POST autosave and submit | In-progress online exam | Save MCQ/written answers; refresh; submit; double-submit. | Answers bind to per-attempt snapshot questions, not mutable source-bank rows. One terminal attempt results; no duplicate snapshot/answers. |
| ONEX-03 | Admin edits/deletes bank source after Student A1 starts | Existing snapshot | Start Student A1; change source bank; reopen Student A1; start Student B1 if in scope. | Student A1 keeps immutable snapshot. Later attempt may use updated bank. Existing grading structure remains coherent. |
| ONEX-04 | Reviewer assignment create/list and `/assessments/reviewer/dashboard/` | Admin; Reviewer A | Assign exact Cohort/Semester/Exam online scope; sign in Reviewer. | Assignment appears with anonymous work count. Reviewer sees Cohort, Exam, anonymous code, and grading data only—never name/email/phone/national ID/profile/history. |
| ONEX-05 | POST `/assessments/reviewer/online/<assignment_id>/claim/`; GET/POST grading page | Multiple ungraded online submissions | Open two Reviewer sessions and request work simultaneously; grade written answers; finalize. | A submission is not claimed twice. Claim is scoped and collision-safe. Scores cannot exceed question max; finalization occurs only when assigned written work is complete. |
| ONEX-06 | Reviewer routes with tampered assignment/answer IDs | Reviewer A and Reviewer B | Substitute other reviewer, cohort, exam, claim, and answer IDs. | Denied without identity leakage or grade change. Reviewer cannot grade objective MCQ or release grades. |
| ONEX-07 | `/assessments/manage/exam-review/` and detail/submit | Fully graded online attempt | Admin inspects MCQ + reviewed written total; mark exam stage Passed; separate fixture below threshold mark Failed; try Interview action here. | Grade completeness and exam-stage decision persist with actor/time. Fail is only valid when required minimum rule allows. No progression Interview action exists at exam stage. |
| ONEX-08 | Submit an online Semester Exam with every written response blank, then use Reviewer claim and Admin grading/release | At least one written question and enough MCQ points that an MCQ-only calculation would pass | Submit with blank written answers; inspect attempt state and pass value, reviewer queue/count, claim, finalization, and progression readiness before/after every written score. | Blank written questions remain unscored and under review; `is_pass` stays unknown and the attempt cannot be released or used by progression until every required written answer is graded. Each missing section remains claimable; another reviewer cannot grade an answer outside their exact claimed section. |
| ONEX-09 | Results/history before and after Admin release | Reviewed attempt | Student opens results before release; Admin releases; Student reopens. | Before release no numeric grade/feedback/weighted contribution. After release policy-allowed grade appears. Snapshot correct answers never appear. |
| ONEX-10 | Randomized question points and stable maximum | Two online Diploma attempts with different bank question points; matching offline exam code | Start both attempts; compare selected questions, per-question points, section totals, persisted possible-points snapshot, all-correct percentages, and Admin review percentage. Compare offline section grade maximum. | Each random set sums to the same configured section/assessment maximum; allocated question points preserve bank weighting to the nearest cent; all-correct score is 100%, threshold decisions use that stable denominator, and offline grading uses the same section maximum. |
| ONEX-11 | Autosave persistence and flush/submit race | Online Diploma attempt; Redis and Celery available, then separately unavailable for a controlled local check | Save disjoint answer deltas; delay a flush while submitting; retry an old flush after submit; separately simulate Redis failure and task-scheduling failure. | Redis-only state is not cleared before its database commit; Admin's final durable submission wins; terminal attempts reject stale flushes; autosave falls back to PostgreSQL when Redis or task scheduling fails, and no stale generation mutates a submitted attempt. |
| ONEX-12 | Delayed write-behind flush across the timer/deadline | Online Diploma attempt with write-behind enabled; pause the Celery autosave worker after the live autosave request is accepted | Save an answer before the attempt deadline; keep the flush queued until after the timer/deadline expires; resume the worker; verify the stored answer, then separately try a fresh autosave and submit after expiry. | The already accepted server-side delta is durably persisted even though its delayed flush runs later; fresh autosave/submit requests after expiry remain rejected; no stale generation can mutate a submitted attempt. |
| ONEX-13 | `/assessments/manage/reviewer-assignments/` and POST `/assessments/manage/reviewer-assignments/<assignment_id>/revoke/` | Admin; canonical active assignment plus a retained legacy section-scoped duplicate with an in-progress claim and historical finalized claims | Open the list from a selected cohort/semester workspace; try creating the same logical assignment again; attempt to revoke the legacy row while its claim is active; finish the claim and retry; then try claiming or offline grading through the revoked assignment ID. | Duplicate creation is blocked. Revocation is refused while a claim is in progress. After finalization, the legacy row can be deactivated without deleting its claims or audit history; the canonical row remains active, the revoke event is auditable, the selected workspace scope is preserved, and the revoked ID cannot claim or grade new work. |

## 11. Diploma Semester Exam — offline path

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| OFFEX-01 | `/assessments/manage/<id>/roster/`; POST `/roster/generate/` | Offline and online Diploma applications/enrollments | Generate roster twice. | One collision-safe code per assessment+enrollment; repeat does not duplicate. Delivery mode is copied from authoritative application/enrollment projection. |
| OFFEX-02 | POST `/roster/<code_id>/change-mode/` | Unused/unprinted code | Change through UI; inspect corresponding application delivery mode and code. Then try used/printed/historical code. | Allowed change updates authoritative application mode and only unused projection. Historical used/printed values remain preserved. Record ambiguity as an open question if “printed” is not represented in UI. |
| OFFEX-03 | `GET /roster/export-offline/` | Mixed roster | Export as Admin and unauthorized role. | Admin receives only intended offline code data. Unauthorized user is denied. Export contains no unnecessary identity beyond the operational roster contract. |
| OFFEX-04 | Reviewer offline assignment and `/assessments/reviewer/section/<assignment_id>/` | Reviewer A assigned one section | Enter section total for anonymous code; test negative, above max, non-number, wrong section/code. | Only score within configured section max is saved. Reviewer sees no reconstructed question/answer data and no student identity. |
| OFFEX-05 | Concurrent offline grading | Two reviewer sessions and same code/section | Submit same section simultaneously, then different sections simultaneously. | Unique code+section grade prevents duplicate rows. Conflicting grade behavior is deterministic and does not produce a partial total. Different sections remain independently valid. |
| OFFEX-06 | `/assessments/manage/<id>/offline-grades/` and section action | Admin | Inspect section totals; attempt finalization with missing section, then after all sections complete. | Missing section blocks complete reviewed total. Completed sections sum into one preserved offline Semester Exam grade. |
| OFFEX-07 | Admin exam review and release | Complete offline attempt | Mark Passed/Failed per threshold, release, inspect Student history. | Grade remains preserved independently of stage decision. Student sees only released academic result; anonymous code is displayed safely and correct answers are irrelevant/not exposed. |
| OFFEX-08 | Offline code section-layout snapshot and historical review | Offline code issued for an assessment with at least two sections | Record the code's section titles/order/maxima; change a section title/order/maximum and add or remove a section where allowed; enter grades only for sections in the issued code; finalize and inspect Admin review. Then make the code inactive through the supported administrative path if available and reopen the historical result. | The issued code retains its saved section identity/order/title/maxima and denominator; out-of-layout grades are rejected; a referenced section cannot be deleted; later edits do not change the saved percentage/pass threshold or labels. Historical result remains evaluable after code retirement. Record any unavailable retirement control as an unverified UI path. |

## 12. Exceptional access, attempt history, timing, and precision

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| EXC-01 | `/assessments/manage/<id>/exceptional-access/` and enrollment picker | Course Quiz and Semester Exam | Search/paginate picker using HTMX; compare direct full request; select exact eligible enrollment. | Course Quiz picker uses CourseEnrollment; Semester Exam uses DiplomaEnrollment. Ineligible, expired, restricted, cross-scope enrollments are absent/rejected. HTMX does not push fragment URL. |
| EXC-02 | Same POST action | Eligible assessment | Try close ≤ open, empty reason, duplicate active override, and unsupported Homework type. | Validation blocks each. At most one active override per assessment+exact enrollment exists. |
| EXC-03 | POST `/exceptional-access/<override_id>/cancel/` | Active unused and used overrides | Cancel unused; try cancel again; inspect used override history after fresh run. | State becomes Cancelled once. Audit preserves actor/reason/window/use metadata. Used override and linked attempt history remain. |
| EXC-04 | Start/autosave/submit during override | Original assessment closed | Start inside override window, then autosave/submit after override closes or enrollment expires. | Every mutation revalidates override plus exact active enrollment and timer. Override does not become a permanent bypass. |
| EXC-05 | Grade/result projections | Scores around a threshold, e.g. raw 59.995 and 59.994 | Complete fixture through normal UI/grading and inspect percentage/decision. | Percent is Decimal quantized to two places with `ROUND_HALF_EVEN` before pass/fail comparison. `possible <= 0` yields no result, not a silent fail. |

## 13. Entrance Exam — separate admissions workflow

All routes in this section use `/entrance-exams/`. Do not substitute normal
Assessment routes, status words, attempt models, result pages, or grade-release
behavior.

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ENT-01 | `/entrance-exams/manage/create/`, detail/edit, question add/edit/delete | Admin; Cohort A | Create one Cohort Entrance Exam with valid window, duration, MCQ and written questions; test invalid MCQ structure and invalid window. | Exam remains Cohort-only. Invalid configuration is rejected with preserved form data. No normal Assessment row/route is created. |
| ENT-02 | POST application detail action `invite_to_entrance`; `/entrance-exams/manage/<exam_id>/emails/` | Diploma application in allowed status | Assign exam and optionally send invite; repeat assignment/send. | One EntranceAttempt per application+exam. Resend is explicitly logged as resend; ordinary retry does not create duplicate attempt or duplicate non-resend log. |
| ENT-03 | `/entrance-exams/access/<uuid-token>/`, `/qr/` | Assigned Student | Open own token and QR; try another student's authenticated session, altered token, unauthenticated session. | Only owning Student may see exam context. Token/QR does not expose grade, threshold, identity data, or another student's access. |
| ENT-04 | POST `/start/`; GET `/take/` | Assigned; before/open/closed/inactive fixtures | Start in each condition; double-start in two tabs. | Only Assigned + active + open-window starts. One attempt transitions to In Progress; repeat cannot restart or extend deadline. |
| ENT-05 | POST `/snapshot/` | In-progress attempt | Save each answer, edit it, refresh; send wrong question/choice and wrong answer type. | Latest valid per-attempt snapshot persists. Invalid cross-question data is ignored/rejected without exposing correctness or corrupting other snapshots. |
| ENT-06 | POST `/submit/` | All questions answered | Submit valid complete answers; separately omit one, duplicate a question, send unknown question, or mix written/choice payload; double-submit valid form. | Full coverage exactly once is required. Invalid submission remains unfinalized. Valid submission completes once under row lock; duplicates do not create extra answers/results. |
| ENT-07 | Timer expiry and POST `/finalize-expired/` | In-progress attempt with saved snapshots | Wait past server deadline; call finalize twice; alter client clock/localStorage before call. | Only server-persisted snapshots are finalized. First call is durable; later calls return stable terminal state. Client state cannot extend or replace server data. |
| ENT-08 | `/entrance-exams/manage/grading/` and attempt detail | Completed written exam | Admin grades written answers; test negative/above maximum and threshold boundary. | Numeric advisory score/result is visible to Admin only; two-decimal scoring rule applies. Student result remains non-numeric. |
| ENT-09 | POST `/entrance-exams/manage/grading/<attempt_id>/decision/` | Graded attempt | Record Accept, Reject, and Interview on separate fixtures; test direct GET and stale/repeated decision. | POST only. Administrative application outcome may differ from numeric result and is audited. Stale/invalid transition does not overwrite history. |
| ENT-10 | `/entrance-exams/access/<token>/results/`, `/profile/exams/`, Student dashboard/record | Submitted/graded/decided attempts | Inspect after submission, grading, and final admin decision; inspect page source. | Student sees Assigned/Scheduled/In progress/Submitted/Awaiting review, then Accepted/Rejected/Interview requested as appropriate—never numeric grade, pass/fail, threshold, correct choice, or answer key. |
| ENT-11 | `/entrance-exams/manage/<exam_id>/finalize-pending/` | Multiple expired in-progress attempts | Invoke once, then again. | Pending attempts finalize from saved snapshots; already terminal attempts are unchanged; repeat finalizes zero additional rows. |
| ENT-12 | Bulk email tabs and `/emails/log/`; POST `/emails/<app_id>/resend/` | Test mail sink | Send/resend selected invitations/results; search/filter logs; exercise single-application resend. | Queue/log status, masked recipient handling, actor, time, action, resend flag, and failures are visible to Admin. Submission remains committed even if email fails. Single-recipient behavior is recorded as pass/fail for the open question in section 20. |
| ENT-13 | `/entrance-exams/access/<token>/`, POST `/start/`, Admin grading/decision, and email history | Same active cohort exam with assigned, unassigned, accepted, activated, and rejected application fixtures; two browser sessions for one assigned student | Open the shared token with each fixture; confirm an unassigned student is denied and no attempt is created; confirm accepted, activated, and rejected application tokens are denied; submit two starts as simultaneously as practical; grade one attempt below/above the threshold, then accept the application independently of that calculated result; inspect the resulting neutral email/log copy. | Token access requires the pre-existing exact assigned attempt and the allowed application state. Only one first start timestamp/deadline is kept and a replay is idempotent. Admin sees the advisory numeric/pass-fail result and can accept/reject/interview/revert independently. Student/result email history contains only submitted/awaiting-admission-review language and no numeric points or pass/fail; external delivery is **UNVERIFIED** unless observed in the configured sink. |

## 14. Attendance

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| ATT-01 | `GET/POST /attendance/sessions/create/` | Admin | Create Diploma session with Cohort A+Semester 1 and valid Cairo times; create Course session with V1; test mixed/mismatched targets and close ≤ open. | Exact target validation holds; invalid form preserves selections/errors. Session begins Scheduled unless current service legitimately opens it from time state. |
| ATT-02 | POST `/sessions/<id>/open/` and `/close/` | Scheduled session | Open; try open again; close; try scan and close again. | Only Scheduled opens; only valid open session accepts scans; repeated/stale transitions are rejected without timestamp/actor corruption. |
| ATT-03 | Student attendance QR from authorized student context and `GET /attendance/scan/` | Open Cohort A session | Allow camera; scan Student A1; remain on scanner; scan Student A1 again; scan Student B1. | First scan records and immediately returns ready for next. Duplicate reports Already recorded without a second row. Wrong cohort reports a stable rejection and remains ready. |
| ATT-04 | POST `/attendance/scan/api/` | Secretary | Send invalid format, invalid/expired signature where applicable, closed session, wrong session/cohort, inactive enrollment, and missing session. | Structured codes/messages distinguish invalid request/token, wrong scope, closed window, and other domain rejection. No invalid record is created. |
| ATT-05 | Scanner UI | Browser with camera controls | Deny permission; retry; simulate network failure; restore; scan consecutive valid students. | States cover permission requested, ready/scanning, detected, submitting, recorded, duplicate, invalid/wrong/closed, network error, and ready again. No navigation after each scan. |
| ATT-06 | Concurrent duplicate scan | Same student QR, same session, two Secretary/Admin devices | Submit as simultaneously as manual coordination allows. | One AttendanceRecord exists because session+enrollment is unique. Both devices get deterministic recorded/duplicate outcomes, not server error. Mark high-concurrency proof as LOAD TEST REQUIRED. |
| ATT-07 | `GET /attendance/sessions/<id>/`, `/records/` | Secretary and Admin | Compare list/detail/records; inspect student/status/scanner/time labels. | Secretary may view attendance records but not create/open/close/delete. Status uses icon+translated text, not color alone or raw code. |
| ATT-08 | POST `/sessions/<id>/delete/` | Empty and recorded sessions | Delete empty session; try recorded session; direct GET. | Empty deletion is audited. Recorded academic evidence cannot be deleted. GET does not mutate. |
| ATT-09 | `/attendance/my-attendance/` and `/profile/record/` | Student with past, closed, open, and future sessions | Open both. | Percentage excludes future sessions and uses the same required-session cap as progression evaluation. Student sees only own data. |
| ATT-10 | Long-lived Student QR | Same student across two valid sessions | Reuse the valid Student QR in the later session. | QR itself remains usable; server authorizes against current session/enrollment/context. Do not report long lifetime alone as a defect. |
| ATT-11 | Edit a Diploma session/period window, inspect retained scans, then sync its cohort+semester grade items | Existing ended attendance session with scans and a previously synchronized attendance grade item; Admin | Move the session window so retained scans now fall before open/after close, or move the close time into the future so no session is currently countable; inspect detail/exclusion reasons; sync the exact cohort+semester; inspect the derived grade item and progression preview. | Raw scans remain available as evidence but out-of-window scans are excluded. A changed summary replaces the old derived percentage; when no session is countable the prior derived score is cleared (not left stale and not written as 0%). Any manual score/notes/visibility metadata remains intact. |

## 15. Semester evaluation, progression, Interview, and rejoin

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| PROG-01 | `/progression/manage/diploma/<d>/cohort/<c>/semester/<s>/evaluation-settings/` | Admin | Set three weights not totaling 100, invalid percentages, then valid settings. | Invalid config is rejected. Valid scope is exact Cohort+Semester and renders translated component names. |
| PROG-02 | POST `/progression/manage/grade-items/<enrollment>/<semester>/sync/` | Released/available component sources | Sync one student; repeat; tamper cohort/semester/enrollment. | Attendance, Homework, Semester Exam grade items update idempotently for exact enrollment+semester. No duplicate grade items or cross-cohort data. |
| PROG-03 | Component preview/evaluate routes | All components applicable and present | Use scores where one component fails minimum but weighted overall passes; finalize. | Calculated evaluation is FAIL. Component failure is explicit; grades are not rewritten. |
| PROG-04 | Same | All applicable components pass but weighted overall fails; then full pass fixture | Finalize each. | First is calculated FAIL by overall minimum; second is calculated PASS. Percentages are rounded to two decimals before comparisons. |
| PROG-05 | Same | One component has no configured source | Preview/finalize. | Unconfigured component is Not applicable: no blocked reason, excluded denominator, remaining weights renormalize to 100%. |
| PROG-06 | Same | Component is configured but source/result missing | Preview/finalize. | Missing applicable result produces a blocked reason, never automatic FAIL or zero. Finalization is unavailable until source is ready. |
| PROG-07 | POST `/progression/manage/students/<student_id>/evaluate/` and bulk evaluate route | Current active semester plus older/future semesters | Evaluate current active semester; attempt stale/future parallel action; repeat finalized action. | Recommended action is current active semester only. Finalized calculated result is immutable by default; repeat cannot silently rewrite it. |
| PROG-08 | POST `/grade-items/.../reevaluate/` and bulk reevaluate | Finalized evaluation with downstream pending/final outcome | Explicitly re-evaluate after changed source. | Fresh source calculation is stored with before/after audit; downstream final progression resets to pending. Previous history remains. |
| PROG-09 | POST confirm/promote and exceptional-promote routes | Calculated PASS and FAIL fixtures | Confirm normal PASS; for FAIL submit exceptional promote blank then valid reason. | Normal continuation follows calculated PASS. Exceptional promotion requires reason, preserves calculated FAIL, and labels final decision exceptional. |
| PROG-10 | `/progression/manage/interview/<enrollment>/<semester>/request/` | Finalized PASS and FAIL fixtures | Request Interview from each; inspect Student view; attempt promote while pending. | Both can become Interview Pending. Student sees neutral pending state. No final progression resolves until interview completion. |
| PROG-11 | `/interview/.../decision/` and inline pass/fail HTMX actions | Interview Pending | Complete as Continue on calculated FAIL and Do Not Continue on calculated PASS; omit required details/reason if form demands it. | Final decision may differ either direction, is marked exceptional/override, and preserves calculated result, interview, actor/time/reason. |
| PROG-12 | HTMX inline Interview actions | Same fixture | Execute with HTMX; reset fixture and execute canonical full-page form; refresh canonical cohort-semester URL. | Business result, authorization, audit, messages, and available next actions are equivalent. Fragment replaces only the intended student row and remains refresh-safe. |
| PROG-13 | Lock/unlock and other current student actions | Progress record | Lock; try progression mutation; unlock; repeat. | Locked state blocks incompatible transitions. Actor/time/reason/history remain; UI only exposes valid current action. |
| PROG-14 | `/progression/manage/rejoin/eligibility/` and POST `/rejoin/create/<enrollment>/` | Failed Semester N in Cohort A; immediate Cohort B; target Semester N not finalized | Open eligibility; prepare plan; repeat. | Only immediately-next Cohort same-semester eligible row is actionable. One active/planned plan per student. No new payment, intake, or Entrance Exam is requested. |
| PROG-15 | POST `/rejoin/<plan>/activate/` | Planned rejoin | Activate; inspect both enrollments and Student Learning/Record. | Source enrollment becomes restricted; one target enrollment is active starting at Semester N; successful source history combines with target path without migration/overwrite. |
| PROG-16 | POST `/rejoin/<plan>/cancel/` | Planned and active fixtures | Cancel plan with reason; revoke active plan with reason; repeat. | Planned cancels safely. Active revocation deletes replacement enrollment and restores source active; original history remains. Repeat cannot create/restore duplicates. |
| PROG-17 | Rejoin missed window | Target Cohort B Semester N finalized | Open eligibility and try crafted create POST. | Direct rejoin shortcut is absent/rejected. No configurable grace/deadline is invented; later return uses normal application rules. |
| PROG-18 | `/profile/progression/`, `/progression/my-progress/`, `/profile/record/` | Student with history | Compare calculated result, final outcome, interview, rejoin, completion, and grades. | Student sees own preserved history. Grade visibility follows release independently; Interview Pending is neutral; exceptional differences remain explicit. |
| PROG-19 | Contextual rejoin eligibility and plan routes under `/progression/manage/diploma/<diploma>/cohort/<cohort>/semester/<semester>/` | At least two cohorts/semesters with both eligible and ineligible records and one existing plan | Open Eligibility and Plans from a semester workspace; change plan status; try opening a row from another cohort through the current context URL; then open the secondary diploma/global report. | Context pages show only the selected semester, do not offer a scope-switching selector, and route actions back into that context. A forged cross-context action is rejected without mutation. Secondary reports may filter across scopes without replacing the semester workspace. |
| PROG-20 | Semester preview/finalization | One Required and one Optional Diploma assessment; vary inherited defaults and semester overrides | Leave each assessment without an attempt (also try Assigned and Expired). Exercise Required + Require All on/off and Optional + Allow Missing on/off, first inherited then overridden. | Required missing blocks only when Require All is on. Optional missing blocks only when Allow Missing is off. Allowed missing work is omitted, never zeroed. Semester override wins independently over the diploma default. |
| PROG-21 | Semester preview/finalization | Required/Optional Homework and Semester Exam with policy settings both on and off | Try latest attempts in progress, submitted, under review, and a graded Semester Exam not yet given Admin Pass/Fail. | Every pending/incomplete attempt blocks regardless of Required/Optional or missing-work flags. The exam remains blocked until its separate Admin review decision is saved. |
| PROG-22 | Semester component preview | Seeded Required Homework released at 14/20 for the online Diploma 1 student, a 60-point Optional Homework with no attempt, and the same student's scored and Admin-reviewed Semester Exam | Preview while the optional assessment is missing and omission is disabled; enable omission and compare; then submit/grade the Optional Homework and compare both included scores. Finally omit every assessment in the Exam component while Attendance remains scored. | The missing Optional Homework blocks while omission is disabled; when allowed, it is omitted and the included Required Homework remains 70% (14/20), not zeroed. With both scored, aggregation uses their earned/possible points. An all-omitted Exam is Not applicable for that student; remaining component weights are renormalized without changing configured weights/minimums. |
| PROG-23 | Semester preview/finalization with no applicable source | Required/Optional assessment fixtures and no attendance sessions | Configure policy to omit every missing assessment in Homework and Exam. Preview/finalize with no attendance result; then add one valid Attendance result and retry. | With no scored component, preview gives an explicit blocked reason and finalization is refused. Once Attendance is scored, omitted assessment components stay Not applicable and Attendance is reweighted; no zero evaluation is created. |
| PROG-24 | Assessment exceptional-access attempt history → semester preview, grade sync, finalize, and re-evaluate | A completed attempt, then a later exceptional attempt for the same student/assessment | Start a later attempt and leave it in progress; submit/grade it below the earlier score; inspect preview, synced grade item, finalized source snapshot, and explicit re-evaluation snapshot. Separately cancel a still-later attempt. | A newer non-cancelled in-progress attempt blocks instead of reusing the older score; after grading, every path uses the latest attempt even when its score is lower. A cancelled attempt is ignored. Multiple distinct exam assessments still use the locked existing component aggregation, independently of retake ordering. |
| PROG-25 | Finalize and explicitly re-evaluate one semester | Scored Attendance and assessments; no pending work; a resolved final outcome and no interview | Finalize; resolve a final outcome; inspect the immutable config/source snapshot; edit a requirement flag and/or inherited policy; inspect without re-evaluating; then use the explicit Admin re-evaluation action and inspect audit before/after. | Existing finalized data is unchanged by edits alone. Re-evaluation records the effective policy/value sources, each assessment requirement, included/omitted attempt IDs and scores, and the prior/new snapshots; the downstream final outcome returns to pending. |
| PROG-26 | Same diploma/semester represented in two cohorts plus a Course Quiz | At least one assessment and result in each cohort | Preview each student's result and inspect the persisted source snapshot. | Only active Diploma assessments for the exact Cohort+Semester are considered. A sibling cohort or Course Quiz never blocks, fills, or changes this semester component. |
| PROG-27 | POST `/rejoin/<plan>/activate/` and contextual activation route | A planned rejoin plus stale fixtures where the source was changed or expired, the failure was re-evaluated, the target is no longer the immediate next cohort, the diploma/semester relation changed, or a target-semester evaluation was finalized | Attempt each activation after making its prerequisite stale; confirm a plan with an inactive/expired source has no Activate button; then activate an unchanged valid plan. | Every stale plan is rejected without creating/restricting enrollments or changing plan status. The UI does not offer activation for an inactive/expired source. A valid activation atomically revalidates the source failed outcome and target, restricts the source, and creates exactly one fresh active target enrollment at the failed semester. |
| PROG-28 | Contextual and global Rejoin Eligibility pages | One planned rejoin that becomes stale because its target is no longer the immediate next active cohort, plus one valid planned rejoin | Open Eligibility before and after the state change; inspect each row's state and available action; try direct activation for the stale plan. | A stale plan is visibly ineligible and has no Activate action; valid rows remain actionable. Direct activation independently rejects the stale plan without mutation. |
| PROG-29 | Contextual/global Rejoin Eligibility and create-plan POST | A finalized failed outcome on an Expired, Completed, or Restricted source enrollment; an Active source whose `expires_at` is already past while the expiry job is delayed; one otherwise eligible Active, non-expired source | Open Eligibility and the create-plan route for each inactive/expired source; also submit a crafted valid-looking create POST. | Inactive/expired source rows are not actionable, including a stale `status=active` row past its expiry timestamp. The create page shows no available transition, and service POST rejects without creating a plan. The valid Active source remains eligible; no historical status is changed. |
| PROG-30 | Concurrent final decision, Interview request/completion, promotion, and re-evaluation | One finalized evaluation with two Admin sessions; use separate eligible actions that would conflict if allowed to overlap | Coordinate simultaneous requests for Interview vs final PASS/FAIL, and Interview completion vs final resolution/re-evaluation. Reopen the workspace and inspect outcome, interview, promotion decision, progress row, and audit history after each race. | PostgreSQL serializes through evaluation → outcome → interview. Exactly one valid state transition wins; stale actions receive a validation response. No resolved outcome is overwritten to Interview Pending/Pending, and no promotion coexists with an unresolved interview or re-evaluation snapshot. Mark multi-session race proof as LOAD TEST REQUIRED. |
| PROG-31 | Retired legacy `promote-year` route and historical year-boundary metadata | Admin and Student; two otherwise identical pass histories that cross an academic-year boundary, one with and one without an old `academic_year_promote` row | Try opening and posting to `/progression/manage/students/<student_id>/promote-year/`; inspect historical decision records; compare next-semester visibility, assessment availability, and attendance context for both students. | The legacy route is not registered and cannot mutate progression. Historical decision records remain readable and unchanged. Neither the record nor old academic-year configuration changes visibility: canonical semester progression state controls next-semester access for both students. |
| PROG-32 | Semester preview/finalization with only cancelled attendance sessions | Active Cohort+Semester evaluation config, at least one cancelled period session, and otherwise valid assessment results | Preview the semester, inspect attendance applicability and blocked reasons, then finalize if the other applicable components are complete. | Cancelled sessions do not make Attendance an applicable or blocking source; they remain excluded from attendance totals. Active/future non-cancelled sessions still make the component applicable and prevent premature finalization until a countable result exists. |
| PROG-33 | Contextual semester progression CSV export | Disposable synthetic student whose name begins with `=`, `+`, `-`, or `@` (including leading spaces) | Download the semester CSV and inspect the name cell in a spreadsheet application without enabling formulas/macros. | The name is emitted as literal text, not an executable spreadsheet formula; other numeric and translated decision columns remain unchanged. |

## 16. Certificates

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| CERT-01 | `GET/POST /certificates/manage/templates/create/`; POST `/certificates/manage/templates/<template_id>/source/upload/` | Admin | Create HTML-only, PDF, and JPG/JPEG templates; test wrong extension/content and oversize. | Valid sources create protected preview; invalid sources fail clearly without orphaned public objects. Use PSD only under the open question in section 21. |
| CERT-02 | `/certificates/manage/templates/<id>/editor/`; POST mapping save | Ready source preview | Place Student Name, Date, certificate number, diploma, course, semester, cohort, graduation date; resize/style/align/direction; save/reload. | Mapping persists normalized page coordinates and width/height boxes; UI loads saved layout. Unknown fields/invalid coordinates are rejected. |
| CERT-03 | POST `/preview-generate/` | Arabic and English templates | Generate preview for Arabic and English/mixed-direction sample data. | Arabic uses embedded Cairo shaping/bidi and correct RTL alignment; English uses Inter/LTR; codes/dates remain LTR. Preview and final use the same rendering path. |
| CERT-04 | POST `/certificates/manage/generate/<student_id>/` | Completed/graduated enrollment and corrected profile name | Generate; double-submit; separately try incomplete student. | One active certificate per enrollment. Current corrected profile name appears. Ineligible student is rejected. Concurrent duplicate proof is LOAD TEST REQUIRED. |
| CERT-05 | Student `/certificates/` and `/<cert_id>/download/` | Available own certificate | Download as owner; alter ID to another student; try unauthenticated/expired student with preserved certificate history. | Owner gets protected, time-limited delivery. Other student is denied. Historical certificate remains accessible per policy even after enrollment expiry. |
| CERT-06 | POST revoke/regenerate routes | Existing certificate | Revoke with reason; inspect Student list/download; regenerate; inspect old and new records. | Revocation preserves immutable issued history and disables inappropriate access. Regeneration uses governed status/history and does not silently overwrite the old artifact/number. |
| CERT-07 | POST template delete/clear-source | Template with and without generated history | Clear source then delete disposable template; inspect storage/audit; try GET. | POST-only. Storage failures become durable reconciliation/error state. Generated-certificate history is not cascade-erased. |

## 17. Communications, intake administration, audit, and private storage

| ID | Route/action | Preconditions | Exact steps | Expected result |
|---|---|---|---|---|
| COM-01 | `/communications/templates/`, create/edit/variables/preview, footer | Admin | Create/edit template; insert supported variables; submit unresolved/unknown variables; preview Arabic/English and footer. | Supported variables render safely; unresolved variables block send/are clearly reported. Template and footer are translated/escaped; untrusted text is not injected as executable HTML. |
| COM-02 | `/communications/bulk/create/` → `/bulk/preview/` → history/detail | Test mail sink | Select recipients, add valid/invalid manual email, preview, send; refresh/double-submit. | Recipient set is de-duplicated; invalid addresses are skipped/reported without full address in logs. One BulkEmailSend lifecycle progresses through queue/processing/sent/failed. |
| COM-03 | Email failure | Configure sink to fail safely | Trigger application, intake, Entrance result, and bulk messages. | Domain transaction remains committed. Email shows Failed/retry metadata without exposing secrets or full sensitive payload. |
| COM-04 | `/intake/admin/forms/` create/detail/field delete and response routes | Admin | Create required and optional fields, multi-choice options; submit malformed options; inspect responses. | Builder validates field configuration. Response display uses labels/options, not stored codes. Only Admin can access student responses. |
| COM-05 | `/audit/` and `/audit/<event_id>/` | Mutations from prior sections | Filter/list/detail payment waiver, grade release/override, attendance delete, progression, rejoin, certificate, access changes. | Events are append-only, role-protected, and include actor/time/target/before-after/reason as applicable. No mutation control appears. |
| COM-06 | `/storage/protected/` generated URL | Private homework, document, certificate, audio assets | Use valid owner/Admin URL; copy to unrelated profile; wait past expiry where signed link applies; alter key/query. | Signed/proxy authorization is rechecked. Expired/tampered/cross-user access fails; no bucket/object credentials or public URL leak. |
| COM-07 | `GET/POST /manage/landing/` and `GET/POST /accounts/manage/settings/` | Admin, Super Admin | Update landing/general settings with valid data; submit invalid data; compare public `/` and staff navigation; try limited roles. | Valid settings render through the intended public/admin pages, invalid forms preserve errors, and limited roles are denied. No raw configuration secret is displayed. |

## 18. HTMX and full-page parity matrix

For every row, perform the same filter/action twice on resettable fixtures:

1. With JavaScript/HTMX enabled through the rendered UI.
2. With JavaScript disabled, or by opening the canonical URL and submitting the
   ordinary form/link.
3. Refresh the resulting browser URL.
4. Compare records, permissions, translated labels, errors, pagination, focus,
   and available actions.

| ID | Canonical route and HTMX target | Expected parity |
|---|---|---|
| HTMX-01 | `/applications/programs/`, `#results` | Search, status/type/action/cohort filters and pagination match; pushed URL refreshes to the same full page. |
| HTMX-02 | `/academics/manage/diplomas/`, `/courses/`, enrollment lists, `#results` | Same rows, scope, actions, labels, and pagination in fragment/full page. |
| HTMX-03 | `/assessments/manage/`, `#results`; question form fragments | Same validation and entered values. Fragment response never becomes a broken stable browser page. |
| HTMX-04 | `/attendance/sessions/`, `#attendance-results` | Same session rows/filters and authorization; canonical URL is refresh-safe. |
| HTMX-05 | `/payments/` payment/waiver/import result frames | Same filtered datasets and translated statuses; no tab/frame leakage after refresh. |
| HTMX-06 | `/entrance-exams/manage/` and email tab/log fragments | Same selection, search, bulk actions, and log state. Reload retains a usable canonical page. |
| HTMX-07 | `/profile/apply/` plus `/applications/program-targets/` | Same eligible target choices and validation when HTMX is absent. |
| HTMX-08 | Student sidebar pages under `/profile/` replacing `#main` | Shell persists under HTMX; direct navigation returns complete page; business state/actions are identical. |
| HTMX-09 | Progression inline Interview row actions | Same backend transition/audit as full-page forms; only intended row swaps; CSRF and role checks are identical. |
| HTMX-10 | Exceptional-access enrollment picker | Search/pagination replaces only picker, uses `hx-push-url=false`, and direct canonical exceptional-access page remains usable. |
| HTMX-11 | Global search `/workspace/search/` | Results are role-scoped; rapid typing replacement cannot flash unauthorized stale results. |
| HTMX-12 | Student timed assessment, then sidebar navigation replacing `#main` | The old timer/autosave listeners and recurring interval stop when the form is detached; at most the already-debounced answer save may finish. Returning to the canonical assessment URL creates one fresh timer/autosave instance and no stale request changes a submitted/closed attempt. |

Also verify JavaScript used inside HTMX-inserted content initializes after the
relevant HTMX lifecycle, not only after `DOMContentLoaded`. Modal confirmation,
form disabling/loading, scanner, assessment autosave/timer teardown (including
HTMX-12), and local Alpine disclosures must not own or infer backend
authorization/state.

## 19. Arabic RTL, English LTR, responsive, labels, and accessibility

Run this matrix on at least: public registration, Admin application detail,
Assessment Take, Reviewer grading, Attendance scanner, Progression semester
table, Certificate editor, Student Learning, and Student Record.

| ID | Route/action | Exact steps | Expected result |
|---|---|---|---|
| UI-01 | `/i18n/setlang/` from each representative page | Switch Arabic → English → Arabic; refresh and navigate HTMX/full page. | Arabic is RTL, English LTR, choice/action/error text translates, and selected language persists. |
| UI-02 | Same pages | Inspect email, phone, date/time, seating/exam code, certificate number in Arabic. | Mixed-direction values remain readable LTR; surrounding layout stays RTL; directional icons mirror appropriately. |
| UI-03 | Same pages | Use 320px, 375px, 768px, and desktop widths; zoom to 200%. | No task-critical data/action disappears. Controls remain usable, tables/cards do not force destructive clipping, and touch targets are about 44px minimum. |
| UI-04 | Admin/student list pages | Compare desktop and mobile representations for same backend result. | Same rows, current actions, permissions, status labels, and pagination; presentation may differ but business logic does not. |
| UI-05 | Every state-heavy page | Inspect statuses including application, payment, attempt, Homework, exam review, attendance, enrollment, progression, certificate, email. | Each is icon + translated text, not color alone; stored codes never appear. Tone is domain-appropriate without collapsing distinct statuses into one generic label. |
| UI-06 | Forms and invalid submissions | Keyboard through labels/fields/help/errors/buttons; submit errors; inspect focus. | One `<h1>`, label association, `aria-describedby`, visible focus, error announcement/live region, keyboard-operable modal/drawer, and no focus loss after HTMX validation. |
| UI-07 | Confirmation modal and timed pages | Use keyboard only, Escape, cancel/confirm, reduced-motion preference. | Focus is trapped/restored, destructive reason fields are enforced where required, and motion is minimal/respects reduced motion. |
| UI-08 | Assessment/Entrance timers | Change browser clock/timezone; background tab; refresh near deadline. | Countdown may tick locally but authoritative deadline remains server-provided; no client manipulation extends submission. |
| UI-09 | Confirmed native forms, including form-level delete/rejoin actions and per-button payment-waiver actions | Cancel once, then confirm; try a required-field form with invalid and valid values; use a reason-required rejoin action. | Cancel performs no mutation and leaves controls usable. Confirm resumes normal native validation and submit listeners, preserves the selected submit button/action and reason, and the confirmation modal works again on later actions. |
| UI-10 | Primary navigation for Super Admin, Admin, Secretary, Reviewer, Moderator, and Student | One account per role; Arabic and English | Open the same workspace set at desktop and mobile widths; follow primary and contextual links; try direct URL access separately. | Primary destinations match each role matrix, Admin grading/progression work is reached from its selected academic context, secondary recovery reports remain reachable, and no navigation change weakens backend authorization. |

## 20. Two-cohort, ~200-student scale-readiness checklist

### 20.1 Manual checks on a pre-provisioned dataset

Use two active cohorts, Cohort A and Cohort B, with approximately 200 active
students each. These are usability and bounded-query observations, not a load
test.

| ID | Route/action | Manual steps | Expected result |
|---|---|---|---|
| SCALE-M01 | `/accounts/manage/students/`, application/payment/enrollment lists | Search, filter, move through first/middle/last pages for students in both cohorts. | Results paginate, remain correctly scoped, and do not freeze browser or render 400 rows at once. |
| SCALE-M02 | `/assessments/manage/homework/` | Moderator A filters queue and compares known A1 membership; Moderator B repeats. | Each queue contains only assigned active cluster/cohort students; no cross-cohort row appears. |
| SCALE-M03 | Reviewer dashboard/claim | Inspect online/offline ungraded counts and claim several items. | Counts and queue remain responsive; anonymous scope holds; no duplicate manual claim is observed. |
| SCALE-M04 | `/attendance/scan/` | Scan 20 consecutive valid QRs, duplicates, and wrong-cohort codes without leaving page. | Stable ready-after-result loop, no progressive slowdown, exactly one record per session+student. |
| SCALE-M05 | `/progression/manage/diploma/<d>/cohort/<c>/semester/<s>/` | Paginate/filter; sync/evaluate a small approved subset through individual actions. | Page is bounded and Cohort A/B projections remain isolated. Do not manually trigger unapproved bulk mutations across all 200. |
| SCALE-M06 | `/entrance-exams/manage/<id>/emails/`, `/emails/log/`, communications history | Search/select across pages and inspect totals. | Pagination/selection behavior is explicit; bulk action does not silently act on unselected/off-page records. |
| SCALE-M07 | `/assessments/manage/<id>/roster/` | Generate/inspect both cohort rosters separately. | About 200 unique codes per exact assessment/cohort; no code/enrollment crosses cohorts and repeated generation is stable. |
| SCALE-M08 | Student Dashboard/Learning/Record for samples at start, middle, end of each cohort | Open Arabic/English and mobile/desktop. | Response/UI stays usable and shows only sample student's exact cohort/enrollment history. |
| SCALE-M09 | Contextual semester progression page and its CSV export | Cohort at the approved 200-student size; multiple assessments with long attempt histories including canceled, pending, and lower-scoring latest attempts | Inspect the projection/export against the known latest non-cancelled attempt per student/assessment; use query profiling to compare rows fetched with distinct student/assessment pairs. | Results retain the canonical latest-attempt semantics from PROG-24, canceled attempts remain ignored, and the database returns at most one attempt per pair rather than transferring all historical retries into application memory. |

### 20.2 Cases reserved for the later confirmed load test

Do not claim these from manual clicking. They require an approved load plan,
non-production environment, monitoring, success thresholds, and cleanup plan.

| ID | Concurrent operation | Required proof |
|---|---|---|
| LOAD-01 | Approximately 400 students open dashboards/learning/assessment lists across two cohorts | Latency/error/resource measurements under the 2 vCPU / 4 GB / 30 GB ceiling; no unbounded query/memory growth. |
| LOAD-02 | About 200 students in one cohort start a Course Quiz or online Semester Exam near-simultaneously | One Attempt 1 per student, valid snapshots, bounded DB lock contention, no cross-student answers. |
| LOAD-03 | Periodic autosave from active exam users | Stable write rate, no lost last-valid snapshot, no stale overwrite, and acceptable DB/worker utilization. |
| LOAD-04 | Simultaneous submit at timer/window boundary | Exactly one terminal submission per attempt, deterministic server deadline enforcement, no 500/duplicate answers. |
| LOAD-05 | Two or more reviewers claim from a large anonymous queue | No duplicate claim, correct count convergence, bounded lock wait. |
| LOAD-06 | Offline code generation for ~200 enrollments in each cohort | Unique codes, idempotent rerun, bounded transaction/memory, no cross-cohort collision. |
| LOAD-07 | Attendance scanning burst from multiple devices | One record per session+enrollment, deterministic duplicate response, acceptable latency. |
| LOAD-08 | Payment CSV at allowed 1,000-row ceiling with one bad row and then all valid | Atomic reject then atomic confirm; row-level reasons; bounded memory/lock duration; no partial records. |
| LOAD-09 | Bulk grade release, progression sync/evaluate/re-evaluate/auto-promote on ~200 students | No partial transitions, duplicate audit, stale final outcome, or worker/DB exhaustion; operations remain within infrastructure ceiling. |
| LOAD-10 | Bulk Entrance/communications email queue for ~400 recipients | Idempotent queue rows, bounded Celery memory/concurrency, correct Sent/Failed totals, domain transaction unaffected by provider failure. |
| LOAD-11 | Concurrent certificate generation/regeneration for eligible cohort | One active certificate per enrollment, stable numbering, storage cleanup/retry safety, bounded CPU/memory. |
| LOAD-12 | Concurrent media checkpoints for active viewers | Server sequence/rate rules hold without cross-user progress and without saturating web/DB capacity. |

## 21. Open questions and observed contract/code gaps

These are not assumed outcomes. Resolve them before converting the related case
to a release-blocking assertion.

1. **Entrance single-recipient email:** `DOKKIMASHORA.md` marks the behavior
   VERIFY/PARTIAL, while current URLs expose
   `/entrance-exams/manage/<exam_id>/emails/<app_id>/resend/`. Execute ENT-12
   and record whether every invitation/result state is actually supported.
2. **Certificate PSD support:** the broad certificate contract describes PSD,
   current forms/services accept `.psd`, and adapters mention flattened PSD;
   however the Compact Codebase Index says PSD is legacy-only and current
   editor messages mention PDF/JPG. Do not require new PSD uploads for release
   until the product decision is reconciled. PDF and JPG/JPEG remain testable.
3. **Offline roster “printed” state:** the contract says used/unprinted code
   projections may synchronize. Confirm whether the UI/model exposes a durable
   printed marker. If absent, test only unused versus used and record the gap.
4. **Teacher/Instructor:** Teacher is explicitly deferred and Instructor is not
   a real role. The acceptance criterion is absence/denial of active workflow,
   not a Teacher dashboard. Reactivation requires a product decision.
5. **Normal-assessment timeout recovery:** Entrance Exam has an explicit
   `finalize-expired` route; normal Assessments do not expose the same named
   recovery route. Record the actual UI/service behavior at expiry in QUIZ-04
   and ONEX-02 without assuming Entrance semantics.
6. **Attendance choice localization:** attendance model choices are currently
   plain strings while templates may translate surrounding labels. If raw
   English appears in Arabic, log the exact route/status as a localization gap
   rather than silently redefining the expected label.
7. **Scale fixture creation:** repository seed utilities were inspected but are
   intentionally unchanged by this task. The later load-test owner must approve
   how the two ~200-student cohorts are provisioned and cleaned up.

## 22. Execution record template

Copy this block for each run:

```text
Run ID:
Build/commit + dirty snapshot identifier:
Environment/base URL:
Executed by / date / Cairo time:
Browser/device/viewport:
Locale:
Scenario IDs:
Fixture object IDs (non-sensitive):
Result: PASS | FAIL | BLOCKED | NOT RUN | LOAD TEST REQUIRED
Observed result:
Expected result:
Evidence links/screenshots:
Data cleanup completed:
Defect ID and priority:
Open question raised:
```

Release sign-off must list every scenario not run, every environment limitation,
all open questions, and every case deferred to the confirmed load test. Manual
execution must never be reported as concurrency/load proof.
