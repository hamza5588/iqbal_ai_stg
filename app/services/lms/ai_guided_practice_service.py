"""AI Guided Practice - Target-PDF-driven practice sessions, independent of the Learning Path.

Hierarchy:  active Target PDF version -> main topics -> subtopics -> sessions.

* **Versions.** Each Target PDF attached to the grade's live diagnostic is one
  ``AIGPPdfVersion`` (keyed by its RAG thread). A version is shown only once its
  topic hierarchy has been extracted (``status == 'ready'``). Replacing or
  removing a Target PDF in the admin panel changes the attached PDFs, so the
  active set follows automatically; old versions, their question pools and all
  student sessions are kept for history. While a newly attached PDF is still
  processing (or failed) and nothing else is ready, the grade's previously
  ready version stays in use.
* **Question pool.** Per subtopic, questions are generated from that subtopic's
  page range only (``generate_mcqs_from_content`` with strict grounding), then
  independently checked against the same excerpt (``verify_mcq_grounding``) and
  for a correct answer key (``verify_mcq_keys``), de-duplicated (exact + near
  duplicate) and stored. Pools are shared by all students of that PDF version;
  the LLM is only called when a student has run out of unseen questions and the
  PDF still yields new ones. A per-subtopic DB lease stops concurrent requests
  from generating the same pool twice.
* **Selection.** Unseen questions first (difficulty-balanced, shuffled), then
  the least-recently-seen ones when the finite pool is exhausted. Question order
  and option order are shuffled independently; the stored option order per
  session item keeps grading exact. A session never repeats a question and is
  shorter than the configured size when the pool is smaller.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.models.lms_models import (
    AIGPPdfVersion,
    AIGPQuestion,
    AIGPSession,
    AIGPSessionItem,
    AIGPTopic,
)
from app.services.lms import ai_guided_practice_topics as topics_mod
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_LABELS = "ABCD"
_DIFFICULTIES = ("easy", "medium", "hard")


def session_size() -> int:
    try:
        return max(1, min(30, int(os.getenv("AI_GUIDED_PRACTICE_SESSION_SIZE", "10"))))
    except ValueError:
        return 10


POOL_CAP = int(os.getenv("AI_GUIDED_PRACTICE_POOL_CAP", "80"))
MAX_DRY_ROUNDS = 2  # rounds in a row that added nothing new -> pool exhausted
ROUNDS_PER_REQUEST = 2
LOCK_TTL_SECONDS = 300
LOCK_WAIT_SECONDS = float(os.getenv("AI_GUIDED_PRACTICE_LOCK_WAIT_SECONDS", "60"))
PROCESSING_STALE = timedelta(minutes=15)
PENDING_RETRY = timedelta(seconds=90)
INGEST_WAIT_LIMIT = timedelta(hours=2)
NEAR_DUPLICATE_JACCARD = 0.8
_MAX_SOURCE_CHARS = 12000
_MIN_RANGE_CHARS = 300

# In-process throttle so dashboard polling does not spawn a job per request.
_last_enqueued: Dict[int, float] = {}


# -- helpers ----------------------------------------------------------------------


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def question_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:180]


def _strip_latex(text: str) -> str:
    """Drop TeX commands/braces so near-dup matching is not fooled by formatting."""
    t = re.sub(r"\\[a-zA-Z]+\s*", " ", text or "")
    return re.sub(r"[{}$^_\\]", " ", t)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+(?:\.\d+)?", _strip_latex(text).lower()))


def _jaccard(a: set, b: set) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _fingerprint(question_text: str, options: Sequence[dict], correct_index: int) -> tuple:
    """(stem tokens, normalised correct answer)."""
    correct = options[correct_index].get("text") if 0 <= correct_index < len(options) else ""
    return frozenset(_tokens(question_text)), " ".join(sorted(_tokens(correct or "")))


def is_near_duplicate(fp: tuple, others: Iterable[tuple], threshold: float = NEAR_DUPLICATE_JACCARD) -> bool:
    """Same question reworded: near-identical stem with the same correct answer,
    or an almost identical stem (>= 0.95) whatever the answer."""
    stem, answer = fp
    if not stem:
        return False
    for other_stem, other_answer in others:
        if not other_stem:
            continue
        sim = _jaccard(stem, other_stem)
        if sim >= 0.95 or (sim >= threshold and answer == other_answer):
            return True
    return False


def _options(q: AIGPQuestion) -> List[dict]:
    try:
        data = json.loads(q.options_json or "[]")
    except (TypeError, ValueError):
        data = []
    return [o for o in data if isinstance(o, dict)]


def _has_positional_option(options: List[dict]) -> bool:
    from app.services.lms.deficiency_chat_service import _has_positional_option as positional

    return positional(options)


def option_order(q: AIGPQuestion, rng: random.Random = random) -> List[int]:
    """Display order of canonical option indexes; positional options keep A-D order."""
    opts = _options(q)
    order = list(range(len(opts)))
    if not q.preserve_option_order and not _has_positional_option(opts):
        rng.shuffle(order)
    return order


def _difficulty_ladder(count: int) -> List[str]:
    """Balanced mix (about 30% easy, 40% medium, 30% hard)."""
    base = ["easy", "medium", "hard", "medium", "easy", "hard", "medium", "easy", "hard", "medium"]
    return [base[i % len(base)] for i in range(max(0, count))]


# -- versions -------------------------------------------------------------------


def version_for_thread(rag_thread_id: str) -> Optional[AIGPPdfVersion]:
    if not rag_thread_id:
        return None
    return get_db().query(AIGPPdfVersion).filter(AIGPPdfVersion.rag_thread_id == str(rag_thread_id)).first()


def register_version(
    rag_thread_id: str,
    *,
    assessment_id: Optional[int],
    grade_level: Optional[str],
    filename: Optional[str],
    owner_id: Optional[int] = None,
    pdf_bytes: Optional[bytes] = None,
    replaces_version_id: Optional[int] = None,
) -> AIGPPdfVersion:
    """Create (idempotently) the version row for a Target PDF thread."""
    db = get_db()
    existing = version_for_thread(rag_thread_id)
    if existing:
        if pdf_bytes and not existing.outline_json:
            existing.outline_json = topics_mod.dumps(topics_mod.outline_from_pdf(pdf_bytes))
            db.commit()
        return existing
    version = AIGPPdfVersion(
        rag_thread_id=str(rag_thread_id),
        rag_owner_id=owner_id,
        assessment_id=assessment_id,
        grade_level=str(grade_level or ""),
        original_filename=filename,
        status="pending",
        outline_json=topics_mod.dumps(topics_mod.outline_from_pdf(pdf_bytes)) if pdf_bytes else None,
        replaces_version_id=replaces_version_id,
    )
    db.add(version)
    try:
        db.commit()
    except IntegrityError:  # registered concurrently
        db.rollback()
        return version_for_thread(rag_thread_id)
    return version


def _claim_for_processing(version_id: int, force: bool = False) -> bool:
    db = get_db()
    now = _now()
    q = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == version_id)
    if not force:
        q = q.filter(
            or_(
                AIGPPdfVersion.status.in_(("pending", "failed")),
                (AIGPPdfVersion.status == "processing")
                & (AIGPPdfVersion.processing_started_at < now - PROCESSING_STALE),
            )
        )
    else:
        # Admin retry: never two extractions of one version at once (QA B7).
        q = q.filter(
            AIGPPdfVersion.status != "ready",
            or_(
                AIGPPdfVersion.status != "processing",
                AIGPPdfVersion.processing_started_at.is_(None),
                AIGPPdfVersion.processing_started_at < now - PROCESSING_STALE,
            ),
        )
    claimed = q.update(
        {AIGPPdfVersion.status: "processing", AIGPPdfVersion.processing_started_at: now},
        synchronize_session=False,
    )
    db.commit()
    return claimed == 1


def _thread_owner(version: AIGPPdfVersion) -> Optional[int]:
    if version.rag_owner_id:
        return version.rag_owner_id
    try:
        from app.utils.rag_service import _get_user_id_for_thread

        return _get_user_id_for_thread(version.rag_thread_id)
    except Exception:  # noqa: BLE001
        return None


def _thread_chunks(version: AIGPPdfVersion) -> List[dict]:
    owner = _thread_owner(version)
    if not owner:
        return []
    from app.utils.rag_vectorstore import query_all_chunks

    return query_all_chunks(thread_id=version.rag_thread_id, user_id=int(owner))


def _thread_headings(version: AIGPPdfVersion) -> List[dict]:
    try:
        from app.utils.rag_service import _get_thread_topics

        return (_get_thread_topics(version.rag_thread_id) or {}).get("topics") or []
    except Exception as exc:  # noqa: BLE001
        logger.info("Headings unavailable for %s: %s", version.rag_thread_id, exc)
        return []


def process_version(version_id: int, *, force: bool = False, use_llm: bool = True) -> AIGPPdfVersion:
    """Extract and store the topic hierarchy; mark the version ready or failed.

    Never raises: a failure is recorded on the version and other versions are
    untouched (they stay active).
    """
    db = get_db()
    if not _claim_for_processing(version_id, force=force):
        return db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == version_id).first()
    version = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == version_id).first()
    if not version.rag_owner_id:
        version.rag_owner_id = _thread_owner(version)
    try:
        outline = json.loads(version.outline_json) if version.outline_json else []
    except (TypeError, ValueError):
        outline = []
    try:
        result = topics_mod.extract_hierarchy(
            _thread_chunks(version),
            _thread_headings(version),
            outline,
            version.original_filename or "",
            use_llm=use_llm,
        )
        # Re-processing replaces a hierarchy that no session has used yet.
        old_ids = [t.id for t in db.query(AIGPTopic.id).filter(AIGPTopic.version_id == version.id).all()]
        if old_ids:
            used = db.query(AIGPSession.id).filter(AIGPSession.topic_id.in_(old_ids)).first()
            if used:
                raise topics_mod.HierarchyError("Version already has practice sessions; not re-extracting.")
            db.query(AIGPQuestion).filter(AIGPQuestion.version_id == version.id).delete(synchronize_session=False)
            db.query(AIGPTopic).filter(AIGPTopic.version_id == version.id, AIGPTopic.parent_id.isnot(None)).delete(
                synchronize_session=False
            )
            db.query(AIGPTopic).filter(AIGPTopic.version_id == version.id).delete(synchronize_session=False)
        for i, main in enumerate(result["main_topics"]):
            parent = AIGPTopic(
                version_id=version.id,
                title=main["title"],
                page_start=main.get("page_start"),
                page_end=main.get("page_end"),
                sort_order=i,
            )
            db.add(parent)
            db.flush()
            for j, sub in enumerate(main["subtopics"]):
                db.add(
                    AIGPTopic(
                        version_id=version.id,
                        parent_id=parent.id,
                        title=sub["title"],
                        page_start=sub.get("page_start"),
                        page_end=sub.get("page_end"),
                        sort_order=j,
                        is_whole_section=bool(sub.get("is_whole_section")),
                        title_source=sub.get("kind"),
                    )
                )
        version.structure = result["structure"]
        version.warnings_json = topics_mod.dumps(result.get("warnings") or [])
        version.status = "ready"
        version.error_message = None
        version.ready_at = _now()
        db.commit()
        logger.info(
            "AI Guided Practice: version %s (%s) ready with %d main topics",
            version.id, version.original_filename, len(result["main_topics"]),
        )
    except Exception as exc:  # noqa: BLE001 - recorded, never propagated
        db.rollback()
        version = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == version_id).first()
        no_text_yet = isinstance(exc, topics_mod.HierarchyError) and "no extractable text" in str(exc)
        young = version.created_at and version.created_at > _now() - INGEST_WAIT_LIMIT
        version.status = "pending" if (no_text_yet and young) else "failed"
        version.error_message = str(exc)[:2000]
        version.processing_started_at = None if version.status == "pending" else version.processing_started_at
        db.commit()
        log = logger.info if version.status == "pending" else logger.warning
        log("AI Guided Practice: version %s processing %s: %s", version_id, version.status, exc)
    return version


def enqueue_processing(version: AIGPPdfVersion) -> None:
    """Fire-and-forget processing of a pending/stale version (throttled per process)."""
    if version is None or version.status == "ready":
        return
    if version.status == "failed":
        return  # admin re-processes explicitly
    if version.status == "processing" and version.processing_started_at and (
        version.processing_started_at > _now() - PROCESSING_STALE
    ):
        return
    last = _last_enqueued.get(version.id, 0)
    if time.time() - last < PENDING_RETRY.total_seconds():
        return
    _last_enqueued[version.id] = time.time()
    from app.tasks import lms_tasks

    lms_tasks.enqueue_ai_guided_practice_processing(version.id)


def on_target_pdf_attached(
    rag_thread_id: str,
    *,
    assessment_id: Optional[int],
    grade_level: Optional[str],
    filename: Optional[str],
    owner_id: Optional[int],
    pdf_bytes: Optional[bytes] = None,
) -> None:
    """Upload hook: register the new Target PDF and process it in the background.

    Best effort - never blocks or fails the admin upload.
    """
    try:
        version = register_version(
            rag_thread_id,
            assessment_id=assessment_id,
            grade_level=grade_level,
            filename=filename,
            owner_id=owner_id,
            pdf_bytes=pdf_bytes,
        )
        enqueue_processing(version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Guided Practice registration failed for %s: %s", filename, exc)
        try:
            get_db().rollback()
        except Exception:  # noqa: BLE001
            pass


def _student_target_pdfs(student_id: int) -> Tuple[Optional[object], List[dict]]:
    from app.services.lms.deficiency_chat_service import _get_target_threads
    from app.services.lms.diagnostic_service import get_student_diagnostic

    diag = get_student_diagnostic(student_id)
    if not diag:
        return None, []
    return diag, _get_target_threads(diag.id)


def active_context(student_id: int) -> dict:
    """Active (ready) versions for this student's grade plus processing/failed state."""
    diag, targets = _student_target_pdfs(student_id)
    if not diag or not targets:
        return {"state": "no_active_pdf", "ready": [], "processing": [], "failed": [], "using_previous": False,
                "grade_level": getattr(diag, "grade_level", None)}
    versions = []
    for t in targets:
        v = register_version(
            t["rag_thread_id"],
            assessment_id=diag.id,
            grade_level=diag.grade_level,
            filename=t.get("original_filename"),
            owner_id=None,  # resolved from the RAG thread itself when processed
        )
        if v.status != "ready":
            enqueue_processing(v)
        versions.append(v)
    ready = [v for v in versions if v.status == "ready"]
    processing = [v for v in versions if v.status in ("pending", "processing")]
    failed = [v for v in versions if v.status == "failed"]
    # Only PDFs attached right now are ever shown. There is deliberately no "previous
    # version of this grade" fallback: it revived PDFs an admin had removed (QA B5)
    # and could surface a long-replaced version (L2). Continuity during a swap comes
    # from replace_target_pdf, which keeps the old PDF attached until the new one is
    # ready.
    using_previous = False
    state = "ready" if ready else ("processing" if processing else "failed")
    return {
        "state": state,
        "ready": ready,
        "processing": processing,
        "failed": failed,
        "using_previous": using_previous,
        "grade_level": diag.grade_level,
    }


