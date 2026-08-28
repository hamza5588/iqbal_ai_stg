# A-325 — Pydantic Extraction Models

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | None |

## Description

Define all Pydantic models for PDF extraction and MCQ output.

## Objective

Create app/services/quiz/models.py with ExtractedQuestion, ExtractedAnswer, PDFExtractionResult, QuestionAnswerPair, MCQOption, MCQQuestion, MCQBatchResult and validators (4 unique options).

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

- Define all Pydantic models for PDF extraction and MCQ output.
- Create app/services/quiz/models.py with ExtractedQuestion, ExtractedAnswer, PDFExtractionResult, QuestionAnswerPair, MCQOption, MCQQuestion, MCQBatchResult and validators (4 unique options).

## Files to Create

- `app/services/quiz/models.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] All models validate correctly in unit tests
- [ ] MCQQuestion rejects !=4 options

## Constraints

- Use Pydantic v2 model_validator
- Follow app/services/lesson/models.py style

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-325 Pydantic models for PDF→MCQ pipeline.

Create app/services/quiz/models.py with:
- ExtractedQuestion, ExtractedAnswer (flexible number: int|str)
- PDFExtractionResult (title, questions, answers, format_detected, confidence, warnings)
- QuestionAnswerPair (match_confidence, is_matched)
- MCQOption (label A-D, text, latex optional)
- MCQQuestion with validator: exactly 4 unique options, correct_option_label valid
- MCQBatchResult (quiz_title, questions, failed_conversions)

Add tests in tests/test_quiz_models.py
```
