# A-305 — Platform Default Diagnostic

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Database |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | A-302 |

## Description

Seed default onboarding diagnostic.

## Objective

Baseline for all students.

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

- Seed script or admin-created diagnostic quiz

## Files to Create

- `scripts/seed_default_diagnostic.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Default diagnostic exists for Math

## Constraints

- Can use manual questions initially

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-305: seed_default_diagnostic.py creating a published diagnostic assessment with sample MCQs for Math topics.
```