def _student_grade(student_id: int) -> Optional[str]:
    try:
        from app.services.lms import class_service

        return class_service.get_student_diagnostic_grade(student_id)
    except Exception:  # noqa: BLE001
        return None


def _active_version_ids(student_id: int) -> set:
    return {v.id for v in active_context(student_id)["ready"]}


# -- dashboard ------------------------------------------------------------------


def _progress_by_topic(student_id: int, topic_ids: List[int]) -> Dict[int, dict]:
    if not topic_ids:
        return {}
    db = get_db()
    rows = (
        db.query(AIGPSession)
        .filter(AIGPSession.student_id == student_id, AIGPSession.topic_id.in_(topic_ids))
        .order_by(AIGPSession.id.asc())
        .all()
    )
    seen = dict(
        db.query(AIGPSession.topic_id, func.count(func.distinct(AIGPSessionItem.question_id)))
        .join(AIGPSessionItem, AIGPSessionItem.session_id == AIGPSession.id)
        .filter(
            AIGPSession.student_id == student_id,
            AIGPSession.topic_id.in_(topic_ids),
            AIGPSessionItem.answered_at.isnot(None),
        )
        .group_by(AIGPSession.topic_id)
        .all()
    )
    out: Dict[int, dict] = {}
    for s in rows:
        p = out.setdefault(
            s.topic_id,
            {"sessions_completed": 0, "last_score": None, "best_score": None, "last_practiced_at": None,
             "in_progress_session_id": None, "questions_answered": 0, "questions_correct": 0},
        )
        p["questions_answered"] += int(s.answered_count or 0)
        p["questions_correct"] += int(s.correct_count or 0)
        if s.status == "completed":
            p["sessions_completed"] += 1
            p["last_score"] = s.score_percent
            if s.score_percent is not None and (p["best_score"] is None or s.score_percent > p["best_score"]):
                p["best_score"] = s.score_percent
        elif s.status == "in_progress":
            p["in_progress_session_id"] = s.id
        last = s.last_activity_at or s.created_at
        if last and (p["last_practiced_at"] is None or last.isoformat() > p["last_practiced_at"]):
            p["last_practiced_at"] = last.isoformat()
    for tid, n in seen.items():
        out.setdefault(tid, {"sessions_completed": 0, "last_score": None, "best_score": None,
                             "last_practiced_at": None, "in_progress_session_id": None,
                             "questions_answered": 0, "questions_correct": 0})["questions_seen"] = int(n)
    for p in out.values():
        p.setdefault("questions_seen", 0)
    return out


