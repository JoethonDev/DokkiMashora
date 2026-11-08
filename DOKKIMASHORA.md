# DokkiMashora — Product, Business, Architecture & Codebase Contract

**Status:** Authoritative product/business contract and compact codebase index  
**Audience:** Engineers, LLM implementers, UI/UX architects, reviewers  
**Default language:** Arabic  
**Secondary language:** English  
**Timezone:** Africa/Cairo  
**UI/UX:** Brand-preserving, responsive on mobile and desktop for every role; locked UX contract in §20

## 1. How to Read This File

This file answers:

1. What DokkiMashora means as a product.
2. What business rules are locked.
3. Which workflows must remain separate.
4. Where each major concept is implemented.
5. Which capabilities are implemented, partial, missing, or deprecated.

Implementation status labels:

- **IMPLEMENTED** — current repository evidence confirms the capability exists.
- **PARTIAL** — relevant structures exist, but the full locked business rule has not been confirmed.
- **MISSING** — required by the locked business contract but no complete implementation has been confirmed.
- **DEPRECATED** — existing code/concept should not be used for new behavior and is expected to be removed or migrated.
- **VERIFY** — appears implemented but needs targeted current-code verification before relying on it.

This is not a chronological bug log.

---

# 2. Product Topology

```text
DokkiMashora
├── Diploma
│   ├── Cohort
│   │   ├── Semester
│   │   │   ├── Diploma Lectures
│   │   │   ├── Homework
│   │   │   ├── Semester Exam
│   │   │   ├── Attendance
│   │   │   └── Semester Evaluation
│   │   └── Clusters
│   └── Entrance Exam before initial activation
│
└── Standalone Course
    ├── Course Version
    │   ├── Lectures
    │   └── Quiz / end-course quiz
    └── no Diploma/Cohort/Semester ownership
```

## 2.1 Standalone Course

Canonical topology:

```text
Course -> CourseVersion -> Lecture
```

A Course is standalone and does not belong to a Diploma, Cohort, or Semester.

Course learning is fully online.

Student access is scoped to the student's exact active, non-expired `CourseEnrollment` for the exact CourseVersion.

## 2.2 Diploma

Diploma content is delivered directly by:

```text
Diploma -> Cohort -> Semester -> DiplomaLecture
```

Diploma lecture rows are Cohort + Semester scoped.

Every Cohort is an independent content, assessment, grading, attendance, and progression boundary.

## 2.3 Academic deletion preservation

Deleting a Semester hard-deletes that Semester's `DiplomaLecture` delivery rows; it does not delete standalone courses or course versions.

Deleting a Cohort hard-deletes that Cohort's delivery and student relations (diploma lectures, enrollments, watch progress, progression, assignments, clusters/memberships, cohort entrance exams); it does not delete the Diploma's semester definitions or another Cohort's data.

Deleting a Course hard-deletes its `CourseVersion` → `Lecture` content; deleting a CourseVersion hard-deletes its lectures.

Application, payment, issued-certificate, promotion-decision, and audit history are preserved: nullable academic references detach with `SET_NULL`, and immutable history is never cascade-deleted. Re-evaluation marks prior decisions/interviews as superseded instead of deleting them. Destructive academic actions run through named atomic services with explicit Admin permission checks, POST + CSRF, and cascade-specific confirmation.

---

# 3. Identity, Registration & Applications

## 3.1 Root identity

A person has one root identity:

- User
- StudentProfile

Public registration must never create a second identity for an existing person.

## 3.2 Duplicate detection

Public registration checks identity uniqueness against:

1. national ID;
2. email;
3. phone.

A match rejects the new public registration.

Do not merge, update, or silently reuse the existing identity through public registration.

## 3.3 First registration

First registration may create:

- identity only; or
- identity + exactly one initial program application.

The initial program request may target one Diploma/Cohort or one standalone Course, never both.

## 3.4 Later applications

An authenticated existing student may apply to additional programs without creating a new root identity.

Multiple simultaneous applications to different exact targets are allowed.

A duplicate active/pending application for the same exact target is blocked.

## 3.5 Cohort assignment

Diploma applications never let the applicant choose a cohort. The applicant selects a Diploma (only diplomas with at least one open cohort are listed) and the latest open cohort — highest `start_date`, tie-broken by newest `id` — is assigned automatically. The assignment is internal and is not exposed in the apply UI. Standalone Course applications are unaffected: the applicant selects the course.

This rule is shared by every apply entry point: applying from the student profile and applying during public sign-up.

## 3.6 Historical bulk import

**Status:** IMPLEMENTED 2026-11-06; service/flow validated with rollback tests and Admin page rendering.

Historical student/application migration is an administrative exception to the live admissions workflow, not a second live lifecycle. Implementation lives in `apps/applications/imports.py`, `apps/applications/models.py` (`ApplicationImport` / `ApplicationImportRow`), and the Admin **Admissions → Applications → Import historical applications** screen.

- **Two-step flow.** The existing student CSV import creates/updates root identities first. The historical application import only matches existing `StudentProfile` records and never creates or updates identity data.
- **One row per historical application.** A student may appear on multiple rows for different exact targets. Repeated identity rows must agree.
- **Allowed outcomes.** Only `activated` and `rejected` historical applications may be imported. In-progress/live applications must use the normal admissions workflow.
- **Activated outcome.** Creates the `ProgramApplication` and its matching `DiplomaEnrollment` or `CourseEnrollment`. This is the only path allowed to create an activated application/enrollment without the live payment, document, intake-form, and entrance-exam gates. It is a named, permissioned, audited historical service; it is never a generic bypass or a change to `activate_application_if_ready`.
- **Rejected outcome.** Creates the application and rejection history only; no enrollment.
- **Original dates.** `application_date`, `decision_date`, `activated_at`, `expires_at`, and `completed_at` are preserved. The application's `created_at`/`reviewed_at` and enrollment dates are backdated; the import itself is timestamped now. The original reviewer is not fabricated: `reviewed_by` stays empty and the decision records the importing Admin plus `source=historical_import`.
- **Derived enrollment state.** Unless explicitly supplied, a diploma enrollment is `expired` when its cohort/expiry date has passed, otherwise `active`; a course enrollment is `completed` when a completion date is supplied, `expired` when its access window has passed, otherwise `active`. `restricted` is explicit only. `pending` is not a valid historical outcome. Diploma `entry_semester` defaults to the diploma's first semester; the resolved semester is stored and shown in the preview. For a course with more than one version, `course_version` or `course_version_id` is required.
- **Attendance side effect.** Only an active imported enrollment gets an attendance code; expired/completed/restricted enrollments do not. Codes for the batch are created in one set-based pass.
- **No live side effects.** Historical import never sends email, creates payment records or waivers, requests/uploads documents, creates intake responses, creates entrance attempts, or issues certificates.
- **Targets.** Diploma, cohort, and course records must already exist. Names resolve only when unique; ambiguous names require explicit IDs.
- **Existing target conflict.** If the student already has an application for the same exact target, the row is blocked for manual review. Live and previously imported applications are never overwritten. Live submission and historical confirmation both lock the matched `StudentProfile` row before checking existing targets, so the check is serialized.
- **Atomic and audited.** The file is previewed, then confirmed in one transaction. Any row error blocks the whole file. Batch preview/confirm is audited and every imported application receives an append-only decision record carrying the batch provenance. Row errors are stored language-neutrally and rendered in the viewer's active language. Admin/Super Admin only.
- **Upload safeguards.** UTF-8 CSV, 10 MB maximum, 1,000 rows maximum, preview before confirm.
- **Scope boundary.** Historical payments, grades, progression decisions, attendance history, entrance attempts, documents, and certificates are not part of this import. They require separate designs.

---

# 4. Admissions Lifecycle

Conceptual initial lifecycle:

```text
Registration
   ↓
Application
   ↓
Admin review / requested requirements
   ↓
Account activation as required
   ↓
Payment / waived payment
   ↓
Diploma only: Entrance Exam
   ↓
Admin decision / possible interview
   ↓
Program activation
   ↓
Enrollment
```

Exact intermediate statuses may be implementation-specific, but UI should expose meaningful state and available next action rather than raw status codes.

