# Student Dashboard — New UI Integration Map

Branch: `ui_integration_for_teacher` (baseline commit `6c3c3ea`).
Design source: `updated_new_ui/Student Dashboard files/` (7 static HTML mockups, no JS, one byte-identical `<style>` block) + `student_dashboard_view/*.jpeg` (visual truth) + `updated_new_ui/icons/`.

## 1. Current system (source of truth for behaviour)

### Serving
- `GET /student-dashboard` → `app/routes/chat.py:student_dashboard()` (`@login_required`; admin → `/admin/`, teacher → `/teacher-dashboard`) → `render_template('student_dashboard/student_dashboard.html', lms_onboarding=get_onboarding_status(uid))`.
- Role landing: `app/utils/routes.py:get_default_route_by_role` → student → `/student-dashboard`.
- `templates/student_dashboard (2).html` — unreferenced older copy (no route, no test, no include).

### `templates/student_dashboard/student_dashboard.html` (8,402 lines)
| Lines | Content |
|---|---|
| 1–250 | Head: Tailwind CDN + config, FontAwesome, in-app-confirm, marked, DOMPurify, KaTeX, hljs, `teacher/js/{chat-response-formatter,markdown-parser,backend-formatter,markdown-examples}.js` |
| 254–3349 | Inline CSS (`tt-*` toolbar, `mll-*` lessons, chat, `svc-*` view-lesson modal) |
| 3350–3393 | MathJax config (`typeset:false`), pdf.js, MathJax, Chart.js, `typesetMathIn` / `insertHtmlAndTypeset` |
| 3396–3569 | Shell: toasts, loading overlay, header (logo, avatar, logout), toolbar (Chat with Lesson / My Lessons / Chat History dropdown), `#chatArea/#chatMessages`, `#lessonsArea`, `#chatInputArea` (mic, speaker, textarea, send), chat context menu |
| 3571–3729 | `#viewLessonModal` (lesson content, Ask Question, DOCX, PPT), `#lessonQAModal` (unused 3-column Q&A modal) |
| 3731–7816 | Inline script: lessons list, lesson chat, chat history, voice/TTS, toasts, logout |
| 7818–8043 | `#lmsJoinClassModal`, `#lmsOnboardingModal`, onboarding gate, LMS overview (`#lmsOverviewSection`), learning path render, mark complete |
| 8045–8076 | `#lmsAttemptResultModal`, FAB bar (Ask Tutor / Practice / Diagnostic), `#lmsStudentModal` (assignments + quiz taking) |
| 8077–8400 | `lms-ui.css`, `lms-core.js`, `lms-student.js`, `lms-deficiency-chat.js`, `lms-panels.js` + inline assignment/quiz-taking script |

### Feature → entry point → API
**Shell** — avatar initial + username from session; `logout()` → `POST /auth/logout` (in-app confirm); `showToast`; `window.alert` and `console.error` routed to toasts; loading overlay.

**Onboarding gate** — `initLmsOnboardingGate()` → `GET /api/lms/students/me/onboarding-status` → if `!diagnostic_completed`: `setLmsDiagnosticGate(true)` (diagnostic modal cannot be closed, FAB hidden) + `openLmsDiagnostic()`. `lmsStudentNeedsDiagnostic()` blocks Join Class, My Quizzes, AI Tutor, Practice until submitted.

**LMS overview** — `loadLmsStudentDashboard()` → `GET /api/lms/students/me/dashboard` → `{onboarding, overall_progress, weak_topics[{topic_id,topic_name,score_percent,mastery_status}], mastery[], pending_assignments[], learning_path{items[{id,item_type,item_id,status,title,label}],current_step,completed_count,total_count,percent,weak_area_progress{cleared,total,weak_remaining,percent}}, learning_path_progress{completed,total,percent}, classes[{id,name,grade_level}]}`. Cards: Learning Path, Pending Quizzes, Weak Topics, My Classes (Topic Mastery + pie hidden, ecb0213). Learning path steps (`renderLmsLearningPathEnhanced`): Open Learning Chat / Start challenge / Start (`lmsLaunchPathStep`) / Mark done (`PUT /students/me/learning-path {item_id}`) / Practice again. Quiz History (`loadLmsAttemptHistory` → `GET /students/me/attempts`; submitted → result modal `GET /attempts/:id/results`; in-progress diagnostic → `resumeLmsDiagnostic`).

