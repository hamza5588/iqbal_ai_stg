"""Post-diagnostic deficiency chat — questions from teacher target PDF only."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.models.lms_models import AssessmentAttempt, DeficiencyChatSession, StudentProfile
from app.services.lms import assessment_service, tutor_service
from app.services.lms.mcq_utils import (
    is_label_only,
    normalize_options,
    pick_display_fields,
    resolve_correct_option_index,
)
from app.services.lms.diagnostic_service import get_default_diagnostic
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.path_generator import get_weak_topics
from app.services.lms.performance_service import analyze_attempt
from app.services.quiz.diagnostic_generator import generate_mcqs_from_content, get_section_text
from app.utils.db import get_db
from app.utils.rag_service import _get_thread_topics

logger = logging.getLogger(__name__)

PRACTICE_QUESTIONS_PER_WEAK_AREA = 2

# Adaptive difficulty: practice ramps up on correct answers and eases back on
# wrong ones, starting from a rung set by the student's diagnostic score.
_DIFF_LADDER = ["easy", "medium", "hard"]
_DIFF_RANK = {"easy": 0, "medium": 1, "hard": 2}


def _start_rank_for_score(score_percent: float) -> int:
    """Diagnostic score on a weak topic -> starting difficulty rung."""
    if score_percent < 55:
        return 0  # still weak -> begin easy
    return 1


def _ladder_from(start_rank: int) -> List[str]:
    """Difficulty rungs from ``start_rank`` up to hard (>=2 questions)."""
    rungs = _DIFF_LADDER[start_rank:]
    if len(rungs) < 2:
        rungs = _DIFF_LADDER[max(0, start_rank - 1):]
    return rungs


def _get_target_threads(assessment_id: int) -> List[dict]:
    """Return all target PDF threads for a diagnostic."""
    targets = assessment_service.list_target_pdfs(assessment_id)
    if targets:
        return targets
    assessment = assessment_service.get_assessment(assessment_id)
    src = assessment.pdf_source
    if src and src.target_rag_thread_id:
        return [{"rag_thread_id": src.target_rag_thread_id, "original_filename": src.target_original_filename}]
    return []


def _resolve_target_pdf_context(student_id: int) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Return (primary target_rag_thread_id, owner_id, diagnostic_assessment_id)."""
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            targets = _get_target_threads(assessment.id)
            if targets:
                return targets[0]["rag_thread_id"], assessment.created_by, assessment.id
        except LMSNotFoundError:
            pass

    platform_diag = get_default_diagnostic()
    if platform_diag:
        targets = _get_target_threads(platform_diag.id)
        if targets:
            return targets[0]["rag_thread_id"], platform_diag.created_by, platform_diag.id
    return None, None, None


def _resolve_all_target_threads(student_id: int) -> Tuple[List[str], Optional[int], Optional[int]]:
    """Return (all target thread ids, owner_id, diagnostic_assessment_id)."""
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            targets = _get_target_threads(assessment.id)
            if targets:
                return [t["rag_thread_id"] for t in targets], assessment.created_by, assessment.id
        except LMSNotFoundError:
            pass
    platform_diag = get_default_diagnostic()
    if platform_diag:
        targets = _get_target_threads(platform_diag.id)
        if targets:
            return [t["rag_thread_id"] for t in targets], platform_diag.created_by, platform_diag.id
    return [], None, None


_SECTION_STOPWORDS = frozenset(
    {"and", "or", "the", "of", "in", "with", "a", "an", "to", "for", "on",
     "basic", "general", "concepts", "concept", "problems", "problem",
     "skills", "skill", "topics", "topic", "practice", "math", "mathematics"}
)


