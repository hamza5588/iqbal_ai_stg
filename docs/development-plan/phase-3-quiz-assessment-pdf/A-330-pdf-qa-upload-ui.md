# A-330 — PDF Q&A Upload UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-329 |

## Description

Teacher uploads Q&A PDF for quiz generation.

## Objective

Entry point for PDF pipeline.

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

- Upload component
- Processing status polling

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/teacher_dashboard.html`

## Acceptance Criteria

- [ ] Upload triggers pipeline
- [ ] Progress shown

## Constraints

- Reuse existing PDF upload patterns

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-330 Teacher PDF Q&A upload UI in quiz creation hub: file upload, link to assessment draft, poll extraction status.
```
