# F-002 — Question Bank Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-001 |

## Description

Normalized question bank for MCQ storage independent of lesson JSON.

## Objective

Single source of truth for all quiz/diagnostic questions.

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

- Create `questions` table linked to topic_id and created_by (user FK)
- Fields: question_text, question_latex (nullable), explanation, difficulty, is_active
- Support MCQ: options JSON array (4 strings), correct_option_index (0-3)
- Store correct_answer_raw for PDF-sourced answers

## Files to Create

- `docs/development-plan/migrations/002_questions.sql`
- `app/services/lms/question_bank_service.py`

## Files to Modify

- `app/models/database_models.py`
- `app/utils/db.py`

## Acceptance Criteria

- [ ] questions table exists with FK to topics and users
- [ ] question_bank_service CRUD: create, get, list_by_topic, soft-delete

## Constraints

- correct_option_index must be 0-3 when options length is 4
- Do not migrate lesson assessment_quiz yet (separate task A-312)

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior backend engineer implementing F-002 Question Bank schema for IqbalAI.

DEPENDS ON: F-001 (topics table must exist)

TASK: Create normalized questions table and question_bank_service.

IMPLEMENT:
1. Question SQLAlchemy model in database_models.py:
   - topic_id FK, created_by FK, question_text, question_latex, options (JSON/Text), correct_option_index
   - correct_answer_raw, explanation, difficulty (easy/medium/hard), is_active, timestamps
2. Migration 002_questions.sql
3. app/services/lms/question_bank_service.py with create/read/update/list/filter by topic
4. Pydantic schema for validation in app/services/lms/schemas.py (QuestionCreate, QuestionRead)

Validate on create: exactly 4 options, unique option texts, correct_option_index in range.

No HTTP routes yet. Match coding style of app/models/models.py accessors if needed.
```