def _version_tree(version: AIGPPdfVersion, student_id: int) -> dict:
    db = get_db()
    rows = (
        db.query(AIGPTopic)
        .filter(AIGPTopic.version_id == version.id)
        .order_by(AIGPTopic.parent_id.isnot(None), AIGPTopic.sort_order, AIGPTopic.id)
        .all()
    )
    mains = [t for t in rows if t.parent_id is None]
    subs = [t for t in rows if t.parent_id is not None]
    pool = dict(
        db.query(AIGPQuestion.topic_id, func.count(AIGPQuestion.id))
        .filter(AIGPQuestion.version_id == version.id, AIGPQuestion.status == "valid")
        .group_by(AIGPQuestion.topic_id)
        .all()
    )
    progress = _progress_by_topic(student_id, [s.id for s in subs])
    by_parent: Dict[int, List[AIGPTopic]] = {}
    for s in subs:
        by_parent.setdefault(s.parent_id, []).append(s)
    topics = []
    for m in mains:
        sub_dicts = []
        for s in by_parent.get(m.id, []):
            p = progress.get(s.id) or {}
            if p.get("in_progress_session_id"):
                status = "in_progress"
            elif p.get("sessions_completed"):
                status = "completed"
            else:
                status = "not_started"
            sub_dicts.append(
                {
                    "subtopic_id": s.id,
                    "title": s.title,
                    "page_start": s.page_start,
                    "page_end": s.page_end,
                    "is_whole_section": bool(s.is_whole_section),
                    "pool_size": int(pool.get(s.id, 0)),
                    "pool_exhausted": bool(s.pool_exhausted),
                    "insufficient_content": bool(s.insufficient_content),
                    "status": status,
                    "progress": p or None,
                }
            )
        done = sum(1 for s in sub_dicts if s["status"] == "completed")
        topics.append(
            {
                "topic_id": m.id,
                "title": m.title,
                "page_start": m.page_start,
                "page_end": m.page_end,
                "subtopics": sub_dicts,
                "subtopics_total": len(sub_dicts),
                "subtopics_practised": done,
            }
        )
    try:
        warnings = json.loads(version.warnings_json or "[]")
    except (TypeError, ValueError):
        warnings = []
    return {
        "version_id": version.id,
        "filename": version.original_filename,
        "structure": version.structure,
        "ready_at": _iso(version.ready_at),
        "warnings": warnings,
        "topics": topics,
    }


def dashboard(student_id: int) -> dict:
    ctx = active_context(student_id)
    sources = [_version_tree(v, student_id) for v in ctx["ready"]]
    db = get_db()
    totals = (
        db.query(
            func.count(AIGPSession.id),
            func.coalesce(func.sum(AIGPSession.answered_count), 0),
            func.coalesce(func.sum(AIGPSession.correct_count), 0),
            func.max(AIGPSession.last_activity_at),
        )
        .filter(AIGPSession.student_id == student_id, AIGPSession.status == "completed")
        .one()
    )
    state = ctx["state"]
    if state == "ready" and not any(src["topics"] for src in sources):
        state = "no_topics"
    return {
        "state": state,
        "grade_level": ctx["grade_level"] or _student_grade(student_id),
        "session_size": session_size(),
        "using_previous_version": ctx["using_previous"],
        "sources": sources,
        "processing": [
            {"version_id": v.id, "filename": v.original_filename, "status": v.status} for v in ctx["processing"]
        ],
        "failed": [
            # Admin-side error text stays in the admin panel (QA B13).
            {"version_id": v.id, "filename": v.original_filename} for v in ctx["failed"]
        ],
        "summary": {
            "sessions_completed": int(totals[0] or 0),
            "questions_answered": int(totals[1] or 0),
            "questions_correct": int(totals[2] or 0),
            "last_practiced_at": _iso(totals[3]),
        },
    }


# -- source text + generation ---------------------------------------------------


def _subtopic_scope(main: AIGPTopic, sub: AIGPTopic) -> str:
    """Topic/SLO the generator and the grounding judge enforce: the subtopic alone.

    Naming the main topic too let the judge accept questions on *other* parts of
    the unit (live run: quadrant questions accepted for "Quadratic Equations").
    """
    return main.title if sub.is_whole_section else sub.title


