# Student UI E2E — 25/25 PASS

Base: http://127.0.0.1:5057 · run 131215

| Section | Item | Status | Evidence |
|---|---|---|---|
| Auth/shell | Student login lands on new student dashboard | PASS | http://127.0.0.1:5057/student-dashboard#diagnostic-quiz |
| Auth/shell | Top nav shows Diagnostic, My Learning Path, My Classes, AI Tutor with correct icons | PASS | Diagnostic, My Learning Path, My Classes, AI Tutor |
| Auth/shell | Logo/branding matches new UI | PASS | IQBAL AI — Intelligent Learning Platform |
| Auth/shell | Teacher/admin cannot stay on student dashboard (redirects) | PASS | teacher→/teacher-dashboard; admin→/admin/ |
| Diagnostic | Diagnostic gate blocks gated features until complete (fresh student) | PASS | orientation forced; Learning Path / Classes / AI Tutor blocked |
| Diagnostic | Start diagnostic → orientation → quiz loads questions from API (timer visible) | PASS | 32 questions, timer 41:14 |
| Diagnostic | Math in questions/options renders (MathJax) | PASS | 1 typeset math elements |
| Diagnostic | Tools: Explain this question, Workspace, question map | PASS | explain: 'In simpler words: Read it slowly, one part at a time. The question is asking: Wh' |
| Diagnostic | Answer questions (saved to API), submit with unanswered confirm, topic results shown | PASS | score 9%, 7 topic rows, gate unlocked |
| Diagnostic | Diagnostic hub loads real stats + Taken row with inline results (API match) | PASS | weak=6, taken score 9% = API |
| Diagnostic | Completed diagnostic is not restarted (existing rule: results shown; retake only via explicit button) | PASS | start → already_completed; UI shows previous results + explicit Retake button |
| Diagnostic | Timeout auto-submit path (expired attempt is finalized and scored) | PASS | expired attempt auto-submitted, results + gate unlocked |
| Learning Path | Path overview loads after diagnostic; weak topics / progress match API | PASS | 7 topics (6 weak), progress '1 of 7 topics' = API |
| Learning Path | Learning Chat (deficiency) starts and accepts an answer | PASS | wrong → feedback + Next question |
| Learning Path | Tutor help (Need more help), advance, Pause & Exit still work | PASS | tutor reply "Level 1 – Prompt\n\nNo problem! Let's start with the first step.\n\nDo you know what"; advance=yes; paused back to path |
| Learning Path | Guided practice (per topic) opens with a question and hint | PASS | MEDIUM  The graph of  𝑥 = 3  is: a horizontal line a vertical line a line throug / hint: Hint:  Suggest a strategy without solving.  Question: The gr |
| My Classes | Join class via code; enrolled class loads with teacher + lessons (API match) | PASS | class 25 joined, 7 lessons |
| My Classes | Assigned quiz visible, taken end-to-end, Review matches API | PASS | 4 questions, score 0% = API |
| Lesson viewer | Open lesson: content renders; Word + PowerPoint download | PASS | 'Quadratic Equations 145157'; downloads ['Quadratic_Equations_145157.docx', 'Quadratic_Equations_145157.pptx'] |
| Lesson viewer | Lesson chat answers (or asks to use teacher PDF) and is saved as a conversation | PASS | reply 'Lesson Summary\n\nThis lesson introduces quadratic equations, detailing their definition, methods of s'; conversation saved |
| AI Tutor | Send message via /api/lms/tutor/chat and get a response | PASS | "First, let's break it down. You can think of 7 times 8 as adding 7 together 8 times.\n\nDoes that make" |
| AI Tutor | History restores on reload; Chat History lists lesson chat and opens it; clear works | PASS | restored, reopened lesson conversation, cleared (API empty) |
| Cross-cutting | Mobile (390px): every main view usable, no horizontal scroll | PASS | diagnostic, learning-path, classes, tutor |
| Cross-cutting | No JS errors (pageerror / console) on primary paths | PASS | 0 page errors, 0 unexpected console errors |
| Cross-cutting | No missing static assets (icons/css/js 404s) | PASS | 0 static 404s |
