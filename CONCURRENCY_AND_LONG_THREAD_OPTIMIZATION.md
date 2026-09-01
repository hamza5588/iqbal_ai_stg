# Concurrent Users + Long-Running Threads: Optimization Plan

**Scope:** Live 209 staging host (`iqbalaiv11`) plus current application code.  
**Goal:** Raise safe concurrent-user capacity and stop long RAG/lesson turns from starving login, polling, and other users.  
**Date:** 19 Aug 2026  
**Reviewed as:** senior DevOps + full-stack (request path, Gunicorn, nginx, Celery, Postgres, Redis, Milvus).

This is an analysis and task list, not a change log. Implement in the phase order at the end. Do not raise Gunicorn worker count on this host until RAM and DB pool math are fixed.

---

## 1. Current production-like topology (209)

| Layer | What is running now |
|---|---|
| Host | **4 vCPU, 7.8 GiB RAM, 0 swap**, load ~0.3 idle |
| Edge | nginx 1.25, 80+443, `worker_connections 4096`, **one** upstream `flask_app1:5000` |
| Web | Gunicorn **9 workers × 8 gthreads**, timeout **300s**, bind `0.0.0.0:5000` |
| App | Flask, LangGraph RAG chat (Postgres checkpointer), lesson Q&A graph at startup |
| Jobs | **One** Celery worker, `concurrency=2`, queues `default,ingest,ingest_large,headings` |
| Data | Postgres 17 (`max_connections=100`, `shared_buffers=128MB`), Redis 7 (`maxmemory 1gb`, **`allkeys-lru`**), Milvus 2.4 standalone |
| Deploy | Bind-mount `.:/app`, `ENV=staging`, `LLM_PROVIDER=groq`, `USE_CELERY_FOR_INGESTION=true` |
| Idle RAM | Flask **3.65 GiB**, Celery **1.19 GiB**, Milvus 289 MiB, Postgres 216 MiB, ~**1.9 GiB available** |

Theoretical web slots: **72** in-flight HTTP requests (9×8). That number is not real capacity on this box. Memory, Groq, and Postgres will fail first.

---

## 2. How a long thread actually occupies the system

Teacher RAG chat (`POST /api/rag/chat`) is **synchronous on a Gunicorn thread**:

1. Redis chat lock per `thread_id` (5s wait, 600s auto-expire).
2. LangGraph invoke with Postgres checkpoint + up to **15 tool rounds**.
3. Each LLM call defaults to **60s** timeout (`GROQ_TIMEOUT` / `OPENAI_TIMEOUT` unset).
4. Quality-gate / extra tool loops can push a lecture turn **past 300s**.
5. Nginx `proxy_read_timeout` is **1800s**, so the browser can wait far longer than Gunicorn will.

While that thread is busy, it still holds:

- 1 Gunicorn gthread
- 1+ Postgres pool connections (app session + LangGraph `PostgresSaver`)
- 1 Redis lock
- 1 Groq semaphore slot **inside that worker process only**

A gunicorn `--timeout 300` on **gthread kills the whole worker process**, including the other 7 threads on it. One hung lecture can drop 8 concurrent users.

Ingestion is correctly off the web process (Celery). Chat is not, and should stay on the web path for request/response — but worker sizing and timeouts must match that choice.

---

## 3. What is already in good shape

Keep these; do not “optimize” them blindly.

- Celery ingestion (`USE_CELERY_FOR_INGESTION=true`) so uploads do not block nginx.
- `task_acks_late=True` and `worker_prefetch_multiplier=1` (re-delivery correctness).
- Redis chat lock keyed by `thread_id` (cross-process; in-process `threading.Lock` was a real race).
- SQLAlchemy `pool_pre_ping` + `pool_recycle=300` + per-PID engine (safe with fork).
- `TOKENIZERS_PARALLELISM=false` at startup.
- nginx long timeouts and `client_max_body_size 100M` for PDF ingest.
- Separate queues exist (`ingest` vs `ingest_large` vs `headings`) even if they share one worker today.

---

## 4. Bottlenecks at the current level (ranked)

### P0 — Will fail first under concurrent users + long turns

