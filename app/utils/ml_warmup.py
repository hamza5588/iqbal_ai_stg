"""
Eager-load heavy ML models once per OS process (Gunicorn worker / dev Flask / Celery).

Gunicorn uses multiple worker processes; each has its own memory, so each worker
runs this once at import time when using run:app. That removes the "first request
loads SentenceTransformer / Whisper" delay from user-visible latency (at the cost
of slower container start and higher baseline RAM).

Environment:
  WARMUP_ML_ON_START       — for HTTP workers (run.py): default true. Set false to skip.
  WARMUP_WHISPER           — when WARMUP_ML_ON_START: load faster-whisper (default true).
  WARMUP_RAG_EMBEDDINGS    — when WARMUP_ML_ON_START: load MiniLM via rag_service (default true).
  WARMUP_PIPER             — preload Piper TTS for listed langs (default false; set true to preload en).
  WARMUP_PIPER_LANGS       — comma-separated langs for Piper, default "en" (e.g. "en,ur").
  WARMUP_CELERY_EMBEDDINGS — for Celery workers: preload RAG embeddings (default true).
  SKIP_EXTRA_STARTUP       — if true, all warmups here are skipped (matches create_app).
"""
from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _skip_all() -> bool:
    return str(os.getenv("SKIP_EXTRA_STARTUP", "false")).lower() in ("1", "true", "yes")


def warmup_http_worker_models() -> None:
    """Call from run.py after create_app() — Gunicorn and `python run.py` web process."""
    if _skip_all():
        logger.info("ML warmup skipped (SKIP_EXTRA_STARTUP=true)")
        return
    if not _truthy("WARMUP_ML_ON_START", "true"):
        logger.info("ML warmup skipped (WARMUP_ML_ON_START=false)")
        return

    if _truthy("WARMUP_WHISPER", "true"):
        try:
            from app.utils.faster_whisper_engine import warmup_whisper_model

            ok = warmup_whisper_model()
            if ok:
                logger.info("HTTP worker ML warmup: faster-whisper ready")
            else:
                logger.warning("HTTP worker ML warmup: faster-whisper not available (see earlier logs)")
        except Exception as exc:
            logger.warning("HTTP worker ML warmup: faster-whisper failed: %s", exc)

    if _truthy("WARMUP_RAG_EMBEDDINGS", "true"):
        try:
            from app.utils.rag_service import warmup_rag_embeddings

            if warmup_rag_embeddings():
                logger.info("HTTP worker ML warmup: RAG embeddings ready")
        except Exception as exc:
            logger.warning("HTTP worker ML warmup: RAG embeddings failed: %s", exc)

    if _truthy("WARMUP_PIPER", "false"):
        try:
            from app.services.voice_service import warmup_piper_voice_languages

            raw = (os.getenv("WARMUP_PIPER_LANGS") or "en").strip()
            langs: List[str] = [x.strip().lower() for x in raw.split(",") if x.strip()]
            if not langs:
                langs = ["en"]
            warmup_piper_voice_languages(langs)
            logger.info("HTTP worker ML warmup: Piper preload attempted for %s", langs)
        except Exception as exc:
            logger.warning("HTTP worker ML warmup: Piper preload failed: %s", exc)


def warmup_celery_embeddings_only() -> None:
    """Call from celery_worker_entry after create_app() — avoids loading Whisper in Celery."""
    if _skip_all():
        return
    if not _truthy("WARMUP_CELERY_EMBEDDINGS", "true"):
        logger.info("Celery ML warmup skipped (WARMUP_CELERY_EMBEDDINGS=false)")
        return
    try:
        from app.utils.rag_service import warmup_rag_embeddings

        if warmup_rag_embeddings():
            logger.info("Celery worker ML warmup: RAG embeddings ready")
    except Exception as exc:
        logger.warning("Celery worker ML warmup: RAG embeddings failed: %s", exc)
