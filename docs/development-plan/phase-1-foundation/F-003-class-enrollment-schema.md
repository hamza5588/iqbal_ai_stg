# F-003 — Class Enrollment Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | None |

## Description

Teacher-owned classes and student enrollments with join codes.

## Objective

Foundation for assignments and teacher dashboards.

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

- classes table: teacher_id, name, description, join_code (unique), grade_level, is_active
- class_enrollments: class_id, student_id, enrolled_at, status
- Unique constraint on (class_id, student_id)

## Files to Create

- `docs/development-plan/migrations/003_classes.sql`
- `app/services/lms/class_service.py`

## Files to Modify

- `app/models/database_models.py`
- `app/utils/db.py`

## Acceptance Criteria

- [ ] Teacher can create class via service
- [ ] Student can enroll by join_code via service
- [ ] List students in class, list classes for teacher/student

## Constraints

- Do not confuse with User.class_standard field — that is grade label, not Class entity
- join_code: secure random 6-8 chars, unique

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-003 Class and Enrollment schema for IqbalAI LMS.

Create:
1. Class and ClassEnrollment SQLAlchemy models
2. Migration 003_classes.sql
3. app/services/lms/class_service.py:
   - create_class(teacher_id, name, grade_level, ...) -> generates join_code
   - enroll_student(join_code, student_id)
   - list_teacher_classes(teacher_id)
   - list_class_students(class_id)
   - list_student_classes(student_id)

Use secrets/token for join_code generation. Add indexes on teacher_id, join_code.
No routes yet — service layer only with tests if feasible.
```