def _judge_scope(main: AIGPTopic, sub: AIGPTopic) -> str:
    """Scope for the generator and the grounding judge: this subtopic only, with its
    sibling subtopics named as out of scope (they often share the same pages)."""
    scope = _subtopic_scope(main, sub)
    if sub.is_whole_section:
        return scope
    siblings = [
        t
        for (t,) in get_db()
        .query(AIGPTopic.title)
        .filter(AIGPTopic.parent_id == main.id, AIGPTopic.id != sub.id)
        .order_by(AIGPTopic.sort_order)
        .all()
    ]
    if not siblings:
        return scope
    return f"{scope} (only this subtopic; out of scope: {', '.join(siblings[:12])})"


_FOCUS_STOPWORDS = frozenset({"the", "and", "of", "a", "an", "to", "in", "on", "for", "with", "its", "their"})


def focus_excerpt(text: str, title: str, context_lines: int = 2, min_chars: int = 120) -> str:
    """Lines of ``text`` that mention ``title`` (all its key words) plus neighbours.

    Used for subtopics printed in a "Topics:" list, whose text is the whole unit.
    Falls back to the full text when the focused part is too small.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if w not in _FOCUS_STOPWORDS]
    if not words:
        return text
    lines = (text or "").splitlines()
    stems = [w[:5] if len(w) > 5 else w for w in words]  # "triangles" ~ "triangle"
    # The title as a phrase: "Real Numbers" must not match "the real part of complex
    # numbers" (QA B11: Real Numbers practice drifted into Complex Numbers).
    phrase = re.compile(r"\b" + r"\w*\W+".join(re.escape(st) for st in stems) + r"\w*", re.I)
    keep = set()
    for i, line in enumerate(lines):
        low = line.lower()
        if re.match(r"\s*(sub\s*-?\s*)?topics?\b", low):
            continue  # the list line itself names every subtopic
        if phrase.search(low):
            keep.update(range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)))
    focused = "\n".join(lines[i] for i in sorted(keep)).strip()
    return focused if len(focused) >= min_chars else text


def _find_heading(text: str, title: str, skip_at_start: bool = False) -> Optional[int]:
    """Offset of ``title`` in ``text`` (whitespace/case-insensitive), or None."""
    words = re.findall(r"\w+", title or "")
    if not words:
        return None
    pattern = re.compile(r"\W*".join(re.escape(w) for w in words), re.I)
    for m in pattern.finditer(text or ""):
        if skip_at_start and m.start() < 5:
            continue
        return m.start()
    return None


def _has_own_heading(sub: AIGPTopic) -> bool:
    return not sub.is_whole_section and (sub.title_source in (None, "heading"))


def _next_section_title(main: AIGPTopic, sub: AIGPTopic) -> Optional[str]:
    """Heading that ends this subtopic: the next sibling, else the next main topic."""
    db = get_db()
    if _has_own_heading(sub):
        sibling = (
            db.query(AIGPTopic.title)
            .filter(
                AIGPTopic.parent_id == main.id,
                AIGPTopic.sort_order > sub.sort_order,
                or_(AIGPTopic.title_source.is_(None), AIGPTopic.title_source == "heading"),
            )
            .order_by(AIGPTopic.sort_order)
            .first()
        )
        if sibling:
            return sibling[0]
    nxt = (
        db.query(AIGPTopic.title)
        .filter(
            AIGPTopic.version_id == main.version_id,
            AIGPTopic.parent_id.is_(None),
            AIGPTopic.sort_order > main.sort_order,
        )
        .order_by(AIGPTopic.sort_order)
        .first()
    )
    return nxt[0] if nxt else None


def subtopic_source(version: AIGPPdfVersion, main: AIGPTopic, sub: AIGPTopic) -> Tuple[str, List[dict]]:
    """Text of the subtopic's page range in the Target PDF (+ the chunks used)."""
    from app.services.lms.deficiency_chat_service import _looks_like_answer_key_table

    chunks = _thread_chunks(version)
    start = sub.page_start or main.page_start
    end = sub.page_end or main.page_end or start
    by_page: Dict[int, List[str]] = {}
    for c in chunks:
        try:
            page = int(c.get("page") or 0)
        except (TypeError, ValueError):
            continue
        text = (c.get("text") or "").strip()
        if not text or _looks_like_answer_key_table(text):
            continue
        if start and end and start <= page <= end:
            by_page.setdefault(page, []).append(text)
    # A subtopic from a printed "Topics:" list has no heading of its own: read the
    # parent section; the generation/grounding scope still names the subtopic.
    own_title = sub.title if _has_own_heading(sub) else main.title
    next_title = _next_section_title(main, sub)
    used: List[dict] = []
    for page in sorted(by_page):
        text = "\n".join(by_page[page])
        if page == start:
            cut = _find_heading(text, own_title)
            if cut is not None:
                text = text[cut:]
        if page == end and next_title:
            cut = _find_heading(text, next_title, skip_at_start=(page == start))
            if cut is not None:
                text = text[:cut]
        if text.strip():
            used.append({"page": page, "text": text.strip()})
    if sub.title_source == "topic_list" and used:
        # context_lines=0: neighbour lines name sibling Topics: entries and the
        # generator then emits off-subtopic items the grounding judge still accepts
        # (QA Agent 2: Factor Theorem pool was mostly Remainder Theorem).
        focused = [
            {"page": c["page"], "text": focus_excerpt(c["text"], sub.title, context_lines=0, min_chars=0)}
            for c in used
        ]
        focused = [c for c in focused if c["text"].strip()]
        if sum(len(c["text"]) for c in focused) >= 120:
            used = focused
    if not used:
        # Only retrieve by meaning when the page range yielded nothing. Widening a
        # short on-page excerpt pulls sibling units and nulls source_page (QA A2).
        try:
            from app.services.lms.deficiency_chat_service import _rag_semantic_section_text

            sem, _thread = _rag_semantic_section_text(
                _subtopic_scope(main, sub), [version.rag_thread_id], _thread_owner(version) or 0
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("Semantic retrieval failed for %s: %s", sub.title, exc)
            sem = ""
        if sem.strip():
            used.append({"page": None, "text": sem.strip()})
    elif sum(len(c["text"]) for c in used) < _MIN_RANGE_CHARS:
        logger.info(
            "Keeping short on-page excerpt for %s (%d chars); skipping semantic widen",
            sub.title,
            sum(len(c["text"]) for c in used),
        )
    text = "\n\n".join(c["text"] for c in used).strip()
    return text, used


def source_windows(used: List[dict], max_chars: int = _MAX_SOURCE_CHARS) -> List[List[dict]]:
    """Split a subtopic's source chunks into consecutive windows of <= ``max_chars``.

    Generation rounds rotate through the windows, so a long section is covered end
    to end instead of always the first 12k characters (QA B9).
    """
    pieces: List[dict] = []
    for c in used:
        t = c["text"]
        for i in range(0, len(t), max_chars):
            pieces.append({"page": c.get("page"), "text": t[i : i + max_chars]})
    windows: List[List[dict]] = []
    cur: List[dict] = []
    size = 0
    for piece in pieces:
        if cur and size + len(piece["text"]) > max_chars:
            windows.append(cur)
            cur, size = [], 0
        cur.append(piece)
        size += len(piece["text"]) + 2
    if cur:
        windows.append(cur)
    return windows


def _locate_source(question: dict, used_chunks: List[dict]) -> Tuple[Optional[int], Optional[str]]:
    opts = question.get("options") or []
    idx = question.get("correct_option_index")
    correct = opts[idx].get("text") if isinstance(idx, int) and 0 <= idx < len(opts) else ""
    want = _tokens(f"{question.get('question_text') or ''} {correct or ''}")
    best, best_score = None, 0.0
    for c in used_chunks:
        overlap = len(want & _tokens(c["text"]))
        # Prefer chunks with a real page when scores tie (semantic chunks use page=None).
        better = overlap > best_score or (
            overlap == best_score and best is not None and best.get("page") is None and c.get("page") is not None
        )
        if better:
            best, best_score = c, overlap
    if not best:
        return None, None
    return best.get("page"), re.sub(r"\s+", " ", best["text"])[:400]


def _generate_candidates(
    text: str, scope: str, ladder: List[str], grade_level: Optional[str], exclude: List[str], thread_id: str
) -> List[dict]:
    """Generate -> structural filter -> grounding check -> answer-key check."""
    from app.services.lms.deficiency_chat_service import _mcq_to_queue_item
    from app.services.quiz.diagnostic_generator import generate_mcqs_from_content
    from app.services.quiz.mcq_answer_check import verify_mcq_keys
    from app.services.quiz.mcq_grounding_check import verify_mcq_grounding

    mcqs = generate_mcqs_from_content(
        text,
        scope,
        len(ladder),
        grade_level=grade_level or None,
        difficulty_ladder=ladder,
        exclude_question_texts=exclude,
        strict_grounding=True,
    )
    items = []
    for i, mcq in enumerate(mcqs or []):
        item = _mcq_to_queue_item(mcq, 0, scope, scope, thread_id, ladder[i] if i < len(ladder) else "medium")
        if not item:
            continue
        item["explanation"] = (getattr(mcq, "explanation", None) or "").strip() or None
        item["preserve_option_order"] = bool(getattr(mcq, "preserve_option_order", False))
        items.append(item)
    grounded = verify_mcq_grounding(items, text, scope)
    return verify_mcq_keys(grounded)


def _acquire_generation_lock(topic_id: int) -> bool:
    db = get_db()
    now = _now()
    got = (
        db.query(AIGPTopic)
        .filter(
            AIGPTopic.id == topic_id,
            or_(AIGPTopic.generation_locked_until.is_(None), AIGPTopic.generation_locked_until < now),
        )
        .update({AIGPTopic.generation_locked_until: now + timedelta(seconds=LOCK_TTL_SECONDS)},
                synchronize_session=False)
    )
    db.commit()
    return got == 1


def _release_generation_lock(topic_id: int) -> None:
    db = get_db()
    db.query(AIGPTopic).filter(AIGPTopic.id == topic_id).update(
        {AIGPTopic.generation_locked_until: None}, synchronize_session=False
    )
    db.commit()


def _wait_for_lock_release(topic_id: int) -> None:
    deadline = time.time() + LOCK_WAIT_SECONDS
    db = get_db()
    while time.time() < deadline:
        db.expire_all()
        row = db.query(AIGPTopic.generation_locked_until).filter(AIGPTopic.id == topic_id).first()
        if not row or not row[0] or row[0] < _now():
            return
        time.sleep(1.5)


def _valid_pool(topic_id: int) -> List[AIGPQuestion]:
    return (
        get_db()
        .query(AIGPQuestion)
        .filter(AIGPQuestion.topic_id == topic_id, AIGPQuestion.status == "valid")
        .order_by(AIGPQuestion.id)
        .all()
    )


def _explanation(item: dict) -> Optional[str]:
    """Generator's explanation, else the verbatim PDF fragment the grounding checker
    cited (QA B12: most questions had none, so wrong answers got no explanation)."""
    text = (item.get("explanation") or "").strip()
    if text:
        return text
    evidence = re.sub(r"\s+", " ", (item.get("grounding_evidence") or "")).strip()
    return f'From the Target PDF: "{evidence}"' if evidence else None


def _store_new_questions(
    version: AIGPPdfVersion, sub: AIGPTopic, candidates: List[dict], used_chunks: List[dict]
) -> int:
    db = get_db()
    existing = _valid_pool(sub.id)
    keys = {q.dedupe_key for q in existing}
    fps = [_fingerprint(q.question_text, _options(q), q.correct_option_index) for q in existing]
    added = 0
    for item in candidates:
        options = [
            {"label": o.get("label"), "text": o.get("text"), "latex": o.get("latex")}
            for o in (item.get("options") or [])
        ]
        idx = item.get("correct_option_index")
        if not isinstance(idx, int) or not 0 <= idx < len(options) or len(options) < 2:
            continue
        key = question_key(item.get("question_text") or "")
        if not key or key in keys:
            continue
        fp = _fingerprint(item.get("question_text") or "", options, idx)
        if is_near_duplicate(fp, fps):
            continue
        page, excerpt = _locate_source(item, used_chunks)
        db.add(
            AIGPQuestion(
                version_id=version.id,
                topic_id=sub.id,
                question_text=item.get("question_text") or "",
                question_latex=item.get("question_latex"),
                options_json=json.dumps(options, ensure_ascii=False),
                correct_option_index=idx,
                explanation=_explanation(item),
                difficulty=item.get("difficulty") if item.get("difficulty") in _DIFFICULTIES else "medium",
                source_page=page,
                source_excerpt=excerpt,
                dedupe_key=key,
                key_verified=item.get("key_verified"),
                preserve_option_order=bool(item.get("preserve_option_order")),
            )
        )
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            continue
        keys.add(key)
        fps.append(fp)
        added += 1
    db.commit()
    return added


def ensure_pool(sub_id: int, target_size: int) -> dict:
    """Grow one subtopic's pool towards ``target_size`` (bounded, locked, grounded).

    Returns {"generated": n, "error": str|None, "waited": bool}.
    """
    db = get_db()
    sub = db.query(AIGPTopic).filter(AIGPTopic.id == sub_id).first()
    if not sub or sub.parent_id is None:
        raise LMSNotFoundError("Subtopic not found")
    if sub.pool_exhausted or sub.insufficient_content:
        return {"generated": 0, "error": None, "waited": False}
    target_size = min(int(target_size), POOL_CAP)
    if len(_valid_pool(sub.id)) >= target_size:
        return {"generated": 0, "error": None, "waited": False}
    if not _acquire_generation_lock(sub.id):
        _wait_for_lock_release(sub.id)  # another request is generating this pool
        return {"generated": 0, "error": None, "waited": True}
    generated, error = 0, None
    try:
        main = db.query(AIGPTopic).filter(AIGPTopic.id == sub.parent_id).first()
        version = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == sub.version_id).first()
        text, used = subtopic_source(version, main, sub)
        if not text.strip():
            sub.insufficient_content = True
            sub.pool_exhausted = True
            sub.last_generation_error = "No text found for this subtopic in the Target PDF."
            db.commit()
            return {"generated": 0, "error": sub.last_generation_error, "waited": False}
        scope = _judge_scope(main, sub)
        windows = source_windows(used) or [[{"page": None, "text": text}]]
        for _ in range(ROUNDS_PER_REQUEST):
            pool = _valid_pool(sub.id)
            deficit = target_size - len(pool)
            if deficit <= 0:
                break
            ladder = _difficulty_ladder(min(10, deficit + 2))
            exclude = [q.question_text for q in pool][-60:]
            window = windows[int(sub.generation_rounds or 0) % len(windows)]
            window_text = "\n\n".join(c["text"] for c in window)
            try:
                candidates = _generate_candidates(
                    window_text, scope, ladder, version.grade_level, exclude, version.rag_thread_id
                )
            except Exception as exc:  # noqa: BLE001 - recorded, pool reuse continues
                error = f"Question generation failed: {exc}"
                logger.warning("AI Guided Practice generation failed for subtopic %s: %s", sub.id, exc)
                db.rollback()
                sub = db.query(AIGPTopic).filter(AIGPTopic.id == sub_id).first()
                sub.last_generation_error = error[:2000]
                db.commit()
                break
            added = _store_new_questions(version, sub, candidates, window)
            generated += added
            sub = db.query(AIGPTopic).filter(AIGPTopic.id == sub_id).first()
            sub.generation_rounds = int(sub.generation_rounds or 0) + 1
            sub.last_generated_at = _now()
            sub.last_generation_error = None
            sub.dry_rounds = 0 if added else int(sub.dry_rounds or 0) + 1
            total = len(_valid_pool(sub.id))
            # Exhausted only after every window had a dry round.
            dry_limit = max(MAX_DRY_ROUNDS, len(windows))
            if sub.dry_rounds >= dry_limit or total >= POOL_CAP:
                sub.pool_exhausted = True
            if total == 0 and sub.dry_rounds >= dry_limit:
                sub.insufficient_content = True
            db.commit()
            if sub.pool_exhausted:
                break
    finally:
        _release_generation_lock(sub_id)
    return {"generated": generated, "error": error, "waited": False}


