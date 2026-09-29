# Teacher Dashboard — New UI Integration Map

Branch: `ui_integration_for_teacher` (from `dil_feedback_improment`).
Design source: `updated_new_ui/Teacher Dashbaord file/` (12 static HTML mockups, no JS; all 12 share one byte-identical `<style>` block) + `teacher_dashboard_view/*.jpeg` (visual truth) + `updated_new_ui/icons/`.

## 1. Current system (source of truth for behavior)

### Serving
- `GET /teacher-dashboard` → `app/routes/chat.py:teacher_dashboard()` (`@login_required @teacher_required`) → `render_template('teacher_dashboard.html')`.
- Role landing: `app/utils/routes.py:get_default_route_by_role` → teacher → `/teacher-dashboard`. `/student-dashboard` redirects teachers here.
- `/teacher-static/<path>` served from `app/__init__.py:342` (legacy asset route).

### `templates/teacher_dashboard.html` (13,864 lines)
| Lines | Content |
|---|---|
| 1–250 | Head: Tailwind CDN + inline tailwind config, FontAwesome, marked, DOMPurify, KaTeX, hljs, pdf.js, MathJax, Chart.js, `teacher/js/{chat-response-formatter,markdown-parser,backend-formatter,markdown-examples}.js`, `js/in-app-confirm.js`, `css/ui-scale.css` |
| 255–3740 | ~3.5k lines inline CSS (`tt-*` shell, `mll-*` lessons list, chat, modals) |
| 3782–4228 | Shell markup: header, toolbar tabs, LMS action bar, `#chatArea/#chatMessages`, `#promptArea`, `#chatInputArea`, floating menu, chat context menu |
| 4229–4682 | Modals: `#setPromptModal`, `#saveTemplateModal`, `#testPromptModal`, `#viewLessonModal` (+ conv/lesson panels), `#expandedLessonPreviewModal`, `#expandedConversationSummaryModal` |
| 4683–12808 | **One ~8.1k-line inline script** (lessons, RAG chat, prompts, TTS/STT, history) — `#createLessonModal` is built in-script |
| 12856–13247 | LMS modals: `#ragPromptModal`, `#lmsClassHubModal`, `#lmsAssignModal`, `#lmsDiagnosticHubModal`, `#lmsQuizHubModal` |
| 13249–13860 | LMS inline script + `lms/lms-core.js`, `lms/lms-panels.js`, `lms/lms-teacher-class.js`, `lms/lms-ui.css` |

### Feature → entry point → API

**Shell / auth**
- User info: `loadTeacherUserInfo()` → `GET /user_info`; per-teacher localStorage isolation `ensureTeacherLocalStorageIsolation(user)`, `_teacherScopedStorageKey()`.
- Logout: `logout()` → `/auth/logout`. Avatar → `openSettings()`.
- Toasts `#toastContainer`, loading overlay `#loading-overlay`, `in-app-confirm.js`.

**Lessons**
- List: `showMyLessonsPage()` → `GET /api/lessons/my_lessons?page&per_page&q` → `{lessons[], total, total_pages, page}`; lesson fields: `id,title,focus_area,grade_level,created_at,version_number,teacher_name,is_public,has_child_version,status`.
- Row actions: `viewLesson(id,ver)` (`/api/lessons/lesson/:id/view`), `editLesson`/`saveLessonEdit`, `downloadLessonDocx` (`/api/lessons/download_lesson/`), `downloadLessonPPT` (`/api/lessons/download_lesson_ppt/`), `showLessonFAQ` (`/api/lessons/faqs/`), `toggleLessonPublication`, `deleteLesson` (hidden when `has_child_version`), version dropdown `toggleVersionDropdown/selectVersionFromDropdown`, pagination.
- Create: `showCreateLessonWizard()` → step1 validate (title, grade required; subject; context; mode *Generate from PDF* / *Use PDF as lesson*) → step2 `POST /api/rag/ingest` + poll `/api/rag/ingest/status/:task` + cancel `/api/rag/ingest/cancel/:task` → `finalizeCreateLesson` (`/api/lessons/create` or `/api/lessons/create_from_uploaded_document`).
- View-lesson modal: draft editor, `save_draft`, `get_draft`, `apply_prompt`, `finalize_version`, source-PDF render (pdf.js), conversation summary (`/api/conversations/:id/summary/regenerate`), expand previews, `openLessonSourceChat()`.

