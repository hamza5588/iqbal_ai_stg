# F-008 — Assignment Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-003, F-004 |

## Description

Assignments link ONE quiz to ONE class with due date. Quiz-only — no multi-item.

## Objective

Teacher assigns MCQ quiz to class.

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

- assignments: teacher_id, class_id, quiz_id (FK assessments where type=quiz), title, due_date, status
- assignment_submissions: assignment_id, student_id, attempt_id (nullable), status, submitted_at

## Files to Create

- `docs/development-plan/migrations/008_assignments.sql`
- `app/services/lms/assignment_service.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Create assignment with quiz + class + due_date
- [ ] List assignments for class (teacher) and student
- [ ] Track submission status per student

## Constraints

- One quiz per assignment — no assignment_items multi-type table

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-008 Assignment schema — QUIZ ONLY.

Assignment model: teacher_id, class_id, quiz_id (FK assessments), title, instructions, due_date, status (draft|published|closed)
AssignmentSubmission: assignment_id, student_id, attempt_id nullable, status (not_started|in_progress|submitted|overdue)

assignment_service.py:
- create_assignment(teacher_id, class_id, quiz_id, due_date, ...)
- publish_assignment, list_for_class, list_for_student
- link_attempt_to_submission(assignment_id, student_id, attempt_id)

Migration 008_assignments.sql
```