# -- selection --------------------------------------------------------------------


def select_questions(
    pool: Sequence[Tuple[int, str]],
    history: Dict[int, Tuple[datetime, int]],
    n: int,
    last_sequence: Sequence[int] = (),
    rng: random.Random = random,
) -> Tuple[List[int], int]:
    """Pick up to ``n`` distinct question ids -> (ordered ids, number reused).

    pool: [(question_id, difficulty)]; history: {question_id: (last_seen_at, times_seen)}.
    Unseen first, difficulty-balanced; then least recently / least often seen.
    """
    unseen = [p for p in pool if p[0] not in history]
    rng.shuffle(unseen)
    buckets: Dict[str, List[int]] = {}
    for qid, diff in unseen:
        buckets.setdefault(diff if diff in _DIFFICULTIES else "medium", []).append(qid)
    picked: List[int] = []
    order = ["easy", "medium", "hard"]
    while len(picked) < n and any(buckets.values()):
        for d in order:
            if buckets.get(d) and len(picked) < n:
                picked.append(buckets[d].pop())
    reused = 0
    if len(picked) < n:
        seen = [p[0] for p in pool if p[0] in history]
        jitter = {qid: rng.random() for qid in seen}
        seen.sort(key=lambda q: (history[q][0], history[q][1], jitter[q]))
        for qid in seen:
            if len(picked) >= n:
                break
            picked.append(qid)
            reused += 1
    for _ in range(6):
        rng.shuffle(picked)
        if len(picked) < 2 or list(picked) != list(last_sequence)[: len(picked)]:
            break
    return picked, reused