| ID | Finding | Why it hurts now |
|---|---|---|
| P0.1 | **9 Gunicorn workers on 4 CPU / 8 GB / no swap** | Flask already uses 3.65 GiB idle. Embeddings, Whisper, and PDF parse have nowhere to go. OOM-killer is the real concurrency limiter. |
| P0.2 | **gthread timeout 300s vs lecture turns that can exceed 300s** | Timeout kills **all 8 threads** in that worker. Nginx still waits up to 1800s. |
| P0.3 | **Redis `allkeys-lru`** | Chat locks, Celery broker keys, and progress keys are evictable. Lost lock → two workers run the same LangGraph thread (the bug Redis was added to fix). |
| P0.4 | **Groq limiter is per-process, default 4** | Unset `GROQ_MAX_CONCURRENT_REQUESTS` → **9 × 4 = 36** possible Groq calls. 429s, latency spikes, thread pile-up. |
| P0.5 | **Postgres pool math vs `max_connections=100`** | 9 web workers × (5+5 overflow) = **90**, plus Celery children, plus LangGraph checkpointer connections. Idle today is 28; under load this hits 100 and checkouts block. |

### P1 — Starves other users once P0 is stressed

| ID | Finding | Why it hurts |
|---|---|---|
| P1.1 | **One Flask replica, one Celery process for all queues** | A large PDF (`ingest_large`) and headings share `concurrency=2`. Chat users wait on CPU/RAM, not on a queue they can isolate. |
| P1.2 | **Timeout mismatch** | LLM 60s × 15 tool rounds ≫ Gunicorn 300s ≪ nginx 1800s. Failures look like “hangs” then 502/504. |
| P1.3 | **`PostgresSaver.from_conn_string` at import, never pooled/closed** | Every Gunicorn worker **and** the Celery worker import `rag_service.py` and open a checkpointer connection. Extra PG sessions outside SQLAlchemy pool accounting. |
| P1.4 | **Lock TTL 600s vs worker kill at 300s** | If the worker dies, the Redis lock can block that thread for up to 10 minutes (`429 CONCURRENT_REQUEST_TIMEOUT`). |
| P1.5 | **No container memory limits** | One runaway worker can OOM the host (Postgres/Redis/Milvus included). |

### P2 — Correctness / efficiency debt that shows up at modest concurrency

| ID | Finding | Why it hurts |
|---|---|---|
| P2.1 | **`ingest_tasks.py` defaults `LOAD_TEST_MODE` to `"true"`** | `os.getenv("LOAD_TEST_MODE", "true")` — opposite of `config.py` / `rag_service.py` (`"false"`). Headings routing and load-test behavior disagree when the env var is unset (current 209 state). |
| P2.2 | **Embedding parallelism default 4** when `LOAD_TEST_MODE` is false | Staging still runs 4 parallel embed batches on a 4-core box that also serves chat. |
| P2.3 | **nginx `proxy_buffering off` on all locations** | Right for SSE/streaming; wasteful for JSON login/dashboard. Holds upstream longer. |
| P2.4 | **No upstream keepalive** | Every request opens a new connection to Gunicorn. |
| P2.5 | **Whisper loaded per worker on first STT** | Extra hundreds of MB × 9 processes. |
| P2.6 | **Bind-mount `.:/app`** | Fine for staging deploys; extra I/O and no immutable image. Not the first production lever. |
| P2.7 | **Postgres `shared_buffers=128MB` default** | OK for this RAM budget if web workers shrink; too small if DB becomes the bottleneck later. |

**Current safe estimate on this host (do not advertise 72 users):** about **8–15 concurrent RAG turns** plus light dashboard traffic, if Groq is healthy. Above that, expect 429s, 502s after 300s, and RAM pressure.

---

## 5. Target shape (this host)

Stay on **one VM** for staging. Optimize for *isolation of long work* and *headroom*, not max theoretical slots.

| Component | Current | Target on 209 (8 GB) |
|---|---|---|
| Gunicorn workers | 9 | **3** (≈ 2–4; never more than CPU count here) |
| Threads / worker | 8 | **8–12** (I/O-bound LLM waits) |
| Concurrent HTTP slots | 72 | **24–36** (real, RAM-safe) |
| Gunicorn timeout | 300s | **600s**, aligned with lock TTL and worst lecture turn |
| Celery | 1 worker × 2, all queues | Keep 2 concurrency **or** split: `ingest`/`headings` vs `ingest_large` if RAM allows a second small worker |
| Redis policy | `allkeys-lru` | **`noeviction`** (or split cache vs broker/locks) |
| Groq global cap | 4 × 9 processes | **One Redis semaphore**, cap **4–8** for the whole host |
| PG pool / process | 5+5 | Keep **5+5** after dropping to 3 workers → ~50 connections, safe vs 100 |
| Container `mem_limit` | none | Flask ~3.5g, Celery ~1.5g, leave ~2g for PG+Redis+Milvus+OS |

