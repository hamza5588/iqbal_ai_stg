# A-307 — Diagnostic Results UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-304, F-006 |

## Description

Show topic scores after diagnostic.

## Objective

Student sees strengths/weaknesses.

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

- Bar/list of topic scores
- Strong/weak labels

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/student_dashboard/student_dashboard.html`

## Acceptance Criteria

- [ ] Shows Algebra 90%, Fractions 55% style results

## Constraints

- Call onboarding complete after viewing

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-307 Diagnostic results UI after submit: topic breakdown chart/list, mark diagnostic complete via S-201 API.
```
