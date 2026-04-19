# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IqbalAI is an AI-powered education platform built with Flask. It supports multi-provider LLMs, a production-grade RAG pipeline for PDF ingestion, LangGraph-based lesson Q&A, and a full RBAC system with three roles (Student, Teacher, Admin).

## Commands

### Local Development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py  # http://localhost:5000
```

### Docker (Production-like)

```bash
docker-compose up  # http://127.0.0.1:9080 via nginx
```

### Tests

```bash
pytest tests/
pytest tests/test_llm_gateway.py  # single test file
```

### Celery Worker (for background PDF ingestion)

```bash
celery -A app.tasks.ingest_tasks worker --loglevel=info
# Large-doc queue:
celery -A app.tasks.ingest_tasks worker -Q large_docs --loglevel=info
```

## Architecture

### Application Factory (`app/__init__.py`)

Flask app is created via `create_app()`. Blueprints are registered for: `auth`, `chat`, `files`, `chatbot`, `lesson`, `rag`, `subscription`, `admin`, `load_test`. The LangGraph lesson Q&A graph is compiled at startup and stored in `app.lesson_graph`.

### Routes → Services → Utils layering

- `app/routes/` — thin HTTP handlers, delegate to services
- `app/services/` — business logic (chat, chatbot, lesson management)
- `app/utils/` — shared infrastructure (LLM factory, RAG, auth, DB)
- `app/tasks/` — Celery async tasks (PDF ingestion)

### LLM Provider Abstraction (`app/utils/llm_factory.py`)

Pluggable factory supporting `openai`, `groq`, and `vllm`. Controlled by `LLM_PROVIDER` env var. The new `app/utils/llm_gateway.py` adds per-request telemetry context tracking.

### RAG Pipeline (`app/utils/rag_service.py`)

PDF ingestion is ~6,000 lines. Flow: upload → Celery task (or in-process for dev) → PDF parse → chunk → embed → store in Milvus (prod) or ChromaDB (local). Files over `RAG_LARGE_DOC_THRESHOLD_MB` (default 40MB) route to a separate Celery queue (`large_docs`). Post-ingestion markdown export is supported.

Set `USE_CHROMA_LOCAL=true` for local dev to skip Milvus.
Set `USE_CELERY_FOR_INGESTION=false` for local dev (synchronous ingestion).

### Lesson Q&A with LangGraph (`app/services/lesson/`)

- `lesson_qa_graph.py` — defines and compiles the LangGraph graph with a Postgres checkpointer for state persistence across turns
- `teacher_service.py` / `student_service.py` — role-specific lesson logic
- Graph is compiled once at app startup; access via `current_app.lesson_graph`

### RBAC (`app/rbac/`)

Three roles: `Student`, `Teacher`, `Admin`. Use decorators:
- `@admin_required` — admins only
- `@teacher_required` — teachers and admins
- `@login_required` — any authenticated user

Template helpers expose role checks to Jinja2. See `app/rbac/README.md` for details.

### Data Models

- `app/models/models.py` (~2,200 lines) — core domain models (User, Lesson, Conversation, etc.)
- `app/models/database_models.py` — SQLAlchemy ORM table definitions

### Key Configuration (`app/config.py`)

All configuration via environment variables. Important ones:

| Variable | Purpose |
|---|---|
| `ENV` | `local` / `staging` / `production` |
| `DATABASE_URL` | PostgreSQL (prod) or SQLite (dev) |
| `LLM_PROVIDER` | `openai` / `groq` / `vllm` |
| `USE_CHROMA_LOCAL` | `true` for local vector store |
| `USE_CELERY_FOR_INGESTION` | `false` for sync dev ingestion |
| `MILVUS_HOST` / `MILVUS_PORT` | Production vector DB |
| `EMBEDDING_MODEL_NAME` | Default: `sentence-transformers/all-MiniLM-L6-v2` |
| `EMBEDDING_DIM` | Default: `384` |
| `STRIPE_*` | Payment integration keys |

### Deployment (Docker)

`docker-compose.yml` starts: Flask (Gunicorn, 9 workers × 8 threads), PostgreSQL 17, Milvus, Redis 7, Nginx. The `deploy.sh` script automates Linux/Ubuntu Docker deployment.

## Important Patterns

- **HTTPS enforcement**: Routes check `X-Forwarded-Proto` in staging/production — don't bypass this.
- **Session security**: Cookies are Secure + HTTPOnly; sessions expire after 24h.
- **Celery reliability**: Workers use `late_ack` and `worker_prefetch_multiplier=1` — don't change without understanding task re-delivery implications.
- **Memory management**: Whisper model loads lazily; thread parallelism is disabled at startup to prevent OOM — don't eagerly initialize heavy models.
- **DB pool**: SQLAlchemy uses `pool_pre_ping`, `pool_recycle=300`; pool sizes are bounded — adjust carefully for production worker counts.
- **Load test mode**: `LOAD_TEST_MODE=true` reduces concurrency on staging — check this flag before diagnosing apparent performance issues in staging.
