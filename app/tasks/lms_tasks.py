"""Celery tasks for LMS post-diagnostic background work."""
from __future__ import annotations

import logging

from app.celery_app import celery

logger = logging.getLogger(__name__)


def _celery_async_enabled() -> bool:
    """True only when USE_CELERY_FOR_INGESTION is set (production/staging)."""
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return bool(current_app.config.get("USE_CELERY_FOR_INGESTION", False))
    except Exception:
        pass
    from app.config import Config

    return bool(Config.USE_CELERY_FOR_INGESTION)


def _broker_reachable(timeout: float = 1.0) -> bool:
    """Fast Redis ping so enqueue does not hang the request when the broker is down (QA A2)."""
    try:
        from flask import current_app, has_app_context
        from urllib.parse import urlparse
        import redis

        if has_app_context():
            url = current_app.config.get("CELERY_BROKER_URL") or ""
        else:
            from app.config import Config

            url = Config.CELERY_BROKER_URL or ""
        if not url.startswith("redis"):
            return True
        parsed = urlparse(url)
        client = redis.Redis(
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port or 6379,
            db=int((parsed.path or "/0").lstrip("/") or 0),
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        client.ping()
        return True
    except Exception:
        return False


@celery.task(
    bind=True,
    name="app.tasks.lms_tasks.prewarm_deficiency_chat_task",
    queue="default",
    max_retries=0,
)
def prewarm_deficiency_chat_task(self, student_id: int) -> dict:
    """
    Build the Learning Chat question queue for a student who just finished their
    diagnostic. Runs on the worker (off the request thread) so live Groq MCQ
    generation is spread out and rate-limited instead of bursting when every
    student opens Learning Chat at once.
    """
    from app.services.lms import deficiency_chat_service
    from app.utils.llm_gateway import llm_workflow

    try:
        # This runs on the Celery worker, off the request thread - the
        # llm_workflow set in the /deficiency/sessions route (same MCQ
        # generation, run inline instead) never reaches this process, so it
        # was logging as workflow="unknown" without its own tag.
        with llm_workflow(
            "lms_deficiency_chat_mcq_generation",
            user_id=student_id,
            user_role="student",
            traffic_source="production",
        ):
            warmed = deficiency_chat_service.prewarm_session(student_id)
        return {"student_id": student_id, "warmed": bool(warmed)}
    except Exception as exc:  # noqa: BLE001 — best-effort, never surface
        logger.warning("prewarm_deficiency_chat_task failed for student %s: %s", student_id, exc)
        return {"student_id": student_id, "warmed": False, "error": str(exc)}


def enqueue_deficiency_chat_prewarm(student_id: int, *, allow_inline: bool = True) -> None:
    """
    Enqueue the Learning Chat prewarm on Celery when async is enabled; otherwise
    run it in-process (local dev / no broker) unless ``allow_inline`` is False.
    All failures are swallowed — the on-demand path in
    ``deficiency_chat_service.start_session`` remains the fallback.

    Follow-up quizzes pass ``allow_inline=False``: without a worker the weekly
    quiz submit must not wait on live Groq MCQ generation for every student.
    """
    if not _celery_async_enabled() and not allow_inline:
        return
    if _celery_async_enabled():
        try:
            prewarm_deficiency_chat_task.delay(student_id=student_id)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Celery unavailable for Learning Chat prewarm (student %s), running inline: %s",
                student_id, exc,
            )

    from app.services.lms import deficiency_chat_service

    try:
        deficiency_chat_service.prewarm_session(student_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Inline Learning Chat prewarm failed for student %s: %s", student_id, exc)


@celery.task(
    bind=True,
    name="app.tasks.lms_tasks.variant_pool_prewarm_task",
    queue="default",
    max_retries=0,
)
def variant_pool_prewarm_task(self, assessment_id: int) -> dict:
    """Top up the diagnostic's equivalent-variant pool a few at a time,
    off the request thread and under the Groq rate limiter."""
    from app.services.lms import diagnostic_variant_service
    from app.utils.llm_gateway import llm_workflow

    try:
        with llm_workflow("diagnostic_variant_generation", traffic_source="production"):
            result = diagnostic_variant_service.ensure_variant_pool(assessment_id)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("variant_pool_prewarm_task failed for assessment %s: %s", assessment_id, exc)
        return {"assessment_id": assessment_id, "generated": 0, "error": str(exc)}


def enqueue_variant_pool_prewarm(assessment_id: int) -> None:
    """Fire-and-forget top-up of the diagnostic variant pool.

    Prefer Celery; if async is off, run a small inline top-up so retakes still
    have something ready (full fill happens on the retake start path).
    """
    if not _celery_async_enabled():
        try:
            from app.services.lms import diagnostic_variant_service

            diagnostic_variant_service.ensure_variant_pool(assessment_id, max_generate=4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Inline variant pool prewarm failed for %s: %s", assessment_id, exc)
        return
    try:
        variant_pool_prewarm_task.delay(assessment_id=assessment_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue variant pool prewarm for %s: %s", assessment_id, exc)


# -- AI Guided Practice -------------------------------------------------------


def _run_in_background_thread(name: str, fn, *args) -> None:
    """No Celery worker: run ``fn`` on a daemon thread with its own app context + DB session."""
    import threading

    from flask import current_app, has_app_context

    if not has_app_context():
        fn(*args)
        return
    app_obj = current_app._get_current_object()

    def _target():
        with app_obj.app_context():
            try:
                from app.utils.db import get_session_factory

                get_session_factory().remove()
            except Exception:  # noqa: BLE001
                pass
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Background %s failed: %s", name, exc)

    threading.Thread(target=_target, name=name, daemon=True).start()


def _aigp_process(version_id: int) -> None:
    from app.services.lms import ai_guided_practice_service
    from app.utils.llm_gateway import llm_workflow

    with llm_workflow("ai_guided_practice_topic_extraction", traffic_source="production"):
        ai_guided_practice_service.process_version(version_id)


def _aigp_topup(subtopic_id: int, target_size: int) -> None:
    from app.services.lms import ai_guided_practice_service
    from app.utils.llm_gateway import llm_workflow

    with llm_workflow("ai_guided_practice_question_generation", traffic_source="production"):
        ai_guided_practice_service.ensure_pool(subtopic_id, target_size)


@celery.task(bind=True, name="app.tasks.lms_tasks.ai_guided_practice_process_task", queue="default", max_retries=0)
def ai_guided_practice_process_task(self, version_id: int) -> dict:
    try:
        _aigp_process(version_id)
        return {"version_id": version_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_guided_practice_process_task failed for %s: %s", version_id, exc)
        return {"version_id": version_id, "error": str(exc)}


@celery.task(bind=True, name="app.tasks.lms_tasks.ai_guided_practice_topup_task", queue="default", max_retries=0)
def ai_guided_practice_topup_task(self, subtopic_id: int, target_size: int) -> dict:
    try:
        _aigp_topup(subtopic_id, target_size)
        return {"subtopic_id": subtopic_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_guided_practice_topup_task failed for %s: %s", subtopic_id, exc)
        return {"subtopic_id": subtopic_id, "error": str(exc)}


def enqueue_ai_guided_practice_processing(version_id: int) -> None:
    """Extract a Target PDF's topic hierarchy off the request thread."""
    if _celery_async_enabled() and _broker_reachable():
        try:
            ai_guided_practice_process_task.delay(version_id=version_id)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery unavailable for AI Guided Practice processing %s: %s", version_id, exc)
    elif _celery_async_enabled():
        logger.warning("Celery broker unreachable; AI Guided Practice processing %s via thread", version_id)
    _run_in_background_thread(f"aigp-process-{version_id}", _aigp_process, version_id)


def enqueue_ai_guided_practice_topup(subtopic_id: int, target_size: int) -> None:
    """Pre-generate the next set of questions so the student's next attempt is instant."""
    if _celery_async_enabled() and _broker_reachable():
        try:
            ai_guided_practice_topup_task.delay(subtopic_id=subtopic_id, target_size=target_size)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery unavailable for AI Guided Practice top-up %s: %s", subtopic_id, exc)
    elif _celery_async_enabled():
        logger.warning("Celery broker unreachable; AI Guided Practice top-up %s via thread", subtopic_id)
    _run_in_background_thread(f"aigp-topup-{subtopic_id}", _aigp_topup, subtopic_id, target_size)
