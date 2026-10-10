# Teacher UI E2E — 27/27 passed

Base URL: https://iqbalai.com · run 113311

| Section | Item | Status | Evidence |
|---|---|---|---|
| Auth/shell | Teacher login lands on new teacher dashboard | PASS | https://iqbalai.com/teacher-dashboard#lessons |
| Auth/shell | Top nav shows Lessons, Classes, Quizzes, Analytics, AI Tutor with correct icons | PASS | My Lessons, Classes, Quizzes, Analytics, AI Tutor |
| Auth/shell | Logo/branding matches new UI | PASS | /static/teacher/icons/iqbal-ai-logo.png |
| Auth/shell | Non-teacher cannot access teacher routes | PASS | legacy route removed (404); student /teacher-dashboard -> 302 /; anonymous -> 401 ; student /api/lms/classes/mine -> 200 |
| Auth/shell | Deep link #analytics opens that view | PASS | https://iqbalai.com/teacher-dashboard#analytics |
| Classes | Create class form works and persists | PASS | class #16 code 18HRSUGS (persists after reload) |
| Classes | Add students (eligible list, multi-select + single add) | PASS | added a+b (multi-select) and c (row Add); API roster = e2e_student_a, e2e_student_b, e2e_student_c |
| Classes | Roster displays enrolled students; remove + join-code flow | PASS | 3 rows → removed C → 2 rows → C joined via code → 3 rows |
| Quizzes | Quiz upload/create path (PDF → MCQ generate → preview → publish) | PASS | 5 MCQs generated; Status: completed / Confidence: 93% / Questions: 5 / 5 requested |
| Quizzes | Quizzes list loads (tabs, expand shows MCQs) | PASS | 2 quizzes; 'E2E PDF Quiz 113311' expanded with 5 MCQs |
| Quizzes | Assign quiz to class works end-to-end | PASS | assignment #23 visible to student A |
| Quizzes | Students submit the assigned quiz (data for analytics) | PASS | qa.student.a.ui.1790965865: 0/5.0; qa.student.b.ui.1790965865: 5/5.0; tagged quiz e2e.student.b: 4/4.0; student C not submitted |
| Analytics | Topic progress renders real data | PASS | 3 students; A overall 0% (API 0.0), latest 0%, 0 topic rows; B 100% with 2 topic rows + chart |
| Analytics | Quiz results collapsed ↔ expanded states work | PASS | collapsed (2 / 3 submitted) → expanded (3 student rows) → collapsed |
| Analytics | Struggling students view loads | PASS | 0 struggling (matches API) |
| Analytics | Analytics roster progress matches API (no all-zeros when data exists) | PASS | UI progress == API for all 3; on-track/help/not-attempted = 2/0/1 |
| Lessons | Create lesson (Use PDF as lesson) → appears in list | PASS | lesson #54 |
| Lessons | List lessons loads from API (search, tabs, filters, pagination) | PASS | 1 lessons; search + subject filter OK |
| Lessons | View / edit / publish / Word / PowerPoint / FAQ / delete flows | PASS | view, edit+save, Unpublish→Publish, word:E2E_Lesson_AsIs_113311_edited.docx, ppt:E2E_Lesson_AsIs_113311_edited.pptx, faq, delete |
| Lessons | Create lesson (Generate from PDF) → opens lesson chat in AI Tutor | PASS | switched to AI Tutor with the new PDF conversation |
| AI Tutor | Lesson chat: send message → response or graceful error | PASS | assistant: 'The document contains a Mathematics Diagnostic Baseline Assessment designed for Grade IX students, focusing on various mathematical concepts such as probability, geometry, and statistics. It includes ' |
| AI Tutor | Chat history dropdown lists and re-opens conversations | PASS | 2 conversations listed; 'E2E Lesson Gen 113311' re-opened with 3 messages |
| AI Tutor | Teaching Assistant: history load + send → reply or graceful error | PASS | reply: 'AI  One effective tip for teaching fractions is to use visual aids, such as fraction circles or bars. These tools help students see the relationship between dif'; persisted history messages: 2 |
| AI Tutor | Set Prompt modal opens from AI Tutor | PASS | opened + closed |
| Cross-cutting | Mobile width: no horizontal overflow, nav usable | PASS | lessons:0px classes:0px quizzes:0px analytics:0px tutor:0px |
| Cross-cutting | No console-breaking JS errors on primary paths | PASS | 0 errors |
| Cross-cutting | No missing static assets (icons/css/js 404s) | PASS | 0 static failures (0 non-static 4xx/5xx logged) |
