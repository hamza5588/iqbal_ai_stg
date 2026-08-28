# A-332 — Auto Quiz Creation

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-329, A-302 |

## Description

Auto-create quiz record from pipeline output.

## Objective

Bridge pipeline to assignment.

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

- POST finalize creates published-ready quiz

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- `app/services/quiz/pipeline.py`

## Acceptance Criteria

- [ ] Quiz created with all pipeline questions attached

## Constraints



---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-332: POST /api/lms/quizzes/from-pdf/<source_id>/finalize — attach all converted questions, set creation_mode=pdf_qa_auto.
```
