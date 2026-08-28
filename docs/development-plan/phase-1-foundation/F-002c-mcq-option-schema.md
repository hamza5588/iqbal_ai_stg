# F-002c — Mcq Option Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-002 |

## Description

Formalize MCQ option storage with LaTeX support for math rendering.

## Objective

Ensure consistent 4-option MCQ structure across PDF pipeline and UI.

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

- Options stored as JSON array of {label, text, latex?}
- Validator: exactly 4 options, unique text, valid correct_option_index
- Helper to render for API responses

## Files to Create

- `app/services/lms/mcq_utils.py`

## Files to Modify

- `app/services/lms/schemas.py`
- `app/services/lms/question_bank_service.py`

## Acceptance Criteria

- [ ] mcq_utils.validate_mcq() rejects invalid MCQs
- [ ] API schema documents 4-option structure

## Constraints

- Compatible with MathJax frontend rendering

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-002c MCQ option schema helpers.

Create app/services/lms/mcq_utils.py:
- MCQOption pydantic model: label (A-D), text, latex optional
- validate_mcq(options: list, correct_index: int) -> raises ValueError with clear message
- normalize_options() — ensure labels A,B,C,D
- shuffle_options(correct_index) -> new index after shuffle

Integrate validation into question_bank_service create/update paths.
Update schemas.py with MCQOption and MCQQuestionCreate types.
```