## 4.1 Available-action principle

Pages show only actions valid **now**.

Backend prepares authorized available actions/readiness.

UI does not display every possible transition at once.

---

# 5. Roles & Authorization

## Super Admin

Full platform administration.

## Admin

Program, admission, academic, assessment, payment, progression, certificate, user, and configuration administration through protected workflows.

## Moderator

Current product scope:

- Diploma only.
- Scoped to assigned Diploma cluster/students.
- Assesses Homework only for students inside authorized cluster scope.
- May grade Homework and provide Homework feedback, but **never releases grades** (release authority is Admin/Super Admin only — §8.2).

Moderator is not a general academic administrator.

## Reviewer

Semester Exam grading only, within assigned anonymous scope.

Reviewer grades:

- assigned anonymous Semester Exam written work (online);
- assigned offline section totals.

Reviewer **never releases grades** (release authority is Admin/Super Admin only — §8.2).

Reviewer may know:

- Cohort;
- Exam;
- anonymous student code / seating code;
- grading data necessary for the assigned section/written answer.

Reviewer must not see:

- student name;
- email;
- phone;
- national ID;
- profile;
- unrelated history;
- unnecessary identifying data.

## Secretary

Attendance workflow, including attendance sessions and scanning as authorized.

## Student

Own application, enrollment, learning, assessment, progression, grade, attendance, and certificate access according to state.

## Teacher

**DEFERRED / NOT A CURRENT PRODUCT ROLE.**

Existing code/data may temporarily contain Teacher structures, but new UX/business behavior must not depend on Teacher unless explicitly reactivated.

## 5.1 Security boundary

UI visibility is never authorization.

Every protected operation enforces authentication, role, assignment/scope, ownership, enrollment, state, schedule, and exact target as relevant.

## 5.2 Admin trust model (LOCKED)

For the audited administrative capabilities, **Admin == Super Admin**: equal trust.

This is an intentional permission model. No hierarchy gate is implemented for the audited actions unless a future product decision changes this policy.

*Decision source: locked during repository-audit implementation review (REPOSITORY_AUDIT_IMPLEMENTATION_DECISIONS.md §14).*

### 5.3 IT operator access (LOCKED)

A Django account with `is_staff=True` and no active product role is the IT
operator. Its navigation is limited to **General settings** and **Audit**, and
its dashboard redirects to General settings. It does not receive academic,
admissions, payment, progression, or Django Admin access.

The Audit view and General Settings/system-wide email log are gated by
`is_infrastructure_manager`: Admin/Super Admin or a role-less `is_staff` IT
account. Existing product staff roles (Moderator/Reviewer/Secretary) keep their
own locked navigation and cannot read the audit trail, SMTP configuration,
Redis contents, or one-time email tokens. No new product role is introduced.

*Decision source: locked by user 2026-10-31; sensitive operational surfaces
hardened during the security review.*

---

# 6. Payment & Activation

Payment is mandatory before activation for every:

- Diploma;
- standalone Course.

Admin may waive payment.

A waiver is an audited business event recording at least:

- actor;
- timestamp;
- application/program target;
- reason;
- previous requirement state;
- resulting state.

Activation requires either satisfied payment or a valid recorded Admin waiver.

If a waiver was the sole source of activation and is later revoked, the
application returns to its pre-activation payment state and the enrollment
created by that activation returns to pending. A real paid record or an
unrelated activation is preserved.

## 6.1 Payment CSV matching

Payment CSV upload matching uses national ID, email, or phone. All three are unique per student record, so any single match is sufficient — there is no priority order.

Upload is atomic per file: any row that fails to match or validate rejects the entire file; partial application is not allowed. The rejection response identifies the exact row number(s) and the reason for each failure so the file can be corrected and re-uploaded.

---

# 7. Entrance Exam

Entrance Exam is Diploma/Cohort only.

It is completely separate from normal learning Assessments.

Separate product boundary includes:

- models;
- URL namespace;
- entry points;
- student screens;
- admin screens;
- attempt lifecycle;
- result language;
- permissions;
- reporting.

Lower-level model-agnostic utilities may be shared.

## 7.1 Purpose

Entrance Exam assists Admin's admission decision.

Numeric score/result is internal advisory information.

It is not the student's final admission result.

## 7.2 Student-visible outcome

Students never see:

- entrance numeric grade;
- entrance pass/fail;
- hidden scoring thresholds.

Students may see:

- assigned/ready;
- scheduled;
- in progress;
- submitted;
- awaiting review;
- later administrative outcome:
  - accepted;
  - rejected;
  - interview requested.

## 7.3 Admin authority

Admin may make the later admission decision independently from the calculated entrance score.

## 7.4 Email

Entrance communication supports:

- bulk cohort sending/resending;
- send/resend to one student/application.

**Status:** bulk confirmed by existing docs; single-recipient behavior is **VERIFY / PARTIAL**.

## 7.5 AI / Gemini assistance layer

**Status: REMOVED / DEFERRED.** The AI assistance layer (formerly `apps/ai/`,
Gemini suggestions for entrance-exam and assessment grading) is **not an active
feature**. It was removed per `REPOSITORY_AUDIT_IMPLEMENTATION_DECISIONS.md` §35
(no production callers; dashboard statistics were permanent zeros).

If this layer is re-introduced, staff keep the final decision — it must never be
a hidden final authority, and a provider failure or timeout must never block or
fail the student's submission.

## 7.6 Question scoring

- MCQ questions score by the selected choice; a choice's score cannot exceed the
  question's maximum score.
- Written questions score by the question's maximum score and have no choices.

---

# 8. Normal Assessments — Locked Vocabulary

```text
Assessment
├── Diploma
│   ├── Homework
│   └── Semester Exam
│
└── Standalone Course
    └── Quiz / end-course quiz
```

## Deprecated assessment concepts

Do not use for new product behavior:

- `additional_assessment`;
- separate Diploma `final_exam` workflow;
- arbitrary extra Diploma assessment types.

Existing rows/constants/routes require safe migration/removal before destructive cleanup.

## 8.1 Question validity invariants

- MCQ questions have at least two choices and exactly one correct choice.
- Written questions have no choices; converting an MCQ to written deletes stale choices.
- Exam/assessment start and submit validate active status, window, assignment/ownership, attempt status, and timer expiry on the server.
- Admin may grant exceptional access to selected active course enrollments for Course Quizzes or selected active diploma enrollments for Diploma Semester Exams. The override has its own open/close window, is audited, does not bypass enrollment or the attempt timer, and is revalidated on start, autosave, and submit.

## 8.1.1 Scoped Semester Exam autosave performance exception

**Status: APPROVED SCOPED EXCEPTION — 2026-08-29.** The online Diploma
Semester Exam autosave path may use narrowly targeted performance techniques:
set-based snapshot-answer updates, a short atomic write, client-side delta
payloads, an attempt-scoped Redis cache for immutable snapshot metadata, and
bounded web/database/runtime tuning.

This exception applies only to the existing online Diploma Semester Exam
autosave flow. It does not merge Entrance Exam, Course Quiz, Homework, or other
assessment workflows, and it does not waive authentication, CSRF, exact
ownership/enrollment scope, exceptional-access checks, timer/deadline
revalidation, answer validation, audit/history requirements, or the rule that
PostgreSQL is the durable authority for saved answers.

The Redis cache must be bounded, attempt-scoped, safe on cache miss/failure, and
written or invalidated consistently with committed database state. Redis
write-behind was explicitly authorized on 2026-08-30 for this scoped flow after
durability, submit ordering, retry, coalescing, and outage behavior were
implemented and stress-validated. Redis remains a versioned pending buffer;
PostgreSQL remains the durable authority, and submit drains pending state before
finalization.

## 8.2 Grade release & correct answers

### Release defaults

- Standalone Course Quiz: default **automatic** grade release; configurable **Automatic / Manual**.
- Diploma Homework: default **manual** grade release; configurable **Automatic / Manual**.
- Diploma Semester Exam: default **manual** grade release; configurable **Automatic / Manual**.

### Release authority