def _match_target_section(
    weak_area_name: str, target_thread_ids: List[str]
) -> Tuple[str, str, int]:
    """Best target-PDF heading for a weak area, across all target PDFs.

    Returns (section, thread_id, relevance) where relevance is the count of
    shared meaningful words (a substring hit counts as 2). relevance == 0
    means no real match - the caller should not trust this section for the
    skill and should generate grade-appropriate questions instead."""
    name = (weak_area_name or "").strip()
    if not name:
        return name, (target_thread_ids[0] if target_thread_ids else ""), 0

    name_lower = name.lower()
    name_tokens = {t for t in name_lower.split() if t not in _SECTION_STOPWORDS}
    best = None
    best_score = 0
    best_thread = target_thread_ids[0] if target_thread_ids else ""

    for thread_id in target_thread_ids:
        topics = _get_thread_topics(thread_id).get("topics") or []
        for entry in topics:
            heading = (entry.get("topic") or entry.get("heading") or "").strip()
            if not heading:
                continue
            h_lower = heading.lower()
            h_tokens = {t for t in h_lower.split() if t not in _SECTION_STOPWORDS}
            if name_lower in h_lower or h_lower in name_lower:
                score = 2 + len(name_tokens & h_tokens)
            else:
                score = len(name_tokens & h_tokens)
            if score > best_score:
                best_score = score
                best = heading
                best_thread = thread_id
    return (best or name), best_thread, best_score


def _mcq_to_queue_item(
    mcq, topic_id: int, topic_name: str, pdf_section: str, rag_thread_id: str,
    difficulty: str = "medium",
) -> Optional[dict]:
    from app.services.quiz.display_format_qa import (
        finalize_display_text,
        finalize_option_text,
        student_render,
    )

    options = normalize_options(
        [{"label": o.label, "text": o.text, "latex": getattr(o, "latex", None)} for o in mcq.options]
    )
    if any(is_label_only(o.get("text") or "") and is_label_only(o.get("latex") or "") for o in options):
        return None
    correct_idx = resolve_correct_option_index(options, mcq.correct_option_label)
    if correct_idx is None:
        logger.warning("Skipping MCQ with unresolved correct label %s", mcq.correct_option_label)
        return None
    q_text, q_latex = finalize_display_text(mcq.question_text, mcq.question_latex)
    safe_opts = []
    for o in options:
        ot, ol = finalize_option_text(o.get("text"), o.get("latex"))
        safe_opts.append(
            {
                "label": o.get("label"),
                "text": ot,
                "latex": ol,
                "render": student_render(ot, ol, inline=True),
            }
        )
    return {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "pdf_section": pdf_section,
        "rag_thread_id": rag_thread_id,
        "question_text": q_text or mcq.question_text,
        "question_latex": q_latex,
        "question_render": student_render(q_text, q_latex, inline=False),
        "options": safe_opts,
        "correct_option_index": correct_idx,
        "difficulty": difficulty if difficulty in _DIFF_RANK else "medium",
        "source": "target_pdf",
        "answered": False,
        "correct": None,
    }


def _resolve_weak_topic_id(topic_id: int, topic_name: str) -> int:
    """A weak area must map to a real curriculum topic, otherwise its mastery
    can't be tracked and it never clears from Weak Topics. Backfill a missing
    id from the skill name."""
    if topic_id:
        return topic_id
    try:
        from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label

        topic = get_or_create_topic_from_pdf_label(topic_name)
        return topic.id if topic else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve topic id for weak area %s: %s", topic_name, exc)
        return 0


def _question_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:180]


def _prior_question_texts_by_topic(student_id: int) -> Dict[int, List[str]]:
    """Previously served Learning Chat questions, grouped by topic.

    This keeps repeat sessions useful: the same weak/needs-practice topic can
    be practiced again, but the student should see fresh stems.
    """
    db = get_db()
    rows = (
        db.query(DeficiencyChatSession.questions_json)
        .filter(DeficiencyChatSession.student_id == student_id)
        .order_by(DeficiencyChatSession.updated_at.desc())
        .limit(25)
        .all()
    )
    by_topic: Dict[int, List[str]] = {}
    seen: set[str] = set()
    for (raw_questions,) in rows:
        try:
            questions = json.loads(raw_questions or "[]")
        except json.JSONDecodeError:
            continue
        for q in questions:
            topic_id = q.get("topic_id") or 0
            text = (q.get("question_text") or q.get("question_latex") or "").strip()
            key = f"{topic_id}:{_question_key(text)}"
            if not topic_id or not text or key in seen:
                continue
            seen.add(key)
            by_topic.setdefault(topic_id, []).append(text)
    return by_topic


