# AN-706 — Struggling Student Alerts

| Field | Value |
|-------|-------|
| **Phase** | Phase 7 — Analytics |
| **Type** | Backend/Frontend/AI/Testing |
| **Priority** | P2 |
| **MVP** | No |
| **Dependencies** | A-309 |

## Description

Struggling Student Alerts. See development plan roadmap.

## Objective

Flag students below threshold per topic/class.

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

- Flag students below threshold per topic/class.

## Files to Create

- `See task — implement as needed for AN-706`

## Files to Modify

- `app/routes/lms_routes.py`
- `templates/`

## Acceptance Criteria

- [ ] AN-706 implemented and tested
- [ ] Follows existing codebase patterns

## Constraints

- Do not duplicate existing functionality
- Minimal focused diff
- Reuse existing auth/RAG/LLM

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior full-stack engineer implementing task AN-706 for IqbalAI LMS.

Flag students below threshold per topic/class.

BEFORE CODING:
1. Read docs/development-plan/README.md and dependency tasks listed for AN-706
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