Only **Admin / Super Admin** may execute a manual Diploma grade release. Moderator and Reviewer never release grades. Backend authorization remains authoritative.

### Visibility before/after release

Before release, student-facing UI must not expose numeric grade, feedback, or weighted contribution according to the configured visibility. After release, the appropriate academic grade/result/history may be shown.

### Global and per-student switches (LOCKED)

Grade visibility has two independent switches:

- **Assessment global switch** — Admin/Super Admin only.
- **Per-student switch** — the released/hidden state of that student's attempt.

The per-student switch is authoritative. The global switch is a bulk action: showing or hiding globally updates all currently releasable student switches, after which an individual student switch can override that result. A student with the switch on sees grades even if the last global action was hidden; a student whose switch is off remains hidden after a global show. Per-student release/hide actions never change the global switch.

When visible, each question shows the student's earned score out of that question's total points (for example, `8 / 10`), in addition to any allowed overall result and feedback. Correct answers and answer keys remain hidden.

### Correct answers

Correct answers are hidden from Students for **all assessments** — Course Quiz, Diploma Semester Exam, and Entrance Exam included — **before and after grade release**. Never expose answer keys, correct MCQ choices, model answers, or question-bank solutions. Students may still see whatever released grade, result, or feedback the corresponding assessment policy allows. A future requirement to expose correct answers is a separate product modification.

Entrance Exam remains completely separate and never exposes numeric grading (§7.2).

### 8.3 Scoring precision (LOCKED)

Percentages are **quantized to two decimals before any pass/fail comparison** — never compared at raw precision. `percent = round(earned / possible * 100, 2)` (Decimal, `ROUND_HALF_EVEN`) and only the rounded value is compared to the passing threshold. This applies uniformly to normal Assessments, Diploma Semester Exam, Homework, Entrance Exam, and the Progression two-level rule. `possible <= 0` → no result; a missing component produces a blocked reason, never an automatic fail.

*Decision source: locked by user 2026-08-28 (Demo v1 review decision D-002).*

---

# 9. Attempt Policy

Current business use is one attempt per Assessment.

Backend may stay future-ready for configurable multiple attempts.

Frontend must not expose max-attempt configuration while business remains always one.

An active exceptional-access start is a controlled fresh-run exception. The
prior attempt and its answers/per-attempt question snapshot remain preserved as
history; the new exceptional run is created as the next attempt number (normally
Attempt 2) and receives its own independent question snapshot. The
exceptional-access override and its audit events remain preserved.

Retake/re-evaluation uses the **latest valid score**.

---

# 10. Standalone Course Assessment

Standalone Courses use Quiz / end-course quiz.

Course quiz grades release **automatically by default**, configurable **Automatic / Manual** (§8.2).

---

# 11. Homework

Homework is a Diploma Assessment scoped to:

```text
Cohort + Semester
```

A Homework may accept multiple uploaded files.

All files belong to **one logical Homework submission**.

```text
Homework 1
├── file-1.pdf
├── file-2.docx
└── file-100.pdf

=> one submission
=> one aggregate grade
=> one feedback/result
```

Individual files are attachments, not independently gradable homework attempts.

## 11.1 Submission minimum

For file-based Homework, **at least one valid attachment is required** before the Student may submit:

- `0` valid attachments → cannot submit;
- `1+` valid attachments → may become submit-ready, subject to other readiness rules.

`can_upload` controls whether attachments may be added; `can_submit` controls whether the logical submission may be submitted. `remaining_slots` is rendered only when a configured attachment limit actually exists; no specific count is hard-coded.

## 11.2 Grade release

Diploma Homework grade is released **manually by default**, configurable **Automatic / Manual** (§8.2). Only Admin/Super Admin releases; Moderator grades but never releases.

## 11.3 Scope

Moderator may grade Homework only for students inside authorized Diploma cluster scope.

**Status: IMPLEMENTED.** Homework uses one logical aggregate submission with validated attachments, aggregate grade/feedback, and manual-by-default release controls.

---

# 12. Diploma Semester Exam

Semester Exam is Diploma-only and scoped to:

```text
Cohort + Semester
```

It supports online and offline students. For Diploma applications, the
student's attendance/delivery mode is selected on the application itself and
is propagated to generated Semester Exam codes. Per-code Admin toggles are not
an active source of mode selection; the Admin Exam Code Roster may update the
authoritative application mode and synchronize only unused, unprinted code
projections. Historical code values remain preserved.

## 12.1 Exam structure

For online students, generation may use:

- randomized questions from lecture question banks according to configured counts;
- manually configured written questions that are always included and are not randomized.

Each student's generated question snapshot may differ.

The configured section grading structure/maximums remains stable even when randomized questions differ.

## 12.2 Online Semester Exam flow

```text
Admin configures Semester Exam
        ↓
Student starts
        ↓
System creates per-student randomized snapshot
  + required manual written questions
        ↓
Student submits
        ↓
Objective/MCQ portion calculated
        ↓
Reviewer receives anonymous grading assignment
        ↓
Reviewer grades written questions only
        ↓
System computes reviewed exam total
        ↓
Semester Evaluation (exam score included immediately)
        ↓
Admin final semester Pass/Fail decision

Optional: Admin may inspect the complete online exam and release grades.
This review does not gate semester evaluation.
```

Reviewer does not see student identity.

Admin may inspect the full online exam after review.

A progression **Interview is NOT an exam-stage action** — it is requested in the Semester Evaluation / progression workflow (§13.3–13.4). The exam stage owns only academic grading/review.

## 12.3 Offline Semester Exam flow

Admin opens the Exam Code Roster from the Semester Exam detail page and
generates an anonymous `seating_no` / exam code for each enrolled student.

Admin may print and hand the code to the offline process.

Reviewer grades using the anonymous code.

### Critical offline section rule

Offline grading does **not** require the LMS to store, reconstruct, or display randomized questions or student answers.

For each configured exam section, Reviewer enters only the **section total grade** against the configured section maximum.

Example:

```text
Section A
Maximum: 20
Reviewer entered: 14

Section B
Maximum: 30
Reviewer entered: 21
```

The section is a grading structure, not a reconstructed online question snapshot.

The sum of section grades forms the reviewed offline Semester Exam grade.

When an offline code is issued, its section order, displayed title, and maximum
are snapshotted for that code. Later edits to the assessment layout must not
reinterpret an issued code's grades or denominator; a section referenced by an
issued code cannot be deleted. Online randomized attempts likewise use the
question and possible-points snapshots saved for that attempt. Historical
results remain readable if an exam code is later made inactive, while new
grading actions still require an active offline code.

### Optional exam review

After Reviewer grading, Admin may inspect the grade/section totals and release grades.

Exam review is optional for evaluation. A graded Semester Exam feeds the Semester Evaluation immediately; the per-exam review never blocks evaluation or the final progression decision.

The final academic progression decision is the Admin's semester-level Pass/Fail after the Semester Evaluation is finalized. Failing or reviewing the exam stage does not destroy the grade record.

The exam stage owns only academic grading/review: **grade completeness, the exam minimum, academic exam result/review, grade preservation, and grade release** (§8.2). A progression **Interview is NOT an exam-stage action** — it belongs to the Semester Evaluation / progression workflow (§13.3–13.4).

**Status: IMPLEMENTED.** Offline section grades are scoped to the active offline exam code and assessment; Admin review persists completeness, exam-stage Pass/Fail, actor/time, preserved grades, and release handoff. Exam-stage review is optional and is not an evaluation gate.

---

## 12.5 Diploma attendance periods and dated sessions

Diploma attendance is assigned once for each:

```text
Cohort + Semester
```

That assignment is one bounded Attendance Period from a start time to an end
time. It is not recreated for every meeting day. Each held day is a numbered
dated Attendance Session under that period. The database prevents two dated
sessions for the same period and date.

- A student's QR scan is recorded once per dated session.
- A repeated scan on the same date is idempotent and does not increase the
  student's attendance count.
- A scan on another dated session in the same period is valid and increases
  the student's attended-session count.
- Session `open_at` and `close_at` remain the configured, editable scan window.
  Manually closing a session records a separate `closed_at` instant and never
  overwrites either configured boundary.
