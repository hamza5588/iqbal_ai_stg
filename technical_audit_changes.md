## IqbalAI Technical Audit Fixes

This document summarizes the key fixes applied to the IqbalAI staging environment based on the technical audit.  
For each issue you will find:

- **Issue description (from audit)**
- **File(s) changed**
- **Before code**
- **After code**

You can export this Markdown file to PDF from your editor or browser print dialog.

---

## Issue 1 — Nginx HTTPS Proxy Timeouts & Rate Limiting

- **Files**: `new_ngnix.conf`

**Audit summary**  
Outer nginx layer had 60-second proxy timeouts and no real rate limiting; long RAG and ingest requests were being cut off at 60s with 504, and there was no throttling on real traffic.

### 1.1 Rate-limit IP Whitelist

**Before**

```nginx
geo $rate_limit_key {
    default                $binary_remote_addr;
    127.0.0.1              "";   # localhost
    YOUR_LOAD_TEST_IP      "";   # ← replace with your load test server IP
}
```

**After**

```nginx
geo $rate_limit_key {
    default                $binary_remote_addr;
    127.0.0.1              "";   # localhost
    104.219.55.160         "";   # staging load-test server IP (whitelisted)
}
```

**Notes**

- `104.219.55.160` is the staging host for `staging.iqbalai.com` and is now exempt from rate limiting.
- The HTTPS server block already uses 1800s timeouts and limit_req as per the shared `new_ngnix.conf`.

---

## Issue 2 — Per-User Chat Lock “Zombie Lock” After 504

- **File**: `app/routes/rag_routes.py`

**Audit summary**  
Per-user chat locks used `acquire(blocking=False)`. If nginx killed a slow response (504), the Flask worker continued processing while still holding the lock. Any subsequent request from that user hit an instant 429 until the first request finished.

### 2.1 Per-User Lock Acquisition

**Before**

```python
# One message at a time per user: queue by rejecting concurrent requests
user_lock = _get_user_chat_lock(user_id)
if not user_lock.acquire(blocking=False):
    return jsonify({
        'error': 'Please wait for your current message to be answered before sending another.',
        'code': 'CONCURRENT_REQUEST'
    }), 429
```

**After**

```python
# One message at a time per user: enforce a per-user lock with a bounded wait.
# This prevents "zombie" locks from immediately causing 429s after upstream timeouts,
# while still guaranteeing only a single in-flight chat per user.
user_lock = _get_user_chat_lock(user_id)
acquired = user_lock.acquire(blocking=True, timeout=120)
if not acquired:
    return jsonify({
        'error': 'Your previous message is still being processed. Please try again in a moment.',
        'code': 'CONCURRENT_REQUEST_TIMEOUT'
    }), 429
```

**Impact**

- Second request will **wait up to 120 seconds** before giving a 429.
- Eliminates the “instant 429 for 90 seconds after a 504” behavior while still enforcing one in-flight chat per user.

---

## Issue 3 — `create_app()` Called Inside Every Celery Task

- **Files**:
  - `app/tasks/ingest_tasks.py`
  - `app/tasks/load_test_tasks.py`
  - `app/celery_app.py` (ContextTask is already used here)

**Audit summary**  
Each Celery task (`ingest_pdf_task`, `run_load_test_task`, `generate_analysis_task`) created its own Flask app and app context. That duplicated work (DB init, Milvus health checks) and created independent SQLAlchemy engines per task, competing on Postgres and Milvus.

### 3.1 Ingest Task — Remove `create_app()` and Use ContextTask

**Before** (`app/tasks/ingest_tasks.py`)

```python
@celery.task(bind=True, name='app.tasks.ingest_tasks.ingest_pdf_task')
def ingest_pdf_task(self, file_bytes_b64: str, thread_id: str, filename: str,
                    user_id: int, conversation_id: int = None):
    """
    Celery task to ingest a PDF document in the background.
    Runs inside a Flask application context so get_db() and current_app work.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        try:
            return _run_ingest_in_context(self, file_bytes_b64, thread_id, filename, user_id, conversation_id)
        ...
```

**After**

