# LMS Visual + Backend QA Report

- BASE_URL: http://127.0.0.1:5057
- Run: 20260929_124739
- Git: ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+28 uncommitted paths)
- Summary: **36 PASS / 1 FAIL / 0 BLOCKED** (37 items)

## Accounts & IDs

```json
{
  "accounts": {
    "teacher": "qa.teacher.20260929_124739@test.local",
    "A": "qa.student.a.20260929_124739@test.local",
    "B": "qa.student.b.20260929_124739@test.local",
    "C": "qa.student.c.20260929_124739@test.local"
  },
  "ids": {
    "teacher_id": 51,
    "student_A": 52,
    "student_B": 53,
    "student_C": 54,
    "class_id": 23,
    "join_code": "5LJL1TBX",
    "diag_attempt_A": 87,
    "diagnostic_id": 25,
    "deficiency_session_A": 37,
    "pdf_quiz_id": 51,
    "assignment_id": 29,
    "quiz_attempt_A": 88,
    "lesson_id": 16,
    "diag_attempt_B": 89
  },
  "password": "QaPass!2026x"
}
```

## Setup results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Health: app responds at BASE_URL | n/a | n/a | ok: GET /auth/login 200 | n/a | **PASS** | ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+28 uncommitted paths)  |
| Register TEACHER via UI → lands on /teacher-dashboard | ok: screenshots/T00_teacher_landing.png | ok: register_email → verify → register | n/a | ok: users.id=51 role=teacher | **PASS** | qa.teacher.20260929_124739@test.local → http://127.0.0.1:5057/teacher-dashboard#lessons  |
| Register STUDENTS A, B, C via UI (grade 7) → land on /student-dashboard | ok: screenshots/S00_student_landing_gate.png | ok: 3× register via UI | n/a | ok: A: id=52 grade=7th; B: id=53 grade=7th; C: id=54 grade=7th | **PASS** | A: id=52 grade=7th; B: id=53 grade=7th; C: id=54 grade=7th  |