def _grade_appropriate_fallback(
    topic_id: int, topic_name: str, score_percent: float, ladder: List[str],
    grade_level: Optional[str], exclude_question_texts: Optional[List[str]] = None,
) -> List[dict]:
    """When the target PDF has no usable section for a weak area, generate
    grade-appropriate practice questions for the skill instead (DIL: practice
    must still match the student's grade and the actual skill)."""
    try:
        from app.services.quiz.remediation_generator import generate_remediation_mcqs

        mcqs = generate_remediation_mcqs(
            topic_name=topic_name,
            topic_description=topic_name,
            count=len(ladder),
            score_percent=score_percent,
            difficulty=ladder[len(ladder) // 2],
            purpose="practice",
            grade_level=grade_level,
            exclude_question_texts=exclude_question_texts or [],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Grade-fit fallback gen failed for %s: %s", topic_name, exc)
        return []

    items: List[dict] = []
    for i, mcq in enumerate(mcqs):
        diff = ladder[i] if i < len(ladder) else ladder[-1]
        item = _mcq_to_queue_item(mcq, topic_id, topic_name, topic_name, "", diff)
        if item:
            item["source"] = "grade_fit_ai"
            items.append(item)
    return items


def _build_question_queue(
    weak_topics: List[dict],
    target_thread_ids: List[str],
    rag_owner_id: int,
    grade_level: Optional[str] = None,
    used_question_texts_by_topic: Optional[Dict[int, List[str]]] = None,
) -> List[dict]:
    """Generate Learning Chat MCQs, difficulty-laddered per weak area and
    pitched to the student's grade. Prefers the teacher target PDF; falls
    back to AI generation for the skill when the PDF has no usable section."""
    queue: List[dict] = []
    used_question_texts_by_topic = used_question_texts_by_topic or {}
    seen_texts: set[str] = set()

    for entry in weak_topics:
        topic_name = (entry.get("topic_name") or "Practice area").strip()
        topic_id = _resolve_weak_topic_id(entry.get("topic_id") or 0, topic_name)
        score = float(entry.get("score_percent") or 0)
        ladder = _ladder_from(_start_rank_for_score(score))
        exclude_texts = used_question_texts_by_topic.get(topic_id, [])
        exclude_keys = {_question_key(t) for t in exclude_texts if t}
        pdf_section, thread_id, relevance = _match_target_section(topic_name, target_thread_ids)

        section_text = ""
        if relevance > 0:
            section_text = get_section_text(thread_id, rag_owner_id, pdf_section)
            if not section_text.strip():
                for alt_thread in target_thread_ids:
                    if alt_thread == thread_id:
                        continue
                    section_text = get_section_text(alt_thread, rag_owner_id, pdf_section)
                    if section_text.strip():
                        thread_id = alt_thread
                        break
        else:
            logger.info(
                "Weak area %s has no relevant target-PDF section - generating "
                "grade-appropriate questions for the skill", topic_name,
            )

        topic_items: List[dict] = []
        if section_text.strip():
            try:
                mcqs = generate_mcqs_from_content(
                    section_text, pdf_section, len(ladder),
                    grade_level=grade_level, difficulty_ladder=ladder,
                    exclude_question_texts=exclude_texts,
                )
                for i, mcq in enumerate(mcqs):
                    diff = ladder[i] if i < len(ladder) else ladder[-1]
                    item = _mcq_to_queue_item(
                        mcq, topic_id, topic_name, pdf_section, thread_id, diff
                    )
                    key = _question_key((item or {}).get("question_text") or "")
                    if item and key and key not in exclude_keys:
                        topic_items.append(item)
            except Exception as exc:
                logger.warning("Target PDF MCQ gen failed for %s: %s", pdf_section, exc)

        if len(topic_items) < len(ladder):
            logger.info("No target PDF questions for %s - using grade-fit AI", topic_name)
            fallback_ladder = ladder[len(topic_items):] or ladder
            topic_items = topic_items + _grade_appropriate_fallback(
                topic_id,
                topic_name,
                score,
                fallback_ladder,
                grade_level,
                exclude_texts + [q.get("question_text") or "" for q in topic_items],
            )

        for item in topic_items:
            key = _question_key(item.get("question_text") or "")
            if key and key not in exclude_keys and key not in seen_texts:
                seen_texts.add(key)
                queue.append(item)

    return queue


def _load_questions(session: DeficiencyChatSession) -> List[dict]:
    try:
        return json.loads(session.questions_json or "[]")
    except json.JSONDecodeError:
        return []


def _refresh_question_display(q: dict) -> dict:
    """Re-run display finalize on a cached queue item (fixes old \\{ set braces)."""
    from app.services.quiz.display_format_qa import (
        finalize_display_text,
        finalize_option_text,
        student_render,
    )

    out = dict(q)
    q_text, q_latex = finalize_display_text(out.get("question_text"), out.get("question_latex"))
    out["question_text"] = q_text or out.get("question_text")
    out["question_latex"] = q_latex
    out["question_render"] = student_render(q_text, q_latex, inline=False)
    refreshed_opts = []
    for o in out.get("options") or []:
        ot, ol = finalize_option_text(o.get("text"), o.get("latex"))
        refreshed_opts.append(
            {
                **o,
                "text": ot,
                "latex": ol,
                "render": student_render(ot, ol, inline=True),
            }
        )
    out["options"] = refreshed_opts
    return out


def _save_questions(session: DeficiencyChatSession, questions: List[dict]) -> None:
    session.questions_json = json.dumps(questions, ensure_ascii=False)


def _base_rank(session: DeficiencyChatSession) -> int:
    try:
        weak = json.loads(session.weak_topics_json or "[]")
    except json.JSONDecodeError:
        weak = []
    scores = [float(w.get("score_percent") or 0) for w in weak if w.get("score_percent") is not None]
    if not scores:
        return 0
    return _start_rank_for_score(sum(scores) / len(scores))


def _current_target_rank(session: DeficiencyChatSession, questions: List[dict]) -> int:
    """Adaptive difficulty: base rung + 1 per past correct, - 1 per past wrong."""
    rank = _base_rank(session)
    for q in questions[: session.current_index]:
        if not q.get("answered"):
            continue
        rank += 1 if q.get("correct") else -1
    return max(0, min(2, rank))


def _reorder_pending(session: DeficiencyChatSession, questions: List[dict]) -> None:
    """Sort the not-yet-served questions so the next one sits at the student's
    current adaptive difficulty (up on correct, down on wrong)."""
    idx = session.current_index
    tail = questions[idx:]
    if len(tail) <= 1:
        return
    target = _current_target_rank(session, questions)
    tail.sort(
        key=lambda q: (
            abs(_DIFF_RANK.get(q.get("difficulty", "medium"), 1) - target),
            _DIFF_RANK.get(q.get("difficulty", "medium"), 1),
        )
    )
    questions[idx:] = tail


def _session_state(session: DeficiencyChatSession) -> dict:
    questions = _load_questions(session)
    total = len(questions)
    current = None
    if session.current_index < total:
        q = _refresh_question_display(dict(questions[session.current_index]))
        q.pop("correct_option_index", None)
        q["difficulty"] = q.get("difficulty") or "medium"
        current = q
    weak = []
    try:
        weak = json.loads(session.weak_topics_json or "[]")
    except json.JSONDecodeError:
        pass
    tutor_state = _load_tutor_state(session)
    assist_level = tutor_state.get("assist_level", 1)
    tutor_messages = []
    for m in tutor_state.get("messages") or []:
        role = m.get("role", "")
        text = m.get("content") or m.get("text") or ""
        if role == "assistant":
            tutor_messages.append({"role": "bot", "text": text, "levelLabel": ""})
        elif role == "user":
            tutor_messages.append({"role": "user", "text": text})
    session_mode = (
        "enrichment"
        if questions and questions[0].get("source") == "enrichment_ai"
        else "practice"
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "current_index": session.current_index,
        "total_questions": total,
        "correct_count": session.correct_count,
        "weak_topics": weak,
        "current_question": current,
        "mode": session_mode,
        "has_pdf": bool(session.rag_thread_id),
        "pdf_label": "Teacher target content PDF",
        "tutor_assist_level": assist_level,
        "tutor_assist_label": tutor_service.get_deficiency_assist_level_label(assist_level),
        "tutor_messages": tutor_messages,
        "completed": session.status == "completed",
    }


def _close_old_sessions(student_id: int) -> None:
    db = get_db()
    sessions = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id == student_id,
            DeficiencyChatSession.status.in_(("active", "paused")),
        )
        .all()
    )
    for session in sessions:
        session.status = "completed"
    db.commit()


