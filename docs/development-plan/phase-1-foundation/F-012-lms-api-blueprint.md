# F-012 — Lms Api Blueprint

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Backend |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-011 |

## Description

Register /api/lms blueprint with standard JSON responses.

## Objective

HTTP layer for all LMS features.

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

- Blueprint app/routes/lms_routes.py registered in app/__init__.py
- Standard response envelope: {success, data, error}
- login_required on all routes; RBAC on mutations

## Files to Create

- `app/routes/lms_routes.py`
- `app/utils/lms_api.py`

## Files to Modify

- `app/__init__.py`

## Acceptance Criteria

- [ ] Blueprint registered; /api/lms/health returns 200
- [ ] Helper json_success/json_error used

## Constraints

- Prefix /api/lms — do not conflict with /api/lessons

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-012 LMS API blueprint scaffold.

Create app/routes/lms_routes.py with bp = Blueprint('lms', __name__, url_prefix='/api/lms')
Create app/utils/lms_api.py: json_success(data, status=200), json_error(message, code, status=400)

Register blueprint in app/__init__.py
Add GET /api/lms/health (login optional) and stub route structure comments for future tasks.

Follow patterns from app/routes/lesson_routes.py for auth decorators.
```