## Student results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Before diagnostic: gate forces diagnostic; Join/Tutor/Path blocked; Learning Path locked (API) | ok: screenshots/S01_gate_orientation.png | ok: nav + join blocked | ok: diagnostic_completed=false, path items=0 | ok: student_profiles=[(0,)] | **PASS** |   |
| Diagnostic: start → real questions (API count) → timer counts down | ok: screenshots/S02_diag_in_quiz.png | ok: timer 41:14→41:11 | ok: 32 questions, time_limit=41m | ok: attempt 87 in_progress, expires_at=2026-09-29 20:30:34.592235 | **PASS** | Q1 UI='Which number is irrational?' API id=None  |
| Math renders on diagnostic (MathJax, no red/broken TeX, no raw LaTeX) | ok: screenshots/S02b_diag_math.png: 1 mjx, 0 merror, no raw TeX | n/a | n/a | n/a | **PASS** | question #1  |
| Diagnostic submit (deliberate mix) → results by topic; UI = API = DB (attempt, answers, topic scores) | ok: screenshots/S03_diag_results.png | ok: answered 31/32, unanswered confirm | ok: score 19.0/32.0 = 59.38%, 7 topics | ok: attempt submitted score=19.0; is_correct=19; 7 topic score rows match; profile=(1, 25) | **PASS** | expected 19 correct; UI 59%  |
| Retake rule: start again returns the finished attempt; UI shows results + explicit Retake only | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: Back link hidden on reopen after completing: {'view': 'diagnostic', 'needs': False, 'mandatory': False, 'allow': True, 'back': 'inline-flex', 'backRect': 0} screenshots/FAIL_10_retake_rule_start_again_returns_the_fini.png |
| Diagnostic hub: stat cards + Taken row = API dashboard/attempts = DB | ok: screenshots/S04_diag_hub.png | ok: Taken → View Results | ok: weak=1 chips match | ok: score (19.0, 32.0); weak rows=1 | **PASS** | UI 59%  |
| Learning Path overview after diagnostic = dashboard/learning-path/progress APIs = DB mastery | ok: screenshots/S05_learning_path.png | ok: nav | ok: 7 topics/1 weak, 1 steps, path '6 of 7 topics', overall_progress=61.29 | ok: weak set equal; weighted avg 61.29 | **PASS** |   |
| Learning Chat: start, wrong answer → tutor help → correct answers; progress updates (UI+API+StudentTopicScore) | ok: screenshots/S06_learning_chat.png, screenshots/S06c_learning_chat_done.png | ok: 2 answers, 1 deliberate wrong + tutor; tutor: Level 1 – Prompt  No problem! Let's start with the first ste | ok: overall 61.29 → 63.64; weak 1; path '6 of 7 topics' | ok: session 37 (2, 2, 'completed'); 1 topic score rows changed: [(34, ((0.0, 'weak', 8), (20.0, 'weak', 10)))] | **PASS** |   |
| Guided practice (per-topic) opens; hint or graceful error (API + DB) | ok: screenshots/S07b_guided_practice_hint.png | ok: MEDIUM  Point (2,-5) lies in quadrant: I II III IV Need a hint? / hint: Hint:  Suggest a strategy without solving.  Question: Point  | ok: 201 | ok: practice_sessions [(2, 34, 'active')] | **PASS** |   |
| My Classes: enrolled class (API = DB) with assignment visible | ok: screenshots/S08_my_classes.png | ok: class expanded | ok: classes/mine=23; assignment 29 not_started | ok: enrollment ('active', '2026-09-29 19:49:17.162551') | **PASS** |   |
| Take assigned quiz in UI (deliberate 3 of 5) → Review = API results = DB attempt/submission | ok: screenshots/S09_quiz_taking.png, screenshots/S09b_quiz_result.png, screenshots/S09c_quiz_review.png | ok: 5 answered via UI | ok: results 3.0/5.0 60.0% | ok: attempt ('submitted', 3.0, 5.0); submission ('submitted', 88) | **PASS** | expected 3 correct; UI 60% (3 correct)  |
| Attempts history: rows = API; View Result modal = API results | ok: screenshots/S10_attempt_result_modal.png | ok: View Result modal | ok: 2 attempts | ok: 2 attempt rows | **PASS** | modal 60%  |
| Lesson viewer: class lesson opens, content renders (browse_lessons?class_id = UI = DB) | ok: screenshots/S11_lesson_viewer.png | ok: View | ok: 1 lessons for class | ok: 1 public grade-7 lessons by teacher | **PASS** |   |
| Lesson chat: ask question → answer persisted as conversation (API + DB) | ok: screenshots/S12_lesson_chat.png | ok: send | ok: 200 | ok: conversation (44, 'Conversation with QA Lesson 20260929_124739') msgs 2 | **PASS** |   |
| AI Tutor: send → reply; history API + DB; reload restores; clear empties | ok: screenshots/S13_ai_tutor.png | ok: send, reload restore, clear | ok: 2 msgs → 0 | ok: tutor_chat_messages=[(2,)] | **PASS** | 'First, we need to find a common denominator for the fractions. The denominators are 4 and 8.\n\nWhat is the least common m'  |
| Join class via code (Student B, diagnostic done BEFORE joining) — UI + API + DB | ok: screenshots/S14_join_class_modal.png, screenshots/S14b_B_classes.png | ok: join modal | ok: classes/mine | ok: enrollment [('active',)] | **PASS** |   |

