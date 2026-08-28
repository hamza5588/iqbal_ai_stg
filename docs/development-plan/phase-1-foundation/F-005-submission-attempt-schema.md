# F-005 — Submission Attempt Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-004 |

## Description

Student assessment attempts and per-question answers.

## Objective

Enable scoring and performance analytics.

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

- assessment_attempts: student_id, assessment_id, assignment_id (nullable), started_at, submitted_at, score, max_score, status
- attempt_answers: attempt_id, question_id, selected_option_index, is_correct

## Files to Create

- `docs/development-plan/migrations/005_attempts.sql`
- `app/services/lms/attempt_service.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Start attempt, save answers, submit attempt
- [ ] Calculate score from correct_option_index

## Constraints

- Prevent answer leakage in API — separate delivery vs grading

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-005 Submission and Attempt schema.

Models: AssessmentAttempt, AttemptAnswer
Service attempt_service.py:
- start_attempt(student_id, assessment_id, assignment_id=None)
- save_answer(attempt_id, question_id, selected_option_index)
- submit_attempt(attempt_id) -> computes score, marks completed
- get_attempt_results(attempt_id) for student/teacher

Migration 005_attempts.sql. Index on (student_id, assessment_id).
```