**RAG / lesson chat (main workspace)**
- `sendMessage()` → `POST /api/rag/chat` + progress poll `/api/rag/chat-progress/`; `/api/rag/thread/`.
- History dropdown `#chatHistoryDropdown` (Today / Yesterday / 7 days) ← `GET /get_conversations`; `loadChat` → `/get_messages/:id`; rename `/update_conversation_title/`, duplicate `/duplicate_conversation/`, delete `/delete_conversation/`.
- Save lesson from chat `saveLessonFromChat()` + save-flow guidance, download chat, reset chat, TTS `/api/tts`, STT `/api/stt`, voice input.
- Set Prompt (`showRAGPromptModal`): `GET/POST/DELETE /api/rag/prompt`, `/api/rag/prompt/preview`, templates (localStorage), test prompt.

**Classes** (`#lmsClassHubModal` + `lms-teacher-class.js`)
- List `GET /api/lms/classes/mine` → `{id,name,description,grade_level,join_code,student_count}`.
- Create `POST /api/lms/classes {name, grade_level, description?}`; grade select from `GET /api/lms/classes/grade-options`, restricted by `GET /api/lms/users/me/grade-profile` (`teaching_grades`); update teaching grades `PUT /api/lms/teachers/me/grades {grades[]}`.
- Detail (`lmsOpenClassDetail`): roster `GET /api/lms/classes/:id/students` (`student_id,username,email,grade_label,overall_progress,is_struggling,weak_topics…`), eligible `GET /api/lms/classes/:id/eligible-students`, add `POST …/students {student_id}`, remove `DELETE …/students/:sid`.

**Quizzes** (`#lmsQuizHubModal`)
- Create from PDF `POST /api/lms/quizzes/from-pdf` (title, file, mcq_count) → poll status → `GET /api/lms/quizzes/:id/preview` → `POST /api/lms/quizzes/:id/publish`.
- List `GET /api/lms/quizzes` → `{id,title,status}` only.
- Assign (`#lmsAssignModal`): `POST /api/lms/assignments {title,class_id,quiz_id,due_date}` → `POST /api/lms/assignments/:id/publish`.

**Analytics** (`openLmsTeacherAnalytics` in `lms-panels.js`) — tabs:
- Topic Performance — **hidden by product decision (d10856c)**; `…/analytics/topics`.
- Topic Progress (default) — student select → `GET /api/lms/classes/:id/students/:sid/progress/by-topic` → `{topics:[{topic_name, series:[{label,assessment_type,score_percent,correct,total}]}]}` → Chart.js bar chart + stats.
- Quiz Results — `GET …/analytics/quizzes` → `[{title, avg_score_percent, student_results:[{username,status,score,max_score,score_percent}]}]` (Completion column dropped — 3e2b345).
- Struggling — `GET …/analytics/struggling` → roster rows with `weak_topics[{topic_name,score_percent}]` (Progress column hidden — de29322).
- Roster — `GET …/students` → pie (on track / needs help) + table.
- Also available: `GET …/students/:sid/report` (`topics`, `recent_attempts`), `…/export.csv`.

**AI Tutor (LMS Teaching Assistant)** — `openLmsTutorPanel('teacher')` modal: history `GET/DELETE /api/lms/tutor/history?mode=teacher`, send `POST /api/lms/teacher/tutor {message}` → `{reply}`. Single thread, no conversation list.

**Dormant**: `#lmsDiagnosticHubModal` (PDF diagnostic builder) has no button in the teacher UI (diagnostics are admin-created per `/api/lms/quizzes` POST guard).

