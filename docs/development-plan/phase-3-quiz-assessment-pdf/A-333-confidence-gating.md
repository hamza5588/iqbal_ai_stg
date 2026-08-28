# A-333 — Confidence Gating

| Field | Value |
|-------|-------|
| **Phase** | Phase 3 — Quiz / Assessment / PDF |
| **Type** | AI + Backend |
| **Priority** | P0 |
| **MVP** | Yes |
| **Dependencies** | A-327 |

## Description

Block publish if confidence <0.60; flag 0.60-0.84 for review.

## Objective

Quality gate before teacher publish.

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

- Block publish if confidence <0.60; flag 0.60-0.84 for review.
- Quality gate before teacher publish.

## Files to Create

- `app/services/lms/assessment_service.py`

## Files to Modify

- (none expected)

## Acceptance Criteria

- [ ] Publish blocked when overall_confidence < 0.60
- [ ] Warnings returned for 0.60-0.84

## Constraints

- API returns clear error message to teacher

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement A-333 confidence gating.

In assessment_service.publish():
- Compute overall_confidence from QuizPdfSource and pair match_confidences
- < 0.60: raise LMSValidationError('Review required — confidence too low')
- 0.60-0.84: allow publish but set requires_review flag
- >= 0.85: normal publish

Expose confidence in GET assessment API.
```
