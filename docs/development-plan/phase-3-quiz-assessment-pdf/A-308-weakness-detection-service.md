# A-308 — Weakness Detection Service

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | Backend |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | A-304, F-006 |

## Description

Identify weak/strong topics from scores.

## Objective

Drive learning paths.

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

- analyze_diagnostic(attempt_id)
- Thresholds: weak <60%, strong >=80%

## Files to Create

- `app/services/lms/performance_service.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Returns weak_topics and strong_topics lists

## Constraints

- Configurable thresholds

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-308 in performance_service: analyze_attempt(attempt_id) -> weak_topics, strong_topics using configurable thresholds.
```
