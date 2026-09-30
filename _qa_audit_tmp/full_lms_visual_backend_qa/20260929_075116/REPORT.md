# LMS Visual + Backend QA Report

- **BASE_URL:** http://127.0.0.1:5001, the user's running `run.py` (local SQLite `instance/iqbalai_local.db`; `.env` has `SKIP_EXTRA_STARTUP=true`)
- **Date:** 2026-09-29, final run `20260929_075116` (an earlier run `20260929_064214` was used for root-cause probes)
- **Git:** `ui_integration_for_teacher` @ `6c3c3ea`, plus 19 uncommitted paths (the new student UI)
- **Mode:** QA only. Nothing fixed, committed or deployed.
- **Summary (after targeted re-checks): 31 PASS / 4 FAIL / 2 BLOCKED** (37 items)
- **Suite:** `../playwright/run_qa.py` (copy in this folder). Re-checks: `../playwright/recheck_final.py` → `rechecks.json`. Raw per-layer results: `results.json`.

Every item was judged on 4 layers: VISUAL (screenshot and asset checks), INTERACTION (real UI flow), API (same user's session) and DB (SQLite rows). All accounts were registered through the real UI: `register_email` → verify link token read from `email_verification_tokens` → register form.

## Accounts & IDs

| Role | Email | user_id |
|---|---|---|
| Teacher | qa.teacher.20260929_075116@test.local | 38 |
| Student A (enrolled by teacher, then diagnostic) | qa.student.a.20260929_075116@test.local | 39 |
| Student B (diagnostic first, then joined by code = late enroll) | qa.student.b.20260929_075116@test.local | 40 |
| Student C (enrolled, never attempted; used for the in-session retake re-check) | qa.student.c.20260929_075116@test.local | 41 |

Password for all: `QaPass!2026x`. class_id **20**, join code **P7CQH0OB** (grade 7) · platform diagnostic **25** (grade 7, 32 Q, 41 min) · A's diagnostic attempt **75** · Learning Chat session **28** · PDF quiz **48** (5 MCQs) → assignment **26** → A's quiz attempt **76** · lesson **13** · B's diagnostic attempt **77**.

## Student results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Register via UI → /student-dashboard | ✓ | ✓ | – | users 39/40/41 role=student, grade 7th | **PASS** | S00 |
| Before diagnostic: gate forces diagnostic; Classes/Tutor/Path/Join blocked | ✓ | ✓ nav blocked | onboarding `diagnostic_completed=false`, path items 0 | student_profiles `diagnostic_completed=0` | **PASS** | S01 |
| Diagnostic start → real questions → timer counts down | ✓ | ✓ timer decreasing | 32 questions, 41 min | attempt 75 in_progress, expires_at set | **PASS** | S02 |
| Math renders (fractions/√, no merror, no raw TeX) | ✓ mjx, 0 merror | – | – | – | **PASS** | S02b |
| Diagnostic submit (deliberate ~60% plan) → results by topic | ✓ | ✓ unanswered confirm | score 19/32 = 59.38 % | attempt 75 score 19; 19 `is_correct` rows | **FAIL** | Overall score correct everywhere, but the per-topic breakdown is non-deterministic (F1) |
| Retake rule | ✓ | ✓ (re-check, student C) | start → `already_completed=true`, same attempt | 1 attempt row | **PASS** | Retake is only offered as the explicit "Retake with new questions" button. Suite click timeout was a harness artifact; the Back link is visible after submit and on reopen (R3, `rechecks.json`) |
| Diagnostic hub: stat cards + Taken row | ✓ | ✓ | weak chips = API | score (19,32) | **PASS** | S04 |
| Learning Path overview = dashboard / learning-path / progress | ✓ | ✓ | 12 topics / 3 weak, path "9 of 12", overall 62.71 | weak set = DB; weighted avg 62.71 | **PASS** | S05 |
| Learning Chat: wrong → tutor help → correct; progress updates | ✓ | ✓ 5 answers, tutor reply | overall 62.71 → 62.50, path "10 of 13" | session 28 completed (3/5); Algebra Basics 0 → 75 %; **new** topic 69 created 0 % weak | **PASS** (with finding F4) | S06, S06b, S06c |
| Guided practice (per topic) | ✓ graceful message | ✓ | 400 "No practice question available for this topic" | 0 active questions for topic 69 | **BLOCKED** | Backend has no bank questions for AI-created topics (R2) |
| My Classes: enrolled class + assignment visible | ✓ | ✓ | classes/mine = 20; assignment 26 not_started, class_id 20 | enrollment active | **PASS** | S08 |
| Take assigned quiz (deliberate 3 of 5) → Review | ✓ | ✓ | 3/5 = 60 % | attempt 76 submitted 3/5; submission (submitted, 76) | **PASS** | S09, S09b, S09c |
| Attempts history + View Result modal | ✓ | ✓ | 2 attempts | 2 rows | **PASS** | S10 |
| Lesson viewer (class lesson) | ✓ PDF rendered | ✓ | browse_lessons?class_id=20 → 1 | 1 public grade-7 lesson | **PASS** | S11 |
| Lesson chat | ✓ graceful error bubble | ✓ | **500** | conversation 38 saved, user message only | **BLOCKED (environment)** | F5 |
| AI Tutor: send, reload restores, clear | ✓ | ✓ | 2 msgs → 0 after clear | tutor_chat_messages 2 | **PASS** | S13 |
| Join class by code (B, diagnostic before join) | ✓ | ✓ | classes/mine = 20 | enrollment active | **PASS** | S14 |
| Cross-check `/students/me/dashboard` = UI cards | ✓ | – | ✓ | ✓ | **PASS** | items 11/12 |
| Cross-check `/students/me/progress` = overall | n/a: no "Overall Progress" card in the student UI (hidden by product decision ecb0213) | – | 62.71 | weighted avg 62.71 | **PASS** | |
| Weak Topics badges = real mastery (not UNKNOWN) | ✓ | – | ✓ | `mastery_status='weak'` rows | **PASS** at read time (but see F2: they change on read) | |

## Teacher results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Register via UI → /teacher-dashboard | ✓ | ✓ | – | users 38 role=teacher | **PASS** | T00 |
| Shell: 5 nav items + icons, logo, no broken images | ✓ | ✓ | – | – | **PASS** | T01 |
| Create class (teaching grade 7) persists | ✓ | ✓ | classes/mine id 20, code P7CQH0OB | classes row (38, '7', code, active) | **PASS** | T02 |
| Add Students (multi-select A + C) | ✓ | ✓ | roster = A, C | 2 active enrollments | **PASS** | T03 |
| Quiz PDF → 5 MCQs → preview → publish | ✓ | ✓ | quiz 48 published; list = UI | assessment published, 5 questions | **PASS** | T04, T04b, T05 |
| Assign quiz to class | ✓ | ✓ | assignment 26 listed | assignments (26, published, 20, 48, 38) | **PASS** | T06 |
| Lesson create (Use PDF as lesson, grade 7) + publish + view | ✓ | ✓ | my_lessons total = UI rows | lessons 13 (38, '7', public, finalized) | **PASS** | T07, T07b, T07c |
| Roster tab: A, B, C | ✓ | ✓ | 3 | 3 active enrollments | **PASS** | T08 |
| Analytics Topic Progress | ✓ | ✓ | overall 62.3 = student progress = DB ✓; **topic detail UI 6 / by-topic API 7 / DB 14 rows** | – | **FAIL** | F3, T09, R1 |
| Analytics Quiz Results collapsed ↔ expanded | ✓ | ✓ | A 60 %; submissions API = attempt 76 | submissions A submitted, C not_started | **PASS** | T10, T10b |
| Analytics Struggling | ✓ | ✓ | struggling = {A, C} | C has no data (overall 0.0) | **FAIL** (consistency) | F6, T11 |
| Analytics Roster progress (not all zeros; late-enroll B included) | ✓ | ✓ | A 62.3 / B 81.25 / C 0.0 | weighted avg equal | **PASS** | T12 |
| Student report endpoint = student-facing scores | – | – | 2 attempts + 14 topics match | topic scores match | **PASS** | |
| Teaching Assistant chat + history | ✓ | ✓ | 2 history msgs | – | **PASS** | T13 |

## Cross-role scenarios

| # | Scenario | Result | Proof (same ids) |
|---|---|---|---|
| S1 | Student finishes diagnostic → teacher analytics/roster update | **PASS** for overall %, **FAIL** for topic detail | Roster A 62 % = `roster.overall_progress` 62.3 = A's `/students/me/progress` 62.3 = weighted `student_topic_scores` 62.3 (T12). Topic-level detail disagrees (F3). Late enroll: B took the diagnostic *before* joining and still shows 81.25 in the roster = B's own progress (diagnostic data counts after joining). |
| S2 | Assign → student sees → submits → teacher sees | **PASS** | assignment 26 (DB published, class 20) → A sees it "Not Attempted" (S08) → submits 3/5 via UI (attempt 76, submission row) → teacher Quiz Results "1 / 3 submitted", A 60 % (T10) = analytics API = submissions API = DB |
| S3 | Learning Chat changes mastery; reload keeps it | **FAIL** | LC wrote Algebra Basics 0 → 75 % (DB). But simply **viewing** the diagnostic results afterwards rewrote 4 `student_topic_scores` rows and moved Overall Progress (F2, `api_dumps/S3b_results_view_mastery_diff.json`). |
| S4 | Math spot-check | **PASS** | S02b: fraction/√ question, MathJax output, 0 `mjx-merror`, no raw TeX |
| S5 | Reload / re-login keeps diagnostic + enrollment | **PASS** | fresh login: no gate, class 20 present; `diagnostic_completed=1` |
| S6 | Role isolation | **PASS** | teacher → student APIs 403 (dashboard, assignments, quiz start, join); student → teacher APIs 403 (create class, roster, analytics, assignments, teacher tutor); `/student-dashboard` as teacher → 302 `/teacher-dashboard`; `/teacher-dashboard` as student → 302 (`api_dumps/S6_isolation.json`) |

## Failures (detailed)

### F1: Diagnostic topic breakdown differs between views of the same attempt (backend / service) — HIGH
- **Repro:** A submits diagnostic 25 (attempt 75). Compare the results screen with `GET /api/lms/attempts/75/results`.
- **Expected:** one fixed topic grouping per attempt. The table, tiles and API agree.
- **Actual:** the results table showed 6 topic rows while the API, called seconds later, returned 5 (`AssertionError: (6, 5)`). Within one response, `topic_breakdown` and `all_topics` use different groupings. On the student's own results screen (run 064214, `screenshots/S03_diag_results.png`) the table says **Geometry 9/16 (56 %)** while the "Strong areas" tile says **Geometry 100 %, 9 of 9**, and "Areas to improve" lists **Algebra Basics 1 of 11**, which isn't in the table. The overall score (19/32) is correct and matches the DB.
- **Layer:** service. `performance_service.topic_breakdown_for_attempt` and `weakness_analyzer.analyze_diagnostic_attempt` each group via AI (`_topic_buckets_from_diagnostic_analysis`), and each results call can regroup.

### F2: `GET /attempts/:id/results` (a read) rewrites StudentTopicScore → Weak Topics, Learning Path, Overall Progress and teacher analytics drift without any student activity (backend / service) — CRITICAL
- **Repro (no writes by the student):** A (run 064214, student 35). Call `/students/me/dashboard` twice (no change), then `/attempts/73/results` twice.
- **Actual (`20260929_064214/api_dumps/A_mastery_overwrite_probe.json`):** each results call (16–22 s) rewrote `student_topic_scores`. Geometry went 55.6 % weak (sample 18) → 0 % weak (sample 6); new topic rows were created; Overall Progress went **54.1 → 65.96**; Weak Topics went {Algebra Basics, Geometry, Triangle Properties} → {Algebra Basics, Geometry}. Reproduced in the final suite as S3b (4 rows changed after one "View Results" click).
- **Consequences:** Learning Chat gains are overwritten on the next results view. A's single 32-question diagnostic has produced **14** topic rows with overlapping AI names ("Geometry", "Geometry and Triangles", "Coordinate Geometry", …), with sample weights summing to 61. The `topics` table grew by 28 rows today. Diagnostic results take 16–22 s per view (an LLM call).
- **Code:** `attempt_service.get_attempt_results()` (documented "idempotent") calls `performance_service.update_topic_scores_from_attempt()` (`blend=False`) for diagnostics on every call (`attempt_service.py:676-680`). The analysis cache lives inside the shared assessment's description JSON (`weakness_analyzer._set_cache`), keyed per attempt, and is read-modify-written per request.
- **Callers that trigger it:** student hub "View Results", Quiz History "View Result", reopening a completed diagnostic, and the old student UI's same paths.

### F3: Teacher Topic Progress detail ≠ by-topic API ≠ DB (backend, consequence of F1/F2) — HIGH
- **Repro:** teacher → Analytics → class 20 → expand A.
- **Actual (re-check after the loading spinner cleared, `rechecks.json`, R1):** UI 6 topics, `/progress/by-topic` 7, `student_topic_scores` 14, with different names in each. The overall % row does match (62 %).
- **Note:** the suite's first attempt counted rows during the spinner (the loading placeholder reuses `.td-empty`); the re-check waited properly.

### F4: Learning Chat can lower progress and add weak topics (service) — MEDIUM (observation)
- Session 28: 3 of 5 correct. Algebra Basics improved 0 → 75 %, but the session also **created** topic 69 at 0 % (weak), so Overall Progress fell 62.71 → 62.50 and the path went "9 of 12" → "10 of 13".
- A previously stored Learning Chat question (earlier session) had a wrong key: mean of {4, 8, 6, 5, 3} = 5.2, options 5/6/4/7, `correct_option_index` → "6".

### F5: Lesson chat returns 500 on this server (environment) — BLOCKED
- `POST /api/lessons/ask_question` → 500 "Failed to generate an answer". The UI shows the graceful bubble; the conversation and user message are saved; no bot reply.
- **Proof:** `logs/app.log` at 06:49 for lesson 12: `RuntimeError: LESSON_QA_GRAPH is not initialized`. The server was started with `.env` `SKIP_EXTRA_STARTUP=true`, which skips `init_lesson_qa_graph()` (`app/__init__.py:295-307`). The same flow passed earlier today on a server started with `SKIP_EXTRA_STARTUP=false`. Re-test after restarting 5001 with that flag.

### F6: Struggling Students lists a student with no data (backend rule) — LOW
- C (no attempts, `overall_progress` 0.0) is in `/analytics/struggling` and the Struggling tab, while the Roster tab shows C as "Not Attempted".
- **Cause:** `is_struggling = weak >= 2 or progress < 60` (`class_service.py:293`, `analytics_service.py:205/246`), and `get_overall_progress()` returns 0.0 when there's no data. Previously noted in the teacher handoff.

### Blocked: guided practice for AI-created topics
- `POST /api/lms/practice/sessions {topic_id: 69}` → 400 "No practice question available for this topic"; the DB has 0 active questions for topic 69. The UI shows the message (R2). Practice depends on question-bank rows for the topic, which AI-created diagnostic topics don't have.

## Visual notes (vs `updated_new_ui` screenshots)
- **Student** (S00–S14, M_S_*): nav, icons, logo, stat cards, diagnostic hub/quiz/results, learning path, split Learning Chat, classes (lessons + quizzes), lesson viewer and tutor match the design layout. Mobile 390 px: no horizontal overflow on any view.
- **Teacher** (T00–T13, M_T_*): matches the design layout for lessons, classes, quizzes, analytics (Topic Performance tab hidden by product decision) and roster donut/cards.
- The teacher page shows every success message **twice** (for example "Quiz published successfully!" top-right plus bottom-centre, T05). This comes from the shared `lmsShowToast`; the student page already hides the duplicate.
- The Roster shows C as "Not Attempted" while Struggling counts C (F6).
- The notification bell and 📎 attach from the design are hidden (no backend).
- Full-page screenshots draw the sticky top bar mid-page (a Playwright stitching artifact, not a UI bug).

## Residual risks / not tested
- **Staging (dil.iqbalai.com) was not tested:** it creates real accounts/data on a shared server; run with approval.
- **Timeout auto-submit** was not re-run here (passed in today's student UI E2E via a forced expiry).
- **Teacher "Generate from PDF" lesson + RAG lesson chat** was not run (long LLM flow; covered by the teacher E2E earlier). Lesson edit, FAQ, Word/PPT and delete for teachers were not re-run here.
- LLM-dependent outputs (tutor replies, grouping) are non-deterministic; the numbers above are from this run.
- 8 throwaway `qa.*@test.local` users (runs 064214 + 075116), classes 19–20, quizzes 47–48, lessons 12–13 remain in the local DB.
