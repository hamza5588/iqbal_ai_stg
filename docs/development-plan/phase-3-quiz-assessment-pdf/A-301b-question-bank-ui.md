# A-301b — Question Bank UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend/Frontend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | A-301 |

## Description

Question Bank UI

## Objective

Question Bank UI

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

- Implement A-301b Manual Question Bank UI for teachers: table of questions, create/edit form with 4 options, topic selector.

## Files to Create

- (determine during implementation)

## Files to Modify

- `app/routes/lms_routes.py`

## Acceptance Criteria

- [ ] A-301b complete

## Constraints

- Follow existing patterns

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-301b Manual Question Bank UI for teachers: table of questions, create/edit form with 4 options, topic selector.
```
