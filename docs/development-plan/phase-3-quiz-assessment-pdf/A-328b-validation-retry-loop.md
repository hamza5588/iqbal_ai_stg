# A-328b — Validation Retry Loop

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-328 |

## Description

Retry AI on Pydantic validation failure (max 2).

## Objective

Robust pipeline.

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

- Retry AI on Pydantic validation failure (max 2).
- Robust pipeline.

## Files to Create

- `app/services/quiz/retry_utils.py`

## Files to Modify

- `app/services/quiz/mcq_converter.py`

## Acceptance Criteria

- [ ] Invalid MCQ triggers retry with error message in prompt
- [ ] Fails gracefully after max retries

## Constraints

- Max 2 retries

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-328b validation retry loop.

Wrap convert_pair_to_mcq with retry_on_validation_error(max_retries=2).
Pass ValidationError details back to LLM on retry.
Log failures to failed_conversions list in MCQBatchResult.
```
