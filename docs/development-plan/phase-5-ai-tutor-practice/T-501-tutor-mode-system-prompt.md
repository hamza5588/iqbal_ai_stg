# T-501 — Tutor Mode System Prompt

| Field | Value |
|-------|-------|
| **Phase** | Phase 5 — AI Tutor + Guided Practice |
| **Type** | Backend/Frontend/AI/Testing |
| **Priority** | P1 |
| **MVP** | Yes |
| **Dependencies** | None |

## Description

Tutor Mode System Prompt. See development plan roadmap.

## Objective

Add Socratic tutor system prompt to prompt_service.

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

- Add Socratic tutor system prompt to prompt_service.py: hints, step-by-step, avoid giving final answer when inappropriate for learning.

## Files to Create

- `See task — implement as needed for T-501`

## Files to Modify

- `app/routes/lms_routes.py`
- `templates/`

## Acceptance Criteria

- [ ] T-501 implemented and tested
- [ ] Follows existing codebase patterns

## Constraints

- Do not duplicate existing functionality
- Minimal focused diff
- Reuse existing auth/RAG/LLM

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior full-stack engineer implementing task T-501 for IqbalAI LMS.

Add Socratic tutor system prompt to prompt_service.py: hints, step-by-step, avoid giving final answer when inappropriate for learning.

BEFORE CODING:
1. Read docs/development-plan/README.md and dependency tasks listed for T-501
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
