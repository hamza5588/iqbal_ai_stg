# A-304 — Submission & Scoring Engine

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-005, A-303 |

## Description

Score attempts and trigger mastery update.

## Objective

Complete assessment loop.

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

- POST submit computes score
- Calls performance_service.update_topic_scores_from_attempt

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- `app/services/lms/attempt_service.py`
- `app/services/lms/performance_service.py`

## Acceptance Criteria

- [ ] Score calculated correctly
- [ ] Topic scores updated

## Constraints

- Idempotent submit

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-304: POST /attempts/<id>/submit — grade answers, set score, call performance_service, return results with topic breakdown.
```
