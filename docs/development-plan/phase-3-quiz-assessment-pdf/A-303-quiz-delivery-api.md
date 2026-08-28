# A-303 — Quiz Delivery API

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-004, A-302 |

## Description

Deliver MCQs to students without leaking answers.

## Objective

Secure quiz taking.

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

- GET questions without correct_index
- Start attempt session

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- `app/services/lms/attempt_service.py`

## Acceptance Criteria

- [ ] Student receives questions without answers
- [ ] Attempt tracked

## Constraints

- Never expose correct_option_index in delivery API

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-303 Quiz delivery: POST /quizzes/<id>/start, GET /attempts/<id>/questions (strip answers), POST /attempts/<id>/answer. Student only.
```