**Diagnostic** (`lms-student.js`, modal `#lmsDiagnosticModal`) — `GET /api/lms/diagnostics/default` → resume in-progress / show previous results / time-over / orientation → `POST /api/lms/quizzes/:id/start {retake}` → `GET /attempts/:id/questions` → answers `POST /attempts/:id/answer` (localStorage backup + retry queue) → timer (auto-submit at 0) → `POST /attempts/:id/submit {time_expired}` → results (score, N of M, topic table, weak/strong tiles) → Start Learning Chat / Start challenge / Retake with new questions / Continue. Tools: Explain this question (`POST /attempts/:id/questions/:qid/clarify`), Workspace (notes + canvas), Clear answer, Copy, A-/A+, question map.

**Learning Chat** (`lms-deficiency-chat.js`) — `POST /api/lms/deficiency/sessions {force_new, mode:practice|enrichment}` → answer `…/answer`, advance `…/advance`, pause `…/pause`, tutor `…/explain {message}` (auto-retry, reconnect resend), suggested prompts, Need more help, Copy, A-/A+, sound toggle, streak celebration.

**Guided practice** (`lms-panels.js`) — `POST /practice/sessions {topic_id}`, `GET …/:id`, `POST …/answer`, `POST …/hint`.

**Quizzes** — `GET /students/me/assignments` → `{assignment_id,title,quiz_id,due_date,status,submitted_at,score_percent,can_start}`; `POST /quizzes/:id/start {assignment_id}` → `GET /attempts/:id/questions` → answer → submit (unanswered confirm) → result + topic breakdown.

**Classes** — Join: `GET /users/me/grade-profile` hint, `POST /classes/join {join_code}`. List: dashboard `classes`.

**Lessons** — `GET /api/lessons/browse_lessons?page&per_page&q&topic_slug` (only lessons of teacher×grade links from active enrollments) + `GET /api/lms/topics?subject=Math` filter; row actions View (`GET /api/lessons/lesson/:id/view` → content or source PDF via pdf.js), Word (`/api/lessons/download_lesson/:id`), PowerPoint (`/api/lessons/download_lesson_ppt/:id`), Chat.

**Lesson chat** — one conversation per lesson (localStorage `Student_chat_lesson_bindings` chatId→lessonId): `POST /create_conversation`, title `Conversation with <lesson>` (`PUT /update_conversation_title/:id`), history `GET /get_conversations`, messages `GET /get_messages/:id`, persist `POST /save_message`, ask `POST /api/lessons/ask_question {lesson_id, question[, allow_rag]}` incl. `needs_rag_confirmation` Yes/No flow; rename / delete (`DELETE /delete_conversation/:id`); voice input (Web Speech) + read-aloud (speechSynthesis).

**AI Tutor** (`lms-panels.js`) — `GET/DELETE /api/lms/tutor/history?mode=student`, `POST /api/lms/tutor/chat {message}` → `{reply}`.

## 2. New UI screens → backend