Production (separate, larger host or two app nodes): 2 Flask replicas behind nginx, dedicated ingest_large worker, Postgres `max_connections` raised with pool math documented, Redis without LRU on lock DB.

---

## 6. Tasks and solutions

Each task is independently shippable. **Do P0 before adding users or load tests.**

### Task 1 — Right-size Gunicorn for 4 CPU / 8 GB

**Problem:** 9 workers duplicate the full Python/embedding/LangGraph footprint. Idle Flask is already 3.65 GiB. Long turns make this worse, not better.

**Solution:**

- Change Dockerfile CMD (and/or compose `command`) to `--workers 3 --threads 8 --timeout 600 --graceful-timeout 60 --keep-alive 5`.
- Add `--max-requests 500 --max-requests-jitter 50` so workers recycle and leak memory less.
- Add `--worker-tmp-dir /dev/shm` to avoid slow heartbeat files on bind-mounted disk.

**Files:** `Dockerfile` (live copy under `server_files/Dockerfile`), optionally `docker-compose.yml` `command:` override so image rebuild is not required.

**Acceptance:** Flask RSS drops well below ~2.5 GiB idle; `docker stats` shows ≥2.5 GiB host available; login still fast while 5 chats run.

---

### Task 2 — Align timeouts for long RAG / lesson threads

**Problem:** Tool rounds × 60s LLM timeout can exceed 300s. Gunicorn then kills the worker; nginx keeps the client until 1800s.

**Solution:**

- Set explicit env: `GROQ_TIMEOUT=120`, `OPENAI_TIMEOUT=120` (one call, not the whole turn).
- Keep `RAG_LESSON_MAX_TOOL_ROUNDS_PER_TURN` at 15 for quality, **or** cap non-lesson Q&A lower (e.g. 6) so Q&A cannot run 15 minutes.
- Gunicorn timeout **≥** max observed lecture (recommend 600s).
- nginx: **360–600s** for `/api/rag/chat` and lesson create; keep **1800s** only on ingest/upload locations. Do not use 1800s on `/`.
- Chat lock auto-expire = gunicorn timeout + 30s (e.g. 630), **or** heartbeat the Redis lock every N seconds from the graph.

**Files:** `server_files/nginx.conf`, `server_files/Dockerfile`, `.env` / `server_files/.env`, `app/routes/rag_routes.py` (lock wait already 5s — keep short), `app/utils/chat_lock.py`.

**Acceptance:** A 7-minute lecture completes without 502. A 20-minute stuck turn fails in Gunicorn, releases the lock, and does not take 8 other users with it (after Task 1, only 8 threads die per worker — still bad, which is why timeout must be rare).

---

### Task 3 — Redis: never evict locks or Celery keys

**Problem:** `--maxmemory-policy allkeys-lru` can drop `chat_lock:*`, Celery task keys, and progress keys under memory pressure.

**Solution:**

- Staging: `redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy noeviction`.
- Better: two logical DBs or two instances — DB0 broker+locks (`noeviction`), DB1 cache (`allkeys-lru`).
- Point `CHAT_LOCK_REDIS_URL` / Celery broker at the noeviction DB.

**Files:** `server_files/docker-compose.yml` redis `command`.

**Acceptance:** `redis-cli INFO stats` evicted_keys stays 0 during a 30-minute chat+ingest soak. Duplicate-send still returns 429, not crossed answers.

---

### Task 4 — Process-global Groq (and OpenAI) concurrency cap

**Problem:** `GroqRateLimiter` is a process singleton. 9 workers × 4 slots = 36 upstream calls. Default env is unset.

**Solution:**

- Implement a Redis-based limiter (INCR/EXPIRE or Redlock semaphore) shared by all Gunicorn workers and Celery.
- Set `GROQ_MAX_CONCURRENT_REQUESTS=6` for this host (tune with 429 rate).
- Fail with a fast 429/`GroqBusyError` instead of stacking 72 blocked threads.

