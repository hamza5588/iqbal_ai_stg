# F-002b — Question Source Metadata

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-002 |

## Description

Track question origin: manual, pdf_qa_converted, pdf_ai, mixed.

## Objective

Enable audit trail and analytics by question source.

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

- Add source_type enum column to questions
- Add source_pdf_thread_id (nullable FK/string matching rag_threads.thread_id)
- Add source_question_number, extraction_confidence (nullable float)

## Files to Create

- `docs/development-plan/migrations/002b_question_source.sql`

## Files to Modify

- `app/models/database_models.py`
- `app/services/lms/question_bank_service.py`

## Acceptance Criteria

- [ ] Questions can be filtered by source_type
- [ ] PDF-derived questions link to rag thread id

## Constraints

- Backward compatible — existing questions default source_type='manual'

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-002b: Add question source metadata to the questions table.

Add columns:
- source_type: VARCHAR CHECK IN ('manual','pdf_qa_converted','pdf_ai','mixed') DEFAULT 'manual'
- source_pdf_thread_id: nullable string (rag thread)
- source_question_number: nullable int
- extraction_confidence: nullable float

Update Question model, migration SQL, and question_bank_service to accept/set these fields.
Update Pydantic schemas accordingly.
```