| # | Screen | Maps to | Gaps (backend truth wins) |
|---|---|---|---|
| — | Top cards (01, 03) | dashboard | Pending Diagnostic = 1 if diagnostic not completed, else 0. Weak Topics = `weak_topics`. Learning Path Progress = `learning_path_progress`. |
| 01 | Diagnostic hub: search, subject/grade filters, All/Available/Taken, rows, inline result | `diagnostics/default` + diagnostic attempts from `students/me/attempts` + `attempts/:id/results` | One platform diagnostic per student, not a list of several. Rows = the current diagnostic + each submitted diagnostic attempt. Title is "Diagnostic Assessment" (admin titles are meaningless to students, same rule as attempt history). Filters run client-side. |
| 02 | Diagnostic quiz page | `lms-student.js` flow rendered inline (orientation, timer, tools, map, submit, results) | Design shows plain Prev/Next. Explain, Workspace, Clear, Copy, A-/A+ and the question map are kept. |
| 03 | My Learning Path: topic rows (Available/Completed + View Results) | `mastery` / `weak_topics` + learning-path steps + Quiz History | Design rows have teacher lesson actions (Edit/Delete/Unpublish…), so they are dropped. Each row = one assessed topic. Weak → Start Learning Path (Learning Chat) + Guided practice; cleared → View Results (score %, question count, status, last assessed). |
| 04 | Learning Path interactive (question + AI helper side by side) | Learning Chat (`deficiency/*`) | Tutor panel is always visible (was a toggle). The 💡 icon maps to Need more help. |
| 05 | My Classes: Join Class, class cards, Lessons + Quizzes columns, inline quiz result | `classes/mine`, `browse_lessons?class_id`, `students/me/assignments`, `attempts/:id/results` | **Additive API fields:** classes/mine (student) gets `teacher_name`, `joined_at`; assignments get `class_id`, `attempt_id`; `browse_lessons` accepts `class_id`. The class code is not exposed to students (API hides it), so it is not shown. Time Taken is not stored, so it is not shown. |
| 06 | Lesson viewer (content left, lesson chat right) | lesson view + lesson chat | 📎 has no backend, so the input bar uses 🎤 voice input + 🔊 read-aloud instead. Word/PPT buttons are kept in the header. |
| 07 | AI Tutor + Chat History popover + suggestion chips | LMS tutor (`tutor/*`) | Tutor is a single thread. The popover lists the tutor thread plus lesson conversations (`/get_conversations`), with open/rename/delete. 📎 is hidden (no backend). |

Shared chrome: logo, 4-item icon nav (Diagnostic, My Learning Path, My Classes, AI Tutor), bell hidden (no notifications backend, same as teacher UI), avatar menu (name, role, Logout).

## 3. Target architecture
- One entry `/student-dashboard` → `templates/student/dashboard.html` (extends `student/base.html`; `partials/{topbar,mainnav,stat_cards,modals}.html`). Hash views: `#diagnostic`, `#learning-path`, `#classes`, `#tutor`, plus full-page views `#diagnostic-quiz` (`diagnostic/quiz.html`), `#learning-chat` (`learning_path/practice.html`), `#lesson` (`lessons/viewer.html`).
- `static/student/css/student-dashboard.css` (`sd-` prefix), `static/student/icons/*`, `static/student/js/student-{shell,diagnostic,learning-path,classes,quiz,lessons,tutor}.js`.
- `lms-student.js` / `lms-deficiency-chat.js` (student-only) render into the inline views. `lms-core.js` / `lms-panels.js` are shared with the teacher dashboard and stay unchanged.
- Diagnostic gate: while `!diagnostic_completed`, the dashboard opens the diagnostic quiz view. Learning Path, My Classes and AI Tutor show the diagnostic prompt instead of their content, and Join Class / Practice / Tutor are blocked (same as the old modal gate). The Back link is hidden during a mandatory diagnostic.

## 4. Status — integrated, verified, legacy removed

### What changed
- `/student-dashboard` (`app/routes/chat.py`) renders `templates/student/dashboard.html`: one shell (`base.html`, `partials/{topbar,mainnav,stat_cards,modals}.html`) with hash-routed views in per-screen folders: `diagnostic/{index,quiz}.html`, `learning_path/{index,practice}.html`, `classes/index.html`, `lessons/viewer.html`, `ai_tutor/index.html`. Role redirects and `lms_onboarding` context are unchanged.
- New static: `static/student/css/student-dashboard.css` (all `sd-` classes; remaps the platform theme to the design blue and forces the light LMS palette on this page), `static/student/icons/*` (trimmed/resized from `updated_new_ui/icons`), `static/student/js/student-{shell,diagnostic,learning-path,classes,quiz,lessons,tutor}.js`.
- `lms-student.js` / `lms-deficiency-chat.js` (student-only) keep all their logic: timer, answer backup/retry, explain, workspace, results, retake, tutor retries. They now render into the page views (`[data-inline]`) with the new markup, via `sdOpen/Close*View` hooks. `lms-core.js` / `lms-panels.js` (shared with teachers) are unchanged; the tutor functions are overridden on the student page only.
- Quiz taking and the lesson list/view/chat code moved out of the monolith (same endpoints). Lesson formatting, PDF rendering and download helpers were moved verbatim.
- **Additive API fields only:** `GET /api/lms/classes/mine` (student) adds `teacher_name`, `joined_at` (`class_service.list_student_class_details`); `GET /api/lms/students/me/assignments` adds `class_id`, `attempt_id`; `GET /api/lessons/browse_lessons` accepts `class_id` (same teacher×grade access rule, narrowed to one enrolled class).
- Removed: `templates/student_dashboard/student_dashboard.html`, `templates/student_dashboard (2).html`, `static/images/iqbal-ai-icon-green.png` (only the old page used it), and the old overview's path renderer in `lms-student.js`. `tests/test_lecture_generation_fixes_static.py` now reads the new student sources; `scripts/load/_deploy_and_test.py` lists the new files.
- Functions of the old page that no button called (Lesson Q&A modal with teaching recommendations, math demo/help, share/email lesson, duplicate/download chat) were not ported.

