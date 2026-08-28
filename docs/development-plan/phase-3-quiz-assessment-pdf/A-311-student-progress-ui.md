# A-311 — Student Progress UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-310 |

## Description

Visual progress dashboard.

## Objective

Student tracks mastery.

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

- Progress section in student dashboard

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/student_dashboard/student_dashboard.html`

## Acceptance Criteria

- [ ] Shows mastery labels per topic

## Constraints



---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-311 Student progress UI section consuming A-310 API with color-coded mastery badges.
```
