# A-329 — Conversion Orchestrator

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | A-326, A-328b, F-002 |

## Description

Celery pipeline: PDF upload → ingest → extract → convert → save DB.

## Objective

End-to-end PDF to quiz questions in database.

## Project Context

- **Application:** IqbalAI (`iqbal_ai_stg`) — Flask monolith, PostgreSQL, Redis/Celery, RAG (Milvus/Chroma), LangChain/LangGraph
- **Entry point:** `run.py` → `app/__init__.py`
- **ORM models:** `app/models/database_models.py`
- **Domain accessors:** `app/models/models.py`
- **Config:** `app/config.py`, `.env`
- **RBAC:** `app/rbac/permissions.py`, `app/rbac/decorators.py`
- **Migrations:** No Alembic — use SQL scripts in `docs/development-plan/migrations/` and update `app/utils/db.py` `init_db()`
- **Existing patterns:** Follow `app/services/lesson/models.py` for Pydantic; follow `app/routes/lesson_routes.py` for API blueprints
- **Do NOT** duplicate existing auth, lesson CMS, or RAG ingest — extend and reuse

## Requirements

- Celery pipeline: PDF upload → ingest → extract → convert → save DB.
- End-to-end PDF to quiz questions in database.

## Files to Create

- `app/services/quiz/pipeline.py`
- `app/tasks/quiz_pdf_tasks.py`

## Files to Modify

- `app/celery_app.py`

## Acceptance Criteria

- [ ] Pipeline saves questions to question bank and links assessment
- [ ] Status updates on QuizPdfSource

## Constraints

- Reuse existing Celery ingest where possible

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-329 PDF→MCQ orchestrator pipeline.

pipeline.py:
- run_pdf_quiz_pipeline(assessment_id, rag_thread_id, pdf_text)
- Steps: extract -> pair -> convert each -> save via question_bank_service -> add to assessment
- Update QuizPdfSource extraction_status and overall_confidence

Celery task quiz_pdf_tasks.process_pdf_quiz.delay(assessment_id, thread_id)
Hook after RAG ingest completes or accept pre-extracted text from rag chunks.
```