**Files:** `app/utils/rag_service.py` (`GroqRateLimiter`), `app/utils/llm_factory.py`, env on 209.

**Acceptance:** Under 20 parallel chats, Groq 429 rate drops; login/health stay responsive; `INFO` logs show a single global in-flight count.

---

### Task 5 — Postgres connection budget

**Problem:** `max_connections=100`. Each web process pool is 10. 9 workers + Celery + checkpointer can exhaust PG. Checkout wait then looks like “site freeze”.

**Solution:**

- After Task 1 (3 workers): keep `pool_size=5`, `max_overflow=5`.
- Document formula: `(gunicorn_workers + celery_child_procs) * (pool_size + max_overflow) + checkpointers + admin < 0.7 * max_connections`.
- Give LangGraph `PostgresSaver` a small dedicated pool or `NullPool` + short-lived conns; do not ignore it in the budget.
- Optional: Postgres `max_connections=150` only after RAM is stable.

**Files:** `app/config.py`, `app/utils/db.py`, `app/utils/rag_service.py` (checkpointer init), Postgres image config if raising max.

**Acceptance:** Soak test with 20 concurrent chats: `pg_stat_activity` count stays < 70; no `remaining connection slots` errors.

---

### Task 6 — Isolate long ingest from chat CPU/RAM

**Problem:** One Celery `-c 2` handles `ingest_large` (multi-minute, high RSS) and `headings` (many small LLM calls). Chat on the same 4 cores fights ingest.

**Solution (staging, RAM-limited):**

- Keep **one** worker but **concurrency=1** for `ingest_large` via a dedicated worker if a second container fits (~+400–800 MB).
- Prefer: `celery_worker` `-Q default,ingest,headings -c 2` and `celery_large` `-Q ingest_large -c 1`.
- If RAM cannot fit a second worker: `-c 2` on mixed queues is acceptable only after Flask workers drop to 3.

**Do not** raise `worker_prefetch_multiplier` or disable `late_ack` without a re-delivery review.

**Files:** `server_files/docker-compose.yml` (`celery_worker` command; optional second service), `app/tasks/ingest_tasks.py`.

**Acceptance:** Uploading a 40MB+ PDF does not add >2s to unrelated `/health` or login. Headings jobs do not sit behind one large ingest forever (separate queue consumer).

---

### Task 7 — Fix `LOAD_TEST_MODE` default mismatch

**Problem:** `app/tasks/ingest_tasks.py` uses `os.getenv("LOAD_TEST_MODE", "true")`. Config and `rag_service.py` default `"false"`. On 209 the var is **unset**, so Celery thinks load-test mode is on.

**Solution:** Same default everywhere: `"false"`. Staging should set `LOAD_TEST_MODE` explicitly only during a test.

**Files:** `app/tasks/ingest_tasks.py` (change default to `"false"`), optionally set `LOAD_TEST_MODE=false` in `server_files/.env`.

**Acceptance:** Unset env → headings queue follows production path (`ingest` unless `RAG_HEADINGS_QUEUE` is set). Staging load tests still work when the flag is `true`.

---

### Task 8 — nginx: protect short requests from long proxies

**Problem:** `/` uses 1800s timeouts and `proxy_buffering off`. Login and static HTML share fate with a 20-minute ingest.

**Solution:**

- Keep buffering **off** only for `/ws/`, SSE ingest progress, and (if enabled) streaming chat.
- JSON APIs: buffering on, `proxy_read_timeout 120s` except RAG chat / lesson create (`600s`) and ingest (`1800s`).
- `upstream flask_app { keepalive 16; }` + `proxy_http_version 1.1` + `Connection ""`.
- Optional: `limit_req` on `/auth/login`, `limit_conn` per IP.

**Files:** `server_files/nginx.conf`, `Dockerfile.nginx` (rebuild nginx image).

**Acceptance:** Parallel long chat does not delay login HTML beyond ~200ms extra. `nginx -t` clean.

---

### Task 9 — Memory governors (OS and app)

**Problem:** No swap, no `mem_limit`, Whisper and embedding models per process.

**Solution:**

- Compose `mem_limit` / `mem_reservation` per service (see table in §5).
- Lazy Whisper must be **one process** or an HTTP STT sidecar — not 3–9 copies. Prefer OpenAI Whisper API if keys exist; if local, load in Celery only.
- `torch.set_num_threads(1)` and `OMP_NUM_THREADS=1` in Flask and Celery entrypoints (4 cores cannot absorb 9 workers × libgomp).
- Staging: `RAG_EMBED_PARALLEL_BATCHES=1` or `2`.

