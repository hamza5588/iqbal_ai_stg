# Celery Implementation Testing Guide

## Local vs production (config-driven)

PDF ingestion can run **with or without Celery**:

- **Local / no Redis:** Set `USE_CELERY_FOR_INGESTION=false` (default) in config or env. Ingestion runs **synchronously in-process**; no Redis or Celery worker needed. Use this when you don’t have enough resources to run a Celery worker.
- **Production:** Set `USE_CELERY_FOR_INGESTION=true` (e.g. in `.env` or environment). Ingestion runs as **Celery tasks** in the background; you need Redis and a Celery worker.

Config is in `app/config.py`; override with env var: `USE_CELERY_FOR_INGESTION=true` or `USE_CELERY_FOR_INGESTION=false`.

## Prerequisites (for Celery / production mode)

1. **Install Dependencies**
   ```bash
   pip install celery redis
   ```

2. **Start Redis Server**
   - **Windows**: 
     - If Redis is installed, run: `redis-server`
     - Or use WSL: `wsl redis-server`
     - Or use Docker: `docker run -d -p 6379:6379 redis`
   
   - **Linux/Mac**: 
     ```bash
     redis-server
     ```
   
   - Verify Redis is running:
     ```bash
     redis-cli ping
     # Should return: PONG
     ```

## Starting the Application

### Step 1: Start Flask Application
```bash
# In terminal 1
python run.py
# or
flask run
```

### Step 2: Start Celery Worker
```bash
# In terminal 2 (new terminal) - use celery_worker_entry so tasks run with Flask app context
celery -A app.celery_worker_entry.celery worker --loglevel=info
```

**Expected Output:**
```
[INFO/MainProcess] Connected to redis://localhost:6379/0
[INFO/MainProcess] celery@hostname ready.
```

## Testing the Implementation

### Test 1: Upload PDF (Non-blocking)

**Request:**
```bash
curl -X POST http://localhost:5000/api/rag/ingest \
  -H "Cookie: session=YOUR_SESSION_COOKIE" \
  -F "file=@test.pdf" \
  -F "conversation_id=123"
```

**Expected Response (Immediate):**
```json
{
  "success": true,
  "message": "PDF ingestion started in background",
  "task_id": "abc123-def456-ghi789",
  "status": "processing",
  "thread_id": "user_1_conv_123",
  "conversation_id": 123,
  "filename": "test.pdf"
}
```

**Key Points:**
- ✅ Response is **immediate** (doesn't wait for processing)
- ✅ Returns `task_id` for status checking
- ✅ Flask app remains responsive to other requests

### Test 2: Check Task Status

**Request:**
```bash
curl -X GET http://localhost:5000/api/rag/ingest/status/abc123-def456-ghi789 \
  -H "Cookie: session=YOUR_SESSION_COOKIE"
```

**Response (While Processing):**
```json
{
  "task_id": "abc123-def456-ghi789",
  "status": "processing",
  "state": "PROCESSING",
  "step": "splitting",
  "progress": 50,
  "message": "Created 100 text chunks from 10 pages"
}
```

**Response (When Complete):**
```json
{
  "task_id": "abc123-def456-ghi789",
  "status": "success",
  "state": "SUCCESS",
  "message": "PDF ingested successfully",
  "thread_id": "user_1_conv_123",
  "conversation_id": 123,
  "filename": "test.pdf",
  "documents": 10,
  "num_pages": 10,
  "pages": 10,
  "chunks": 100
}
```

**Response (If Failed):**
```json
{
  "task_id": "abc123-def456-ghi789",
  "status": "failure",
  "state": "FAILURE",
  "error": "Failed to load PDF: File is corrupted",
  "message": "PDF ingestion failed: Failed to load PDF: File is corrupted"
}
```

### Test 3: Verify Non-blocking Behavior

1. **Start a long PDF upload** (large file)
2. **Immediately make another request** to a different endpoint
3. **Verify** the second request responds immediately (not blocked)

**Example:**
```bash
# Terminal 1: Upload large PDF
curl -X POST http://localhost:5000/api/rag/ingest \
  -F "file=@large_file.pdf"

# Terminal 2: Immediately test another endpoint (should respond instantly)
curl -X GET http://localhost:5000/api/rag/threads
```

### Test 4: Check Celery Worker Logs

Watch the Celery worker terminal for:
- Task received messages
- Progress updates
- Completion or error messages

**Expected Log Output:**
```
[INFO/MainProcess] Task app.tasks.ingest_tasks.ingest_pdf_task[abc123...] received
[INFO/ForkPoolWorker-1] Starting PDF ingestion...
[INFO/ForkPoolWorker-1] Created new thread user_1_conv_123 for user 1
[INFO/ForkPoolWorker-1] Task app.tasks.ingest_tasks.ingest_pdf_task[abc123...] succeeded
```

## Verification Checklist

- [ ] Redis is running (`redis-cli ping` returns PONG)
- [ ] Celery worker is running and connected to Redis
- [ ] Flask app starts without errors
- [ ] `/ingest` endpoint returns immediately with `task_id`
- [ ] `/ingest/status/<task_id>` shows progress updates
- [ ] Task completes successfully and returns full result
- [ ] Other endpoints remain responsive during ingestion
- [ ] Database thread is saved correctly
- [ ] Security validation works (can't access other users' tasks)

## Troubleshooting

### Issue: "Connection refused" to Redis
**Solution:** Make sure Redis is running on port 6379

### Issue: "No module named 'celery'"
**Solution:** Install dependencies: `pip install celery redis`

### Issue: Task stays in PENDING state
**Solution:** 
- Check Celery worker is running
- Check worker logs for errors
- Verify Redis connection

### Issue: "Working outside of application context" or database errors in Celery task
**Solution:** 
- Use `celery -A app.celery_worker_entry.celery worker` (not `app.celery_app.celery`) so tasks run with Flask app context
- Set `USE_CELERY_FOR_INGESTION=true` so init_celery runs and ContextTask is used

### Issue: Task completes but no database record
**Solution:**
- Check Celery worker logs for database errors
- Verify `_save_thread_to_db()` is being called
- Check database permissions

## Monitoring Celery

### Check Active Tasks
```bash
celery -A app.celery_worker_entry.celery inspect active
```

### Check Registered Tasks
```bash
celery -A app.celery_worker_entry.celery inspect registered
```

### Check Worker Stats
```bash
celery -A app.celery_worker_entry.celery inspect stats
```

## Using Flower (Optional - Celery Monitoring Tool)

Install Flower:
```bash
pip install flower
```

Start Flower:
```bash
celery -A app.celery_worker_entry.celery flower
```

Access at: `http://localhost:5555`

## Frontend Integration

Your frontend should:

1. **Upload PDF** → Get `task_id` immediately
2. **Poll status endpoint** every 2-3 seconds:
   ```javascript
   const pollStatus = async (taskId) => {
     const response = await fetch(`/api/rag/ingest/status/${taskId}`);
     const data = await response.json();
     
     if (data.status === 'processing') {
       // Show progress: data.progress, data.message
       setTimeout(() => pollStatus(taskId), 2000);
     } else if (data.status === 'success') {
       // Show success, use data.thread_id, data.conversation_id
     } else if (data.status === 'failure') {
       // Show error: data.error
     }
   };
   ```

## Performance Testing

Test with multiple concurrent uploads:
```bash
# Upload 5 PDFs simultaneously
for i in {1..5}; do
  curl -X POST http://localhost:5000/api/rag/ingest \
    -F "file=@test$i.pdf" &
done
```

All should return immediately with different `task_id`s, and Celery worker will process them in parallel (depending on worker concurrency settings).
