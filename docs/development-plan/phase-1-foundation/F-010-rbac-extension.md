# F-010 — Rbac Extension

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-003 |

## Description

Extend RBAC for LMS permissions.

## Objective

Secure class, quiz, assignment, and performance endpoints.

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

- Add permissions: MANAGE_CLASS, CREATE_QUIZ, CREATE_DIAGNOSTIC, ASSIGN_QUIZ, VIEW_CLASS_PERFORMANCE, MANAGE_QUESTION_BANK
- Map to teacher and admin roles
- Helper: teacher_owns_class(user_id, class_id)

## Files to Create

- `app/rbac/lms_permissions.py`

## Files to Modify

- `app/rbac/permissions.py`
- `app/rbac/roles.py`

## Acceptance Criteria

- [ ] Teachers have LMS permissions; students do not
- [ ] Decorators can guard LMS routes

## Constraints

- Follow existing RBAC pattern in app/rbac/

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Extend RBAC for LMS (F-010).

Add to app/rbac/permissions.py:
MANAGE_CLASS, CREATE_QUIZ, CREATE_DIAGNOSTIC, ASSIGN_QUIZ, VIEW_CLASS_PERFORMANCE, MANAGE_QUESTION_BANK

Assign to Role.TEACHER and Role.ADMIN appropriately.
Create app/rbac/lms_permissions.py with:
- teacher_owns_class(user_id, class_id) -> bool
- student_in_class(user_id, class_id) -> bool

Add @permission_required decorator usage examples in docstring.
Update app/rbac/README.md
```
