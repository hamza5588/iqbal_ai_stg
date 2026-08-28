# S-205 — Class Enrollment UI

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | F-003, F-010 |

## Description

Student joins class via join code; teacher sees roster.

## Objective

Complete class enrollment user flow.

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

- Student: enter join code modal
- Teacher: display join code on class page
- APIs wired to class_service

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/student_dashboard/student_dashboard.html`
- `templates/teacher_dashboard.html`

## Acceptance Criteria

- [ ] Student successfully joins class with valid code
- [ ] Invalid code shows error
- [ ] Teacher sees updated roster

## Constraints

- Reuse existing dashboard modal patterns

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-205 Class enrollment UI.

API endpoints (lms_routes): POST /api/lms/classes/join {join_code}, GET /api/lms/classes/mine
Teacher UI: Create Class modal, show join_code, student list.
Student UI: Join Class modal with code input.
Use class_service from F-003. RBAC: TE-603.
```