- Scan records are preserved as evidence when a period or session window is
  corrected. A record counts only when its status is countable and its scan
  time falls inside the current session window, current parent-period window,
  and any actual manual-close boundary.
- Attendance evaluation counts ended/closed dated sessions, excludes
  cancelled sessions, and applies the configured required-session threshold to
  attended dated sessions. Future scheduled sessions do not lower the current
  percentage, while an ended session with no countable scans remains in the
  denominator.
- Standalone course attendance keeps its existing per-session behavior and is
  not merged into diploma cohort/semester periods.

Reviewer assignments follow the same logical-scope principle: one active
assignment per reviewer, cohort, scope, semester/exam, and optional cluster.
One anonymous grading claim reserves the whole submitted attempt and every
exam section in it; section identity remains on the attempt snapshot only for
grouping and grading display. A claim is finalized only after every written
answer in every section is graded. Sections are never separate assignment or
claim rows.

Admin deactivation preserves the assignment, claim, grade, and audit history.
It is refused while an online claim is in progress. Deactivation serializes
with assignment creation, online claim issuance, and online/offline grading so
no new work can start through a revoked assignment.

---

# 13. Semester Evaluation

Semester Evaluation is a separate academic decision after grades/attendance are available.

It uses exactly three components:

1. Attendance
2. Homework
3. Semester Exam

No arbitrary custom grade-component types are part of the product.

## 13.1 Configuration scope

Admin defines values for the relevant:

```text
Cohort + Semester
```

Configuration includes:

- each component's weight;
- each component's minimum required percentage;
- overall minimum weighted percentage.

Weights must total 100%.

Example:

```text
Component         Weight     Minimum required
Attendance         20%             80%
Homework           20%             50%
Semester Exam      60%             50%

Overall passing minimum: 60%
```

## 13.2 Two-level passing rule

A student passes only when **both levels pass**.

### Level 1 — component minimums

Each component must independently reach its configured minimum.

```text
Attendance >= 80%
AND
Homework >= 50%
AND
Semester Exam >= 50%
```

Failure of any component minimum means the Semester fails, even when weighted overall >= 60%.

### Level 2 — weighted overall minimum

Only after component minimums are satisfied:

```text
overall =
    attendance_percent     * 0.20
  + homework_percent       * 0.20
  + semester_exam_percent  * 0.60
```

Then require:

```text
overall >= 60%
```

### 13.2.1 Applicable-component reweighting (LOCKED)

The evaluation uses exactly three components (Attendance, Homework, Semester
Exam), but a component with **no configured source** for the cohort/semester
is **not applicable**:

- It produces no blocked reason.
- It is excluded from the weighted-overall denominator, and the remaining
  applicable weights are **renormalized** to 100%.

PASS requires every **applicable** component minimum AND the renormalized
weighted overall minimum. Admins are only accountable for the components they
created for that cohort/semester. When all three components are applicable the
renormalized formula is identical to §13.2 above.

#### Diploma assessment completeness and omission (LOCKED)

Only Diploma Homework and Semester Exam assessments are inputs to these two
components; Course Quizzes are not. Each Diploma assessment is marked
**Required** (the default for existing and new assessments) or **Optional**.
Completed scored assessments are included regardless of that label. For an
assessment with no usable result, apply the effective per-semester progression
policy resolved by `resolve_progression_policy`:

- A missing Required assessment blocks finalization when **Require all
  required assessments** is enabled; otherwise it is omitted from that
  student's component calculation.
- A missing Optional assessment blocks finalization when **Allow missing
  optional assessments** is disabled; otherwise it is omitted.
- An assessment with an active attempt (in progress, submitted, or under
  review) always blocks finalization. A graded Semester Exam is included
  immediately from its reviewer-graded / computed score; it never waits for a
  per-exam Admin Pass/Fail decision. The optional Semester Exam review queue is
  for grade release/audit only and does not block evaluation.
- The Admin makes the final semester Pass/Fail decision after the semester
  evaluation is finalized. That final decision is the promotion gate; it is not
  a per-exam decision.
- An absent attempt, an unstarted assigned attempt, or an expired attempt is
  missing. A cancelled attempt is ignored when finding the latest attempt.
  Missing work is never silently converted to zero.

The existing Homework and Semester Exam aggregation rules are applied to the
scored assessments that remain included. If every configured assessment in a
component is intentionally omitted for one student, that component is Not
applicable for that student and its weight is excluded and the remaining
applicable component weights are renormalized. A component with no active
assessment source remains Not applicable for everyone. If no applicable
component has a score, the evaluation remains blocked rather than creating a
zero-score result.

These progression-policy flags control **assessment completeness and
applicability only**. `SemesterEvaluationConfig` remains the sole source of
component weights, component minimums, and the overall minimum; the flags never
change those values. Finalized evaluations retain the effective missing-work
policy, policy sources, assessment required/optional values, and the exact
attempts used/omitted. They remain unchanged when an assessment or progression
rule is later edited. An explicit Admin re-evaluation uses current settings
and records the before/after snapshots in audit history.

*Decision source: locked by user during BUG-019/020 implementation review
(REPOSITORY_AUDIT_IMPLEMENTATION_DECISIONS.md §9/§10), with the later explicit
decision to apply Required/Optional and missing-assessment policy to the
canonical Semester Evaluation. On 2026-10-26 the user locked the later workflow
change: remove the per-exam Admin Pass/Fail evaluation gate, include
reviewer-graded Semester Exams immediately, and move the final Pass/Fail
decision to the Admin after the semester evaluation is finalized. The Semester
Exam review queue remains optional for grade release/audit.*

### Example A — overall sufficient, component failed

```text
Attendance:     100% -> 20
Homework:       100% -> 20
Semester Exam:   40% -> 24
Overall:               64%
```

Semester Exam minimum is 50%.

Result: **FAIL**.

### Example B — both levels pass

```text
Attendance:      90% -> 18
Homework:        70% -> 14
Semester Exam:   60% -> 36
Overall:               68%
```

Result: **PASS**.

## 13.3 Calculated Semester Evaluation vs Final Progression Decision

These are **two separate concepts**, both visible in history:

- **Calculated Semester Evaluation** — derived objectively from the three components (Attendance · Homework · Semester Exam) using `SemesterEvaluationConfig` component minimums and weighted overall minimum (§13.2), with diploma assessment inputs admitted or omitted under §13.2.1. It produces **Calculated PASS** or **Calculated FAIL**. Once Admin finalizes it, it is immutable by default. An explicit Admin re-evaluation may replace it with a fresh source calculation, records before/after values and snapshots in audit history, and resets any downstream final progression outcome to pending. The previous promotion/fail decision and interview are **superseded**, not deleted: they remain in history but are excluded from active-state checks so a new decision can be recorded.
- **Final Progression Decision** — whether the student actually **Continues / Promotes** or **Does Not Continue**. It usually follows the calculated evaluation, but an Interview or an explicit Admin **Exceptional Promote** action may introduce an **exceptional** final decision. Exceptional promotion requires a reason and does not change the calculated result.

Academic scores and the calculated evaluation are **never modified** to match the final progression decision.

Saving **Evaluation settings** for a cohort/semester automatically re-syncs and re-evaluates every active enrollment that already has a finalized evaluation. Any linked final progression outcome returns to pending because the calculated result may change. Active enrollments without a finalized evaluation keep using the live preview with the new settings.

### Processing order

```text
Components available (Attendance + Homework + Semester Exam)
↓
System continuously calculates evaluation preview
↓
Admin explicitly chooses Finalize / Evaluate Semester
↓
Calculated Semester Evaluation becomes: PASS or FAIL
↓
Admin decides the next progression action
```

On the Admin student Progression detail, the recommended Evaluate action is
limited to the enrollment's current active semester. Older and future
unfinalized semesters are not presented as parallel evaluation actions.

All live progression/promotion actions use the finalized semester decision
workflow. The legacy direct `promote-year` endpoint/service is retired; existing
`academic_year_promote` decision rows and their choice label remain preserved as
historical data, but are not a current transition path. Historical academic-year
configuration and promotion rows do not gate student semester visibility,
assessment access, or attendance projections; canonical semester progression
state remains authoritative.