**Files:** `run.py` / `app/__init__.py` / `app/celery_worker_entry.py`, `app/routes/chat.py` (`_get_whisper_model`), `server_files/docker-compose.yml`, env.

**Acceptance:** `docker stats` Flask+Celery stay under limits during ingest+3 chats; no host OOM in `dmesg`.

---

### Task 10 — Long-thread lifecycle (cancel, lock release, no orphan)

**Problem:** User leaves the tab; Gunicorn still runs the graph until timeout; input lock / Redis lock remain. Cancel-on-leave exists in a branch but is not the live 209 agentic checkout.

**Solution:**

- Keep Redis lock.
- Heartbeat/extend lock while the turn runs; release in `finally` (already present) **and** on worker SIGTERM.
- Product: abort fetch + `POST /api/rag/chat/cancel` on tab hide / thread switch so the next message is not 429 for 10 minutes.
- Do not save a cancelled turn.

**Files:** `app/utils/chat_lock.py`, `app/routes/rag_routes.py`, `app/utils/rag_service.py`, teacher dashboard JS.

**Acceptance:** Leave tab mid-lecture → Send is usable within 2s on return; no duplicate lesson saved.

---

### Task 11 — Observability for concurrency (so the next incident is measurable)

**Problem:** Capacity is guessed from `docker stats` and logs. No SLO dashboard for in-flight chats, pool checkout time, Groq 429s, Gunicorn busy threads.

**Solution:**

- Export: Gunicorn busy threads, Redis lock count (`chat_lock:*`), PG `pg_stat_activity`, Groq 429 counter, chat p50/p95 duration, ingest queue depth.
- Alert: available RAM < 800 MiB; PG connections > 70; Redis evicted_keys > 0.

**Files:** admin telemetry (extend), optional `/health/ready` that fails when pool or RAM is exhausted.

**Acceptance:** One admin view answers “how many RAG turns in flight” without SSH.

---

## 7. Suggested implementation order

| Phase | Tasks | Risk | Expected win on 209 |
|---|---|---|---|
| **A — Stop the bleeding** | 1, 3, 7, 5 (document + 3 workers) | Low | RAM headroom, no lock eviction, consistent load-test flag |
| **B — Long threads survive** | 2, 8, 4 | Medium | Lectures finish; login stays up; fewer Groq 429s |
| **C — Isolate heavy jobs** | 6, 9 | Medium | Ingest does not flatten chat |
| **D — Product + visibility** | 10, 11 | Medium | No stuck Send button; measurable capacity |

Do not start with “more workers” or “higher Celery concurrency.” That is the opposite of what this host needs.

---

## 8. Out of scope (later / larger infra)

- Horizontal scale (second `flask_app` replica, managed Redis, managed Postgres).
- Moving RAG chat off Gunicorn onto Celery (changes UX unless you add polling/SSE).
- GPU embeddings / remote vLLM as the primary latency lever.
- Replacing Milvus standalone on the same 8 GB box.

Those are the right moves **after** Phase A–C on this machine, or on a production host with ≥16 GB RAM and ≥8 vCPU.

---

## 9. File map (quick)

| Area | Primary files |
|---|---|
| Gunicorn | `server_files/Dockerfile`, `Dockerfile` |
| Compose / Redis / Celery | `server_files/docker-compose.yml` |
| nginx | `server_files/nginx.conf`, `server_files/Dockerfile.nginx` |
| DB pool | `app/config.py`, `app/utils/db.py` |
| Chat lock | `app/utils/chat_lock.py`, `app/routes/rag_routes.py` |
| LLM cap / embed parallel | `app/utils/rag_service.py`, `app/utils/llm_factory.py` |
| Celery reliability | `app/celery_app.py`, `app/tasks/ingest_tasks.py` |
| Whisper / STT | `app/routes/chat.py` |
| Lesson graph startup | `app/__init__.py`, `app/services/lesson/lesson_qa_graph.py` |

Live applied copies of Docker/nginx live in `server_files/` (gitignored). Code changes belong in the repo; infra knobs should be promoted from `server_files/` into the tracked deploy path once accepted.
