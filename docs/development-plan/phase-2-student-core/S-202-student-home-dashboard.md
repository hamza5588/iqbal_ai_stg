# S-202 — Student Home Dashboard

| Field | Value |
|-------|-------|
| **Phase** | Phase 2 — Student Core |
| **Type** | Backend + Frontend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | S-201, A-310 |

## Description

Dashboard section: next step, weak topics, pending quizzes, progress.

## Objective

Replace static recommendation cards with real LMS data.

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

- Fetch progress, assignments, learning path in one dashboard API
- UI cards for weak topics and pending quizzes

## Files to Create

- (determine during implementation)

## Files to Modify

- `templates/student_dashboard/student_dashboard.html`

## Acceptance Criteria

- [ ] Dashboard shows real weak topics from API
- [ ] Pending assignments listed
- [ ] Progress percentage shown

## Constraints

- Incremental change to monolithic template — use new section IDs

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement S-202 Student home dashboard LMS section.

Create GET /api/lms/students/me/dashboard aggregating: onboarding, progress (A-310), pending assignments, learning path current step.

Update student_dashboard.html: add 'LMS Overview' section at top with cards for Weak Topics, Pending Quizzes, Overall Progress.
Remove or demote static generateTeachingRecommendations for this section only.
Use fetch + render pattern already in template.
```