```python
@celery.task(bind=True, name='app.tasks.ingest_tasks.ingest_pdf_task')
def ingest_pdf_task(self, file_path: str, thread_id: str, filename: str,
                    user_id: int, conversation_id: int = None):
    """
    Celery task to ingest a PDF document in the background.
    Runs inside a Flask application context via Celery's ContextTask wrapper.
    """
    try:
        return _run_ingest_in_context(self, file_path, thread_id, filename, user_id, conversation_id)
    except ValueError as e:
        ...
```

### 3.2 Load Test & Analysis Tasks — Remove `create_app()`

**Before** (`app/tasks/load_test_tasks.py`)

```python
@celery.task(bind=True, name='app.tasks.load_test_tasks.run_load_test_task')
def run_load_test_task(self, config_data: dict, result_id: int):
    """
    Celery task to run a load test scenario.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        ...
        runner = LoadTestRunner(app, config, result_id)
        ...
```

**After**

```python
@celery.task(bind=True, name='app.tasks.load_test_tasks.run_load_test_task')
def run_load_test_task(self, config_data: dict, result_id: int):
    """
    Celery task to run a load test scenario.
    Runs inside a Flask application context via Celery's ContextTask wrapper.
    """
    try:
        config = LoadTestConfig(...)
        # Celery's ContextTask ensures 'current_app' is available internally.
        runner = LoadTestRunner(None, config, result_id)
        import asyncio
        asyncio.run(runner.run())
        return {'success': True, 'result_id': result_id}
    except Exception as e:
        ...
```

Similar simplification is applied to `generate_analysis_task` (no more `create_app()`).

---

## Issue 4 — PostgreSQL Connection Pool Will Exhaust Under Load

- **File**: `app/config.py`

**Audit summary**  
`pool_size=5`, `max_overflow=5` across multiple processes would exceed the default Postgres 100 connections under high concurrency.

### 4.1 SQLAlchemy Engine Options

**Before**

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_pre_ping': True,  # Verify connections before using
    'pool_recycle': 300,    # Recycle connections after 5 minutes
    'echo': False           # Set to True for SQL query logging
}
```

**After**

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_pre_ping': True,   # Verify connections before using
    'pool_recycle': 300,     # Recycle connections after 5 minutes
    'echo': False,           # Set to True for SQL query logging
    'pool_size': 3,          # Bounded core pool size (Issue 4)
    'max_overflow': 3,       # Bounded overflow per process
}
```

**Note**  
You still need to run `ALTER SYSTEM SET max_connections = 200;` on the Postgres server as per the audit.

---

## Issue 5 — Base64 PDF Bytes in Celery Payloads (Redis Bloat)

- **Files**:
  - `app/routes/rag_routes.py`
  - `app/tasks/ingest_tasks.py`

**Audit summary**  
Large PDFs were base64-encoded and sent as Celery arguments, causing big Redis payloads and memory usage under concurrency.

### 5.1 RAG Ingest Route — Temp File Instead of Base64

**Before** (`app/routes/rag_routes.py`)

```python
# Use Celery for background processing (production)
file_bytes_b64 = base64.b64encode(file_bytes).decode('utf-8')
task = ingest_pdf_task.delay(
    file_bytes_b64=file_bytes_b64,
    thread_id=thread_id,
    filename=filename,
    user_id=user_id,
    conversation_id=conversation_id
)
```

**After**

```python
# Use Celery for background processing (production).
# To keep Redis payloads small and avoid broker memory pressure under load,
# we write the upload to a temporary file and pass only the file path.
tmp_dir = current_app.config.get("UPLOAD_TEMP_DIR", None)
tmp_kwargs = {"delete": False, "suffix": ".pdf"}
if tmp_dir:
    tmp_kwargs["dir"] = tmp_dir
with tempfile.NamedTemporaryFile(**tmp_kwargs) as tmp:
    tmp.write(file_bytes)
    tmp.flush()
    tmp_path = tmp.name

task = ingest_pdf_task.delay(
    file_path=tmp_path,
    thread_id=thread_id,
    filename=filename,
    user_id=user_id,
    conversation_id=conversation_id
)
```

### 5.2 Celery Task — Read and Unlink Temp File

**Before** (`app/tasks/ingest_tasks.py`)

```python
def _run_ingest_in_context(self, file_bytes_b64: str, ...):
    self.update_state(...)
    file_bytes = base64.b64decode(file_bytes_b64)
    ...
```

**After**

