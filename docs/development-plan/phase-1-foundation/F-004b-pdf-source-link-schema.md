# F-004b — Pdf Source Link Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-004 |

## Description

Link quizzes to PDF/RAG source and store extraction metadata.

## Objective

Traceability for PDF→MCQ pipeline.

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

- quiz_pdf_sources table: assessment_id, rag_thread_id, original_filename, extraction_status, overall_confidence
- pdf_qa_extractions table: raw extraction JSON, paired count, warnings

## Files to Create

- `docs/development-plan/migrations/004b_pdf_sources.sql`

## Files to Modify

- `app/models/database_models.py`
- `app/services/lms/assessment_service.py`

## Acceptance Criteria

- [ ] Assessment can be linked to RAG thread after PDF upload
- [ ] Extraction metadata persisted for audit

## Constraints

- Reuse rag_threads.thread_id as FK reference

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-004b PDF source link schema.

Create models:
- QuizPdfSource: assessment_id, rag_thread_id, original_filename, extraction_status (pending|processing|completed|failed), overall_confidence, error_message
- PdfQaExtraction: id, quiz_pdf_source_id, raw_extraction_json (Text), pair_count, warnings_json, created_at

Wire into assessment_service: link_pdf_source(assessment_id, thread_id, filename)
Migration 004b_pdf_sources.sql
```
