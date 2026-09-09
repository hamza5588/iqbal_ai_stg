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


def enqueue_deficiency_chat_prewarm(student_id: int) -> None:
    """
    Enqueue the Learning Chat prewarm on Celery when async is enabled; otherwise
    run it in-process (local dev / no broker). All failures are swallowed — the
    on-demand path in ``deficiency_chat_service.start_session`` remains the
    fallback.
    """
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
    """Fire-and-forget top-up of the diagnostic variant pool. Never runs
    inline on a request thread - variant generation is slow and a retake
    falls back to the original question for any slot without a variant."""
    if not _celery_async_enabled():
        logger.debug("Celery async off - skipping variant pool prewarm for %s", assessment_id)
        return
    try:
        variant_pool_prewarm_task.delay(assessment_id=assessment_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue variant pool prewarm for %s: %s", assessment_id, exc)
