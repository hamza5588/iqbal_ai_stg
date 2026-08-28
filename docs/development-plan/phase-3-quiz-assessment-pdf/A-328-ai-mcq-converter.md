# A-328 — AI MCQ Converter

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | A-327, A-325 |

## Description

Convert Q+A pair to 4-option MCQ; PDF answer is correct; AI generates 3 distractors.

## Objective

Core MCQ generation for assignment quizzes.

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

- Convert Q+A pair to 4-option MCQ; PDF answer is correct; AI generates 3 distractors.
- Core MCQ generation for assignment quizzes.

## Files to Create

- `app/services/quiz/mcq_converter.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Output passes MCQQuestion validation
- [ ] Correct answer from PDF always in options

## Constraints

- Shuffle option positions
- Math notation preserved in latex fields

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-328 AI MCQ converter.

mcq_converter.py:
- convert_pair_to_mcq(pair: QuestionAnswerPair) -> MCQQuestion
- LLM generates 3 plausible distractors for math (common mistakes)
- PDF answer must be one of 4 options; shuffle positions; track correct_option_label
- Use LangChain with_structured_output(MCQQuestion)

Prompt: distractors must be plausible wrong answers, unique, no 'all of the above'.
Math: preserve notation in latex fields for MathJax UI.
```
