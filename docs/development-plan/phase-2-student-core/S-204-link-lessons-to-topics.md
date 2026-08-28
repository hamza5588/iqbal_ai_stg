# S-204 — Link Lessons to Topics

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-001 |

## Description

Map lessons.focus_area to taxonomy topics.

## Objective

Enable learning path to reference lessons by topic.

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

- Backfill script matching focus_area strings to topic names
- Admin/teacher optional topic tags on lesson

## Files to Create

- `scripts/backfill_lesson_topics.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Lessons linked to at least one topic where focus_area matches

## Constraints

- Fuzzy match focus_area to topic name — log unmatched

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-204 Link lessons to topics.

Script backfill_lesson_topics.py: for each lesson with focus_area, find topic by similar name, insert lesson_topics.
Add optional topic_ids to lesson update API (teacher only).
```