An Admin may explicitly promote a student regardless of Calculated PASS or FAIL
through **Promote** on an exceptional row. The action opens a reason-required
confirmation modal; submission still requires a non-empty reason. It preserves
the calculated result, records the override actor/time/reason, and is blocked by
an unresolved Interview or locked progression.

## 13.4 Interview

Interview occurs **only after** Semester Evaluation and may be requested after **Calculated PASS or FAIL**.

```text
Calculated PASS or FAIL
↓
Admin requests Interview
↓
Progression becomes INTERVIEW PENDING
↓
No final progression outcome may be completed yet
↓
Interview must be completed
↓
Admin records final progression decision:
PASS / CONTINUE  or  FAIL / DO NOT CONTINUE
```

Once an Interview has been requested, completing it is **mandatory** before progression can resolve.

The final decision may differ from the calculated result in either direction:

- exceptional continuation: Calculated **FAIL** → Interview → **Continue / Promote** (calculated result remains FAIL);
- exceptional failure: Calculated **PASS** → Interview → **Do Not Continue** (calculated result remains PASS).

Both facts remain unchanged and visible in history.

Re-evaluation supersedes the prior Interview and final decision rather than deleting them. They remain visible as superseded history, and a new Interview may be requested for the recalculated evaluation.

### Audit

An Interview-based final decision preserves at minimum:

```text
calculated result
component results
interview record
interview outcome/details
final progression decision
override / decision reason
Admin actor
timestamp
```

When calculated result ≠ final progression decision, the decision is identified as **Exceptional / Override Decision**. Academic grades are never modified to manufacture agreement.

### Student visibility

- Before an Interview is requested, the student may see the finalized calculated Semester result per existing visibility rules.
- During **INTERVIEW PENDING**, the student is shown a **neutral** state ("Interview required" / "Progression decision pending interview") — never a misleading final continuation outcome.
- After the Interview and final decision, the final continuation/progression status is shown.
- Academic grade visibility is governed independently by the grade-release rules (§8.2).

**Status: IMPLEMENTED.** Finalized calculated evaluation, explicit audited re-evaluation, automatic refresh after evaluation-settings changes, superseded prior decisions/interviews, Interview Pending, Interview completion, exceptional final decisions, calculated results, and override audit data are persisted separately.

---

# 14. Progression After Semester Evaluation

## 14.1 Passed semester

Student continues to the next Semester according to Diploma progression.

## 14.2 Failed semester

History is preserved.

Do not overwrite/migrate old Cohort:

- enrollment;
- grades;
- attempts;
- attendance;
- progression decisions.

## 14.3 Direct rejoin rule

A student who failed Cohort A / Semester N may directly rejoin the **immediately next Cohort** at the same failed Semester N.
The source Diploma enrollment must still be active; an expired, completed, or
restricted source enrollment is not eligible for Direct Rejoin. A later return
from an expired enrollment follows the normal new-applicant rules.

Direct rejoin does not require:

- new Entrance Exam;
- new payment;
- new intake form.

Example:

```text
Cohort A
Semester 1 PASS
Semester 2 PASS
Semester 3 FAIL

Immediate Cohort B:
student rejoins at Semester 3
```

Prior Cohort A remains immutable history.

After activation, the source enrollment is restricted and the new target
enrollment is the student's effective enrollment, starting at Semester N.
Student learning and record previews show one effective enrollment with the
preserved successful semester history combined with the current target path.
Revoking an active rejoin deletes the newly created target enrollment and
restores the source enrollment to active; the original source history remains
untouched.
The recovery action also repairs a legacy cancelled plan that still has a
restricted source and active replacement, restoring the source and restricting
the replacement without duplicating the cancellation decision.

Admin has a dedicated **Rejoin Eligibility** workflow for students eligible for the immediately next Cohort at the same failed Semester.
Active rejoin plans are excluded from that eligibility projection because the
student has already moved; they remain available in the Rejoin Plans workflow
for management or revocation.

Display at minimum:

```text
Student · Previous Cohort · Failed Semester · Eligible Next Cohort · Eligibility State · Available Action
```

Typical action: `[ Prepare Rejoin ]`.

Only immediately-next-cohort eligible students appear as actionable.
Resolved PASS outcomes, including exceptional promotions, are excluded from
Rejoin Eligibility. The exact cohort/semester progression workspace is the
primary operational entry for eligibility and plan management. Those pages
keep the selected Diploma/Cohort/Semester fixed; they do not offer a scope
switcher. Every scoped action validates the source enrollment and failed
semester against the selected URL context. The diploma detail page may expose
a diploma-filtered report, and global routes remain secondary filtered or
cross-diploma reports for oversight and recovery; neither replaces the
contextual workspace.

## 14.4 Missed rejoin window

Direct Rejoin eligibility stays open until Semester N in the immediately next Cohort B is **completed/finalized**. Once Cohort B / Semester N is completed/finalized, the Direct Rejoin shortcut expires.

There is no cohort-start cutoff, no grace-period window, no configurable deadline, and no manual Admin window.

A later return follows normal new-applicant rules.

Multiple historical enrollments are intentional.

---

# 15. Attendance

Attendance is a Diploma progression component.

Browser/camera QR scanning is required.

```text
Authorized attendance user
        ↓
Open scan experience
        ↓
Browser/camera reads QR
        ↓
Client sends scan payload
        ↓
Server validates session/student/cohort/auth/window/replay
        ↓
Attendance record
```

Client scan state is not authoritative.

The scanner is a dedicated responsive interaction for Admin/Secretary, allowing rapid scanning of consecutive students with clear success/error feedback and **no navigation away after each scan**.

Required scanner states:

```text
camera permission requested
ready / scanning
QR detected
submitting
recorded
already recorded
invalid QR
wrong session / cohort
session closed
network / server error
ready for next scan
```

Attendance percentage must exclude future sessions.

## 15.1 QR lifetime (LOCKED)

The long-lived student QR model is intentional. The QR represents the student reference and may remain valid for the intended long-term period. Do not shorten the validity merely because of an audit finding.

The signed token has no age cutoff. Attendance authorization always depends on server-side active session/context validation, active attendance code, and current enrollment ownership/status. The long validity must not be reclassified as a vulnerability.

*Decision source: locked during repository-audit implementation review (REPOSITORY_AUDIT_IMPLEMENTATION_DECISIONS.md §12).*

---

# 16. Learning Completion

## 16.1 Standalone Course

Course learning is fully online and sequential.

Lecture completion may depend on video/audio completion.

If a lecture contains both **Video** and **Audio**, they are alternative ways of consuming the lecture: completing **either** satisfies the media-completion requirement (`Video OR Audio`). The student is never required to complete both.

Next lecture may unlock only after current required lecture is complete.

Server remains authoritative for whether the completion threshold is met.

## 16.2 Diploma

Diploma learning does not require lecture-by-lecture watch completion locking.

Diploma progression is driven by Attendance, Homework, Semester Exam, and Semester Evaluation.

Do not force Course watch-progress rules onto Diploma content.

## 16.3 Server-authoritative watch tracking

Watch completion is best-effort compliance tracking, not proctoring. Implementation lives in `apps/academics` (lecture/watch services) with `static/js/youtube-progress.js` as the client observer.

- The server never trusts client-reported completion, watched seconds, enrollment, or duration. Clients post periodic player observations to a same-origin endpoint; the server re-derives access/configuration, locks current progress, and validates sequence, elapsed server time, rate, and movement.
- Video completion derives from the furthest continuously validated position against the server-persisted duration. Replaying an already-watched range does not manufacture completion. Only published/released YouTube lectures with saved duration participate in video tracking. Course audio completion is separately server-validated against persisted HLS duration and participates as the approved alternative in the Video OR Audio rule; unprocessed audio and text do not count.
- Progress is duration-weighted. Diploma progress is always scoped to the student's exact `DiplomaEnrollment.cohort`; progress never leaks between cohorts.
- On a detected violation (skip or over-speed) tracking stops and the current session is blocked with a clear message. The next session resumes from the last server-validated position; already-validated progress is preserved. Never silently pass or fail.
- Lecture metadata (duration/embeddable status) is fetched server-side with the official YouTube Data API on create/edit and persisted. Pending, ready, and unavailable states are explicit; an Admin can retry unavailable or stale pending work in the exact Course Version or Diploma Cohort/Semester. A request-generation token and video-ID check prevent stale tasks from overwriting an edit or newer retry. Admin lecture lists refresh that state from a self-terminating fragment while a check is in flight, so a pending check resolves without a manual reload; retry remains the action for unavailable or stale pending checks only. Students see remediation guidance; metadata failure never silently completes or bypasses Course sequencing. Metadata is never fetched from, or trusted from, student watch requests.