## 2. New UI screens → backend

| # | Screen | Maps to | Gaps (backend truth wins) |
|---|---|---|---|
| 01 | Lessons: toolbar (search, Create, subject/grade filters), All/Published/Drafts tabs, inline Create card, lesson rows w/ actions, pagination | Lessons list + create wizard + row actions | Subject/grade filters & counts: API has only `q` + pagination → client-side filter on current page; Published = `is_public`. Grid/list toggle + Sort: no backend (sort client-side only). |
| 02 | Classes → Add Students tab | class detail eligible + add | Checkbox multi-add → loop `POST …/students`. "Published/Drafts", "v1" chip, subject, class icon: no backend. |
| 03 | Create Class form | `POST /api/lms/classes` | Design has Subject* and separate "Your Teaching Grade*"; backend has `name, grade_level, description`. Teaching grade → existing `PUT teachers/me/grades`. |
| 04 | Classes → Roster tab | roster + remove | ✓ |
| 05 | Quizzes: create card, quiz list w/ expandable MCQ preview, assign card | quiz-from-pdf flow, list, preview, publish, assign | List has only title/status — class/subject/MCQ count/date per quiz not available (MCQ count via preview on expand). |
| 06 | Assign Quiz | assignments create+publish | ✓ |
| 07 | Analytics → Topic Progress: student rows, expand → topic bars + score-over-time | roster + `progress/by-topic` | "Latest score" derived from by-topic series on expand. |
| 08/09 | Quiz Results collapsed/expanded | `analytics/quizzes` | "Correct answers" = `score`, "Total" = `max_score`. |
| 10 | Struggling Students, expand → weak topics + recent performance | `analytics/struggling` (+ `report` for recent attempts) | Progress column hidden per de29322. |
| 11 | Roster: donut + stat tiles + table | `…/students` | "Not attempted" not in API → derive `overall_progress == null/0`. |
| 12 | AI Tutor: chat, Chat History dropdown, suggestion chips, attach/mic | **RAG chat workspace** (history dropdown = `/get_conversations`) | See open decision. |

Shared chrome: header with logo, 5-item icon nav (My Lessons, Classes, Quizzes, Analytics, AI Tutor), bell (no notifications backend), avatar (settings/logout).

## 3. Target architecture
- One authenticated entry `/teacher-dashboard` (SPA-style section switching — preserves all existing global JS functions/DOM IDs with least breakage). Hash routing `#lessons|classes|quizzes|analytics|tutor`.
- `templates/teacher/base.html` + `partials/{topbar,mainnav}.html` + per-screen `templates/teacher/<screen>/*.html` included into the shell.
- `static/teacher/css/teacher-dashboard.css` (design CSS) ; `static/teacher/icons/*` ; logic moved out of the monolith into `static/teacher/js/*.js`.

## 4. Status — integrated, verified, legacy removed

Product decisions applied: AI Tutor tab = RAG lesson chat + "Teaching Assistant" mode (LMS tutor, inline); design elements without backend support are hidden (class subject, class Published/Drafts, class "v1" chip, notification bell, grid/list toggle, per-quiz class/subject/date); Topic Performance tab and Struggling-Students Progress column stay hidden.

