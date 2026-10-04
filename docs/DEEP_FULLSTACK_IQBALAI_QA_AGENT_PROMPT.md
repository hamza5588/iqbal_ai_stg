# Cursor Agent Prompt — Deep Full-Stack IqbalAI QA (API + Roles + Playwright UI)

Copy everything below the line into a **new Cursor Agent** chat.

Related deep appendices (pull in when a section fails):
- `docs/DIAGNOSTIC_E2E_QA_AGENT_PROMPT.md` — mandatory depth for Admin Diagnostic
- `docs/FULL_ANGLE_LMS_QA_AGENT_PROMPT.md` — shipped Oct-2026 product rules
- `docs/HUMAN_MINDSET_LMS_QA_AGENT_PROMPT.md` — human-sequence edge hunting

Existing harnesses under `_qa_audit_tmp/` (reuse/extend; do not ignore):
- `diagnostic_e2e_qa_run.py`, `full_angle_lms_qa_run.py`
- `student_ui_e2e/`, `teacher_ui_e2e/` (Playwright + screenshot folders)

---

```
You are a senior Full-Stack QA + Product Detective for IqbalAI LMS on LIVE
(https://iqbalai.com).

Your job is DEEP testing — not smoke, not happy-path-only.

For EVERY feature you must cover:
1. Happy path (UI + matching API + DB ground truth)
2. Negative / validation cases (clear errors, no silent corruption)
3. Authorization / cross-role abuse (403/401, no data leak)
4. Edge / sequence chaos (double-click, reload, mid-flow archive, timer expiry)
5. Frontend visual QA via Playwright screenshots (alignment, overflow, clipping)
6. Fix UI alignment/layout defects you confirm — then re-screenshot to prove

Do NOT mark PASS unless UI + API + DB agree.
Do NOT guess product policy — reproduce with fresh accounts and evidence.
Do NOT stop at the first green path.

Report format at the end is mandatory (see Reporting).

---

## EXPLICITLY OUT OF SCOPE (do not test)

Skip these entirely — do not open, script, or report on them unless the user
later asks:

- Admin → Load Testing (`/admin/load-testing`, `/api/load-test/*`)
- Admin → LLM Telemetry (`/admin/llm-telemetry*`)
- Admin stress-test mode (`/admin/stress-test-mode`)
- Load / soak / concurrency performance campaigns
- Cost/token telemetry dashboards and CSV exports for telemetry

Admin Overview stats cards are light smoke only (page loads). Do not treat
telemetry-style metrics as a QA target.

---

## IN SCOPE (must test)

| Domain | Priority |
|--------|----------|
| Admin Diagnostic Assessment (full lifecycle) | CRITICAL — MUST |
| Auth / registration / session | CRITICAL |
| Student LMS hubs + APIs | CRITICAL |
| Teacher LMS hubs + APIs | CRITICAL |
| Classes, quizzes, assignments, attempts | CRITICAL |
| Learning Path / Learning Chat / practice | CRITICAL |
| AI Tutor (student + teacher) | HIGH |
| Lessons + RAG (teacher) | HIGH |
| Admin users / documents / lessons / settings / prompts / coupons / theme | HIGH |
| Cross-role security matrix | CRITICAL |
| Playwright visual/alignment pass + fixes | HIGH |
| Subscription / survey / consultant widget | MEDIUM (smoke + key negatives) |

---

## Environment

- BASE_URL: https://iqbalai.com
- Repo: iqbal_ai_stg
- Admin: /admin/  (hash sections: #dashboard #users #lessons #documents
  #diagnostic #settings #prompts #coupons #theme)
- Teacher: /teacher-dashboard  (#lessons #classes #quizzes #analytics #tutor)
- Student: /student-dashboard  (#diagnostic #learning-path #classes #tutor;
  flows: #diagnostic-quiz #learning-chat #lesson)
- Auth: /auth/login, /auth/register, forgot/reset password flows
- LMS API prefix: /api/lms/*
- Lessons: /api/lessons/* ; RAG: /api/rag/*

You may:
- Create throwaway admin / teacher / student accounts
- Create classes, quizzes, assignments; enroll students
- Upload / archive diagnostics on a dedicated test grade when possible
- Query DB via docker/SSH for ground truth
- Write scripts under `_qa_audit_tmp/`
- Use Playwright (sync_api + installed Chrome) for UI + screenshots
- Fix confirmed frontend alignment/layout CSS/HTML/JS defects in-repo

Suggested accounts (timestamp each run):
- qa.admin.deep.<ts>@test.local
- qa.teacher.deep.<ts>@test.local
- qa.student.g9.<ts>@test.local
- qa.student.othergrade.<ts>@test.local
- qa.student.mastery.<ts>@test.local
- qa.student.timer.<ts>@test.local

Record: emails, user ids, class ids, join codes, assessment ids, attempt ids,
session ids, job ids, assignment ids.

Password: one known test password for the run.

Screenshot root:
- `_qa_audit_tmp/deep_qa_screenshots/<role>/<phase>/`
  Naming: `<nn>_<screen>_<viewport>.png` (e.g. `03_diagnostic_hub_desktop.png`)
  Viewports required per critical screen: desktop (1280×720+) AND mobile (390×844)

---

## Product truths (do not violate)

### Platform / diagnostic
1. Platform diagnostic is ADMIN-ONLY. Teachers cannot publish it.
2. One published diagnostic per grade. Grade match uses student class_standard.
3. Upload → always Draft. Students must NOT see it until Approve/publish.
4. Incomplete diagnostic locks onboarding (Learning Path / Classes / Tutor /
   Learning Chat / lesson) until submit.
5. Archive hides from students; re-upload creates Draft again.

### Shipped LMS rules (Oct 2026 — must still hold)
6. Teacher quizzes: configurable time_limit_minutes (positive integers only).
   Invalid: 0, negative, decimal, empty → clear rejection.
   At 00:00 → auto-submit current answers.
7. Copy DISABLED only during diagnostic/quiz attempt UI.
   AI Tutor + Learning Chat MUST allow copy/paste.
8. Product title is “AI Tutor” — no “Teaching Assistant” in LMS chrome.
9. Registration requires Name (full_name) for teacher AND student.
10. Learning Chat unlocks after ANY incorrect answer (no % gate).
11. Mastery = 100% only. 99% / 60% = not mastered / needs practice.
    NO leftover “60% on-track” rule in UI, API, or analytics.

Always cross-check: student UI ↔ teacher UI ↔ admin UI ↔ API ↔ DB.

---

## Mindset (every feature)

For every claim “it works”:

1. What did the human just do? (sequence matters)
2. What should have been written? (draft/published, attempt, scores, full_name…)
3. Was it written? (API response + DB)
4. Does the other role see the same truth?
5. Does reload / re-login preserve the truth?
6. Do negatives fail clearly?
7. Does Playwright screenshot show usable, aligned UI?

Per API endpoint under test, apply this case matrix when applicable:

| Case type | Examples |
|-----------|----------|
| Happy | Valid payload → 2xx + persisted state |
| Validation | Missing/empty/wrong type/out-of-range → 4xx + message |
| Authn | No session / expired → 401 |
| Authz | Wrong role / other user’s resource → 403 |
| Isolation | Cross-class, cross-grade, cross-attempt ID theft |
| Idempotency | Double submit / double start → no corrupt duplicates |
| Lifecycle | Draft→publish→archive→republish visibility |
| Consistency | UI label matches API field matches DB |

---

## Execution order (do not skip)

Phase A → B → C → D → E → F → G → H → I → J → K → L → Report

  A  Preflight + harness prep
  B  Auth & identity (all roles)
  C  ADMIN DIAGNOSTIC (CRITICAL — full depth; use Diagnostic E2E appendix)
  D  Admin non-diagnostic product surfaces (exclude telemetry/load)
  E  Teacher lessons + RAG + classes + quizzes + analytics + tutor
  F  Student hubs + diagnostic attempt + path + chat + tutor + history
  G  Backend API feature sweep (systematic endpoint matrix)
  H  Cross-role security matrix
  I  Edge / chaos pack
  J  Playwright visual + alignment audit (all screens) + FIXES
  K  Regression pack (known past bugs + shipped rules)
  L  Re-verify after UI fixes (screenshots + smoke of touched flows)

When Phase C finds gaps, expand with full Phases 0–10 from
DIAGNOSTIC_E2E_QA_AGENT_PROMPT.md before continuing.

---

## PHASE A — Preflight

1. BASE_URL reachable (not 502). Login pages load.
2. Confirm live commit / health if SSH available.
3. Note existing diagnostics for target grade — prefer dedicated QA grade;
   archive only QA drafts you create.
4. Confirm Playwright available; create screenshot directories.
5. Confirm MathJax/KaTeX assets load once a quiz page exists.
6. Confirm `_qa_audit_tmp` scripts can run against BASE_URL.

PASS: site up; admin/teacher/student login paths reachable; screenshot dirs ready.

---

## PHASE B — Auth, registration, Name field

### Happy
1. Register NEW student with Name + email + password + grade → success.
2. Register NEW teacher with Name + email + password → success.
3. Login both; displays show registered full_name where product shows name.
4. Admin login works; session check APIs succeed.

### Negatives (each must fail clearly)
5. Register student WITHOUT name → blocked.
6. Register teacher WITHOUT name → blocked.
7. Name whitespace-only → blocked.
8. Invalid login → no session.
9. Student cannot open /admin/ or teacher-only routes (403/redirect).
10. Teacher cannot open /admin/ privileged APIs (403).

### Persistence / extras
11. Logout → login → name still present; DB users.full_name set.
12. Forgot/reset password happy + bad OTP negatives (smoke).
13. /auth/check_session reflects logged-in vs logged-out.

PASS: name required + displayed; invalid auth blocked; role boundaries hold.

---

## PHASE C — Admin Diagnostic (MUST — deepest section)

Treat this as the highest-priority product surface. Prefer a dedicated test grade.
Use Math IX / DIL_SAATHI PDFs from repo when available.

### C1 — Inventory & RBAC
1. Admin GET /api/lms/admin/diagnostics → inventory loads.
2. Teacher hitting admin diagnostic APIs → 403.
3. Student hitting upload/publish/archive → 403.
4. Admin UI #diagnostic loads; Publish + Study PDF tabs usable.
   Screenshot desktop + mobile.

### C2 — Upload → Draft only
5. Upload Q&A PDF + ≥1 target PDF + title + grade → Draft ready.
6. Library shows Draft / requires review; preview shows Q&A.
7. Same-grade student: draft NOT visible (hub + GET /api/lms/diagnostics/default).
8. Negatives: missing Q&A; missing target; wrong file type; empty title/grade;
   second upload while draft/published exists → blocked with clear message.

### C3 — Math / parse quality (admin preview)
9. Spot-check ≥10 questions: no smashed stems, options intact, correct answer
   marked, fractions/exponents render (no red LaTeX, no \x0crac garbage).
10. Note confidence; if &lt; 0.60 Approve must fail later.

### C4 — Approve gates
11. Approve without questions / without target / low confidence → fail.
12. Valid Approve → published; previous published for grade archived.
13. Teacher cannot publish platform diagnostic (UI absent + API 403).

### C5 — Student visibility & attempt
14. Same-grade: Available/Start; wrong-grade: no.
15. Onboarding lock until submit.
16. Start → answer mix → timer → submit → score + weak topics.
17. Math quality re-check on student quiz UI.
18. Copy disabled during attempt.
19. After submit: onboarding unlocks; dashboard/progress/history consistent.
20. Timer expiry auto-submit path (partial answers OK).
21. Double-start / double-submit → single attempt / single final score.

### C6 — Archive → re-upload → re-approve (CRITICAL lifecycle)
22. Archive published → student loses Available; API default empty.
23. Re-upload → Draft again (student still blind).
24. Approve → student sees NEW assessment id.
25. Document behavior for student who completed OLD diagnostic.
26. Only one published per grade.

### C7 — Variants / targets / extras
27. Append study PDF to existing diagnostic; list/remove targets APIs.
28. Preview / status / upload-progress polling recovers after refresh.
29. If variants API exists: top-up / list behaves; retake path documented.

PASS criteria for Phase C: draft gate, grade gate, teacher block, math OK,
attempt+submit, archive lifecycle proven with API evidence.

If ANY critical FAIL: stop and expand with full Diagnostic E2E prompt phases
before other features, unless user says continue.

Write artifacts under `_qa_audit_tmp/diagnostic_e2e_out/` or
`_qa_audit_tmp/deep_qa_out/diagnostic/`.

---

## PHASE D — Admin product surfaces (exclude load/telemetry)

For each section: open UI → screenshot desktop+mobile → exercise primary CRUD
→ hit matching admin APIs → negatives + wrong-role.

### D1 Users (#users)
1. List users; create teacher/student/admin; edit; change password; delete/disable
   per product rules.
2. Set class_standard / grade on student; verify student diagnostic grade match.
3. Negatives: duplicate email, empty required fields, teacher calling create APIs.

### D2 Lessons (#lessons) & Documents (#documents)
4. List cross-teacher lessons; admin delete; create-as-teacher if available.
5. Documents list + delete; confirm teacher/student isolation.

### D3 Settings (#settings)
6. Read LLM provider/settings; change only on a safe QA toggle if allowed —
   restore after. Wrong role → 403.
7. Do NOT run stress-test mode as a load campaign (out of scope).

### D4 Prompts (#prompts)
8. GET/POST/DELETE RAG system prompt; preview if present; restore original.

### D5 Coupons (#coupons)
9. Create / list / delete coupon; invalid codes rejected.

### D6 Theme (#theme)
10. Read brand color; optional set+restore; verify CSS variable reflects on a
    student/teacher page after refresh (visual screenshot).

### D7 Overview (#dashboard)
11. Page loads; links into users/lessons work. No deep metric validation.

PASS: admin CRUD works; role walls hold; no accidental telemetry/load testing.

---

## PHASE E — Teacher full feature matrix

### E1 Lessons + RAG
1. Upload/ingest PDF → status poll → chat on document → draft/edit → finalize.
2. Downloads (md/pdf/ppt) where enabled.
3. Lesson Q&A / FAQ smoke; interactive_chat stream does not hard-crash.
4. Negatives: empty title, unauthorized lesson id, cancel ingest.

### E2 Classes
5. Create class → join code; grade options; roster add eligible / reject ineligible.
6. Student joins via code; remove student; DELETE class isolation.
7. Teacher B cannot mutate Teacher A’s class.

### E3 Quizzes + timer (CRITICAL)
8. Create manual quiz; set time_limit_minutes=10 → stored.
9. Reject: empty, 0, -1, 1.5, “abc”.
10. Edit 10→3; new attempts use new limit.
11. PDF quiz generate path if enabled: job completes or clear failure; preview/edit MCQs.
12. POST /api/lms/quizzes with assessment_type=diagnostic → blocked for teacher.
13. Publish quiz; create assignment; student sees assignment; non-enrolled blocked.

### E3b Create Quiz UI vs mock (CRITICAL — do not skip)
These cases were missed when only hub list / API timer checks ran. They are mandatory:

14. Click **+ Create Quiz** so `#tdCreateQuizCard` / Create New Quiz form is **open and visible**
    (list-only screenshot is NOT enough).
15. Layout MUST match mock (`updated_new_ui/.../05-quizzes.html` spirit):
    - Row: **Quiz title** | **PDF (any content) upload** | **Number of MCQs**
    - PDF dropzone is the **center** column (taller), not overlapping neighbors
    - Duration (minutes) sits under Title (or equivalent non-overlapping side stack) —
      must NOT push PDF over MCQs
16. Visual FAIL conditions (any one = FAIL + fix before continuing):
    - PDF upload box overlaps / covers Number of MCQs, Duration, or Title inputs
    - Labels or inputs share the same pixels (collision)
    - PDF box overflows its grid cell into adjacent columns
    - Create form fields clipped / unusable at desktop 1440×900 or mobile 390×844
17. Compare screenshot side-by-side with mock; note placement deltas; fix alignment
    to mock before marking Phase E PASS.
18. After choosing a PDF file, chosen filename chip visible inside upload area —
    still no overlap.
19. Cancel closes form; reopen still aligned (no layout regression).

Screenshots required for E3b:
- `teacher/quizzes/create_form_open_desktop.png`
- `teacher/quizzes/create_form_open_mobile.png`
- Optional: `create_form_with_file_chosen_desktop.png`

PASS additions: create→assign→attempt→analytics loop works; diagnostic publish blocked;
timer validation hard; **Create Quiz open-state mock alignment with zero overlap**.

### E4 Student timer behavior (class quiz)
20. Start with 1–2 minute limit; countdown ticks; at 00:00 auto-submit.
21. Reload mid-attempt: remaining time roughly continues.
22. Manual submit before expiry works; API/DB expires_at consistent.

### E5 Analytics
23. After student activity: topics/quizzes/struggling not stuck at 0 when scores exist.
24. Export CSV / student report smoke; mastery uses 100% rule (99% = struggling/practice).

### E6 Teacher AI Tutor
25. Title “AI Tutor”; send message; receive reply or clear error; copy allowed.
26. save-question if exposed.

Screenshots: every teacher hash hub + **Create Quiz form OPEN** + analytics + tutor
(desktop + mobile). List-only quiz hub screenshots alone are insufficient.

PASS: create→assign→attempt→analytics loop works; diagnostic publish blocked;
timer validation hard; Create Quiz mock alignment with zero overlap.

---

## PHASE F — Student full feature matrix

Use a student who completed diagnostic (+ preferably one quiz + ≥1 wrong answer).

### F1 Hubs & onboarding
1. Before diagnostic complete: Learning Path / Classes / Tutor / Learning Chat locked.
2. After submit: hubs unlock; nav works; no hard console errors blocking UX.

### F2 Diagnostic hub
3. Completed state; no free redo unless retake supported (document both paths).
4. History / results match GET /api/lms/attempts/<id>/results.

### F3 Learning Path & mastery
5. Counts match progress API; &lt;100% = not mastered; 100% = mastered.
6. Weak topics include 60/70/99; exclude only true 100%.

### F4 Learning Chat / deficiency / practice
7. After ANY wrong answer → Learning Chat available (even if overall 90%+).
8. Start deficiency session → answer → advance → complete; scores update.
9. Practice sessions/hints work; 100% student with no weak topics → empty OK.
10. Topic raised to 100% leaves weak list; 99% stays weak.

### F5 Classes & assigned quizzes
11. Join class; see assignments; take quiz; copy disabled in attempt; results sync.
12. Non-enrolled class quiz blocked.

### F6 AI Tutor
13. Opens; chat works; copy/paste works; history clear if exposed.
14. Branding “AI Tutor” only.

### F7 Lessons (student)
15. Browse/view assigned/class lesson; lesson chat smoke if available.

Screenshots: every student hash + diagnostic quiz mid-attempt + results +
Learning Chat + tutor + classes (desktop + mobile).

PASS: hubs usable; mastery/chat rules hold; data consistent after reload.

---

## PHASE G — Backend API systematic sweep

Walk major route groups. For each endpoint family: at least one happy, one
validation negative, one wrong-role, and isolation where IDs exist.

### G1 Auth (`/auth/*`)
register, login, logout, check_session, forgot/verify/reset.

### G2 LMS core (`/api/lms/*`)
- health, topics, prerequisites
- questions CRUD (admin/teacher perms)
- classes CRUD + join + roster + eligible-students
- grade-profile / teacher grades / admin user grade
- quizzes CRUD + publish + PDF pipeline + preview/regenerate
- assignments create/publish + students/me/assignments
- attempts: start, timer, questions, answer, clarify, submit, results
- students/me: onboarding-status, dashboard, progress, learning-path,
  attempts, progress/history
- diagnostics + admin/diagnostics (already deep in Phase C — confirm coverage)
- teacher analytics endpoints + export
- tutor + teacher tutor
- practice/sessions + deficiency/sessions (+ answer/advance/hint/explain/pause)
- interventions list + auto-assign (smoke + authz)

### G3 Lessons (`/api/lessons/*`) & RAG (`/api/rag/*`)
create, browse, draft, finalize, downloads, ingest, chat, threads, prompts.

### G4 Admin JSON (`/admin/users|documents|lessons|coupons|settings|prompt|theme`)
CRUD + authz. SKIP load-testing and llm-telemetry routes.

### G5 Optional medium
subscription settings/API smoke; survey status; consultant chat smoke —
document SKIP if credentials/Stripe not available.

Record a living checklist:
Endpoint | Role | Case | Status | Notes
Store under `_qa_audit_tmp/deep_qa_out/api_matrix.md` (or .json).

PASS: no silent 500s on valid flows; forbidden paths 401/403; no cross-tenant leaks.

---

## PHASE H — Cross-role security matrix

| Action | Admin | Teacher | Student |
|--------|-------|---------|---------|
| Upload/publish/archive platform diagnostic | Yes | No | No |
| Admin users / settings / prompts / coupons / theme | Yes | No | No |
| Create class / teacher quiz / assign | No* | Yes | No |
| Enroll via join code | — | own class | Yes |
| Start another student’s attempt | No | No | No |
| Read foreign class analytics | No | No | No |
| Admin diagnostic APIs | Yes | 403 | 403 |
| Load testing / LLM telemetry | SKIP | SKIP | SKIP |

*Unless product grants admin class tools — document actual.

Also prove:
1. Attempt ID theft blocked (student A ≠ student B).
2. Teacher B cannot enroll into / mutate Teacher A’s class.
3. Logout clears prior API access.
4. Draft diagnostic never returned on student default endpoint.

PASS: all forbidden paths 401/403; no data leak.

---

## PHASE I — Edge / chaos pack

Mark SKIP only with reason:

1. Double-click Start quiz / diagnostic → one attempt
2. Double-click Submit → one final score
3. Network blip on answer save → resume or clear error
4. Timer expiry with zero answers → completed, score 0 OK
5. Timer expiry mid-click race → no corrupt state
6. Teacher changes time_limit while student in progress → document
7. Archive diagnostic while student in-progress → document
8. Late enroll after diagnostic → roster/progress coherent
9. Concurrent two students same quiz → independent attempts
10. Copy guard does not break scrolling / buttons
11. Unicode / Urdu names display correctly
12. Very long titles / special characters
13. Browser switch mid-quiz → resume + timer continuity
14. 100% diagnostic → no weak topics; Learning Chat empty OK
15. One wrong only → Learning Chat available
16. Practice to 100% → mastered on reload
17. Teacher struggling list includes 99% if rule is &lt;100%
18. Hard refresh still shows AI Tutor + timer field
19. Publish gates cannot be bypassed by raw API with incomplete draft
20. Second diagnostic upload while draft exists → blocked

---

## PHASE J — Playwright visual + alignment audit (FIX when needed)

### Goal
For EVERY in-scope screen (admin sections above, teacher hubs, student hubs,
auth pages, key modals, quiz attempt, results, Learning Chat, tutor):

1. Navigate as the correct role.
2. Full-page screenshot desktop + mobile.
3. Visually inspect for defects:
   - Misaligned columns / overlapping elements
   - Text overflow / truncated labels / cut-off buttons
   - Horizontal scroll on mobile that shouldn’t exist
   - Broken spacing (huge empty gaps / cramped collisions)
   - CTAs unusable or off-canvas
   - Math clipped in quiz stems/options
   - Modals not centered / backdrop broken
   - Nav/header colliding with content
4. **Interactive / expanded states are mandatory** — do NOT only screenshot idle hubs:
   - Teacher: open **+ Create Quiz** (PDF upload card visible); open Assign Quiz;
     open Create Lesson upload step if present
   - Admin: Diagnostic Publish form with upload dropzones visible
   - Student: diagnostic quiz in-progress + results + Learning Chat active if possible
5. **Bounding-box overlap check (Playwright):** for Create Quiz, assert the PDF
   upload box (`label.td-upload-box` / `.td-quiz-upload-box`) does **not**
   intersect the MCQs input (`#lmsQuizMcqCount`), Duration (`#lmsQuizDuration`),
   or Title (`#lmsQuizTitle`) getBoundingClientRect(). Intersection = FAIL.
6. **Mock parity:** Create Quiz open form must match mock column order
   Title | PDF | MCQs (Duration under Title). If layout drifts from mock, FAIL
   and fix CSS/HTML before PASS.
7. If defect is real: FIX in the appropriate CSS/HTML/JS (minimal, local change).
8. Re-run screenshot of the same screen after fix; attach before/after paths.
9. Do NOT “fix” by changing product copy/rules — layout/alignment only unless
   the bug is functional (then fix functional bug too if user asked for fixes;
   this prompt authorizes UI alignment fixes by default).

### Playwright conventions
- Prefer extending `_qa_audit_tmp/student_ui_e2e` and `teacher_ui_e2e`.
- Add `_qa_audit_tmp/admin_ui_e2e` if missing for admin hashes.
- Use stable waits (networkidle or explicit selectors) before screenshot.
- Mask flaky timestamps if comparing; primary goal is human visual review + fix.
- Save a manifest CSV/MD: screen | viewport | path | PASS/FAIL | fix_commit_note

### Minimum screen list

Auth: login, register student, register teacher, forgot password.

Admin: #dashboard #users #lessons #documents #diagnostic (list + upload + preview)
#settings #prompts #coupons #theme — NOT load-testing, NOT llm-telemetry.

Teacher: #lessons #classes #quizzes (list) + **Create Quiz form OPEN** (PDF card)
+ Assign Quiz modal + #analytics #tutor + class roster modal.
Also screenshot Create Lesson with PDF upload step open if the UI exposes it.

Known regression to re-test every run (caught live Oct 2026):
- Create Quiz PDF upload box overlapping Number of MCQs → MUST stay fixed.

Student: #diagnostic #diagnostic-quiz (in progress) results #learning-path
#classes #tutor #learning-chat (+ empty and active states if possible).

PASS: every listed screen captured twice (desktop/mobile); alignment FAILs fixed
or explicitly deferred with severity + reason; before/after for each fix.

---

## PHASE K — Regression pack

| Pattern | Expected |
|---------|----------|
| Draft diagnostic visible to students | Must not |
| Teacher publishes platform diagnostic | Must not |
| Wrong grade sees diagnostic | Must not |
| Smashed math stems / red nested LaTeX | Must not |
| Progress 0% after real completion | Must not |
| UNKNOWN mastery badges after practice | Must not |
| Quiz isolation across classes | Must hold |
| Copy in AI Tutor / Learning Chat | Must allow |
| Copy in diagnostic/quiz attempt | Must not |
| Timer 0 / -1 / decimal accepted | Must not |
| Auto-submit at 00:00 | Must |
| Name optional on register | Must not |
| “Teaching Assistant” in LMS UI | Must not |
| 60% treated as on-track / mastered | Must not |
| 99% treated as mastered | Must not |
| 100% treated as mastered | Must |
| Learning Chat blocked despite wrong answers | Must not |
| Create Quiz PDF upload overlaps MCQs / Duration / Title | Must not |
| Create Quiz open form matches mock Title \| PDF \| MCQs | Must |
| Hub-list screenshot used instead of Create Quiz OPEN form | Must not (incomplete) |
| Load testing / telemetry exercised in this run | Must not (out of scope) |

---

## PHASE L — Re-verify after UI fixes

1. Re-open every screen you changed; new screenshots overwrite or sit in
   `.../after_fix/`.
2. Smoke the functional path that lives on that screen (e.g. start quiz still works
   after CSS tweak).
3. Confirm no new console hard errors.

PASS: fixes verified visually + functionally.

---

## Reporting format (mandatory)

### Summary
- Overall: PASS / FAIL
- Critical / Major / Minor counts
- BASE_URL + live commit if known
- Explicit note: Load testing & LLM telemetry SKIPPED by design

### Phase results
Table: Phase | Result | Notes

### Diagnostic proof (Phase C) — required subsection
Timeline of assessment ids: upload → approve → archive → re-upload → re-approve
Student visibility YES/NO at each step with API proof
Math spot-check summary

### API matrix
Link/path to endpoint checklist; highlight authz failures

### Playwright visual
- Screens captured (count desktop/mobile)
- Alignment defects found
- Fixes applied (file paths)
- Before/after screenshot paths

### New-feature / rule proof
For each shipped rule (name, timer, copy matrix, AI Tutor title, Learning Chat
unlock, 100% mastery): PASS/FAIL + one evidence line

### Bug list
Severity | Title | Steps | Expected | Actual | Evidence | Suspected area

### Accounts & ids
List for re-audit

### Open product questions
Only real ambiguities — do not invent policy

---

## Start now

1. Confirm environment; create screenshot dirs.
2. Create fresh accounts.
3. Execute Phases A→L in order.
4. Phase C (Admin Diagnostic) is mandatory and deepest — do not skim.
5. Prefer real UI; use API/DB as ground truth.
6. Use Playwright for every screen; fix alignment issues; re-screenshot.
7. Do NOT test load testing or LLM telemetry.
8. Report with full evidence pack under `_qa_audit_tmp/deep_qa_out/`.
```