---

# 17. Enrollment Expiry

Access checks always require exact active, non-expired enrollment.

A scheduled job should also transition stale active enrollment rows to persisted `expired`.

The job improves lifecycle consistency but is not the sole authorization boundary.

Expired students keep own historical applications, grades, attempts, progression, and certificates.

**Status: IMPLEMENTED.** Access-time active/non-expired checks are authoritative, and scheduled expiry normalizes stale enrollment rows without replacing those checks.

---

# 18. Certificates

Generated certificate history is immutable.

Certificate rendering uses the student's **current corrected profile name**.

Marking a diploma enrollment **completed** or **graduated** automatically issues
its certificate after the completion transaction commits. Automatic issuance
uses the same generation and audit path as manual admin issuance; a rendering or
storage failure leaves a retryable failed certificate and never reverses the
completion. Admins can still generate a missing certificate or regenerate an
existing one manually from the Certificates administration page. *Decision
source: locked by user 2026-10-29.*

## 18.0 Course completion and certificate scope

Course completion is **tied to one designated end-course exam**. The exam is a course quiz marked as the course version's end-course exam. Readiness requires every published/released course lecture to be completed, **every active course quiz** to be passed by its **latest attempt**, and the designated end-course exam to be active and passed. An earlier passed attempt does not survive a failed retake. The completion date is the timestamp of the last requirement met; when no requirement timestamp exists, the confirmation time is used.

Passing the designated end-course exam automatically marks the enrollment completed and issues its certificate once all readiness requirements are met. The Admin completion page remains available for manual completion or retry.

The end-course exam cannot be started until every published/released course lecture has met the course completion percentage (default 80%). This exam-entry gate applies to standalone Courses only.

Admin confirmation is authoritative for completion and issuance; payment status and enrollment expiry do not block completion. Marking a course completed is terminal: content access stops immediately and the course leaves the active Learning cards, while the certificate remains available in Record. Course certificates auto-issue on completion with the manual generate/retry fallback. Existing completed course enrollments are not backfilled; they are issued through the manual panel.

Course templates are scoped per course. Course certificate numbers use
`CR-{year}-{course_id}-{student_id}-{sequence}`.

Regeneration applies to both diploma and course certificates: a new certificate
row with a new number is created, and the replaced certificate becomes
**superseded** (admin-only download, hidden from students). Regeneration reason
is optional and regenerating does not create a `PromotionDecision`; the audit
log records the old/new certificate and reason. *Decision source: locked by user
2026-10-29.*

## 18.1 Template editor workflow

Admin certificate workflow:

```text
Template list
↓
Create / upload template (PDF or JPG/JPEG)
↓
System previews a supported template representation
↓
Admin places dynamic fields on the preview
↓
Preview generated certificate
↓
Save field mapping / template configuration
```

The editor exposes the complete dynamic field registry: **Student Name**,
**Date**, certificate number, diploma, course, semester, cohort, and graduation
date. Each field can be placed, resized, styled, moved between source pages,
and saved. Layout persists normalized coordinates with a field box
(width/height 0..1) — mapping schema v2; v1 mappings without width/height still
load (point placement). PDF and PSD artwork are accepted during template
creation and replacement, with protected source previews and the existing
shared rendering path.

PDF and JPG use different source adapters but share one PIL text-placement
path. PDF pages are rasterized at export DPI; JPG/PSD sources use the flattened
image. The bundled Cairo font renders Arabic base glyphs correctly. The renderer
uses base text rather than presentation-form reshaping so every source format
produces the same Arabic result. Field alignment/direction are honored, and
preview-generate and final export share the same rendering function.

**Status: IMPLEMENTED.** Admin can upload PDF/PSD artwork while creating or
editing a template, preview it, place every registered dynamic field, save
mappings, and generate immutable certificate output through source adapters.

## 18.2 Artwork-required templates

Admin template pages require uploaded PDF, PSD, or JPG/JPEG artwork. New
templates upload artwork before continuing to the field-placement editor; an
existing template without ready artwork must upload it before its details can
be edited again.

The structured HTML design renderer and legacy `body_html` remain server-side
fallbacks for existing rows, but the visual HTML design controls are not exposed
in the admin UI. This preserves existing templates and issued certificate
history without making HTML/CSS an admin responsibility.

**Status: IMPLEMENTED.**

---

# 19. Audit & History

Audit/history events are append-only.

## 19.0 Audit log contract (LOCKED)

The audit log is IT's operational trace, not a raw developer log.

- Every state-changing staff action (`POST`/`PUT`/`PATCH`/`DELETE`) is audited.
  Student self-service mutations are excluded from the generic request fallback;
  existing named student-triggered events (application/entrance submission)
  remain. Successful attendance QR scans are intentionally excluded from the
  fallback because each scan is already durably evidenced by an
  `AttendanceRecord`; attendance session/period lifecycle changes are audited.
- Named business events carry the meaningful detail. A request-level fallback
  records a mutation only when no named event was written, so coverage does not
  duplicate well-instrumented workflows.
- Every event belongs to one business section: Admissions, Academics,
  Assessments, Attendance, Progression, Payments, Certificates, Communications,
  Accounts & access, Settings, System, or Security.
- Every event stores an immutable actor snapshot (name + role), target snapshot
  (type, ID, human label), affected-person snapshot where relevant, outcome,
  source, IP, request path/method when available, and a correlation ID.
- Grading events include assessment ID/name/type, attempt or submission ID,
  student snapshot, anonymous/seating code where applicable, grader snapshot,
  section, score before/after, maximum, release state, and reason for overrides.
- System events cover email queue/delivery/skip/failure and background-job
  completion/failure/retry. Email and job traces are searchable by recipient,
  subject, template, task, and correlation ID.
- Nginx, Django, and Celery emit single-line key/value logs to stdout for
  Dozzle. The same request ID links nginx access logs, Django warning/error
  logs, the audit correlation ID, and Celery task logs.
- Action codes are never rendered raw. The UI uses translated business labels
  and a legacy-code mapping for historic rows. Audit history is not rewritten to
  achieve readability.
- Passwords, tokens, secrets, national IDs, phone numbers, request bodies, and
  uploaded file contents are never stored in audit metadata.
- Business audit events are retained. Operational job-run history is bounded
  (90 days) because it is high-volume operational data, not business history.
- Redis management from General Settings is limited to the cache database.
  The Celery broker database is read-only; destructive Redis commands
  (`FLUSHDB`, `FLUSHALL`, `CONFIG SET`, arbitrary command consoles) are not
  exposed. Protected exam-state keys require exact-key confirmation and a reason.
- SMTP credentials entered in General Settings are encrypted at rest and never
  rendered back to the browser or written to audit metadata.

Overrides such as payment waiver, grading override, progression override, access change, promotion/demotion/rejoin decision preserve actor, timestamp, before, after, and reason where applicable.

Historical academic records remain preserved across rejoin/new enrollment boundaries.

The exceptional-access fresh-run reset in §9 is an explicit same-assessment
exception: the replaced attempt and its dependent snapshot are removed, while
the access override audit remains append-only.

## 19.1 Media storage & signed access

All audio lecture files and homework uploads go through the storage-service abstraction; no view or template calls the object store directly. Implementation area: `apps/storage/`.