def get_active_session(
    student_id: int, diagnostic_assessment_id: Optional[int] = None
) -> Optional[DeficiencyChatSession]:
    db = get_db()
    q = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id == student_id,
            DeficiencyChatSession.status.in_(("active", "paused")),
        )
        .order_by(DeficiencyChatSession.updated_at.desc())
    )
    if diagnostic_assessment_id:
        q = q.filter(DeficiencyChatSession.diagnostic_assessment_id == diagnostic_assessment_id)
    return q.first()


def _latest_diagnostic_attempt(student_id: int, assessment_id: int) -> Optional[AssessmentAttempt]:
    db = get_db()
    return (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .order_by(AssessmentAttempt.submitted_at.desc())
        .first()
    )


def _enrichment_areas(student_id: int, diag_id: Optional[int]) -> List[dict]:
    """Topics to stretch a student who has no weak areas: their strongest
    diagnostic topics, so the challenge builds on what they already know."""
    from app.services.lms import performance_service

    rows = performance_service.get_student_mastery(student_id) or []
    strong = sorted(
        (r for r in rows if (r.get("score_percent") or 0) >= 60 and r.get("topic_id")),
        key=lambda r: -(r.get("score_percent") or 0),
    )[:3]
    if strong:
        return [
            {
                "topic_id": r["topic_id"],
                "topic_name": r.get("topic_name") or f"Topic #{r['topic_id']}",
                "score_percent": r.get("score_percent") or 0,
            }
            for r in strong
        ]
    if diag_id:
        attempt = _latest_diagnostic_attempt(student_id, diag_id)
        if attempt:
            try:
                analysis = analyze_attempt(attempt.id)
            except Exception:  # noqa: BLE001
                analysis = {}
            areas = (analysis.get("strong_topics") or []) or (analysis.get("all_topics") or [])
            return list(areas)[:3]
    return []


