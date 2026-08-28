# S-206 — Student Profile Extensions

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-006, S-201 |

## Description

Extended student metadata for LMS state.

## Objective

Centralize student LMS state.

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

- student_profiles table or extend users with JSON metadata
- current_learning_path_id, enrolled_at tracking

## Files to Create

- `docs/development-plan/migrations/010_student_profiles.sql`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Profile stores diagnostic and path state

## Constraints

- Prefer separate table over altering users heavily

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-206 Student profile extensions.

Create student_profiles: user_id PK, diagnostic_completed, diagnostic_completed_at, current_learning_path_id, created_at, updated_at
student_profile_service CRUD. Link from S-201 onboarding gate.
```
