# A-310 — Student Progress API

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-309 |

## Description

API for student mastery overview.

## Objective

Dashboard data.

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

- GET /api/lms/students/me/progress

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Returns all topic scores and overall percent

## Constraints



---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-310 GET /api/lms/students/me/progress returning topic mastery list and overall progress percentage.
```