def _build_enrichment_queue(areas: List[dict], grade_level: Optional[str]) -> List[dict]:
    """Above-level challenge questions for a student with no weak areas."""
    from app.services.quiz.remediation_generator import generate_remediation_mcqs

    ladder = ["medium", "hard"]
    queue: List[dict] = []
    seen: set[str] = set()
    for area in areas:
        name = (area.get("topic_name") or "Challenge area").strip()
        tid = _resolve_weak_topic_id(area.get("topic_id") or 0, name)
        try:
            mcqs = generate_remediation_mcqs(
                topic_name=name,
                topic_description=name,
                count=len(ladder),
                score_percent=float(area.get("score_percent") or 0),
                difficulty="hard",
                purpose="enrichment",
                grade_level=grade_level,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enrichment question gen failed for %s: %s", name, exc)
            continue
        for i, mcq in enumerate(mcqs):
            diff = ladder[i] if i < len(ladder) else "hard"
            item = _mcq_to_queue_item(mcq, tid, name, name, "", diff)
            if not item:
                continue
            item["source"] = "enrichment_ai"
            key = (item.get("question_text") or "")[:120].lower()
            if key and key not in seen:
                seen.add(key)
                queue.append(item)
    return queue


def start_session(
    student_id: int, force_new: bool = False, mode: str = "practice"
) -> dict:
    """Start Learning Chat.

    mode="practice"   -> difficulty-laddered questions on weak areas
    mode="enrichment" -> above-level challenge questions when there are no
                         weak areas (DIL: the practice section must never be
                         empty after a clean diagnostic).
    """
    enrichment = mode == "enrichment"
    target_thread_ids, rag_owner_id, diag_id = _resolve_all_target_threads(student_id)
    target_thread_id = target_thread_ids[0] if target_thread_ids else None

    if not enrichment and (not target_thread_id or not rag_owner_id):
        raise LMSValidationError(
            "No target content PDF has been uploaded yet. "
            "Learning Chat uses study material PDFs for weak areas — ask your admin."
        )

    if force_new:
        _close_old_sessions(student_id)

    existing = None if force_new else get_active_session(student_id, diag_id)
    if existing and (enrichment or existing.rag_thread_id == target_thread_id):
        if existing.status == "paused":
            existing.status = "active"
            get_db().commit()
        return _session_state(existing)

    from app.services.lms import class_service

    grade_level = class_service.get_student_grade(student_id)

    if enrichment:
        areas = _enrichment_areas(student_id, diag_id)
        if not areas:
            raise LMSValidationError(
                "Finish your diagnostic assessment first to unlock challenge questions."
            )
        queue = _build_enrichment_queue(areas, grade_level)
        if not queue:
            raise LMSValidationError(
                "Could not generate challenge questions right now — please try again shortly."
            )
        payload = [
            {
                "topic_id": a.get("topic_id", 0),
                "topic_name": a.get("topic_name") or "Challenge area",
                "score_percent": a.get("score_percent", 0),
                "question_ids": [],
            }
            for a in areas
        ]
        db = get_db()
        session = DeficiencyChatSession(
            student_id=student_id,
            diagnostic_assessment_id=diag_id,
            rag_thread_id=None,
            rag_owner_id=rag_owner_id,
            weak_topics_json=json.dumps(payload, ensure_ascii=False),
            questions_json=json.dumps(queue, ensure_ascii=False),
            status="active",
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return _session_state(session)

    weak = get_weak_topics(student_id)
    if not weak:
        raise LMSValidationError(
            "No weak areas found. Complete your diagnostic assessment first."
        )

    if diag_id:
        attempt = _latest_diagnostic_attempt(student_id, diag_id)
        if attempt:
            analysis = analyze_attempt(attempt.id)
            ai_weak = analysis.get("weak_topics") or []
            if ai_weak:
                # Use the AI weak areas (they carry question_ids + names for PDF
                # section matching) but keep only the ones that are STILL weak in
                # live mastery — so a second Learning Chat session targets what
                # the student has not practised yet, instead of re-serving the
                # frozen diagnostic list every time.
                live_weak_ids = {w.get("topic_id") for w in weak if w.get("topic_id")}
                if live_weak_ids:
                    still_weak = [w for w in ai_weak if w.get("topic_id") in live_weak_ids]
                    weak = still_weak or ai_weak
                else:
                    weak = ai_weak

    prior_questions = _prior_question_texts_by_topic(student_id)
    queue = _build_question_queue(
        weak,
        target_thread_ids,
        rag_owner_id,
        grade_level,
        used_question_texts_by_topic=prior_questions,
    )
    if not queue:
        raise LMSValidationError(
            "Could not generate questions from the target PDF(s) for your weak areas. "
            "Ask your admin to check the target content PDFs cover those topics."
        )

    weak_payload = [
        {
            "topic_id": w.get("topic_id", 0),
            "topic_name": w.get("topic_name") or "Practice area",
            "score_percent": w.get("score_percent", 0),
            "question_ids": w.get("question_ids") or [],
        }
        for w in weak
    ]

    db = get_db()
    session = DeficiencyChatSession(
        student_id=student_id,
        diagnostic_assessment_id=diag_id,
        rag_thread_id=target_thread_id,
        rag_owner_id=rag_owner_id,
        weak_topics_json=json.dumps(weak_payload, ensure_ascii=False),
        questions_json=json.dumps(queue, ensure_ascii=False),
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_state(session)


def prewarm_session(student_id: int) -> bool:
    """
    Best-effort: build the Learning Chat question queue right after the student
    submits their diagnostic, so the later ``POST /deficiency/sessions`` call is a
    cheap DB read instead of a burst of live Groq MCQ generation.

    Spreading MCQ generation across the diagnostic-submit window (instead of
    concentrating it when every student opens Learning Chat at once) is what keeps
    Groq under its per-minute token cap at high concurrency. Any failure here is
    swallowed — Learning Chat start will simply generate on demand as before.

    Returns True when a session queue was warmed.
    """
    try:
        state = start_session(student_id, force_new=False)
        return bool(state.get("total_questions"))
    except LMSValidationError as exc:
        logger.info("Learning Chat prewarm skipped for student %s: %s", student_id, exc)
        return False
    except Exception as exc:  # noqa: BLE001 — prewarm must never break submit
        logger.warning("Learning Chat prewarm failed for student %s: %s", student_id, exc)
        return False


def get_session(session_id: int, student_id: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session:
        raise LMSNotFoundError("Session not found")
    if session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    return _session_state(session)


def _reset_tutor_state(session: DeficiencyChatSession) -> None:
    session.chat_history_json = json.dumps(
        {
            "question_index": session.current_index,
            "assist_level": 1,
            "messages": [],
        },
        ensure_ascii=False,
    )


def submit_answer(session_id: int, student_id: int, selected_option_index: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        raise LMSValidationError("No more questions in this session")

    q = questions[idx]
    is_correct = selected_option_index == q.get("correct_option_index")
    already_correct = q.get("correct") is True

    just_completed = False
    if is_correct:
        q["answered"] = True
        q["correct"] = True
        if not already_correct:
            session.correct_count += 1
        session.current_index = idx + 1
        _reset_tutor_state(session)
        if session.current_index >= len(questions):
            session.status = "completed"
            just_completed = True
        else:
            # Adaptive: pick the next question at the new (higher) difficulty.
            _reorder_pending(session, questions)
    else:
        q["answered"] = True
        q["correct"] = False

    _save_questions(session, questions)
    db.commit()

    if just_completed:
        # Must run after the commit above so questions_json (with this final
        # answer) is what gets read back - see performance_service docstring.
        from app.services.lms import performance_service
        performance_service.update_topic_scores_from_deficiency_session(session_id)
        _mark_learning_path_chat_complete(student_id)

    result = _session_state(session)
    result["last_answer"] = {
        "correct": is_correct,
        "explanation_available": not is_correct,
        "topic_name": q.get("topic_name"),
        "stay_on_question": not is_correct,
    }
    return result


def advance_session(session_id: int, student_id: int) -> dict:
    """Skip the current question (after an incorrect attempt) and close the tutor."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        session.status = "completed"
        db.commit()
        from app.services.lms import performance_service
        performance_service.update_topic_scores_from_deficiency_session(session_id)
        _mark_learning_path_chat_complete(student_id)
        return _session_state(session)

    q = questions[idx]
    if not q.get("answered"):
        q["answered"] = True
        q["correct"] = False
        _save_questions(session, questions)

    session.current_index = idx + 1
    _reset_tutor_state(session)
    just_completed = session.current_index >= len(questions)
    if just_completed:
        session.status = "completed"
    else:
        _reorder_pending(session, questions)
        _save_questions(session, questions)
    db.commit()

    if just_completed:
        # Must run after the commit above so questions_json (with the skipped
        # question just marked incorrect) is what gets read back.
        from app.services.lms import performance_service
        performance_service.update_topic_scores_from_deficiency_session(session_id)
        _mark_learning_path_chat_complete(student_id)
    return _session_state(session)


def pause_session(session_id: int, student_id: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    session.status = "paused"
    db.commit()
    return _session_state(session)


def _load_tutor_state(session: DeficiencyChatSession) -> dict:
    """Per-question tutor history and assistance level (1–5)."""
    try:
        raw = json.loads(session.chat_history_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if isinstance(raw, list):
        raw = {"question_index": session.current_index, "assist_level": 1, "messages": raw}
    if raw.get("question_index") != session.current_index:
        return {"question_index": session.current_index, "assist_level": 1, "messages": []}
    return {
        "question_index": session.current_index,
        "assist_level": int(raw.get("assist_level") or 1),
        "messages": raw.get("messages") or [],
    }


def _save_tutor_state(session: DeficiencyChatSession, state: dict) -> None:
    session.chat_history_json = json.dumps(
        {
            "question_index": state.get("question_index", session.current_index),
            "assist_level": state.get("assist_level", 1),
            "messages": (state.get("messages") or [])[-20:],
        },
        ensure_ascii=False,
    )


def explain_with_tutor(
    session_id: int,
    student_id: int,
    message: str,
    api_key: str = "",
) -> dict:
    """PDF-grounded tutor with 5-level step-by-step assistance."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")

    questions = _load_questions(session)
    current_q = questions[session.current_index] if session.current_index < len(questions) else None

    pdf_excerpt = ""
    pdf_section = (current_q or {}).get("pdf_section") or (current_q or {}).get("topic_name") or ""
    thread_for_excerpt = (current_q or {}).get("rag_thread_id") or session.rag_thread_id
    if thread_for_excerpt and session.rag_owner_id and pdf_section:
        pdf_excerpt = get_section_text(
            thread_for_excerpt, session.rag_owner_id, pdf_section, max_chars=4000
        )
        if not pdf_excerpt.strip():
            diag_id = session.diagnostic_assessment_id
            if diag_id:
                for alt_thread in _get_target_threads(diag_id):
                    alt_id = alt_thread.get("rag_thread_id")
                    if not alt_id or alt_id == thread_for_excerpt:
                        continue
                    pdf_excerpt = get_section_text(alt_id, session.rag_owner_id, pdf_section, max_chars=4000)
                    if pdf_excerpt.strip():
                        break

    tutor_state = _load_tutor_state(session)
    assist_level = min(max(int(tutor_state.get("assist_level") or 1), 1), 5)
    history = tutor_state.get("messages") or []
    prior_turns = sum(1 for m in history if m.get("role") == "assistant")

    from app.services.lms import class_service

    grade_level = class_service.get_student_grade(student_id)

    context = tutor_service.build_deficiency_context(
        weak_topics_json=session.weak_topics_json,
        current_question=current_q,
        pdf_excerpt=pdf_excerpt,
        assist_level=assist_level,
        grade_level=grade_level,
        prior_turns=prior_turns,
        message=message,
    )
    reply = tutor_service.tutor_chat(
        message,
        api_key=api_key,
        mode="deficiency",
        context=context,
        history=history,
        assist_level=assist_level,
    )

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    tutor_state["messages"] = history
    tutor_state["assist_level"] = min(assist_level + 1, 5)
    tutor_state["question_index"] = session.current_index
    _save_tutor_state(session, tutor_state)
    db.commit()

    return {
        "reply": reply,
        "session_id": session_id,
        "assist_level": assist_level,
        "assist_level_label": tutor_service.get_deficiency_assist_level_label(assist_level),
        "next_assist_level": tutor_state["assist_level"],
        "next_assist_level_label": tutor_service.get_deficiency_assist_level_label(
            tutor_state["assist_level"]
        ),
    }


def _mark_learning_path_chat_complete(student_id: int) -> None:
    """After a Learning Chat session: update path from live Weak topics.

    One session (e.g. only Inequality) must NOT mark the whole path 100%
    complete while other topics are still Weak. Path completes only when
    no Weak topics remain (or this was an enrichment challenge).
    """
    from app.services.lms import learning_path_service, performance_service
    from app.models.lms_models import LearningPath

    db = get_db()
    path = learning_path_service.get_active_path_for_student(student_id)
    if not path:
        path = (
            db.query(LearningPath)
            .filter(
                LearningPath.student_id == student_id,
                LearningPath.status.in_(("active", "completed")),
            )
            .order_by(LearningPath.id.desc())
            .first()
        )
    if not path:
        return

    is_enrichment = any(i.item_type == "enrichment" for i in path.items)
    mastery = performance_service.get_student_mastery(student_id)
    still_weak = [m for m in mastery if performance_service.is_weak_mastery(m)]

    if is_enrichment or (mastery and not still_weak):
        for item in path.items:
            if item.item_type in ("practice", "enrichment") and item.status != "completed":
                item.status = "completed"
                item.completed_at = datetime.utcnow()
        path.status = "completed"
        path.updated_at = datetime.utcnow()
        db.commit()
        return

    # Session finished but Weak topics remain — keep path open / in progress.
    for item in path.items:
        if item.item_type == "practice" and int(item.item_id or 0) == 0:
            item.status = "in_progress"
            item.completed_at = None
    path.status = "active"
    path.updated_at = datetime.utcnow()
    db.commit()
