# LMS Visual + Backend QA Report

- BASE_URL: http://127.0.0.1:5001
- Run: 20260929_064214
- Git: ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+19 uncommitted paths)
- Summary: **26 PASS / 7 FAIL / 1 BLOCKED** (34 items)

## Accounts & IDs

```json
{
  "accounts": {
    "teacher": "qa.teacher.20260929_064214@test.local",
    "A": "qa.student.a.20260929_064214@test.local",
    "B": "qa.student.b.20260929_064214@test.local",
    "C": "qa.student.c.20260929_064214@test.local"
  },
  "ids": {
    "teacher_id": 34,
    "student_A": 35,
    "student_B": 36,
    "student_C": 37,
    "class_id": 19,
    "join_code": "63GBDTRR",
    "diag_attempt_A": 73,
    "diagnostic_id": 25,
    "deficiency_session_A": 26,
    "pdf_quiz_id": 47,
    "assignment_id": 25,
    "lesson_id": 12,
    "diag_attempt_B": 74
  },
  "password": "QaPass!2026x"
}
```

## Setup results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Health: app responds at BASE_URL | n/a | n/a | ok: GET /auth/login 200 | n/a | **PASS** | ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+19 uncommitted paths)  |
| Register TEACHER via UI → lands on /teacher-dashboard | ok: screenshots/T00_teacher_landing.png | ok: register_email → verify → register | n/a | ok: users.id=34 role=teacher | **PASS** | qa.teacher.20260929_064214@test.local → http://127.0.0.1:5001/teacher-dashboard#lessons  |
| Register STUDENTS A, B, C via UI (grade 7) → land on /student-dashboard | ok: screenshots/S00_student_landing_gate.png | ok: 3× register via UI | n/a | ok: A: id=35 grade=7th; B: id=36 grade=7th; C: id=37 grade=7th | **PASS** | A: id=35 grade=7th; B: id=36 grade=7th; C: id=37 grade=7th  |