- Private objects are never served through public/unsigned URLs. All access uses signed, time-limited URLs generated by the storage service.
- **Streaming exception (LOCKED):** authenticated application-proxy delivery is an approved exception for Course/Diploma audio and HLS streaming. The proxy view authorizes the exact user + active non-expired enrollment and streams bytes through the app (no direct edge exposure); it does not use a signed URL per segment. Signed URLs remain the contract for non-streaming downloads. *Decision source: locked by user 2026-08-28 (Demo v1 review decision D-006).*
- Uploads are validated server-side for size, type/extension, generated storage names, and safe destinations. Client-side checks are never authoritative.
- Storage failures (upload, signing, deletion) surface as clear user-facing errors — never a silent failure or a broken link. Failed deletions are reconciled durably, not lost.

---

# 20. UI/UX Contract (Locked)

**Status:** LOCKED. Brand-preserving; responsive on mobile and desktop for every role; one design system serves Admin and Student (no separate role design languages). Arabic RTL is the design source; English LTR is first-class.

## 20.1 Shared visual grammar, separated workflows

Separate business workflows (Entrance Exam, Semester Exam, Course Quiz) **may** share the same visual grammar/components when the semantic presentation is identical — e.g., question display, exam section display, status, form field, page header, read-only metadata. They must **never** become one generic backend workflow/template behind large role/workflow flags.

Shared components are semantic, not flag-driven: business orchestration, authorization, mutations, and workflow-specific context stay separate. Mega-templates with many role/workflow flags are forbidden.

## 20.2 Navigation

Student primary navigation: **Dashboard · Learning · Applications · Assessments · Record · Profile.** Attendance, Progression, and Certificates live inside the Record hub and as Dashboard contextual cards. Entrance Exam is contextual only (Dashboard card when assigned), never a sidebar item.

Admin navigation is grouped by mental model, not Django app: Workspace, Learning (incl. Assessments and grading queues), Admissions, Administration.
For academic operations, the selected Diploma → Cohort → Semester workspace is
the primary destination for grading, evaluation, progression, and rejoin work;
global queues and cross-scope reports are secondary.

- Moderator: **Dashboard · Homework Review**.
- Reviewer: **Dashboard · Grading assignments**.
- Secretary: **Dashboard · Attendance**.

## 20.3 Minimal action principle

Pages show only actions valid **now**, prepared by the backend.

The backend determines which actions are currently valid and authorized; the UI determines priority and presentation. The UI never displays every possible transition.

Guideline, not a strict law:

```text
one clear primary action
+ a small useful secondary set
+ roughly <= 3 immediately visible workflow actions
```

Rare/contextual actions may use disclosure/overflow. Desktop and mobile expose the same currently valid and authorized actions for the current object state (task parity); presentation may differ.

## 20.4 Readiness / available-actions projection

Backend supplies a per-domain readiness projection; every domain owns its valid states and transitions (no generic business enum across Entrance Exam, Semester Exam, Homework, Quiz, Application, Progression):

```python
{
    "state": "...domain-specific state...",
    "label": "...translated business label...",
    "tone": "success | warning | danger | info | neutral",
    "description": "...",
    "block_reason": "... or None",
    "primary_action": {"label": ..., "url": ..., "method": ...} | None,
    "secondary_actions": [{"label": ..., "url": ..., "method": ...}, ...],
}
```

Multi-transition pages may receive `available_actions[]`. The UI never calculates state machines. Reference markup must use one consistent shape (never check one structure and iterate another).

## 20.5 Forms and controls

One canonical form system across Admin, Student, and Public. Default interactive touch target **≥ 44px**. Input/select/textarea/file height 44px minimum; label above with `for`; help/error below; brand focus ring; canonical disabled state. Use the canonical form/button/status primitives; no arbitrary raw semantic colors where a token/component exists. Action buttons use the semantic `btn-*` class only for sizing and visual treatment, with no inline padding/text-size/min-height overrides; the shared button primitive supplies the consistent `rounded-sm` radius. `text-red` is the canonical error token.

## 20.6 Status and workflow presentation

- Status is always **icon + text**, never color alone.
- The backend supplies the translated business label; the UI maps tone/icon/presentation only. Domain statuses are never globally mapped into one meaning (e.g., `accepted`, `passed`, `active` may share a success tone while keeping their own labels).
- Workflow detail pages communicate: **Where am I? · What is the current state? · What can I do now? · What happened before?**
- Every detail/content/preview page has a visible back button to an explicit canonical parent URL. Never `javascript:history.back()`.

## 20.7 Responsive

- Admin mobile is first-class; Student desktop is first-class.
- Parity = same task-critical information + same currently valid and authorized actions + same authorization. Presentation may differ; valid functionality is never hidden on any breakpoint.
- Data-dense collections use the presentation that best fits the breakpoint — **one responsive structure**, or **separate desktop/mobile representations when genuinely beneficial**. There is no rule that every list must ship both. If separate markup exists, both derive from the same backend projection, authorization, available actions, and display labels; business-state logic is never duplicated.
- Breakpoints are **design decisions**, not immutable product rules; they follow the approved page-family designs.

## 20.8 RTL/LTR

Arabic RTL is the design source; English LTR must work. Use logical direction utilities. Mixed-direction values — email, phone, code, date, seating number — remain correctly LTR. Directional icons mirror in RTL.

## 20.9 HTMX / Alpine / JS

- Backend owns authorization, readiness, business state, progression, grading, and transitions. Alpine owns local presentation/interactions only. No client-side business state machine.
- Every stable page has a canonical refresh-safe full-page URL. HTMX fragments preserve full-page business behavior and authorization.
- Sidebar navigation uses HTMX to replace only the `#main` content boundary; the shell, sidebar, topbar, and footer persist, while normal links remain the refresh-safe fallback.

## 20.10 Timers

Backend provides the authoritative `deadline` / `expires_at`; the client may tick locally; the server revalidates on autosave and submit. No mandatory timer-polling endpoint unless current implementation proves it necessary.

## 20.11 Accessibility

Durable principles: one `<h1>` per page; skip-to-main; label association; `aria-describedby`; keyboard access; modal/drawer focus management; status always icon + label; AA contrast; `prefers-reduced-motion` respected; live regions. Implementation details are not business history.

## 20.12 Motion and tokens

Operational LMS: no decorative page-entrance animation or card pop. Allowed minimal motion: modal/drawer open-close, toast, loading, small state feedback — all respecting `prefers-reduced-motion`.

Preserve the existing brand and tokens; one canonical semantic status color family. Semantic states are expressed through named primitives (`status-pill-*`, `stat-icon-*`, `badge-*`, `alert-*`, `field-error`, `priority-dot-*`, `state-banner-*`), not raw semantic color utilities spread through page templates.

A UI/UX change may propose a backend change when it materially simplifies UX; business behavior changes require user approval before implementation.

---

# 21. Compact Codebase Index

Engineers still inspect current code before editing.

