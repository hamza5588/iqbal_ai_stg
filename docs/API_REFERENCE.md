# LMS API Reference (I-1009)

Base URL: `/api/lms`

## Health
- `GET /health` — service status

## Curriculum
- `GET /topics?subject=Math&grade_level=`
- `GET /topics/<id>/prerequisites`

## Question Bank
- `GET /questions?topic_id=&difficulty=`
- `POST /questions` — create (teacher)
- `GET|PUT|DELETE /questions/<id>`

## Classes
- `GET|POST /classes`
- `PUT|DELETE /classes/<id>` — update / archive
- `POST /classes/join` — student join (grade must match class)
- `GET /classes/grade-options` — grade dropdown values
- `GET /users/me/grade-profile` — student grade or teacher teaching grades
- `PUT /teachers/me/grades` — teacher sets grades they teach (e.g. `8` or `8,9`)
- `PUT /admin/users/<id>/grade` — admin assigns student grade or teacher grades
- `GET /classes/<id>/eligible-students` — students matching class grade not enrolled
- `POST /classes/<id>/students` — teacher adds student by ID
- `DELETE /classes/<id>/students/<student_id>` — remove from class
- `GET /classes/mine`
- `GET /classes/<id>/students`
- `GET /classes/<id>/roster` — roster + summary metrics
- `GET /classes/<id>/analytics/topics`
- `GET /classes/<id>/analytics/quizzes`
- `GET /classes/<id>/analytics/struggling`
- `GET /classes/<id>/export.csv`
- `GET /classes/<id>/students/<sid>/report`
- `GET /classes/<id>/assignments/<aid>/submissions`

## Quizzes & PDF Pipeline
- `GET|POST /quizzes`
- `GET /quizzes/<id>`
- `PUT /quizzes/<id>/questions`
- `POST /quizzes/<id>/publish`
- `POST /quizzes/from-pdf`
- `GET /quizzes/<id>/pdf-status`
- `GET /quizzes/<id>/preview`
- `POST /quizzes/<id>/questions/<qid>/regenerate`
- `POST /quizzes/<id>/start` — student attempt
- `GET /diagnostics/default` — platform diagnostic for students
- `POST /diagnostics/from-pdf` — upload content PDF, RAG ingest only (returns `assessment_id`, `thread_id`)
- `GET /diagnostics/pdf/<thread_id>/topics` — list PDF section headings for topic selection
- `POST /diagnostics/<assessment_id>/generate` — body `{ topics: [{ topic, page?, question_count }] }`
- `GET /diagnostics/<assessment_id>/preview` — teacher preview with answers
- `GET /diagnostics/<assessment_id>/status` — processing status
- `POST /diagnostics/<assessment_id>/publish` — publish diagnostic

## Attempts
- `GET /attempts/<id>/questions`
- `POST /attempts/<id>/answer`
- `POST /attempts/<id>/submit`
- `GET /attempts/<id>/results`
- `GET /students/me/attempts` — history

## Assignments
- `GET|POST /assignments`
- `POST /assignments/<id>/publish`
- `GET /students/me/assignments`

## Student
- `GET /students/me/onboarding-status`
- `GET /students/me/dashboard`
- `GET /students/me/progress`
- `GET /students/me/progress/history`
- `GET|PUT|POST /students/me/learning-path`

## Tutor (Phases 5 & 9)
- `POST /tutor/chat` — student Socratic tutor
- `POST /teacher/tutor` — teacher assistant
- `POST /teacher/tutor/save-question` — save AI question to bank

## Guided Practice (Phase 5)
- `POST /practice/sessions`
- `GET /practice/sessions/<id>`
- `POST /practice/sessions/<id>/answer`
- `POST /practice/sessions/<id>/hint`

## Interventions (Phase 8)
- `GET /interventions?class_id=&student_id=&topic_id=`
- `POST /interventions/auto-assign`

## Analytics (Phase 7)
- `GET /teacher/analytics/pdf-sources`

## Lessons
- `GET|PUT /lessons/<id>/topics`