## Student results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Before diagnostic: gate forces diagnostic; Join/Tutor/Path blocked; Learning Path locked (API) | ok: screenshots/S01_gate_orientation.png | ok: nav + join blocked | ok: diagnostic_completed=false, path items=0 | ok: student_profiles=[(0,)] | **PASS** |   |
| Diagnostic: start → real questions (API count) → timer counts down | ok: screenshots/S02_diag_in_quiz.png | ok: timer 41:14→41:11 | ok: 32 questions, time_limit=41m | ok: attempt 73 in_progress, expires_at=2026-09-29 14:24:54.964421 | **PASS** | Q1 UI='Which number is irrational?' API id=None  |
| Math renders on diagnostic (MathJax, no red/broken TeX, no raw LaTeX) | ok: screenshots/S02b_diag_math.png: 1 mjx, 0 merror, no raw TeX | n/a | n/a | n/a | **PASS** | question #1  |
| Diagnostic submit (deliberate mix) → results by topic; UI = API = DB (attempt, answers, topic scores) | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: [('Geometry', ['Geometry', '9/16', '56%'], 9, 16, 0), ('Inequalities and Equations', ['Inequalities and Equations', '3/4', '75%'], 3, 4, 0), ('Logarithms', ['Logarithms', '3/5', '60%'], 3, 5, 0), ('Polynomials', ['Polynomials', '3/5', '60%'], 3 screenshots/FAIL_09_diagnostic_submit_deliberate_mix_results.png |
| Retake rule: start again returns the finished attempt; UI shows results + explicit Retake only | n/a | n/a | n/a | n/a | **FAIL** | TimeoutError: Page.click: Timeout 60000ms exceeded. Call log:   - waiting for locator("#lmsDiagCloseBtn")     - locator resolved to <button type="button" class="sd-backlink" id="lmsDiagCloseBtn" onclick="closeLmsDiagnostic()">…</button>   - attempting click ac screenshots/FAIL_10_retake_rule_start_again_returns_the_fini.png |
| Diagnostic hub: stat cards + Taken row = API dashboard/attempts = DB | ok: screenshots/S04_diag_hub.png | ok: Taken → View Results | ok: weak=2 chips match | ok: score (19.0, 32.0); weak rows=2 | **PASS** | UI 59%  |
| Learning Path overview after diagnostic = dashboard/learning-path/progress APIs = DB mastery | ok: screenshots/S05_learning_path.png | ok: nav | ok: 9 topics/2 weak, 1 steps, path '7 of 9 topics', overall_progress=64.0 | ok: weak set equal; weighted avg 64.00 | **PASS** |   |
| Learning Chat: start, wrong answer → tutor help → correct answers; progress updates (UI+API+StudentTopicScore) | ok: screenshots/S06_learning_chat.png, screenshots/S06c_learning_chat_done.png | ok: 2 answers, 1 deliberate wrong + tutor; tutor: Level 1 – Prompt  No problem! Let's start with the first ste | ok: overall 64.0 → 65.38; weak 2; path '7 of 9 topics' | ok: session 26 (2, 2, 'completed'); 1 topic score rows changed: [(34, ((100.0, 'mastered', 10), (100.0, 'mastered', 12)))] | **PASS** |   |
| Guided practice (per-topic) opens; hint or graceful error (API + DB) | n/a | n/a | n/a | n/a | **BLOCKED** | practice API 400 {"error": {"code": "validation_error", "message": "No practice question available for this topic"}, "success": false}; DB active questions for topic 52 = 0; UI shows 'No practice question available for this topic' (screenshots/S07_guided_pract  |
| Attempts history: rows = API; View Result modal = API results | ok: screenshots/S10_attempt_result_modal.png | ok: View Result modal | ok: 1 attempts | ok: 1 attempt rows | **PASS** | modal 59%  |
| Lesson viewer: class lesson opens, content renders (browse_lessons?class_id = UI = DB) | ok: screenshots/S11_lesson_viewer.png | ok: View | ok: 1 lessons for class | ok: 1 public grade-7 lessons by teacher | **PASS** |   |
| Lesson chat: ask question → answer persisted as conversation (API + DB) | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: POST /api/lessons/ask_question -> 500 {"code":"INTERNAL_ERROR","error":"Failed to generate an answer. Please try again."} ; UI shows graceful message; DB conversation [(36, 'Conversation with QA Lesson 20260929_064214')] msgs [('user', 'What is screenshots/FAIL_20_lesson_chat_ask_question_answer_persiste.png |
| AI Tutor: send → reply; history API + DB; reload restores; clear empties | ok: screenshots/S13_ai_tutor.png | ok: send, reload restore, clear | ok: 2 msgs → 0 | ok: tutor_chat_messages=[(2,)] | **PASS** | 'First, we need a common denominator to add these fractions. The common denominator for 4 and 8 is 8.\n\nDoes that make sen'  |
| Join class via code (Student B, diagnostic done BEFORE joining) — UI + API + DB | ok: screenshots/S14_join_class_modal.png, screenshots/S14b_B_classes.png | ok: join modal | ok: classes/mine | ok: enrollment [('active',)] | **PASS** |   |

## Teacher results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Shell: nav labels + icons, logo, no broken images | ok: screenshots/T01_teacher_shell.png | ok: nav rendered | n/a | n/a | **PASS** | My Lessons, Classes, Quizzes, Analytics, AI Tutor  |
| Classes: teaching grade + create class form persists (UI → API → DB) | ok: screenshots/T02_create_class_form.png | ok: created via form | ok: classes/mine has id=19 code=63GBDTRR | ok: classes row (34, '7', '63GBDTRR', 1) | **PASS** | class_id=19 join_code=63GBDTRR  |
| Add Students tab adds A + C (UI → roster API → class_enrollments) | ok: screenshots/T03_add_students.png | ok: multi-select add | ok: roster=['qa_stu_a_064214', 'qa_stu_c_064214'] | ok: enrollments=[(35, 'active'), (37, 'active')] | **PASS** |   |
| Quizzes: PDF → MCQ generate → preview → publish (UI → API → DB) | ok: screenshots/T04_quiz_create.png, screenshots/T04b_quiz_preview.png, screenshots/T05_quizzes_list.png | ok: 5 MCQs generated + published | ok: quiz 47 status=published; list count 1 = UI | ok: assessments ('published', 34, 'quiz'); 5 questions | **PASS** |   |
| Assign quiz to class via UI → assignment published (API + DB) | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: GET /api/lms/assignments -> 400: {"error": {"code": "error", "message": "class_id is required for teachers"}, "success": false} screenshots/FAIL_16_assign_quiz_to_class_via_ui_assignment_p.png |
| Lessons: create (Use PDF as lesson, grade 7) + publish; list = my_lessons API = DB | ok: screenshots/T07_lesson_create.png, screenshots/T07b_lesson_view.png, screenshots/T07c_lessons_list.png | ok: create + publish + view | ok: my_lessons total 1 = UI rows | ok: lessons 12 (34, '7', 1, 'finalized') | **PASS** |   |
| Classes roster tab: A, B, C listed (UI = API = DB) | ok: screenshots/T08_roster.png | ok: Roster tab | ok: 3 students | ok: 3 active enrollments | **PASS** |   |
| Analytics Topic Progress: A's % = roster API = student progress API = DB; detail shows topics | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: (0, 7) screenshots/FAIL_24_analytics_topic_progress_a_s_roster_api_.png |
| Analytics Quiz Results collapsed ↔ expanded = analytics/quizzes API = assignment_submissions DB | n/a | n/a | n/a | n/a | **FAIL** | TypeError: type NoneType doesn't define __round__ method screenshots/FAIL_25_analytics_quiz_results_collapsed_expande.png |
| Analytics Struggling = struggling API; consistent with weak mastery in DB | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: students with weak topics in DB but not struggling: [36] ({35: 3, 36: 1, 37: 0}) screenshots/FAIL_26_analytics_struggling_struggling_api_cons.png |
| Analytics Roster: progress per student = API = DB (not all zeros); late-enrolled B included | ok: screenshots/T12_analytics_roster.png | ok: tab | ok: qa_stu_a_064214: UI 54% API 54.1 DB 54.1; qa_stu_b_064214: UI 81% API 81.25 DB 81.25; qa_stu_c_064214: UI 0% API 0.0 DB None | ok: weighted StudentTopicScore avg matches | **PASS** | late-enroll B: roster 81.25 = own progress 81.25 (diagnostic taken before join counts)  |
| Student report endpoint = student-facing scores (same ids) | n/a | n/a | ok: 1 attempts + 10 topics match student APIs | ok: topic scores match | **PASS** |   |
| AI Tutor: Teaching Assistant send → reply; history API + DB | ok: screenshots/T13_teacher_tutor.png, screenshots/T13b_teacher_chat_history_view.png | ok: send | ok: 2 history msgs | n/a | **PASS** | 'AI\n\nOne effective tip for teaching fractions is to use visual aids, such as fraction circles or bars'  |

## Cross-role results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| S5: reload / re-login keeps submitted diagnostic + enrollment | ok: screenshots/X05_relogin.png | ok: fresh login, no gate, class present | n/a | ok: diagnostic_completed=1 | **PASS** |   |
| S6: role isolation on role-gated APIs + dashboards | n/a | n/a | ok: GET /api/lms/students/me/dashboard → 403; GET /api/lms/students/me/assignments → 403; POST /api/lms/quizzes/47/start → 403; POST /api/lms/classes/join → 403; POST /api/lms/classes → 403; GET /api/lms/classes/19/students → 403; GET /api/lms/classes/19/analy | n/a | **PASS** |   |

## Cross-cutting results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Mobile 390px: student + teacher main views, no horizontal overflow | ok: S:diagnostic:0px S:learning-path:0px S:classes:0px S:tutor:0px T:lessons:0px T:classes:0px T:quizzes:0px T:analytics:0px T:tutor:0px | n/a | n/a | n/a | **PASS** |   |
| No JS page errors; no 404 static assets; failed API calls listed | ok: 0 static 404, 0 page errors | n/a | n/a | n/a | **PASS** | API 4xx/5xx seen: ['400 POST http://127.0.0.1:5001/api/lms/practice/sessions', '500 POST http://127.0.0.1:5001/api/lessons/ask_question']  |

## Failures / blocked (raw)

### FAIL: [Student] Diagnostic submit (deliberate mix) → results by topic; UI = API = DB (attempt, answers, topic scores)

```
AssertionError: [('Geometry', ['Geometry', '9/16', '56%'], 9, 16, 0), ('Inequalities and Equations', ['Inequalities and Equations', '3/4', '75%'], 3, 4, 0), ('Logarithms', ['Logarithms', '3/5', '60%'], 3, 5, 0), ('Polynomials', ['Polynomials', '3/5', '60%'], 3, 5, 0), ('Statistics', ['Statistics', '1/1', '100%'], 1, 1, 0)]
```

### FAIL: [Student] Retake rule: start again returns the finished attempt; UI shows results + explicit Retake only

```
TimeoutError: Page.click: Timeout 60000ms exceeded.
Call log:
  - waiting for locator("#lmsDiagCloseBtn")
    - locator resolved to <button type="button" class="sd-backlink" id="lmsDiagCloseBtn" onclick="closeLmsDiagnostic()">…</button>
  - attempting click action
    2 × waiting for element to be visible, enabled and stable
      - element is not visible
    - retrying click action
    - waiting 20ms
    2 × waiting for element to be visible, enabled and stable
      - element is not visible
    - retrying click action
      - waiting 100ms
    113 × waiting for element to be visible, enabled and stable
        - element is not visible
      - retrying click action
        - waiting 500ms

```

### BLOCKED: [Student] Guided practice (per-topic) opens; hint or graceful error (API + DB)

```
practice API 400 {"error": {"code": "validation_error", "message": "No practice question available for this topic"}, "success": false}; DB active questions for topic 52 = 0; UI shows 'No practice question available for this topic' (screenshots/S07_guided_practice.png)
```

### FAIL: [Teacher] Assign quiz to class via UI → assignment published (API + DB)

```
AssertionError: GET /api/lms/assignments -> 400: {"error": {"code": "error", "message": "class_id is required for teachers"}, "success": false}
```

### FAIL: [Student] Lesson chat: ask question → answer persisted as conversation (API + DB)

```
AssertionError: POST /api/lessons/ask_question -> 500 {"code":"INTERNAL_ERROR","error":"Failed to generate an answer. Please try again."}
; UI shows graceful message; DB conversation [(36, 'Conversation with QA Lesson 20260929_064214')] msgs [('user', 'What is this lesson about?'), ('bot', 'Failed to generate an answer. Please try again.')]. Server started with SKIP_EXTRA_STARTUP=true (.env) → lesson Q&A graph not initialized  (screenshots/S12_lesson_chat.png)
```

### FAIL: [Teacher] Analytics Topic Progress: A's % = roster API = student progress API = DB; detail shows topics

```
AssertionError: (0, 7)
```

### FAIL: [Teacher] Analytics Quiz Results collapsed ↔ expanded = analytics/quizzes API = assignment_submissions DB

```
TypeError: type NoneType doesn't define __round__ method
```

### FAIL: [Teacher] Analytics Struggling = struggling API; consistent with weak mastery in DB

```
AssertionError: students with weak topics in DB but not struggling: [36] ({35: 3, 36: 1, 37: 0})
```