| Domain | Expected implementation areas | Status / contract notes |
|---|---|---|
| accounts | `apps/accounts/models.py`, `forms.py`, `views.py`, `services.py`, `admin.py`, `admin_config.py`, `admin_site.py`, `urls.py`, `constants.py` | IMPLEMENTED; identity writes share one guard; account active state is Django `is_active` only; Arabic display name defaults to the full name; product and Django Admin role boundaries align; Teacher product behavior is DEFERRED |
| students | `apps/students/models.py`, `forms.py`, `views.py`, `services.py`, `identity.py`, `matching.py`, `admin.py`, `selectors.py`, `urls.py`, student templates/fragments | IMPLEMENTED / evolving UX; cross-table identity ownership is validated across profile and account paths; `matching.py` is the shared set-based CSV identity matcher |
| applications | `apps/applications/models.py`, `forms.py`, `services.py`, `imports.py`, `import_errors.py`, `views.py`, `urls.py` | IMPLEMENTED; historical application bulk import accepts activated/rejected records only, previews before an atomic confirm, creates matching enrollments, audits provenance, stores language-neutral row errors, and produces no live email/payment/document/intake/exam side effects |
| intake_forms | `apps/intake_forms/models.py`, `forms.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED |
| academics | `apps/academics/models.py`, `forms.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED; Course Video OR Audio completion, generation-guarded YouTube metadata remediation, read-time enrollment expiry, and course completion readiness (all published lectures + every active quiz's latest attempt + designated end-course exam) are enforced; the end-course exam is blocked until required lecture progress is met; historical Teacher structures are not used by student learning UX |
| entrance_exams | `apps/entrance_exams/models.py`, `forms.py`, `services.py`, `views.py`, `urls.py`, exam templates | IMPLEMENTED; single-recipient email VERIFY; correct answers hidden VERIFY |
| assessments | `apps/assessments/models.py`, `forms.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED; aggregate Homework submissions, release modes, answer-key secrecy, legacy quarantine, and Diploma assessment requirement scope are enforced |
| progression | `apps/progression/models.py`, `forms.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED; canonical evaluation applies inherited missing-assessment policy without changing configured weights/minimums; immutable evaluation/final outcome/interview/rejoin paths remain active; historical final-exam structures are preserved and excluded from new progression |
| attendance | `apps/attendance/models.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED; browser scanner uses the server-authoritative attendance service |
| payments | `apps/payments/models.py`, `services.py`, `selectors.py`, `views.py`, `urls.py` | IMPLEMENTED; payment waiver is audited and activation-safe |
| certificates | `apps/certificates/models.py`, `services.py`, `views.py`, `urls.py` | IMPLEMENTED; validated PDF/JPG/JPEG source preview (PSD kept only for legacy rows), mapping schema v2 (normalized width/height boxes, extensible field registry: student_name, date, course_version, + placeholders), Arabic-capable embedded-font base-glyph rendering through a shared PIL placement path (PDF rasterized at export DPI), single shared preview/export path, diploma/course certificate scopes, per-course templates, automatic issuance on completion, and superseded-row regeneration history are protected |
| communications | `apps/communications/models.py`, `forms.py`, `services.py`, `tasks.py`, `views.py`, `email_config.py`, `email_backend.py` | IMPLEMENTED; canonical Celery queue foundation (`queue_templated_email` + `send_queued_email_task`, QUEUED/PROCESSING/SENT/FAILED lifecycle), DB-backed encrypted SMTP configuration with environment fallback, system-wide email log, and email lifecycle audit are implemented |
| audit | `apps/audit/models.py`, `constants.py`, `labels.py`, `context.py`, `middleware.py`, `selectors.py`, `services.py`, `signals.py`, `views.py` | IMPLEMENTED; sectioned human-readable audit, actor/target/subject snapshots, correlation IDs, request-fallback coverage, Django Admin bridge, staff auth events, system email/job events, append-only writes, and grouped/searchable IT views |
| deployment | `apps/deployment/models.py`, `signals.py`, `health.py`, `views.py`, management commands | IMPLEMENTED; Celery job-run history, bounded 90-day operational cleanup, and existing health probes |
| storage | `apps/storage/services.py`, `views.py` | IMPLEMENTED |
| core | `apps/core/permissions.py`, `forms.py`, `navigation.py`, `views.py`, `models.py`, `system_status.py`, `crypto.py` | IMPLEMENTED; role-scoped navigation, `is_staff` IT access to General settings/Audit, backend permissions enforce Teacher deferral, and operational status services are bounded/degradable |

## 21.1 Assessment implementation objects already discovered

Current codebase discovery identified structures equivalent to:

- Assessment;
- AssessmentAttempt;
- AssessmentQuestion/Choice;
- HomeworkSubmission;
- Diploma lecture question bank;
- AssessmentLectureSection;
- per-attempt snapshot questions/answers;
- SemesterExamCode;
- OfflineSectionGrade;
- ReviewerAssignment.

These structures do not prove the new business rules by themselves.

## 21.2 Progression implementation objects already discovered

Current discovery identified structures equivalent to:

- DiplomaProgressionRule;
- SemesterProgressionRule;
- StudentSemesterProgress;
- PromotionDecision;
- SemesterGradeComponent;
- StudentGradeItem;
- SemesterRejoinPlan.

Again, their existence does not prove the exact two-level evaluation or immediate-next-cohort rejoin rule.

---

# 22. High-Level Alignment Matrix

| Business Area | Contract Status |
|---|---|
| Root identity | IMPLEMENTED |
| Additional applications | IMPLEMENTED |
| Historical application bulk import | IMPLEMENTED |
| Standalone Course topology | IMPLEMENTED |
| Diploma direct lecture topology | IMPLEMENTED |
| Entrance/Assessment separation | IMPLEMENTED |
| Entrance grade hidden from student | IMPLEMENTED |
| Mandatory payment + Admin waiver audit | IMPLEMENTED |
| Scheduled enrollment expiry | IMPLEMENTED |
| Teacher removal/defer | DEPRECATED FOR PRODUCT |
| Moderator cluster homework-only scope | IMPLEMENTED |
| Homework multi-file one grade | IMPLEMENTED |
| Homework minimum one attachment to submit | IMPLEMENTED |
| Diploma only Homework + Semester Exam | IMPLEMENTED; deprecated rows quarantined |
| Course Quiz/end-course quiz | IMPLEMENTED |
| One attempt exposed in FE | IMPLEMENTED |
| Course automatic configurable grade release | IMPLEMENTED |
| Homework manual configurable grade release | IMPLEMENTED |
| Semester Exam manual configurable grade release | IMPLEMENTED |
| Grade release authority Admin/Super Admin only | IMPLEMENTED / VERIFY |
| Per-student grade visibility; global switch is a bulk action | IMPLEMENTED |
| Per-question earned / total points after release | IMPLEMENTED |
| Correct answers hidden (all assessments) | IMPLEMENTED / PARTIAL |
| Semester randomized online exam | IMPLEMENTED |
| Anonymous Reviewer | IMPLEMENTED; anonymous projections and collision-safe claims enforce the identity boundary |
| Offline exam codes | IMPLEMENTED |
| Offline section-total-only grading | IMPLEMENTED |
| Optional exam-stage review (no Interview) | IMPLEMENTED; does not gate Semester Evaluation; Interview is progression-only |
| Evaluation components limited to 3 | IMPLEMENTED |
| Component minimums + overall minimum | IMPLEMENTED |
| Calculated Semester Evaluation vs final progression decision | IMPLEMENTED |
| Interview after Calculated PASS or FAIL; blocks progression | IMPLEMENTED |
| Interview override (either direction) audited | IMPLEMENTED |
| Immediate-next-cohort rejoin + eligibility workflow | IMPLEMENTED |
| Course sequential video/audio completion | IMPLEMENTED |
| Course Video OR Audio media completion | IMPLEMENTED |
| Diploma no lecture watch lock | IMPLEMENTED |
| Browser QR scanning + scanner states | IMPLEMENTED |
| Current-name certificate rendering | IMPLEMENTED |
| Automatic diploma certificate issuance | IMPLEMENTED |
| Course completion readiness + admin confirmation | IMPLEMENTED |
| Course-completion certificates | IMPLEMENTED |
| Superseded-row regeneration (diploma + course) | IMPLEMENTED |
| PDF/PSD field-position editor | IMPLEMENTED |
| Responsive/Admin-mobile UX + responsive collection presentation | IMPLEMENTED |
| Mega-template cleanup (no role/workflow flags) | IMPLEMENTED selectively; historical cohesive templates remain |

---

# 23. Deprecated Documentation Concepts

The following old concepts are not authoritative:

- old multi-document precedence chains;
- append-only MEMORY as mandatory agent reading;
- old UI source-of-truth as final UX contract;
- old audit/fix-plan `MUST FIX` queues;
- Teacher as active current product role;
- Diploma `final_exam` as a separate required normal-assessment type;
- `additional_assessment`;
- arbitrary/custom Semester grade-component types;
- Diploma lecture watch-completion locking;
- per-file Homework grade as desired behavior;
- `ux_architecture_part1.md` / `ux_architecture_part2.md` and `DOKKIMASHORA_UX_PROPOSAL.md` as authoritative UX sources — the durable UX contract is §20 of this file.

The documentation cleanup removed the legacy Markdown files that carried these
concepts. `AGENTS.md` and `DOKKIMASHORA.md` are the only repository authorities.
`README.md` and `docs/DEPLOYMENT.md` remain as explicitly informational
operational/setup documentation; they never override the two contracts.
