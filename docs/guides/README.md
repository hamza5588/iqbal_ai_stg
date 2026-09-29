# IQBAL AI LMS User Guide

End-to-end user guide with annotated UI screenshots for Admin, Teacher, and Student LMS features.

## Files

| File | Description |
|------|-------------|
| `IQBAL_AI_LMS_User_Guide.docx` | Full Word guide with arrows on screenshots |
| `IQBAL_AI_LMS_User_Guide.pdf` | PDF version |
| `IQBAL_AI_Analytics_How_It_Works.docx` | Simple English guide: diagnostic → score → learning path → teacher analytics (Playwright screenshots) |
| `assets/` | Raw + annotated screenshot images |
| `assets/analytics_guide/` | Screenshots for the analytics how-it-works guide |

## Covered Features

### Admin
- Upload & publish platform diagnostic (Q&A PDF + target content PDFs)
- View active diagnostic, add/remove target PDFs

### Teacher
- Class Analytics (Topic Performance, Quiz Results, Struggling Students, Roster)
- PDF Quiz Builder
- Assign Quiz to class
- Manage Classes
- AI Tutor

### Student
- Diagnostic Assessment (timed MCQs, Back/Next navigation)
- My Quizzes (teacher assignments)
- Learning Chat (post-diagnostic weak topics)
- Join Class

## Regenerate

```bash
cd iqbal_ai_stg
python scripts/generate_lms_user_guide.py
python scripts/generate_analytics_guide.py
```

Optional environment variables:

```bash
set GUIDE_BASE_URL=https://iqbalai.com
set QA_TP_TEACHER=qa.teacher.e2e.1789501674@test.local
set QA_TP_STUDENT=qa.student.diag.1789501674@test.local
set QA_TP_PASS=E2eQaTp2026!
```

Copies are also placed on Desktop: `IQBAL_AI_LMS_User_Guide.docx` / `.pdf` and `IQBAL_AI_Analytics_How_It_Works.docx`
