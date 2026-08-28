# A-302 — Quiz Builder API

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | F-004, A-301 |

## Description

API to create quizzes from question IDs.

## Objective

Build and publish assessments.

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

- POST /api/lms/quizzes
- Add/remove/reorder questions
- Publish endpoint

## Files to Create

- `app/routes/lms_routes.py`

## Files to Modify

- `app/services/lms/assessment_service.py`

## Acceptance Criteria

- [ ] Create quiz, add questions, publish

## Constraints

- assessment_type=quiz for assignments

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-302 Quiz Builder API: POST /quizzes, PUT /quizzes/<id>/questions, POST /quizzes/<id>/publish. Use assessment_service.
```