def _student_history(student_id: int, topic_id: int) -> Dict[int, Tuple[datetime, int]]:
    rows = (
        get_db()
        .query(AIGPSessionItem.question_id, func.max(AIGPSession.created_at), func.count(AIGPSessionItem.id))
        .join(AIGPSession, AIGPSession.id == AIGPSessionItem.session_id)
        .filter(AIGPSession.student_id == student_id, AIGPSession.topic_id == topic_id)
        .group_by(AIGPSessionItem.question_id)
        .all()
    )
    return {int(q): (last or datetime.min, int(n or 0)) for q, last, n in rows}


def _last_sequence(student_id: int, topic_id: int) -> List[int]:
    db = get_db()
    last = (
        db.query(AIGPSession.id)
        .filter(AIGPSession.student_id == student_id, AIGPSession.topic_id == topic_id)
        .order_by(AIGPSession.id.desc())
        .first()
    )
    if not last:
        return []
    return [
        r[0]
        for r in db.query(AIGPSessionItem.question_id)
        .filter(AIGPSessionItem.session_id == last[0])
        .order_by(AIGPSessionItem.position)
        .all()
    ]


# -- sessions ---------------------------------------------------------------------


def _load_subtopic(subtopic_id: int) -> Tuple[AIGPTopic, AIGPTopic, AIGPPdfVersion]:
    db = get_db()
    sub = db.query(AIGPTopic).filter(AIGPTopic.id == _safe_id(subtopic_id)).first()
    if not sub or sub.parent_id is None:
        raise LMSNotFoundError("Subtopic not found")
    main = db.query(AIGPTopic).filter(AIGPTopic.id == sub.parent_id).first()
    version = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == sub.version_id).first()
    return sub, main, version


_MAX_ID = 2**31 - 1


def _safe_id(value, *, zero_ok: bool = False) -> int:
    """Integer id within DB range; huge/negative/non-integral ids are "not found", never a 500."""
    if isinstance(value, bool):
        raise LMSNotFoundError("Not found")
    if not isinstance(value, int):
        try:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            raise LMSNotFoundError("Not found")
    if value < (0 if zero_ok else 1) or value > _MAX_ID:
        raise LMSNotFoundError("Not found")
    return value


def _own_session(student_id: int, session_id: int) -> AIGPSession:
    s = (
        get_db()
        .query(AIGPSession)
        .filter(AIGPSession.id == _safe_id(session_id), AIGPSession.student_id == student_id)
        .first()
    )
    if not s:
        raise LMSNotFoundError("Practice session not found")
    return s


