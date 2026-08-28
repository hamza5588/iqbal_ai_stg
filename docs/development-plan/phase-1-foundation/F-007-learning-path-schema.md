# F-007 — Learning Path Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P1 |
| **MVP** | No |
| **Dependencies** | F-001, F-006 |

## Description

Personalized learning path sequences per student.

## Objective

Store ordered remediation steps.

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

- learning_paths: student_id, title, status, created_at
- learning_path_items: path_id, item_type (lesson|quiz|practice), item_id, sort_order, status

## Files to Create

- `docs/development-plan/migrations/007_learning_paths.sql`
- `app/services/lms/learning_path_service.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Create path, add items, mark item complete

## Constraints

- Rule-based generation in P-402 — schema only here

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-007 Learning Path schema (data layer only).

Models: LearningPath, LearningPathItem
learning_path_service.py: CRUD for paths and items, mark_complete, get_current_item
Migration 007_learning_paths.sql
```
