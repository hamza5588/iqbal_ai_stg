# A-326 — AI PDF Structured Extractor

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | A-325, RAG ingest |

## Description

LangChain with_structured_output to extract Q&A from variable PDF formats.

## Objective

Intelligent extraction — not regex-only.

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

- LangChain with_structured_output to extract Q&A from variable PDF formats.
- Intelligent extraction — not regex-only.

## Files to Create

- `app/services/quiz/pdf_extractor.py`
- `Uses LLM gateway from app/utils/llm_gateway.py`

## Files to Modify

- `app/services/quiz/pdf_extractor.py`

## Acceptance Criteria

- [ ] app/utils/llm_gateway.py

## Constraints

- Returns valid PDFExtractionResult from sample PDF text
- Handles Questions/Answers sections and inline formats

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Do not invent answers — only extract from document
Use structured output
```