### What changed
- `/teacher-dashboard` (`app/routes/chat.py`) renders `templates/teacher/dashboard.html`: one shell (`base.html`, `partials/topbar.html`, `partials/mainnav.html`) with five hash-routed views (`#lessons #classes #quizzes #analytics #tutor`), each in its own folder (`lessons/`, `classes/`, `quizzes/`, `analytics/`, `ai_tutor/`).
- Legacy logic moved out of the monolith verbatim: `static/teacher/js/teacher-core.js` (lessons, RAG chat, prompts, TTS/STT) and `teacher-lms-hubs.js` (quiz generate/publish, assign). Jinja values reach them via `window.TEACHER_CFG` (set in `base.html`). Legacy CSS → `static/teacher/css/teacher-legacy.css`; the new design → `teacher-dashboard.css` (all classes `td-`).
- New modules: `teacher-shell.js` (views, avatar menu, inline create-lesson glue, tutor mode), `teacher-classes.js`, `teacher-quizzes.js`, `teacher-analytics.js` — same endpoints as the legacy modals.
- Icons: `static/teacher/icons/*` (trimmed/resized from `updated_new_ui/icons`, ~20 KB each).
- Removed: `templates/teacher_dashboard.html`, `static/lms/lms-teacher-class.js`. Static regression tests in `tests/` now read the new sources.
- Frontend fixes: `getChatMessagesKey()` was called but never defined (reset/delete chat could throw) — now defined with the per-teacher storage prefix.

### How to run the E2E suite (local SQLite only)
```bash
python _qa_audit_tmp/teacher_ui_e2e/seed_users.py            # throwaway teacher + 3 grade-8 students
python _qa_audit_tmp/teacher_ui_e2e/serve.py 5055            # run.py app, requests serialized (SQLite StaticPool)
BASE_URL=http://127.0.0.1:5055 python _qa_audit_tmp/teacher_ui_e2e/run_e2e.py
```
Uses installed Chrome (`CHROME_PATH` to override). Output: `_qa_audit_tmp/teacher_ui_e2e/report.md|json`, screenshots in `_qa_audit_tmp/teacher_ui_screenshots/`. Last run: **27/27 PASS** after legacy removal.

### Residual risks / notes
- **Quiz-from-PDF questions have no `topic_id`**, so they never move topic mastery or `overall_progress` (a 5/5 score still shows 0% progress). Existing backend behaviour; "Latest Score" and Quiz Results do reflect the score.
- "Not Attempted" (Roster / Topic Progress) is derived in the UI: no mastery data and no submitted quiz in the class. The backend itself counts those students as struggling.
- The teacher page is always the design's blue: `static/teacher/css/teacher-theme.css` remaps the admin platform-theme variables (green by default) for this page only, the teacher Tailwind config maps `green`/`emerald` utilities to blue, and the old green literals in `teacher-legacy.css` / `teacher-core.js` were recoloured. The admin theme setting therefore no longer changes the teacher dashboard colours (student/admin pages are unaffected). `_qa_audit_tmp/teacher_ui_e2e/capture_legacy.py` screenshots every legacy screen and reports any remaining green element.
- Success messages show twice (shared `lmsShowToast` also calls the page toast) — pre-existing, shared with the student dashboard.
- Teaching Assistant replies sometimes typeset words as math (e.g. "hands-on") — pre-existing shared `lms-panels.js` formatter.
- Dormant, not wired: PDF Diagnostic Builder modal (diagnostics are admin-only), `openSettings()` / `attachFile()` stubs. Pre-existing orphans left in place: `static/teacher/js/{auth,demo-data,theme-config,theme-switcher}.js`, `static/teacher/components/theme-switcher.html`, `static/teacher/css/chat-{enhanced,redesign}.css`, and the `/teacher-static/` route.
- Unrelated pre-existing test failures (identical on the base commit): 8 failures + 1 collection error in diagnostic / finalize-lesson / learning-path tests, and `test_lms_foundation` is order-dependent in the full run.

## 5. Must-keep feature checklist
Lessons: list/search/paginate, create (both modes, ingest progress, cancel), view modal (versions, draft edit, apply prompt, finalize, source PDF, summaries), edit, Word, PPT, FAQ, publish/unpublish, delete-guard. Chat: send, progress, history (load/rename/duplicate/delete), save lesson from chat, download, reset, TTS/STT, markdown/math/code rendering. Set Prompt + templates + test. Classes: create, teaching grades, join code, roster, add (eligible), remove. Quizzes: PDF→MCQ generate, preview, publish, list, assign. Analytics: 4 visible tabs + chart. LMS Teaching Assistant chat + clear history. Logout, per-teacher localStorage isolation.