def start_session(student_id: int, subtopic_id: int, request_key: Optional[str] = None) -> dict:
    """Start (or resume) a practice session for one subtopic of the active Target PDF."""
    db = get_db()
    sub, main, version = _load_subtopic(subtopic_id)
    if request_key is not None and not isinstance(request_key, str):
        raise LMSValidationError("request_key must be a string")
    request_key = (request_key or "").strip()[:64] or None
    if request_key:
        prior = (
            db.query(AIGPSession)
            .filter(AIGPSession.student_id == student_id, AIGPSession.request_key == request_key)
            .first()
        )
        if prior:
            if prior.topic_id != sub.id:  # QA B8: a key replays one request, not any subtopic
                raise LMSValidationError("request_key was already used for a different subtopic")
            return session_state(student_id, prior.id)
    if version.id not in _active_version_ids(student_id):
        raise LMSValidationError(
            "This subtopic belongs to a Target PDF that is no longer active. Refresh to see the current topics."
        )
    open_session = (
        db.query(AIGPSession)
        .filter(
            AIGPSession.student_id == student_id,
            AIGPSession.topic_id == sub.id,
            AIGPSession.status == "in_progress",
        )
        .order_by(AIGPSession.id.asc())
        .first()
    )
    if open_session:
        state = session_state(student_id, open_session.id)
        state["resumed"] = True
        return state

    n = session_size()
    history = _student_history(student_id, sub.id)
    pool = _valid_pool(sub.id)
    unseen = sum(1 for q in pool if q.id not in history)
    gen_error = None
    if unseen < n and not sub.pool_exhausted and not sub.insufficient_content:
        result = ensure_pool(sub.id, len(pool) + (n - unseen))
        gen_error = result.get("error")
        db.expire_all()
        sub = db.query(AIGPTopic).filter(AIGPTopic.id == subtopic_id).first()
        pool = _valid_pool(sub.id)
    if not pool:
        if sub.insufficient_content:
            reason, message = "insufficient_content", (
                "The Target PDF does not contain enough material on this subtopic to build practice questions."
            )
        elif gen_error:
            reason, message = "generation_failed", "We could not prepare questions right now. Please try again."
        else:
            reason, message = "generation_failed", (
                "No verified questions could be built from the Target PDF for this subtopic yet. Please try again."
            )
        return {"session": None, "reason": reason, "message": message}

    picked, reused = select_questions(
        [(q.id, q.difficulty) for q in pool], history, n, _last_sequence(student_id, sub.id)
    )
    by_id = {q.id: q for q in pool}
    session = AIGPSession(
        student_id=student_id,
        version_id=version.id,
        topic_id=sub.id,
        status="in_progress",
        question_count=len(picked),
        reused_count=reused,
        request_key=request_key,
    )
    db.add(session)
    try:
        db.flush()
        for pos, qid in enumerate(picked):
            db.add(
                AIGPSessionItem(
                    session_id=session.id,
                    question_id=qid,
                    position=pos,
                    option_order_json=json.dumps(option_order(by_id[qid])),
                    seen_before=qid in history,
                )
            )
        db.commit()
    except IntegrityError:  # same request_key raced in
        db.rollback()
        prior = (
            db.query(AIGPSession)
            .filter(AIGPSession.student_id == student_id, AIGPSession.request_key == request_key)
            .first()
        )
        if prior:
            return session_state(student_id, prior.id)
        raise
    # Two different requests raced past the open-session check: keep the oldest.
    dupes = (
        db.query(AIGPSession)
        .filter(
            AIGPSession.student_id == student_id,
            AIGPSession.topic_id == sub.id,
            AIGPSession.status == "in_progress",
        )
        .order_by(AIGPSession.id.asc())
        .all()
    )
    if len(dupes) > 1 and dupes[0].id != session.id:
        # QA B4: this duplicate was never shown - delete it so it does not count as
        # "seen" history or get returned by a request_key replay.
        db.query(AIGPSessionItem).filter(AIGPSessionItem.session_id == session.id).delete(
            synchronize_session=False
        )
        db.query(AIGPSession).filter(AIGPSession.id == session.id).delete(synchronize_session=False)
        db.commit()
        state = session_state(student_id, dupes[0].id)
        state["resumed"] = True
        return state

    remaining_unseen = sum(1 for q in pool if q.id not in history and q.id not in set(picked))
    if remaining_unseen < n and not sub.pool_exhausted:
        try:
            from app.tasks import lms_tasks

            lms_tasks.enqueue_ai_guided_practice_topup(sub.id, len(pool) + n)
        except Exception as exc:  # noqa: BLE001
            logger.info("Pool top-up enqueue failed for subtopic %s: %s", sub.id, exc)
    state = session_state(student_id, session.id)
    state["resumed"] = False
    if gen_error and len(picked) < n:
        state["notice"] = "Fewer new questions were available right now."
    return state


def _render(text: Optional[str], latex: Optional[str], inline: bool) -> str:
    try:
        from app.services.quiz.display_format_qa import student_render

        return student_render(text, latex, inline=inline)
    except Exception:  # noqa: BLE001
        from html import escape

        return escape(text or "")


def session_state(student_id: int, session_id: int) -> dict:
    db = get_db()
    s = _own_session(student_id, session_id)
    sub = db.query(AIGPTopic).filter(AIGPTopic.id == s.topic_id).first()
    main = db.query(AIGPTopic).filter(AIGPTopic.id == sub.parent_id).first() if sub else None
    version = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == s.version_id).first()
    items = (
        db.query(AIGPSessionItem, AIGPQuestion)
        .join(AIGPQuestion, AIGPQuestion.id == AIGPSessionItem.question_id)
        .filter(AIGPSessionItem.session_id == s.id)
        .order_by(AIGPSessionItem.position)
        .all()
    )
    out_items = []
    current = None
    for item, q in items:
        opts = _options(q)
        order = json.loads(item.option_order_json or "[]") or list(range(len(opts)))
        answered = item.answered_at is not None
        if not answered and current is None:
            current = item.position
        entry = {
            "position": item.position,
            "question_text": q.question_text,
            "question_render": _render(q.question_text, q.question_latex, inline=False),
            "difficulty": q.difficulty,
            "options": [
                {
                    "position": pos,
                    "label": _LABELS[pos],
                    "text": opts[orig].get("text"),
                    "render": _render(opts[orig].get("text"), opts[orig].get("latex"), inline=True),
                }
                for pos, orig in enumerate(order)
                if 0 <= orig < len(opts)
            ],
            "answered": answered,
        }
        if answered:
            # Key and explanation leave the server only after the answer is locked in.
            entry.update(_answer_feedback(item, q, order))
        out_items.append(entry)
    return {
        "session_id": s.id,
        "status": s.status,
        "version_id": s.version_id,
        "pdf_filename": version.original_filename if version else None,
        "subtopic_id": s.topic_id,
        "subtopic_title": sub.title if sub else None,
        "main_topic_title": main.title if main else None,
        "is_whole_section": bool(sub.is_whole_section) if sub else False,
        "question_count": s.question_count,
        "answered_count": s.answered_count,
        "correct_count": s.correct_count,
        "incorrect_count": int(s.answered_count or 0) - int(s.correct_count or 0),
        "score_percent": s.score_percent,
        "reused_count": s.reused_count,
        "current_position": current,
        "created_at": _iso(s.created_at),
        "completed_at": _iso(s.completed_at),
        "items": out_items,
    }


def _answer_feedback(item: AIGPSessionItem, q: AIGPQuestion, order: List[int]) -> dict:
    correct_position = order.index(q.correct_option_index) if q.correct_option_index in order else None
    selected_position = (
        order.index(item.selected_original_index) if item.selected_original_index in order else None
    )
    return {
        "correct": bool(item.is_correct),
        "selected_position": selected_position,
        "correct_position": correct_position,
        "correct_label": _LABELS[correct_position] if correct_position is not None else None,
        "explanation": q.explanation,
        "explanation_render": _render(q.explanation, None, inline=False) if q.explanation else None,
        "source_page": q.source_page,
    }


def _lock_session(student_id: int, session_id: int) -> AIGPSession:
    """Own session, row-locked (``FOR UPDATE`` on Postgres) so answer/finish serialise."""
    s = (
        get_db()
        .query(AIGPSession)
        .filter(AIGPSession.id == _safe_id(session_id), AIGPSession.student_id == student_id)
        .with_for_update()
        .first()
    )
    if not s:
        raise LMSNotFoundError("Practice session not found")
    return s


def _refresh_counters(session_id: int) -> None:
    """Recompute answered/correct from the items *in SQL* (never a stale Python copy).

    Answer and Finish can race (QA B2: a finished session scored 10% with 4 correct);
    each writer now recomputes from all committed items, so the last write is right.
    """
    from sqlalchemy import select

    items = AIGPSessionItem.__table__
    answered = (
        select(func.count())
        .where(items.c.session_id == session_id, items.c.answered_at.isnot(None))
        .scalar_subquery()
    )
    correct = (
        select(func.count())
        .where(items.c.session_id == session_id, items.c.answered_at.isnot(None), items.c.is_correct.is_(True))
        .scalar_subquery()
    )
    get_db().query(AIGPSession).filter(AIGPSession.id == session_id).update(
        {AIGPSession.answered_count: answered, AIGPSession.correct_count: correct}, synchronize_session=False
    )