```python
def _run_ingest_in_context(self, file_path: str, thread_id: str, filename: str,
                           user_id: int, conversation_id: int = None):
    """
    Caller passes a temporary file path; this function is responsible for
    reading the bytes and unlinking the file immediately afterwards.
    """
    self.update_state(...)

    try:
        with open(file_path, 'rb') as f:
            file_bytes = f.read()
    finally:
        try:
            os.unlink(file_path)
        except OSError:
            pass
```

---

## Issue 7 — Celery Ingest Worker Concurrency

- **File**: `docker-compose.yml`

**Audit summary**  
Ingest worker concurrency was 4; with many uploads, queueing delays grew large.

### 7.1 Add Dedicated Ingest Worker with Higher Concurrency

**Before**  
Only a default `celery_worker` service existed (handling all queues), with `--concurrency=2`.

**After**

```yaml
celery_worker:
  ...
  command: celery -A app.celery_worker_entry.celery worker -Q default --loglevel=info --concurrency=4
  deploy:
    resources:
      limits:
        cpus: "4"
        memory: 16G
      reservations:
        cpus: "2"
        memory: 8G

celery_worker_ingest:
  build:
    context: .
    dockerfile: Dockerfile
  environment:
    - USE_CELERY_FOR_INGESTION=true
    - CELERY_BROKER_URL=redis://redis:6379/0
    - CELERY_RESULT_BACKEND=redis://redis:6379/0
    ...
  command: celery -A app.celery_worker_entry.celery worker -Q ingest --loglevel=info --concurrency=8
  deploy:
    resources:
      limits:
        cpus: "6"
        memory: 16G
      reservations:
        cpus: "2"
        memory: 8G
```

---

## Issue 8 — Default Celery Worker Underutilizing CPUs

- **File**: `docker-compose.yml`

**Before**

```yaml
celery_worker:
  ...
  command: celery -A app.celery_worker_entry.celery worker --loglevel=info --concurrency=2
```

**After**

```yaml
celery_worker:
  ...
  command: celery -A app.celery_worker_entry.celery worker -Q default --loglevel=info --concurrency=4
```

This matches the 4-CPU limit for the default queue.

---

## Issue 9 — Celery Reliability & Redis `maxmemory` Policy

- **Files**:
  - `app/celery_app.py`
  - `docker-compose.yml` (Redis service)

**Audit summary**  
Celery lacked key reliability settings, and Redis had no `maxmemory` policy, leading to unbounded result growth.

### 9.1 Celery Reliability Settings

**Before** (`app/celery_app.py`)

```python
celery.conf.update(
    accept_content=Config.CELERY_ACCEPT_CONTENT,
    task_serializer=Config.CELERY_TASK_SERIALIZER,
    result_serializer=Config.CELERY_RESULT_SERIALIZER,
    timezone=Config.CELERY_TIMEZONE,
    task_track_started=Config.CELERY_TASK_TRACK_STARTED,
    task_time_limit=Config.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=Config.CELERY_TASK_SOFT_TIME_LIMIT,
)
```

**After**

```python
celery.conf.update(
    accept_content=Config.CELERY_ACCEPT_CONTENT,
    task_serializer=Config.CELERY_TASK_SERIALIZER,
    result_serializer=Config.CELERY_RESULT_SERIALIZER,
    timezone=Config.CELERY_TIMEZONE,
    task_track_started=Config.CELERY_TASK_TRACK_STARTED,
    task_time_limit=Config.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=Config.CELERY_TASK_SOFT_TIME_LIMIT,
    # Reliability settings (Issue 9)
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    result_expires=3600,
)
```

### 9.2 Redis Max Memory

**Before** (`docker-compose.yml`)

```yaml
redis:
  image: redis:7-alpine
  restart: always
  volumes:
    - redis_data_prod:/data
  command: redis-server --appendonly yes
```

**After**

```yaml
redis:
  image: redis:7-alpine
  restart: always
  volumes:
    - redis_data_prod:/data
  command: redis-server --appendonly yes --maxmemory 2gb --maxmemory-policy allkeys-lru
```

---

## Issue 10 — SQLite LangGraph Checkpointer & History Pruning

- **Files**:
  - `requirements.txt`
  - `app/utils/rag_service.py`

**Audit summary**  
Single SQLite connection as a global checkpointer serialized all chats; conversation history was not pruned, leading to token-limit errors.

### 10.1 Use PostgreSQL Checkpointer in Production

