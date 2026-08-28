# P-407 — Adaptive Difficulty Engine

| Field | Value |
|-------|-------|
| **Phase** | Phase 4 — Personalization |
| **Type** | Backend/Frontend/AI/Testing |
| **Priority** | P2 |
| **MVP** | No |
| **Dependencies** | A-309, P-406 |

## Description

Adaptive Difficulty Engine. See development plan roadmap.

## Objective

Select next question difficulty based on recent attempt performance — do not implement before mastery data exists.

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

- Select next question difficulty based on recent attempt performance — do not implement before mastery data exists.

## Files to Create

- `See task — implement as needed for P-407`

## Files to Modify

- `app/routes/lms_routes.py`
- `templates/`

## Acceptance Criteria

- [ ] P-407 implemented and tested
- [ ] Follows existing codebase patterns

## Constraints

- Do not duplicate existing functionality
- Minimal focused diff
- Reuse existing auth/RAG/LLM

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior full-stack engineer implementing task P-407 for IqbalAI LMS.

Select next question difficulty based on recent attempt performance — do not implement before mastery data exists.

BEFORE CODING:
1. Read docs/development-plan/README.md and dependency tasks listed for P-407
2. Inspect existing code in iqbal_ai_stg/app/ — reuse auth, RAG, LLM gateway, RBAC
3. Follow patterns from app/services/lesson/ and app/routes/lesson_routes.py

IMPLEMENTATION RULES:
- Minimal scope — only what this task requires
- Add tests where tests/ already has patterns
- Do not break existing student/teacher dashboards
- Use /api/lms prefix for new APIs
- Register new routes in app/__init__.py

ACCEPTANCE:
- Feature works end-to-end for this task's scope
- RBAC enforced where applicable
- No secrets committed

After implementation, list files changed and how to manually verify.
```