def _finish_if_open(session_id: int, *, only_when_all_answered: bool) -> None:
    """Flip in_progress -> completed exactly once, scoring from the stored counters."""
    db = get_db()
    q = db.query(AIGPSession).filter(AIGPSession.id == session_id, AIGPSession.status == "in_progress")
    if only_when_all_answered:
        q = q.filter(AIGPSession.answered_count >= AIGPSession.question_count)
    q.update({AIGPSession.status: "completed", AIGPSession.completed_at: _now()}, synchronize_session=False)
    s = db.query(AIGPSession).filter(AIGPSession.id == session_id).populate_existing().one()
    if s.status == "completed":
        total = int(s.question_count or 0)
        s.score_percent = round(100.0 * int(s.correct_count or 0) / total, 1) if total else 0.0


def submit_answer(
    student_id: int, session_id: int, position: int, option_position: int, time_spent_seconds=None
) -> dict:
    db = get_db()
    session = _lock_session(student_id, session_id)
    item = (
        db.query(AIGPSessionItem)
        .filter(
            AIGPSessionItem.session_id == session.id,
            AIGPSessionItem.position == _safe_id(position, zero_ok=True),
        )
        .first()
    )
    if not item:
        raise LMSNotFoundError("Question not found in this session")
    q = db.query(AIGPQuestion).filter(AIGPQuestion.id == item.question_id).first()
    order = json.loads(item.option_order_json or "[]") or list(range(len(_options(q))))
    if not 0 <= int(option_position) < len(order):
        raise LMSValidationError("That option does not exist for this question")
    original = order[int(option_position)]
    if item.answered_at is not None:
        if item.selected_original_index == original:  # idempotent retry
            return {**_answer_feedback(item, q, order), "session": _summary(session)}
        raise LMSValidationError("This question was already answered")
    if session.status != "in_progress":
        raise LMSValidationError("This practice session is already finished")
    seconds = None
    if time_spent_seconds is not None:
        try:
            seconds = max(0, min(int(time_spent_seconds), 2 * 60 * 60))
        except (TypeError, ValueError, OverflowError):
            seconds = None
    now = _now()
    open_session = (
        db.query(AIGPSession.id)
        .filter(AIGPSession.id == session.id, AIGPSession.status == "in_progress")
        .scalar_subquery()
    )
    updated = (
        db.query(AIGPSessionItem)
        .filter(
            AIGPSessionItem.id == item.id,
            AIGPSessionItem.answered_at.is_(None),
            # Never record an answer into a session a concurrent Finish already closed.
            AIGPSessionItem.session_id == open_session,
        )
        .update(
            {
                AIGPSessionItem.selected_original_index: original,
                AIGPSessionItem.is_correct: original == q.correct_option_index,
                AIGPSessionItem.answered_at: now,
                AIGPSessionItem.time_spent_seconds: seconds,
            },
            synchronize_session=False,
        )
    )
    if not updated:  # a concurrent submit or finish won
        db.rollback()
        item = db.query(AIGPSessionItem).filter(AIGPSessionItem.id == item.id).populate_existing().one()
        session = db.query(AIGPSession).filter(AIGPSession.id == session.id).populate_existing().one()
        if item.answered_at is not None and item.selected_original_index == original:
            return {**_answer_feedback(item, q, order), "session": _summary(session)}
        if item.answered_at is None:
            raise LMSValidationError("This practice session is already finished")
        raise LMSValidationError("This question was already answered")
    _refresh_counters(session.id)
    db.query(AIGPSession).filter(AIGPSession.id == session.id).update(
        {AIGPSession.last_activity_at: now}, synchronize_session=False
    )
    _finish_if_open(session.id, only_when_all_answered=True)
    db.commit()
    item = db.query(AIGPSessionItem).filter(AIGPSessionItem.id == item.id).populate_existing().one()
    session = db.query(AIGPSession).filter(AIGPSession.id == session.id).populate_existing().one()
    return {**_answer_feedback(item, q, order), "session": _summary(session)}


def complete_session(student_id: int, session_id: int) -> dict:
    db = get_db()
    session = _lock_session(student_id, session_id)
    if session.status == "in_progress":
        _refresh_counters(session.id)
        db.query(AIGPSession).filter(AIGPSession.id == session.id).update(
            {AIGPSession.last_activity_at: _now()}, synchronize_session=False
        )
        _finish_if_open(session.id, only_when_all_answered=False)
        db.commit()
    return session_state(student_id, session.id)


def _summary(session: AIGPSession) -> dict:
    return {
        "session_id": session.id,
        "status": session.status,
        "question_count": session.question_count,
        "answered_count": session.answered_count,
        "correct_count": session.correct_count,
        "incorrect_count": int(session.answered_count or 0) - int(session.correct_count or 0),
        "score_percent": session.score_percent,
    }


def history(student_id: int, limit: int = 50) -> List[dict]:
    db = get_db()
    rows = (
        db.query(AIGPSession, AIGPTopic, AIGPPdfVersion)
        .join(AIGPTopic, AIGPTopic.id == AIGPSession.topic_id)
        .join(AIGPPdfVersion, AIGPPdfVersion.id == AIGPSession.version_id)
        .filter(AIGPSession.student_id == student_id, AIGPSession.status == "completed")
        .order_by(AIGPSession.completed_at.desc(), AIGPSession.id.desc())
        .limit(limit)
        .all()
    )
    active = _active_version_ids(student_id)
    parents = {
        t.id: t.title
        for t in db.query(AIGPTopic).filter(AIGPTopic.id.in_({r[1].parent_id for r in rows if r[1].parent_id})).all()
    } if rows else {}
    return [
        {
            **_summary(s),
            "subtopic_id": t.id,
            "subtopic_title": t.title,
            "main_topic_title": parents.get(t.parent_id),
            "pdf_filename": v.original_filename,
            "version_id": v.id,
            "is_current_pdf": v.id in active,
            "completed_at": _iso(s.completed_at),
        }
        for s, t, v in rows
    ]


# -- admin ------------------------------------------------------------------------


def admin_version_status(thread_ids: Iterable[str]) -> Dict[str, dict]:
    ids = [str(t) for t in thread_ids if t]
    if not ids:
        return {}
    db = get_db()
    versions = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.rag_thread_id.in_(ids)).all()
    counts = dict(
        db.query(AIGPTopic.version_id, func.count(AIGPTopic.id))
        .filter(AIGPTopic.version_id.in_([v.id for v in versions]), AIGPTopic.parent_id.is_(None))
        .group_by(AIGPTopic.version_id)
        .all()
    ) if versions else {}
    return {
        v.rag_thread_id: {
            "version_id": v.id,
            "status": v.status,
            "structure": v.structure,
            "main_topics": int(counts.get(v.id, 0)),
            "error": v.error_message,
            "ready_at": _iso(v.ready_at),
        }
        for v in versions
    }


def reprocess_version(version_id: int) -> dict:
    db = get_db()
    v = db.query(AIGPPdfVersion).filter(AIGPPdfVersion.id == version_id).first()
    if not v:
        raise LMSNotFoundError("Version not found")
    if v.status == "ready":
        raise LMSValidationError("This Target PDF is already processed")
    v = process_version(v.id, force=True)
    return admin_version_status([v.rag_thread_id]).get(v.rag_thread_id, {})
