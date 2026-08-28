# P0-001 — Existing System Analysis

| Field | Value |
|-------|-------|
| **Phase** | Phase 0 — Existing System Analysis |
| **Type** | Documentation |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | None |

## Description

Reference document summarizing what exists in the codebase vs what must be built for the LMS roadmap.

## Objective

Produce a living analysis doc developers can reference before implementing any phase.

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

- Document existing auth, roles, lessons, RAG, chat, RBAC, and database models
- List reusable components and explicit gaps (classes, assignments, question bank, mastery)
- Note: no Alembic; monolithic Jinja dashboards

## Files to Create

- `docs/development-plan/phase-0-existing-system/ARCHITECTURE_BASELINE.md`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Architecture baseline doc exists and matches current codebase
- [ ] Gap list aligns with development-plan phases 1–10

## Constraints

- Read-only analysis — do not implement features in this task
- Do not include secrets from .env

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior software architect documenting the IqbalAI Flask codebase at iqbal_ai_stg.

TASK: Create ARCHITECTURE_BASELINE.md in docs/development-plan/phase-0-existing-system/

Analyze the actual codebase (do not assume). Include:
1. Tech stack and folder structure
2. Existing user roles and auth flow
3. All SQLAlchemy models in app/models/database_models.py
4. Existing API blueprints and key endpoints
5. AI/RAG/LangGraph capabilities
6. What is MISSING for the LMS roadmap (classes, question bank, assignments, mastery, PDF→MCQ pipeline)
7. Reusable patterns (Pydantic in app/services/lesson/models.py, RAG ingest, RBAC)

Keep it factual with file paths. No code changes except the markdown file.
```
