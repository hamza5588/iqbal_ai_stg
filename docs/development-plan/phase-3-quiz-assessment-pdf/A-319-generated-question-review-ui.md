# A-319 — Generated Question Review UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend/Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-331 |

## Description

Generated Question Review UI

## Objective

Generated Question Review UI

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

- Implement A-319 Shared review UI for AI-generated diagnostic questions — reuse A-331 components.

## Files to Create

- (determine during implementation)

## Files to Modify

- `app/routes/lms_routes.py`

## Acceptance Criteria

- [ ] A-319 complete

## Constraints

- Follow existing patterns

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-319 Shared review UI for AI-generated diagnostic questions — reuse A-331 components.
```
