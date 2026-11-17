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

## Adaptive Learning Chat

Diagnostic assessments and subsequent quizzes now share metadata-based practice
selection. Each imported question stores its domain, specific topic, cognitive
level, and difficulty. Printed metadata takes precedence; missing metadata is
classified by the configured AI provider. “Intermediate” is stored as “medium”.

The existing score rule still determines quantity: one question per missing 10%
of the topic score, up to 10, with the existing outstanding-miss coverage up to
20. A score of 33.3% therefore assigns seven questions, not a fixed five.

Practice first reserves exact matches from the linked target PDFs for the
student's missed cognitive and difficulty levels. It then uses the nearest
available questions in the same topic/domain, and only generates questions for
remaining slots. Questions from a different domain or another teacher's unlinked
PDF are excluded. Questions and generated answers must pass the existing answer
verification gate. If enough verified questions cannot be obtained, the student
is asked to retry instead of being shown an incomplete practice set.

A new quiz replaces evidence for the topics it assessed and invalidates old
practice sessions. Weaknesses in unassessed topics remain until cleared. The
student continues to open Learning Chat from the existing learning path.

Combined question-bank PDFs are indexed by variant, keeping each variant's
answer key separate. Unreadable math uses page-image recovery. Structurally
invalid recovered items are retained with metadata but made inactive; correct
the source PDF and reupload before using those items. Existing imports without
metadata continue using their legacy flow until reimported.

Developer verification (disposable SQLite database only):

```powershell
python -m pytest tests/test_adaptive_metadata.py -q -p no:cacheprovider
python scripts/verify_adaptive_pdf.py --bank "../DIL SAATHI _ Question Bank _ Unit 1 to 4.pdf" --diagnostic "../DIL_SAATHI_Baseline_Diagnostic_Mathematics IX (1).pdf"
```

The second command uses the existing Groq credentials for extraction,
classification, fallback generation, and independent answer verification. It
writes its report to `.local/adaptive-pdf-report.json` and never opens the
application database.

## Regenerating the illustrated guides

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
