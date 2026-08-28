# A-301 — Question Bank CRUD API

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-002, F-010 |

## Description

REST API for manual question management.

## Objective

Expose question_bank_service via /api/lms/questions

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

- CRUD endpoints with RBAC MANAGE_QUESTION_BANK
- Pagination and topic filter

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- `app/services/lms/question_bank_service.py`

## Acceptance Criteria

- [ ] Teachers can create/edit/delete questions via API

## Constraints

- Validate MCQ on create

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-301 Question Bank CRUD API in lms_routes.py: GET/POST /questions, GET/PUT/DELETE /questions/<id>. Teacher+admin only. Pagination, filter by topic_id.
```
