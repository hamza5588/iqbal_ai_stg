# A-309 — Mastery Calculation

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-308 |

## Description

Compute mastery status labels.

## Objective

Progress tracking.

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

- mastered/improving/needs_practice/weak

## Files to Create

- `app/services/lms/performance_service.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Correct status for sample scores

## Constraints



---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-309 mastery status rules in performance_service.compute_mastery_status(score, previous_score optional).
```
