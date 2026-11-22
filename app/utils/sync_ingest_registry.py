"""
In-process background PDF ingest jobs when Celery is disabled (local dev / QA).

Large uploads return immediately with a task_id; the UI polls
GET /api/rag/ingest/status/<task_id> the same way as Celery-backed ingest.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class SyncIngestJob:
    task_id: str
    user_id: int
    thread_id: str
    conversation_id: Optional[int]
    filename: str
    state: str = "PENDING"
    step: str = "init"
    progress: int = 5
    message: str = "Starting PDF ingestion..."
    result: Optional[dict] = None
    error: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_processing(self, step: str, progress: int, message: str) -> None:
        with self._lock:
            self.state = "PROCESSING"
            self.step = step
            self.progress = max(0, min(100, int(progress)))
            self.message = message or self.message

    def set_success(self, result: dict) -> None:
        with self._lock:
            self.state = "SUCCESS"
            self.progress = 100
            self.step = "complete"
            self.message = "PDF ingestion complete"
            self.result = dict(result)
            self.error = None

    def set_failure(self, error: str) -> None:
        with self._lock:
            self.state = "FAILURE"
            self.error = error
            self.message = error

    def status_response(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "PENDING":
                return {
                    "task_id": self.task_id,
                    "status": "pending",
                    "state": self.state,
                    "message": self.message,
                    "thread_id": self.thread_id,
                    "conversation_id": self.conversation_id,
                }
            if self.state == "PROCESSING":
                return {
                    "task_id": self.task_id,
                    "status": "processing",
                    "state": self.state,
                    "step": self.step,
                    "progress": self.progress,
                    "message": self.message,
                    "thread_id": self.thread_id,
                    "conversation_id": self.conversation_id,
                }
            if self.state == "SUCCESS":
                res = dict(self.result or {})
                return {
                    "task_id": self.task_id,
                    "status": "success",
                    "state": "SUCCESS",
                    "thread_id": res.get("thread_id") or self.thread_id,
                    "conversation_id": self.conversation_id,
                    "filename": res.get("filename", self.filename),
                    "documents": res.get("documents", res.get("num_pages", 0)),
                    "num_pages": res.get("num_pages", res.get("documents", 0)),
                    "pages": res.get("pages", res.get("num_pages", 0)),
                    "chunks": res.get("chunks", 0),
                    "markdown_download_url": res.get(
                        "markdown_download_url", f"/api/rag/download-markdown/{self.thread_id}"
                    ),
                    "processing_time_seconds": res.get("processing_time_seconds"),
                    "warning": res.get("warning"),
                    "message": res.get("message", "PDF ingested successfully"),
                }
            return {
                "task_id": self.task_id,
                "status": "failure",
                "state": "FAILURE",
                "error": self.error or "PDF processing failed",
                "message": self.error or "PDF processing failed",
                "thread_id": self.thread_id,
                "conversation_id": self.conversation_id,
            }


_jobs: dict[str, SyncIngestJob] = {}
_registry_lock = threading.Lock()


def create_sync_ingest_job(
    user_id: int,
    thread_id: str,
    filename: str,
    conversation_id: Optional[int] = None,
) -> SyncIngestJob:
    task_id = f"sync-{uuid.uuid4().hex}"
    job = SyncIngestJob(
        task_id=task_id,
        user_id=user_id,
        thread_id=thread_id,
        conversation_id=conversation_id,
        filename=filename,
    )
    with _registry_lock:
        _jobs[task_id] = job
    return job


def get_sync_ingest_job(task_id: str, user_id: int) -> Optional[SyncIngestJob]:
    with _registry_lock:
        job = _jobs.get(task_id)
    if not job or job.user_id != user_id:
        return None
    return job


def start_sync_ingest_worker(
    app,
    job: SyncIngestJob,
    file_bytes: bytes,
    run_ingest: Callable[..., dict],
    on_success: Callable[[dict], None],
    on_failure: Callable[[str], None],
) -> None:
    """Run ingest_pdf in a daemon thread with Flask app context and progress updates."""

    def progress_callback(step: str, progress: int, message: str) -> None:
        job.set_processing(step, progress, message)

    def _run() -> None:
        job.set_processing("init", 5, "Starting PDF ingestion...")
        with app.app_context():
            try:
                result = run_ingest(
                    file_bytes=file_bytes,
                    thread_id=job.thread_id,
                    filename=job.filename,
                    progress_callback=progress_callback,
                    user_id=job.user_id,
                )
                if "thread_id" not in result:
                    result["thread_id"] = job.thread_id
                on_success(result)
                job.set_success(result)
            except Exception as exc:
                msg = str(exc)
                on_failure(msg)
                job.set_failure(msg)

    threading.Thread(target=_run, daemon=True).start()