# A-306 — Student Quiz UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-303 |

## Description

MCQ quiz taking interface with MathJax.

## Objective

Student completes quizzes.

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

- Quiz modal or page
- One question at a time or paginated
- Submit flow

## Files to Create

- `templates/student_dashboard/lms_quiz.html or section in student_dashboard.html`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Student can complete quiz end-to-end
- [ ] Math renders via MathJax

## Constraints

- Mobile responsive

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-306 Student quiz UI: MCQ interface calling A-303 APIs, MathJax for latex, progress indicator, submit confirmation.
```
