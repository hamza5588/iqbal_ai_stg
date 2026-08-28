# F-006 — Performance Mastery Schema

| Field | Value |
|-------|-------|
| **Phase** | Phase 1 — Foundation |
| **Type** | Database |
| **Priority** | P0 BLOCKING |
| **MVP** | Yes |
| **Dependencies** | F-001, F-005 |

## Description

Topic-level scores and mastery status per student.

## Objective

Foundation for weakness detection and learning paths.

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

- student_topic_scores: student_id, topic_id, score_percent, mastery_status, last_assessed_at
- mastery_snapshots: student_id, snapshot_json, created_at (historical)
- mastery_status enum: mastered, improving, needs_practice, weak

## Files to Create

- `docs/development-plan/migrations/006_mastery.sql`
- `app/services/lms/performance_service.py`

## Files to Modify

- `app/models/database_models.py`

## Acceptance Criteria

- [ ] Upsert topic score after assessment submit
- [ ] Query all topic scores for student

## Constraints

- Mastery thresholds configurable (default: >=85 mastered, <60 weak)

---

## Cursor Implementation Prompt

Copy everything below this line into Cursor:

```
Implement F-006 Performance and Mastery schema.

Models: StudentTopicScore, MasterySnapshot
performance_service.py:
- update_topic_scores_from_attempt(attempt_id) — aggregate by question topic
- get_student_mastery(student_id) -> list of topic scores + status
- compute_mastery_status(score_percent) using configurable thresholds
- create_snapshot(student_id) for history

Migration 006_mastery.sql
```
