# F-004 — Assessment Quiz Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-001, F-002 |

## Description

First-class assessments/quizzes (diagnostic and assignment quiz types).

## Objective

Decouple quizzes from lesson JSON content.

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

- assessments table: title, type (diagnostic|quiz), creation_mode, created_by, status (draft|published|archived)
- assessment_questions join: assessment_id, question_id, sort_order
- Support time_limit_minutes nullable

## Files to Create

- `docs/development-plan/migrations/004_assessments.sql`
- `app/services/lms/assessment_service.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Create assessment with ordered question IDs
- [ ] Publish/unpublish assessment
- [ ] List assessments by teacher and type

## Constraints

- Do not break existing lesson assessment_quiz embedded in JSON

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-004 Assessment/Quiz schema.

Models:
- Assessment: id, title, description, assessment_type ('diagnostic'|'quiz'), creation_mode ('manual'|'pdf_qa_auto'|'pdf_ai'|'mixed')
  created_by FK, status, time_limit_minutes, created_at, updated_at
- AssessmentQuestion: assessment_id, question_id, sort_order — unique (assessment_id, question_id)

Service assessment_service.py:
- create_assessment, add_questions, remove_question, reorder, publish, get_with_questions

Migration 004_assessments.sql + model registration.
```
