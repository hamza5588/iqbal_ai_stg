# F-001 — Curriculum Taxonomy Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | None |

## Description

Define subjects → topics → subtopics with optional prerequisites and difficulty levels.

## Objective

Create the curriculum taxonomy that all questions, quizzes, diagnostics, and mastery tracking depend on.

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

- Create `topics` table: id, name, slug, parent_id (self-FK), subject, grade_level, description, sort_order, is_active
- Create `topic_prerequisites` table: topic_id, prerequisite_topic_id
- Add indexes on parent_id, subject, slug
- Create seed script for Math topics: Algebra, Fractions, Geometry, Word Problems, Quadratic Equations
- Register models in database_models.py

## Files to Create

- `docs/development-plan/migrations/001_topics.sql`
- `app/services/lms/curriculum_service.py`
- `scripts/seed_topics.py`

## Files to Modify

- `app/models/database_models.py`
- `app/utils/db.py`

## Acceptance Criteria

- [ ] Topics and prerequisites tables exist in PostgreSQL/SQLite dev
- [ ] Seed script populates Math topics
- [ ] curriculum_service can list topics by subject and get prerequisites

## Constraints

- Do not break existing models or init_db
- Use same SQLAlchemy Base from database_models.py
- Slug must be unique per subject

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
You are a senior backend engineer implementing F-001 for IqbalAI LMS foundation.

TASK: Implement curriculum taxonomy schema (topics + prerequisites).

CONTEXT:
- ORM: app/models/database_models.py (SQLAlchemy declarative Base)
- DB init: app/utils/db.py init_db() — no Alembic
- Follow existing model patterns (User, Lesson tables)

IMPLEMENT:
1. Add Topic and TopicPrerequisite SQLAlchemy models to database_models.py
2. Create docs/development-plan/migrations/001_topics.sql with CREATE TABLE IF NOT EXISTS
3. Update init_db() to run migration SQL safely (check table exists)
4. Create app/services/lms/__init__.py and curriculum_service.py with:
   - list_topics(subject, grade_level=None)
   - get_topic_by_id(id)
   - get_prerequisites(topic_id)
5. Create scripts/seed_topics.py to seed Math topics including Quadratic Equations

SEED TOPICS (minimum): Algebra, Fractions, Geometry, Word Problems, Quadratic Equations

Do not implement API routes yet. Add minimal unit tests if tests/ pattern exists.

Verify: run seed script against dev DB without errors.
```
