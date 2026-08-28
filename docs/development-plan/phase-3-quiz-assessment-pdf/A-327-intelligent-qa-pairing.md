# A-327 — Intelligent Q↔A Pairing

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | A-326 |

## Description

Pair questions to answers with confidence scores.

## Objective

Handle varied numbering and layouts.

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

- Pair questions to answers with confidence scores.
- Handle varied numbering and layouts.

## Files to Create

- `pair_questions_answers(extraction: PDFExtractionResult) -> list[QuestionAnswerPair]`

## Files to Modify

- `app/services/quiz/pdf_extractor.py`

## Acceptance Criteria



## Constraints

- All matched pairs have is_matched=True
- Unmatched items flagged in warnings

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
match_confidence 0-1
```