### How to run the E2E suite (local SQLite only)
```bash
python _qa_audit_tmp/teacher_ui_e2e/seed_users.py                     # e2e teacher (once)
SKIP_EXTRA_STARTUP=false python _qa_audit_tmp/teacher_ui_e2e/serve.py 5056   # lesson Q&A graph needs the extra startup
BASE_URL=http://127.0.0.1:5056 python _qa_audit_tmp/student_ui_e2e/run_e2e.py
```
Fresh students are seeded per run (`seed_student.py`: A grade 7 real diagnostic, B grade 8 with the diagnostic marked done, C grade 7 forced timeout, plus a throwaway admin). The teacher creates a fresh class and quiz assignment through the API. Output goes to `_qa_audit_tmp/student_ui_e2e/report.md|json`, with screenshots in `_qa_audit_tmp/student_ui_screenshots/` (desktop + `mobile_*`). Last run: **25/25 PASS** after legacy removal. Full `pytest tests`: 581 passed; the 9 failures + 1 collection error are the pre-existing ones listed in the teacher handoff.

### Residual risks / notes
- **Retake:** the old UI offered "Retake with new questions" after results, and the backend allows it with `retake: true`. That behaviour is kept (starting without `retake` still returns the finished attempt). If the diagnostic must be strictly one-time, remove that button in `lms-student.js` / `student-diagnostic.js`.
- Diagnostics are grade-scoped. Locally only grade 7 has a published one, so grade-8 students see "No diagnostic available". Gate rule unchanged: students with an incomplete diagnostic are sent to it.
- Slow backend paths (same as the old UI): `GET /attempts/:id/results` for a diagnostic can take 10 s or more, and finalizing an expired diagnostic runs LLM topic grouping inside `GET /diagnostics/default` (about 50 s locally).
- Guided practice answers "No practice question available for this topic" for diagnostic-derived topics in the local DB. This is a backend/data limit, and the panel shows the message.
- The lesson ↔ conversation link is still stored in `localStorage` (`Student_chat_lesson_bindings`), so on another device a lesson chat opens read-only from Chat History.
- Hidden or replaced because the backend has no data for them: notification bell, 📎 attach, class code (the API hides it from students), quiz "Time Taken", diagnostic "Assigned on" (shown as questions + time limit). Learning-path rows are per topic, but "Start Learning Path" opens the single Learning Chat that covers all weak topics (no per-topic chat API).
- `console.error` is no longer turned into a toast (it only produced noise). `alert()` still becomes a toast.
- The shared `lms-core.js` formatter sometimes typesets hyphenated words in tutor replies as math. This happens on the teacher page too.
- The local `.env` sets `SKIP_EXTRA_STARTUP=true`, so lesson chat returns 500 locally unless the server runs with it off (both UIs).

## 5. Must-keep feature checklist
Diagnostic: orientation, resume in-progress, timer + auto-submit, answer backup/retry, explain, workspace, clear, copy, font size, question map, unanswered confirm, results (N of M, topic table, weak/strong), time-over, retake with new questions, gate. Learning Chat: start/resume, answer, wrong-answer feedback, next question, pause & exit, tutor (levels, prompts, need more help, retry/reconnect), copy, font, sound, completion, challenge mode. Guided practice + hint. Learning path steps + mark done + quiz history + attempt result modal. Classes: list, join (grade hint). Quizzes: list, start/continue, resume, answer, map, submit, results. Lessons: list per class, search, topic filter, view (markdown/math/source PDF), Word, PPT, chat (conversation per lesson, RAG confirmation, voice, read-aloud), history rename/delete. AI Tutor: history restore, send, clear. Logout, toasts.
