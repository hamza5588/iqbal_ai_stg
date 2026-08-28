# A-331 — MCQ Preview & Edit UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-328, A-333 |

## Description

Review/edit generated MCQs before publish.

## Objective

Teacher quality control.

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

- List all MCQs
- Edit options
- Regenerate distractors button

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/teacher_dashboard.html`

## Acceptance Criteria

- [ ] Teacher can edit and save MCQs

## Constraints

- Mandatory review for confidence <0.85 recommended

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-331 MCQ preview/edit UI: show generated questions, inline edit, regenerate single question via API, save to question bank.
```