## Teacher results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Shell: nav labels + icons, logo, no broken images | ok: screenshots/T01_teacher_shell.png | ok: nav rendered | n/a | n/a | **PASS** | My Lessons, Classes, Quizzes, Analytics, AI Tutor  |
| Classes: teaching grade + create class form persists (UI → API → DB) | ok: screenshots/T02_create_class_form.png | ok: created via form | ok: classes/mine has id=23 code=5LJL1TBX | ok: classes row (51, '7', '5LJL1TBX', 1) | **PASS** | class_id=23 join_code=5LJL1TBX  |
| Add Students tab adds A + C (UI → roster API → class_enrollments) | ok: screenshots/T03_add_students.png | ok: multi-select add | ok: roster=['qa_stu_a_124739', 'qa_stu_c_124739'] | ok: enrollments=[(52, 'active'), (54, 'active')] | **PASS** |   |
| Quizzes: PDF → MCQ generate → preview → publish (UI → API → DB) | ok: screenshots/T04_quiz_create.png, screenshots/T04b_quiz_preview.png, screenshots/T05_quizzes_list.png | ok: 5 MCQs generated + published | ok: quiz 51 status=published; list count 1 = UI | ok: assessments ('published', 51, 'quiz'); 5 questions | **PASS** |   |
| Assign quiz to class via UI → assignment published (API + DB) | ok: screenshots/T06_assign_quiz.png | ok: assign form | ok: assignment 29 listed | ok: assignments (29, 'published', 23, 51, 51) | **PASS** |   |
| Lessons: create (Use PDF as lesson, grade 7) + publish; list = my_lessons API = DB | ok: screenshots/T07_lesson_create.png, screenshots/T07b_lesson_view.png, screenshots/T07c_lessons_list.png | ok: create + publish + view | ok: my_lessons total 1 = UI rows | ok: lessons 16 (51, '7', 1, 'finalized') | **PASS** |   |
| Classes roster tab: A, B, C listed (UI = API = DB) | ok: screenshots/T08_roster.png | ok: Roster tab | ok: 3 students | ok: 3 active enrollments | **PASS** |   |
| Analytics Topic Progress: A's % = roster API = student progress API = DB; detail shows topics | ok: screenshots/T09_analytics_topic_progress.png | ok: row expand | ok: roster 63.64 = student progress 63.64; 7 topics = by-topic API | ok: weighted avg 63.64 | **PASS** | UI 64%  |
| Analytics Quiz Results collapsed ↔ expanded = analytics/quizzes API = assignment_submissions DB | ok: screenshots/T10_quiz_results_collapsed.png, screenshots/T10b_quiz_results_expanded.png | ok: collapse/expand | ok: A 60.0%; submissions API [{"attempt_id": 88, "score_percent": 60.0, "status": "submitted", "student_id": 52, "submitted_at": "2026-09-29T19:51:28 | ok: submissions [(52, 'submitted'), (54, 'not_started')] | **PASS** | UI row: 'Q\nqa_stu_a_124739\n\t3 / 5 (60%)\t3\t5\tAverage\tView Details'  |
| Analytics Struggling = struggling API; consistent with weak mastery in DB | ok: screenshots/T11_struggling.png | ok: tab | ok: 0 struggling [] | ok: weak counts {52: 1, 53: 1, 54: 0} | **PASS** |   |
| Analytics Roster: progress per student = API = DB (not all zeros); late-enrolled B included | ok: screenshots/T12_analytics_roster.png | ok: tab | ok: qa_stu_a_124739: UI 64% API 63.64 DB 63.64; qa_stu_b_124739: UI 81% API 81.25 DB 81.25; qa_stu_c_124739: UI 0% API 0.0 DB None | ok: weighted StudentTopicScore avg matches | **PASS** | late-enroll B: roster 81.25 = own progress 81.25 (diagnostic taken before join counts)  |
| Student report endpoint = student-facing scores (same ids) | n/a | n/a | ok: 2 attempts + 7 topics match student APIs | ok: topic scores match | **PASS** |   |
| AI Tutor: Teaching Assistant send → reply; history API + DB | ok: screenshots/T13_teacher_tutor.png, screenshots/T13b_teacher_chat_history_view.png | ok: send | ok: 2 history msgs | n/a | **PASS** | 'AI\n\nOne effective tip for teaching fractions is to use visual aids, such as fraction strips or pie c'  |

## Cross-role results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| S3b: viewing diagnostic results (read-only) must not change mastery / progress | ok: screenshots/X03_results_view_after_practice.png | ok: View Results | ok: progress 63.64 = 63.64 | ok: no StudentTopicScore change | **PASS** |   |
| S5: reload / re-login keeps submitted diagnostic + enrollment | ok: screenshots/X05_relogin.png | ok: fresh login, no gate, class present | n/a | ok: diagnostic_completed=1 | **PASS** |   |
| S6: role isolation on role-gated APIs + dashboards | n/a | n/a | ok: GET /api/lms/students/me/dashboard → 403; GET /api/lms/students/me/assignments → 403; POST /api/lms/quizzes/51/start → 403; POST /api/lms/classes/join → 403; POST /api/lms/classes → 403; GET /api/lms/classes/23/students → 403; GET /api/lms/classes/23/analy | n/a | **PASS** |   |

## Cross-cutting results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Mobile 390px: student + teacher main views, no horizontal overflow | ok: S:diagnostic:0px S:learning-path:0px S:classes:0px S:tutor:0px T:lessons:0px T:classes:0px T:quizzes:0px T:analytics:0px T:tutor:0px | n/a | n/a | n/a | **PASS** |   |
| No JS page errors; no 404 static assets; failed API calls listed | ok: 0 static 404, 0 page errors | n/a | n/a | n/a | **PASS** | API 4xx/5xx seen: []  |

## Failures / blocked (raw)

### FAIL: [Student] Retake rule: start again returns the finished attempt; UI shows results + explicit Retake only

```
AssertionError: Back link hidden on reopen after completing: {'view': 'diagnostic', 'needs': False, 'mandatory': False, 'allow': True, 'back': 'inline-flex', 'backRect': 0}
```

