"""Question set for a diagnostic retake.

DIL feedback: "on retake the questions should be different but equivalent."

Architecture: an AI-built pool of equivalent variant questions (same
concept, new numbers/wording) is kept per original question in the
assessment's ``description`` meta as ``variant_pool: {orig_qid: [var_qid,
...]}``. Each retake draws one entry per slot; the whole question order is
also shuffled. Building the pool is bounded (rate-limited, resumable) and
happens off the request path - a retake never waits on it, it just falls
back to the original question for any slot that has no ready variant yet.

This module (7a) provides the selection + ordering; variant generation and
validation live in ``_generate_variants`` (7b).
"""
from __future__ import annotations

import json
import logging
import random
from typing import Dict, List

from app.models.lms_models import Assessment
from app.services.lms.assessment_service import get_assessment
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _parse_meta(assessment: Assessment) -> dict:
    if not assessment or not assessment.description:
        return {}
    try:
        meta = json.loads(assessment.description)
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _save_meta(assessment: Assessment, meta: dict) -> None:
    assessment.description = json.dumps(meta, ensure_ascii=False)
    get_db().commit()


def get_variant_pool(assessment_id: int) -> Dict[str, List[int]]:
    assessment = get_assessment(assessment_id)
    pool = _parse_meta(assessment).get("variant_pool") or {}
    return pool if isinstance(pool, dict) else {}


def _original_question_ids(assessment: Assessment) -> List[int]:
    return [aq.question_id for aq in sorted(assessment.questions, key=lambda x: x.sort_order)]


def select_and_shuffle(
    originals: List[int],
    pool: Dict[str, List[int]],
    seed: str,
    attempt_number: int,
) -> List[int]:
    """Pure selection: for each original slot pick variant
    ``(N-2) % len(variants)`` if the slot has ready variants, else keep the
    original; then shuffle with a deterministic per-attempt seed."""
    if attempt_number <= 1:
        return list(originals)
    pick_idx = attempt_number - 2
    chosen: List[int] = []
    for oid in originals:
        variants = pool.get(str(oid)) or []
        chosen.append(int(variants[pick_idx % len(variants)]) if variants else oid)
    random.Random(seed).shuffle(chosen)
    return chosen


def question_set_for_attempt(assessment_id: int, attempt_number: int) -> List[int]:
    """Ordered question ids for attempt N (1 = original)."""
    assessment = get_assessment(assessment_id)
    originals = _original_question_ids(assessment)
    if attempt_number <= 1:
        return originals
    return select_and_shuffle(
        originals,
        get_variant_pool(assessment_id),
        f"{assessment_id}:{attempt_number}",
        attempt_number,
    )


# --- pool building (7b wires the LLM into _generate_variants) --------------

def enqueue_pool_prewarm(assessment_id: int) -> None:
    """Best-effort background build of the variant pool. 7a: no-op
    placeholder; 7b enqueues a Celery task that calls ensure_variant_pool."""
    logger.debug("variant pool prewarm requested for assessment %s", assessment_id)


def ensure_variant_pool(assessment_id: int, target_per_question: int = 2) -> dict:
    """Fill the variant pool up to ``target_per_question`` per original.
    7b implements _generate_variants; until then this is a safe no-op that
    reports the current pool size."""
    pool = get_variant_pool(assessment_id)
    have = sum(len(v) for v in pool.values())
    return {"assessment_id": assessment_id, "variants": have, "generated": 0}
