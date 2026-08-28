# F-011 — Service Layer Scaffolding

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-009 |

## Description

Organize LMS services under app/services/lms/ and quiz under app/services/quiz/.

## Objective

Clean architecture before routes.

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

- Package structure with __init__.py exports
- Consistent error types LMSNotFoundError, LMSValidationError
- Logging pattern matching existing services

## Files to Create

- `app/services/lms/__init__.py`
- `app/services/lms/exceptions.py`
- `app/services/quiz/__init__.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] All F-001–F-008 services importable from packages
- [ ] Exceptions used consistently

## Constraints

- No circular imports with routes

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-011 service layer scaffolding.

Ensure packages:
- app/services/lms/ (curriculum, question_bank, class, assessment, attempt, performance, assignment, learning_path services)
- app/services/quiz/ (models.py placeholder for A-325)

Create exceptions.py: LMSNotFoundError, LMSValidationError, LMSPermissionError
Update __init__.py to export public service functions.

Verify all services import without circular dependency errors.
```