**Before** (`requirements.txt`)

```text
langgraph
langgraph-checkpoint
langgraph-checkpoint-sqlite
langgraph-prebuilt
langgraph-sdk
```

**After**

```text
langgraph
langgraph-checkpoint
langgraph-checkpoint-sqlite
langgraph-checkpoint-postgres
langgraph-prebuilt
langgraph-sdk
```

**Before** (`app/utils/rag_service.py`)

```python
from langgraph.checkpoint.sqlite import SqliteSaver

# -------------------
# 7. Checkpointer
# -------------------
conn = sqlite3.connect(database="chatbot.db", check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)
```

**After**

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.postgres import PostgresSaver

# -------------------
# 7. Checkpointer
# -------------------
_database_url = os.getenv("DATABASE_URL", "")
if _database_url.startswith("postgres"):
    # Use PostgreSQL-backed LangGraph checkpointer in production (Issue 10).
    PostgresSaver.setup(_database_url)
    checkpointer = PostgresSaver.from_conn_string(_database_url)
else:
    # Fallback to SQLite saver for local/development.
    conn = sqlite3.connect(database="chatbot.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn=conn)
```

### 10.2 History Pruning

**New / updated helper** (`app/utils/rag_service.py`)

```python
def _prune_messages(messages, max_turns: int = 15):
    """
    Keep only the system prompt (handled separately) and the last `max_turns`
    user/AI exchanges from the history to avoid unbounded growth and token-limit
    errors on long-running chats.
    """
    ...
```

**Use in chat node**

```python
# Progressive message reduction on token errors
conversation_messages = _prune_messages(state["messages"], max_turns=15)
initial_max_messages = 7  # Start with 7 messages
max_attempts = 4          # Try with 7, 5, 3, 1 messages
```

---

## Issue 11 — Milvus `coll.load()` / `coll.flush()` on Every Call

- **File**: `app/utils/rag_vectorstore_milvus.py`

**Audit summary**  
Repeated `load()` and `flush()` calls on every insert/search/delete caused extra latency and contention.

### 11.1 Insert Chunks

**Before**

```python
coll = Collection(coll_name)
coll.insert(data)
coll.flush()
```

**After**

```python
coll = Collection(coll_name)
coll.insert(data)
logger.info("insert_chunks: inserted %d vectors thread_id=%s user_id=%s", len(vectors), thread_id, user_id)
```

### 11.2 Similarity Search

**Before**

```python
coll_name = _collection_name()
coll = Collection(coll_name)
coll.load()
...
results = coll.search(...)
```

**After**

```python
coll_name = _collection_name()
coll = Collection(coll_name)
safe_tid = str(thread_id).replace('"', '\\"')
expr = f'thread_id == "{safe_tid}" && user_id == {user_id}'
results = coll.search(...)
```

### 11.3 Delete by Thread

**Before**

```python
coll_name = _collection_name()
coll = Collection(coll_name)
coll.load()
...
coll.delete(expr)
coll.flush()
```

**After**

```python
coll_name = _collection_name()
coll = Collection(coll_name)
safe_tid = str(thread_id).replace('"', '\\"')
expr = f'thread_id == "{safe_tid}" && user_id == {user_id}'
coll.delete(expr)
```

---

## Infrastructure — Gunicorn & Service Resource Limits

- **Files**:
  - `Dockerfile`
  - `docker-compose.yml`

### Gunicorn Worker Tuning

**Before** (`Dockerfile`)

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "20", "--threads", "3",
     "--timeout", "1800", "--graceful-timeout", "10", "--keep-alive", "5", "run:app"]
```

**After**

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "9", "--threads", "4",
     "--worker-class", "gthread", "--timeout", "1800", "--graceful-timeout", "30",
     "--keep-alive", "5", "run:app"]
```

### Postgres & Milvus Resource Limits

**Before** (`docker-compose.yml`)

- No `deploy.resources.limits` for Postgres or Milvus.

**After**

```yaml
postgres:
  ...
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 4G

milvus:
  ...
  deploy:
    resources:
      limits:
        cpus: "2"
        memory: 6G
```

---

## How to Export This File to PDF

1. Open `technical_audit_changes.md` in your editor or a Markdown viewer.
2. Use **“Export to PDF”** (if your editor supports it), or
3. Open it in a browser and use **Print → Save as PDF**.

