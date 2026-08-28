# S-203 — Topic-Based Content Browse

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-001 |

## Description

Filter public lessons by taxonomy topic.

## Objective

Connect curriculum to existing lesson browse.

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

- lesson_topics join table
- API filter on browse_lessons by topic_id

## Files to Create

- `docs/development-plan/migrations/009_lesson_topics.sql`

## Files to Modify

- `app/routes/lesson_routes.py`

## Acceptance Criteria

- [ ] Lessons filterable by topic slug
- [ ] UI topic dropdown on student browse

## Constraints

- Optional if lesson_topics empty — show all

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-203 Topic-based lesson browse.

Create lesson_topics (lesson_id, topic_id) join table.
Extend browse_lessons API with optional topic_id or topic_slug query param.
Add topic filter dropdown to student lesson browse UI.
```
