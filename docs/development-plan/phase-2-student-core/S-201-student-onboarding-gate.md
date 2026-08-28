# S-201 — Student Onboarding Gate

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | F-005, F-008, S-205 |

## Description

Redirect new students to diagnostic; show pending assignments on dashboard.

## Objective

Gate student experience until diagnostic complete; surface assignments.

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

- Add diagnostic_completed flag on student profile or user metadata
- Post-login redirect logic in chat routes or auth
- API: GET /api/lms/students/me/onboarding-status

## Files to Create

- `app/services/lms/student_profile_service.py`

## Files to Modify

- `app/routes/chat.py`
- `templates/student_dashboard/student_dashboard.html`

## Acceptance Criteria

- [ ] New student redirected to diagnostic
- [ ] Completed student sees dashboard normally
- [ ] Pending assignments visible

## Constraints

- Do not break existing student dashboard routes

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-201 Student onboarding gate.

Add student profile fields: diagnostic_completed (bool), diagnostic_assessment_id (nullable).
student_profile_service.py: get_onboarding_status(student_id), mark_diagnostic_complete(student_id)

Backend: check after login — if student and not diagnostic_completed, frontend receives flag.
Update student dashboard JS to redirect to diagnostic flow when flag set.
Add /api/lms/students/me/onboarding-status endpoint in lms_routes.py.
```
