# F-009 — Db Migrations

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-001, F-008 |

## Description

Consolidate and wire all LMS migrations into init_db.

## Objective

Reliable schema deployment without Alembic.

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

- Single orchestrator script or init_db hook to run migrations in order
- Idempotent CREATE TABLE IF NOT EXISTS
- Dev seed: topics + optional sample questions

## Files to Create

- `scripts/run_lms_migrations.py`
- `docs/development-plan/migrations/README.md`

## Files to Modify

- `app/utils/db.py`

## Acceptance Criteria

- [ ] Fresh DB gets all LMS tables
- [ ] Re-running migrations is safe

## Constraints

- Do not drop existing tables

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-009: Consolidate LMS migrations.

1. Ensure all migration SQL files in docs/development-plan/migrations/ are ordered 001-008
2. Create scripts/run_lms_migrations.py to execute SQL files against DATABASE_URL
3. Update init_db() to call LMS migration runner after existing table creation
4. Write migrations/README.md documenting process

Test on SQLite dev DB. Must be idempotent.
```
