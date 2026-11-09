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
    shuffle_options,
)
from app.services.lms.diagnostic_service import get_default_diagnostic, get_student_diagnostic
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


MAX_QUESTIONS_PER_WEAK_AREA = 10


def _question_count_for_score(score_percent: float) -> int:
    """How many Learning Chat questions a weak topic gets: one per 10% the student
    was short of full marks in it (50% -> 5 questions, 20% -> 8), at least 1."""
    missing = max(0.0, 100.0 - float(score_percent or 0))
    return max(1, min(MAX_QUESTIONS_PER_WEAK_AREA, -int(-missing // 10)))


# Hard ceiling when a topic needs more than the score rule gives (see _note_outstanding_misses).
MAX_QUESTIONS_TO_CLEAR_TOPIC = 20

# Extra attempts using the same PDF text when answer-key checks drop questions.
_TOP_UP_ROUNDS = 2
_PDF_ONLY_POLICY = "pdf_slo_only_v2"
_PDF_COVERAGE_ERROR = (
    "No verified questions are available from the target PDF for this topic. "
    "Ask your admin to check that the target PDF covers this topic."
)


def _note_outstanding_misses(student_id: int, weak: List[dict]) -> List[dict]:
    """Tag each weak area with how many missed questions the student still has on it.

    Mastery works off outstanding misses: every correct Learning Chat answer cancels one
    (performance_service._upsert_topic_score). The score rule above gives enough questions
    for that as long as the topic was assessed on 10 questions or fewer; on a bigger topic
    (12 questions, 6 wrong -> 5 questions) a student could answer everything correctly and
    still be left Weak. ``min_questions`` lets the queue cover every outstanding miss so
    completing the topic's Learning Chat always completes the topic.
    """
    from app.models.lms_models import StudentTopicScore

    weak = [dict(w) for w in weak]  # never write the tag into the cached diagnostic analysis
    ids = [int(w.get("topic_id") or 0) for w in weak if w.get("topic_id")]
    if not ids:
        return weak
    rows = (
        get_db()
        .query(StudentTopicScore)
        .filter(StudentTopicScore.student_id == student_id, StudentTopicScore.topic_id.in_(ids))
        .all()
    )
    misses = {
        r.topic_id: round((r.sample_size or 1) * (100.0 - (r.score_percent or 0)) / 100.0) for r in rows
    }
    for w in weak:
        w["min_questions"] = misses.get(int(w.get("topic_id") or 0), 0)
    return weak


def _difficulty_plan(score_percent: float, count: int) -> List[str]:
    """``count`` difficulties climbing the ladder for this score, easiest first."""
    rungs = _ladder_from(_start_rank_for_score(score_percent))
    return [rungs[min(len(rungs) - 1, i * len(rungs) // count)] for i in range(count)]


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

    platform_diag = get_student_diagnostic(student_id)  # this grade's study PDFs, not the newest paper of any grade
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
    platform_diag = get_student_diagnostic(student_id)
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


# "All of the above", "Both A and B", "A and C" only make sense in their printed position.
_POSITIONAL_OPTION_WORDS = re.compile(
    r"\b(?:all|none|both|neither)\b(?:\s+of)?(?:\s+(?:the|these))?\s+(?:above|options|choices)\b"
    r"|\b(?:all|none)\s+of\s+these\b|\bboth\s+[A-D]\b",
    re.IGNORECASE,
)
_POSITIONAL_OPTION_LETTERS = re.compile(r"\b[A-D]\s*(?:,|&|and|or)\s*[A-D]\b")


def _has_positional_option(options: List[dict]) -> bool:
    for opt in options:
        text = f"{opt.get('text') or ''} {opt.get('latex') or ''}"
        if _POSITIONAL_OPTION_WORDS.search(text) or _POSITIONAL_OPTION_LETTERS.search(text):
            return True
    return False


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
    # LLMs put the right answer in A/B far more often than chance; shuffle like the question bank.
    options, correct_idx = shuffle_options(
        options, correct_idx,
        preserve_order=bool(getattr(mcq, "preserve_option_order", False)) or _has_positional_option(options),
    )
    stem = (mcq.question_text or "").strip()
    prose = re.sub(r"\\[A-Za-z]+", " ", stem)  # ignore LaTeX commands (\div, \times, \frac)
    stem_words = len(re.findall(r"[A-Za-z]{3,}", prose))
    if stem_words < 1:
        # An expression with no question ("(a+b+c)^{2}" + four numbers) cannot be answered.
        logger.warning("Skipping generated MCQ with no question wording: %r", stem[:80])
        return None
    q_latex_in = getattr(mcq, "question_latex", None)
    if q_latex_in and stem.endswith("?") and len(re.findall(r"[A-Za-z]{2,}", prose)) >= 6:
        # The stem is already a complete question; appending the generator's LaTeX
        # duplicated the expression or leaked the solving formula.
        q_latex_in = None
    q_text, q_latex = finalize_display_text(mcq.question_text, q_latex_in)
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
        "generated_concept": (getattr(mcq, "learning_concept", None) or "").strip() or None,
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


def _served_question_keys(student_id: int, exclude_session_id: Optional[int] = None) -> set:
    """Question keys already served to the student in other Learning Chat sessions."""
    query = get_db().query(DeficiencyChatSession.questions_json).filter(
        DeficiencyChatSession.student_id == student_id
    )
    if exclude_session_id:
        query = query.filter(DeficiencyChatSession.id != exclude_session_id)
    keys: set = set()
    for (raw_questions,) in query.all():
        try:
            questions = json.loads(raw_questions or "[]")
        except json.JSONDecodeError:
            continue
        for q in questions:
            key = _question_key(q.get("question_text") or "")
            if key:
                keys.add(key)
    return keys


def _approved_scopes(entry: dict, topic_name: str) -> List[str]:
    """Trusted printed Topics/SLOs, or the approved broad topic for legacy papers."""
    values = entry.get("slo_names") or []
    if isinstance(values, str):
        values = [values]
    scopes: List[str] = []
    seen: set[str] = set()
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "").strip())
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            scopes.append(clean)
    return scopes or [topic_name]


_SEMANTIC_BANK_MIN_SCORE = 0.28
_SEMANTIC_BANK_CANDIDATE_CAP = 80


def _weak_area_context(
    scope: str, topic_name: str, grade_level: Optional[str] = None
) -> str:
    """Short context the intelligent layer uses to find related Target PDF questions."""
    parts = [
        f"Weak area / SLO: {(scope or '').strip()}",
        f"Mathematics domain: {(topic_name or '').strip() or 'General'}",
    ]
    if grade_level:
        parts.append(f"Grade level: {grade_level}")
    parts.append(
        "Select practice questions from the Target PDF that teach or assess this "
        f"weak area by meaning, not by exact heading match: {(scope or '').strip()}."
    )
    return "\n".join(parts)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _build_scope_queue(
    *,
    topic_id: int,
    topic_name: str,
    approved_scope: str,
    ladder: List[str],
    target_thread_ids: List[str],
    rag_owner_id: int,
    grade_level: Optional[str],
    exclude_texts: List[str],
    exclude_keys: set[str],
    seen_texts: set[str],
) -> List[dict]:
    """Select/generate questions for one weak SLO via AI Target-PDF relevance."""
    from app.services.quiz.mcq_answer_check import verify_mcq_keys
    from app.services.quiz.mcq_grounding_check import verify_mcq_grounding

    context = _weak_area_context(approved_scope, topic_name, grade_level)
    thread_id = target_thread_ids[0] if target_thread_ids else ""
    pdf_section = approved_scope

    def _fresh(items: List[dict]) -> List[dict]:
        out = []
        for item in items:
            key = _question_key(item.get("question_text") or "")
            if key and key not in exclude_keys and key not in seen_texts:
                seen_texts.add(key)
                out.append(item)
        return out

    kept: List[dict] = []

    # 1) Primary: approved Target-bank MCQs ranked by embedding relevance.
    bank_fill = _bank_items_as_queue(
        topic_id=topic_id,
        topic_name=topic_name,
        approved_scope=approved_scope,
        ladder=ladder,
        target_thread_ids=target_thread_ids,
        grade_level=grade_level,
        exclude_keys=exclude_keys,
        seen_texts=seen_texts,
        context=context,
    )
    kept.extend(bank_fill)
    if len(kept) >= len(ladder):
        return kept[: len(ladder)]

    # 2) Textbook / leftover: RAG semantic chunks for this weak-area context.
    section_text = ""
    rag_text, rag_thread = _rag_semantic_section_text(
        context, target_thread_ids, rag_owner_id
    )
    if rag_text.strip():
        section_text = rag_text
        thread_id = rag_thread or thread_id
        pdf_section = approved_scope

    # 3) Thin fallback: exact heading / exact Subtopic bank grounding only.
    if not section_text.strip():
        pdf_section, thread_id, relevance = _match_target_section(
            approved_scope, target_thread_ids
        )
        lookups = []
        if relevance > 0:
            lookups.extend(
                (tid, pdf_section) for tid in [thread_id] + target_thread_ids if tid
            )
        lookups.extend((tid, approved_scope) for tid in target_thread_ids if tid)
        for candidate_thread, heading in dict.fromkeys(lookups):
            try:
                section_text = get_section_text(
                    candidate_thread,
                    rag_owner_id,
                    heading,
                    require_topic_match=True,
                )
            except Exception as exc:  # noqa: BLE001 - no ungrounded fallback
                logger.warning("Could not read target-PDF text for %s: %s", heading, exc)
                continue
            if _looks_like_answer_key_table(section_text):
                logger.info(
                    "Target-PDF text for %s is an answer-key table; not used as source",
                    heading,
                )
                section_text = ""
                continue
            if section_text.strip():
                thread_id = candidate_thread
                pdf_section = heading
                break

    if not section_text.strip():
        exact_text, bank_thread = _exact_bank_source_text(
            topic_id, approved_scope, grade_level, target_thread_ids
        )
        if exact_text.strip():
            section_text = exact_text
            thread_id = bank_thread or thread_id
            pdf_section = approved_scope

    attempted_texts: List[str] = []
    if not section_text.strip():
        logger.warning(
            "No Target-PDF grounding for approved SLO %s after semantic bank + RAG",
            approved_scope,
        )
    else:
        max_rounds = (len(ladder) - len(kept) + 9) // 10 + _TOP_UP_ROUNDS
        for _round in range(max(1, max_rounds)):
            if len(kept) >= len(ladder):
                break
            requested = ladder[len(kept) : len(kept) + 10]
            try:
                mcqs = generate_mcqs_from_content(
                    section_text,
                    approved_scope,
                    len(requested),
                    grade_level=grade_level,
                    difficulty_ladder=requested,
                    exclude_question_texts=exclude_texts + attempted_texts,
                    strict_grounding=True,
                )
            except Exception as exc:  # noqa: BLE001 - no topic-only fallback
                logger.warning("Target PDF MCQ gen failed for %s: %s", approved_scope, exc)
                break
            if not mcqs:
                break
            scope_items = []
            for index, mcq in enumerate(mcqs):
                difficulty = requested[index] if index < len(requested) else requested[-1]
                item = _mcq_to_queue_item(
                    mcq,
                    topic_id,
                    topic_name,
                    pdf_section,
                    thread_id,
                    difficulty,
                )
                if item:
                    item["generation_policy"] = _PDF_ONLY_POLICY
                    attempted_texts.append(item.get("question_text") or "")
                    scope_items.append(item)
            grounded = verify_mcq_grounding(scope_items, section_text, approved_scope)
            kept.extend(verify_mcq_keys(_fresh(grounded)))

    if len(kept) < len(ladder):
        logger.warning(
            "%s / %s: only %d of %d source-verified questions",
            topic_name,
            approved_scope,
            len(kept),
            len(ladder),
        )
    return kept[: len(ladder)]


_KEY_ROW_RE = re.compile(r"(?m)^\s*\d{1,3}\s+[A-D]\b")


def _looks_like_answer_key_table(text: str) -> bool:
    """True for answer-key / mapping-table text ("62 C Numbers Real and ...")."""
    if not text:
        return False
    rows = len(_KEY_ROW_RE.findall(text))
    inline = len(re.findall(r"\b\d{1,3}\s+[A-D]\s+(?:Numbers|Algebra|Geometry|Statistics)\b", text))
    return rows >= 5 or inline >= 5


def _rag_semantic_section_text(
    context: str,
    target_thread_ids: List[str],
    rag_owner_id: int,
    max_chars: int = 12000,
) -> Tuple[str, Optional[str]]:
    """Retrieve Target PDF chunks by meaning for the weak-area context."""
    if not context.strip() or not target_thread_ids or not rag_owner_id:
        return "", None
    try:
        from app.utils.rag_service import _get_retriever
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG retriever unavailable for semantic section: %s", exc)
        return "", None

    parts: List[str] = []
    used_thread = None
    for tid in target_thread_ids:
        if not tid:
            continue
        try:
            retriever = _get_retriever(str(tid), int(rag_owner_id))
            if not retriever:
                continue
            docs = retriever.invoke(context) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Semantic RAG failed for thread %s: %s", tid, exc)
            continue
        chunk_parts = []
        for doc in docs:
            text = (getattr(doc, "page_content", None) or "").strip()
            if not text or _looks_like_answer_key_table(text):
                continue
            chunk_parts.append(text)
        if chunk_parts:
            parts = chunk_parts
            used_thread = tid
            break
    if not parts:
        return "", None
    combined = "\n\n".join(parts).strip()
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[... truncated ...]"
    return combined, used_thread


def _target_bank_doc_filters(target_thread_ids: List[str]):
    """Resolve Target bank documents that belong to these diagnostic Target PDFs."""
    from app.models.lms_models import DiagnosticTargetPdf, TargetBankDocument

    db = get_db()
    filenames = set()
    assessment_ids = set()
    if target_thread_ids:
        for row in (
            db.query(DiagnosticTargetPdf)
            .filter(DiagnosticTargetPdf.rag_thread_id.in_(target_thread_ids))
            .all()
        ):
            name = (row.original_filename or "").strip().lower()
            if name:
                filenames.add(name)
            if row.assessment_id:
                assessment_ids.add(int(row.assessment_id))
    doc_ids = set()
    if filenames or assessment_ids:
        q = db.query(TargetBankDocument.id, TargetBankDocument.original_filename, TargetBankDocument.assessment_id)
        for doc_id, fname, assessment_id in q.all():
            fn = (fname or "").strip().lower()
            if fn and fn in filenames:
                doc_ids.add(int(doc_id))
            elif assessment_id and int(assessment_id) in assessment_ids:
                doc_ids.add(int(doc_id))
    return doc_ids, filenames


def _load_approved_bank_candidates(
    topic_id: int,
    target_thread_ids: List[str],
    *,
    limit: int = _SEMANTIC_BANK_CANDIDATE_CAP,
):
    """Approved Target-bank rows for this diagnostic's PDFs, Domain-first then widen."""
    from app.models.lms_models import Question, QuestionTaxonomy, TargetBankDocument, TargetBankItem

    db = get_db()
    doc_ids, _filenames = _target_bank_doc_filters(target_thread_ids)
    # Only scan bank rows that belong to this diagnostic's Target PDF(s).
    if not doc_ids:
        return []

    base = (
        db.query(TargetBankItem, Question, TargetBankDocument, QuestionTaxonomy)
        .join(Question, Question.id == TargetBankItem.question_id)
        .join(TargetBankDocument, TargetBankDocument.id == TargetBankItem.document_id)
        .outerjoin(QuestionTaxonomy, QuestionTaxonomy.question_id == Question.id)
        .filter(
            TargetBankItem.verification_status == "approved",
            Question.is_active.is_(True),
            TargetBankItem.document_id.in_(list(doc_ids)),
        )
    )

    domain_rows = []
    if topic_id:
        domain_rows = (
            base.filter(QuestionTaxonomy.domain_topic_id == int(topic_id))
            .order_by(TargetBankItem.source_question_number)
            .limit(limit)
            .all()
        )
    rows = domain_rows
    if len(rows) < max(4, limit // 4):
        wider = (
            base.order_by(TargetBankItem.source_question_number)
            .limit(limit)
            .all()
        )
        seen = {item.id for item, _q, _d, _t in rows}
        for row in wider:
            if row[0].id in seen:
                continue
            rows.append(row)
            seen.add(row[0].id)
            if len(rows) >= limit:
                break
    return rows


def _rank_bank_rows_by_context(rows, context: str, limit: int, scope: str = ""):
    """Embed weak-area context + bank stems; keep top related approved items."""
    if not rows or not (context or "").strip():
        return []

    def _as_triple(row):
        return (row[0], row[1], row[2])

    def _exactish(row) -> bool:
        tax = row[3] if len(row) > 3 else None
        printed = ((getattr(tax, "printed_topic", None) or "") if tax is not None else "").casefold()
        want = (scope or "").casefold()
        return bool(want and printed and (want == printed or want in printed or printed in want))

    try:
        from app.utils.rag_service import get_rag_embeddings

        embeddings = get_rag_embeddings()
        query_vec = embeddings.embed_query(context)
        texts = []
        for _item, question, _doc, taxonomy in rows:
            printed = ""
            if taxonomy is not None:
                printed = (getattr(taxonomy, "printed_topic", None) or "") or ""
            stem = (question.question_text or "").strip()
            texts.append(f"{printed}\n{stem}".strip() if printed else stem)
        doc_vecs = embeddings.embed_documents(texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Semantic bank ranking failed; exact-subtopic fallback: %s", exc)
        exact_rows = [r for r in rows if _exactish(r)]
        chosen = exact_rows or rows
        return [_as_triple(chosen[i]) for i in range(min(limit, len(chosen)))]

    scored = []
    for idx, row in enumerate(rows):
        score = _cosine_similarity(query_vec, doc_vecs[idx] if idx < len(doc_vecs) else [])
        if score < _SEMANTIC_BANK_MIN_SCORE:
            continue
        scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], x[1][0].source_question_number or 0))
    if not scored and rows:
        # Soft floor: still return the closest few so Learning Chat can open.
        soft = []
        for idx, row in enumerate(rows):
            score = _cosine_similarity(query_vec, doc_vecs[idx] if idx < len(doc_vecs) else [])
            soft.append((score, row))
        soft.sort(key=lambda x: (-x[0], x[1][0].source_question_number or 0))
        scored = soft[: max(limit, 1)]
        logger.info(
            "Semantic bank used soft top-%d (below floor %.2f) for context",
            len(scored),
            _SEMANTIC_BANK_MIN_SCORE,
        )
    out = []
    for score, row in scored[:limit]:
        out.append(_as_triple(row))
        logger.debug(
            "Semantic bank pick q#%s score=%.3f",
            row[0].source_question_number,
            score,
        )
    return out


def _semantic_bank_rows(
    topic_id: int,
    scope: str,
    topic_name: str,
    grade_level: Optional[str],
    target_thread_ids: List[str],
    limit: int = 12,
    context: Optional[str] = None,
):
    """Approved Target PDF bank MCQs related to the weak area by meaning."""
    ctx = context or _weak_area_context(scope, topic_name, grade_level)
    candidates = _load_approved_bank_candidates(
        topic_id, target_thread_ids, limit=_SEMANTIC_BANK_CANDIDATE_CAP
    )
    if not candidates:
        return []
    return _rank_bank_rows_by_context(candidates, ctx, limit=limit, scope=scope)


def _exact_bank_source_text(
    topic_id: int, scope: str, grade_level: Optional[str], target_thread_ids: List[str], limit: int = 12
) -> Tuple[str, Optional[str]]:
    """Exact Subtopic bank grounding only (thin fallback, no near-name matching)."""
    try:
        from app.models.lms_models import (
            DiagnosticTargetPdf,
            Question,
            QuestionTaxonomy,
            TargetBankDocument,
            TargetBankItem,
        )
        from app.services.lms import taxonomy_service

        sub = taxonomy_service.find_subtopic(topic_id, scope, grade_level or "")
        if not sub:
            return "", None
        db = get_db()
        rows = (
            db.query(TargetBankItem, Question, TargetBankDocument)
            .join(Question, Question.id == TargetBankItem.question_id)
            .join(QuestionTaxonomy, QuestionTaxonomy.question_id == Question.id)
            .join(TargetBankDocument, TargetBankDocument.id == TargetBankItem.document_id)
            .filter(
                QuestionTaxonomy.subtopic_id == sub.id,
                TargetBankItem.verification_status == "approved",
                Question.is_active.is_(True),
            )
            .order_by(TargetBankItem.source_question_number)
            .limit(limit)
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read exact Target bank source for %s: %s", scope, exc)
        return "", None
    if not rows:
        return "", None
    lines = [f"Verified Target PDF questions for {sub.canonical_name}:"]
    filenames = set()
    for item, q, doc in rows:
        opts = json.loads(q.options_json or "[]")
        letter = "ABCD"[q.correct_option_index] if q.correct_option_index is not None else "?"
        correct_text = ""
        if q.correct_option_index is not None and q.correct_option_index < len(opts):
            correct_text = opts[q.correct_option_index].get("text") or ""
        lines.append(
            f"Q{item.source_question_number}. {q.question_text}\n"
            + "\n".join(f"{o.get('label')}. {o.get('text')}" for o in opts)
            + f"\nCorrect answer: {letter}. {correct_text}"
        )
        filenames.add((doc.original_filename or "").strip().lower())
    thread = None
    try:
        for t in (
            get_db()
            .query(DiagnosticTargetPdf)
            .filter(DiagnosticTargetPdf.rag_thread_id.in_(target_thread_ids))
            .all()
        ):
            if (t.original_filename or "").strip().lower() in filenames:
                thread = t.rag_thread_id
                break
    except Exception:  # noqa: BLE001
        pass
    if not thread and target_thread_ids:
        thread = target_thread_ids[0]
    return "\n\n".join(lines), thread


def _bank_source_text(
    topic_id: int,
    scope: str,
    grade_level: Optional[str],
    target_thread_ids: List[str],
    limit: int = 12,
    topic_name: str = "",
) -> Tuple[str, Optional[str]]:
    """Approved Target-PDF MCQs related to this weak area as grounding text."""
    try:
        from app.models.lms_models import DiagnosticTargetPdf

        rows = _semantic_bank_rows(
            topic_id,
            scope,
            topic_name or scope,
            grade_level,
            target_thread_ids,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to exact Subtopic bank
        logger.warning("Could not read semantic Target bank source for %s: %s", scope, exc)
        return _exact_bank_source_text(topic_id, scope, grade_level, target_thread_ids, limit=limit)
    if not rows:
        return _exact_bank_source_text(topic_id, scope, grade_level, target_thread_ids, limit=limit)
    lines = [f"Verified Target PDF questions related to {scope}:"]
    filenames = set()
    for item, q, doc in rows:
        opts = json.loads(q.options_json or "[]")
        letter = "ABCD"[q.correct_option_index] if q.correct_option_index is not None else "?"
        correct_text = ""
        if q.correct_option_index is not None and q.correct_option_index < len(opts):
            correct_text = opts[q.correct_option_index].get("text") or ""
        lines.append(
            f"Q{item.source_question_number}. {q.question_text}\n"
            + "\n".join(f"{o.get('label')}. {o.get('text')}" for o in opts)
            + f"\nCorrect answer: {letter}. {correct_text}"
        )
        filenames.add((doc.original_filename or "").strip().lower())
    thread = None
    try:
        for t in (
            get_db()
            .query(DiagnosticTargetPdf)
            .filter(DiagnosticTargetPdf.rag_thread_id.in_(target_thread_ids))
            .all()
        ):
            if (t.original_filename or "").strip().lower() in filenames:
                thread = t.rag_thread_id
                break
    except Exception:  # noqa: BLE001
        pass
    if not thread and target_thread_ids:
        thread = target_thread_ids[0]
    return "\n\n".join(lines), thread


def _bank_items_as_queue(
    *,
    topic_id: int,
    topic_name: str,
    approved_scope: str,
    ladder: List[str],
    target_thread_ids: List[str],
    grade_level: Optional[str],
    exclude_keys: set[str],
    seen_texts: set[str],
    context: Optional[str] = None,
) -> List[dict]:
    """Serve approved bank MCQs selected by AI relevance to the weak area."""
    from app.services.lms.mcq_utils import options_from_json
    from app.services.quiz.display_format_qa import (
        finalize_display_text,
        finalize_option_text,
        student_render,
    )

    try:
        rows = _semantic_bank_rows(
            topic_id,
            approved_scope,
            topic_name,
            grade_level,
            target_thread_ids,
            limit=max(8, len(ladder) * 3),
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Semantic bank queue failed for %s: %s", approved_scope, exc)
        return []
    thread_id = target_thread_ids[0] if target_thread_ids else ""
    out: List[dict] = []
    for item, q, _doc in rows:
        if len(out) >= len(ladder):
            break
        key = _question_key(q.question_text or "")
        if not key or key in exclude_keys or key in seen_texts:
            continue
        raw_opts = options_from_json(q.options_json or "[]")
        options = normalize_options(raw_opts)
        if not options or q.correct_option_index is None:
            continue
        if q.correct_option_index < 0 or q.correct_option_index >= len(options):
            continue
        if any(is_label_only(o.get("text") or "") and is_label_only(o.get("latex") or "") for o in options):
            continue
        options, correct_idx = shuffle_options(
            options, int(q.correct_option_index), preserve_order=_has_positional_option(options)
        )
        q_text, q_latex = finalize_display_text(q.question_text, q.question_latex)
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
        difficulty = ladder[len(out)] if len(out) < len(ladder) else ladder[-1]
        seen_texts.add(key)
        out.append(
            {
                "topic_id": topic_id,
                "topic_name": topic_name,
                "pdf_section": approved_scope,
                "approved_slo": approved_scope,
                "rag_thread_id": thread_id,
                "question_text": q_text or q.question_text,
                "question_latex": q_latex,
                "question_render": student_render(q_text, q_latex, inline=False),
                "options": safe_opts,
                "correct_option_index": correct_idx,
                "difficulty": difficulty if difficulty in _DIFF_RANK else "medium",
                "source": "target_bank",
                "generation_policy": _PDF_ONLY_POLICY,
                "key_verified": True,
                "bank_item_id": item.id,
                "answered": False,
                "correct": None,
            }
        )
    if out:
        logger.info(
            "Learning Chat semantic bank served %d approved item(s) for scope %s",
            len(out),
            approved_scope,
        )
    return out


def _build_question_queue(
    weak_topics: List[dict],
    target_thread_ids: List[str],
    rag_owner_id: int,
    grade_level: Optional[str] = None,
    used_question_texts_by_topic: Optional[Dict[int, List[str]]] = None,
) -> List[dict]:
    """Generate answer- and source-verified MCQs for approved missed SLOs."""
    queue: List[dict] = []
    used_question_texts_by_topic = used_question_texts_by_topic or {}
    seen_texts: set[str] = set()

    for entry in weak_topics:
        topic_name = (entry.get("topic_name") or "Practice area").strip()
        topic_id = _resolve_weak_topic_id(entry.get("topic_id") or 0, topic_name)
        score = float(entry.get("score_percent") or 0)
        count = max(
            _question_count_for_score(score),
            min(int(entry.get("min_questions") or 0), MAX_QUESTIONS_TO_CLEAR_TOPIC),
        )
        scopes = _approved_scopes(entry, topic_name)[:count]
        exclude_texts = used_question_texts_by_topic.get(topic_id, [])
        exclude_keys = {_question_key(text) for text in exclude_texts if text}
        known_subtopics = {
            (s.get("subtopic_name") or "").casefold(): s.get("subtopic_id")
            for s in entry.get("subtopics") or []
        }
        base, extra = divmod(count, len(scopes))
        for index, approved_scope in enumerate(scopes):
            scope_count = base + (1 if index < extra else 0)
            ladder = _difficulty_plan(score, scope_count)
            items = _build_scope_queue(
                topic_id=topic_id,
                topic_name=topic_name,
                approved_scope=approved_scope,
                ladder=ladder,
                target_thread_ids=target_thread_ids,
                rag_owner_id=rag_owner_id,
                grade_level=grade_level,
                exclude_texts=exclude_texts,
                exclude_keys=exclude_keys,
                seen_texts=seen_texts,
            )
            subtopic_id = known_subtopics.get(approved_scope.casefold()) or _scope_subtopic_id(
                topic_id, approved_scope, grade_level
            )
            for item in items:
                # Tag every item with its canonical Subtopic so completing the
                # session feeds Subtopic mastery (None = Domain-level scope).
                item["subtopic_id"] = subtopic_id
                item["subtopic_name"] = approved_scope if subtopic_id else None
            queue.extend(items)
    return queue


def _scope_subtopic_id(topic_id: int, scope: str, grade_level: Optional[str]) -> Optional[int]:
    """Canonical subtopic of a legacy SLO scope, if one is registered (never creates)."""
    try:
        from app.services.lms import taxonomy_service

        sub = taxonomy_service.find_subtopic(topic_id, scope, grade_level or "")
        return sub.id if sub else None
    except Exception:  # noqa: BLE001
        return None


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


def _drop_non_pdf_pending(
    session: DeficiencyChatSession, target_thread_ids: Optional[List[str]] = None
) -> bool:
    """Remove questions lacking current target-PDF + SLO verification."""
    if session.status not in ("active", "paused"):
        return False
    if target_thread_ids is None:
        try:
            target_thread_ids, _, _ = _resolve_all_target_threads(session.student_id)
        except Exception as exc:  # noqa: BLE001 - do not serve an unverified old queue
            logger.warning("Could not verify target PDFs for session %s: %s", session.id, exc)
            raise LMSValidationError("Could not verify the target PDF. Please try again.") from None
    from app.services.quiz.mcq_grounding_check import GROUNDING_VERIFIER

    allowed = set(target_thread_ids)
    questions = _load_questions(session)
    idx = session.current_index or 0
    pending = questions[idx:]

    def _keep_pending(q: dict) -> bool:
        # Approved Target-bank MCQs selected by semantic relevance are PDF-grounded.
        if (
            q.get("source") == "target_bank"
            and q.get("bank_item_id")
            and q.get("generation_policy") == _PDF_ONLY_POLICY
            and (not q.get("rag_thread_id") or q.get("rag_thread_id") in allowed)
        ):
            return True
        return (
            q.get("source") == "target_pdf"
            and q.get("generation_policy") == _PDF_ONLY_POLICY
            and q.get("grounding_verified") == GROUNDING_VERIFIER
            and q.get("approved_slo")
            and q.get("rag_thread_id") in allowed
        )

    keep = [q for q in pending if _keep_pending(q)]
    if len(keep) == len(pending):
        return False
    logger.warning(
        "Learning Chat session %s: removed %d pending question(s) without current PDF provenance",
        session.id, len(pending) - len(keep),
    )
    _save_questions(session, questions[:idx] + keep)
    current_removed = bool(pending) and (not keep or pending[0] is not keep[0])
    if current_removed:
        _reset_tutor_state(session)
    if not keep:
        # Do not award mastery or mark the path done for discarded questions.
        session.status = "completed"
    get_db().commit()
    return True


def _drop_disproved_pending(session: DeficiencyChatSession) -> None:
    """Remove still-to-come questions whose answer key the maths check disproves.

    Queues built before the fail-closed key check (Oct 2026) still hold questions with
    a wrong key or no correct option; a student must never be shown one. Answered
    history (before ``current_index``) and questions already answered correctly are
    left alone. Items that passed the check when queued carry ``key_verified``.
    """
    from app.services.quiz.mcq_math_verify import check_answer_key

    if session.status not in ("active", "paused"):
        return
    questions = _load_questions(session)
    idx = session.current_index or 0
    keep_tail, removed = [], []
    for q in questions[idx:]:
        if q.get("key_verified") or q.get("correct") is True:
            keep_tail.append(q)
            continue
        verdict = check_answer_key(q)
        if verdict.disproved:
            removed.append(f"{(q.get('question_text') or '')[:60]} [{verdict.status}]")
        else:
            keep_tail.append(q)
    if not removed:
        return
    logger.warning(
        "Learning Chat session %s: removed %d queued question(s) with a disproved key: %s",
        session.id, len(removed), removed,
    )
    _save_questions(session, questions[:idx] + keep_tail)
    _reset_tutor_state(session)
    db = get_db()
    if not keep_tail:  # nothing valid left to do
        had_pdf_questions = any(q.get("source") == "target_pdf" for q in questions[idx:])
        session.status = "completed"
        db.commit()
        if session.rag_thread_id or had_pdf_questions:
            raise LMSValidationError(_PDF_COVERAGE_ERROR)
        _on_session_completed(session.id, session.student_id)
    else:
        db.commit()


def _session_state(session: DeficiencyChatSession) -> dict:
    removed = _drop_non_pdf_pending(session)
    if removed and session.status == "completed":
        raise LMSValidationError(_PDF_COVERAGE_ERROR)
    _drop_disproved_pending(session)
    questions = _load_questions(session)
    total = len(questions)
    current = None
    if session.status in ("active", "paused") and session.current_index < total:
        q = _refresh_question_display(dict(questions[session.current_index]))
        solved_review = bool(q.get("solved_review") and q.get("correct") is True)
        q.pop("correct_option_index", None)
        q.pop("grounding_verified", None)
        q.pop("generated_concept", None)
        q.pop("key_verified", None)
        q.pop("last_selected_option_index", None)
        # solved_review is client-safe (student already got it right); answer key stays server-only.
        q.pop("solved_review", None)
        q["difficulty"] = q.get("difficulty") or "medium"
        q["solved_review"] = solved_review
        current = q
    weak = []
    try:
        weak = json.loads(session.weak_topics_json or "[]")
    except json.JSONDecodeError:
        pass
    tutor_state = _load_tutor_state(session)
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
        if questions
        and (
            questions[0].get("learning_chat_mode") == "enrichment"
            or questions[0].get("source") == "enrichment_ai"
        )
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
        "has_pdf": bool(
            current
            and current.get("generation_policy") == _PDF_ONLY_POLICY
            and (
                (
                    current.get("source") == "target_pdf"
                    and current.get("rag_thread_id")
                )
                or (
                    current.get("source") == "target_bank"
                    and current.get("bank_item_id")
                )
            )
        ),
        "pdf_label": "Teacher target content PDF",
        "tutor_messages": tutor_messages,
        "completed": session.status == "completed",
        # A newer Baseline/Follow-up changed which subtopics are weak and this
        # session's remaining questions were updated to match.
        "refreshed_after_assessment": getattr(session, "close_reason", None) == "refreshed_after_assessment",
        "superseded": getattr(session, "close_reason", None) == "superseded",
        "current_subtopic": (current or {}).get("subtopic_name"),
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
        session.close_reason = session.close_reason or "closed_for_new_session"
    db.commit()


def _is_enrichment_session(questions: List[dict]) -> bool:
    return bool(questions) and (
        questions[0].get("learning_chat_mode") == "enrichment"
        or questions[0].get("source") == "enrichment_ai"
    )


def reconcile_sessions_after_assessment(student_id: int) -> dict:
    """Bring open Learning Chat sessions in line with mastery after a Baseline/Follow-up.

    Policy (no student work is ever thrown away):

    * Pending questions on a Subtopic/Domain that is **no longer weak** are dropped.
    * An **untouched** session (nothing answered yet) whose pending set changed, or
      that misses a newly weak Subtopic, is closed as ``superseded`` without any
      mastery change - the next open/prewarm builds a fresh queue from live weaknesses.
    * A session **with progress** keeps every answered question. If questions remain
      it stays open, flagged ``refreshed_after_assessment`` so the UI can tell the
      student; if none remain it is completed and the answered work is credited.
    """
    from app.services.lms import subtopic_mastery_service

    db = get_db()
    sessions = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id == student_id,
            DeficiencyChatSession.status.in_(("active", "paused")),
        )
        .all()
    )
    if not sessions:
        return {"superseded": 0, "refreshed": 0, "completed": 0}

    weak_domains = {int(w.get("topic_id") or 0) for w in get_weak_topics(student_id)}
    weak_subs = subtopic_mastery_service.get_weak_subtopics(student_id)
    weak_sub_ids = {s["subtopic_id"] for s in weak_subs}
    weak_domains |= {int(s.get("domain_topic_id") or 0) for s in weak_subs}
    weak_domains.discard(0)
    has_weakness = bool(weak_domains or weak_sub_ids)

    def still_weak(item: dict) -> bool:
        sub_id = item.get("subtopic_id")
        if sub_id:
            return int(sub_id) in weak_sub_ids
        return int(item.get("topic_id") or 0) in weak_domains

    summary = {"superseded": 0, "refreshed": 0, "completed": 0}
    completed_ids: List[int] = []
    for session in sessions:
        questions = _load_questions(session)
        idx = session.current_index or 0
        answered = [q for q in questions[:idx]] + [q for q in questions[idx:] if q.get("answered")]
        pending = questions[idx:]
        if _is_enrichment_session(questions):
            if has_weakness and not answered:
                session.status = "completed"
                session.close_reason = "superseded"
                summary["superseded"] += 1
            continue
        keep = [q for q in pending if still_weak(q)]
        covered = {int(q["subtopic_id"]) for q in questions if q.get("subtopic_id")}
        session_domains = _session_topic_ids(session)
        missing_new = {
            s["subtopic_id"] for s in weak_subs
            if int(s.get("domain_topic_id") or 0) in session_domains and s["subtopic_id"] not in covered
        }
        if not answered:
            if len(keep) != len(pending) or missing_new:
                session.status = "completed"
                session.close_reason = "superseded"
                summary["superseded"] += 1
            continue
        if len(keep) == len(pending):
            continue
        _save_questions(session, questions[:idx] + keep)
        _reset_tutor_state(session)
        if keep:
            session.close_reason = "refreshed_after_assessment"
            summary["refreshed"] += 1
        else:
            session.status = "completed"
            session.close_reason = "completed_after_assessment"
            completed_ids.append(session.id)
            summary["completed"] += 1
    db.commit()
    for sid in completed_ids:
        try:
            _on_session_completed(sid, student_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Crediting reconciled Learning Chat session %s failed: %s", sid, exc)
            db.rollback()
    if any(summary.values()):
        logger.info("Learning Chat reconcile for student %s: %s", student_id, summary)
    return summary


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


_PRINTED_TOPIC_RE = re.compile(r"(?:^|;\s*)Topic:\s*([^;]+)", re.IGNORECASE)


def _printed_slo(question) -> Optional[str]:
    """Printed Topic retained by hybrid extraction in the question explanation."""
    match = _PRINTED_TOPIC_RE.search(str(getattr(question, "explanation", "") or ""))
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value[:120] if value else None


def _attach_approved_slos(
    areas: List[dict],
    attempt: Optional[AssessmentAttempt],
    *,
    incorrect_only: bool,
) -> List[dict]:
    """Attach trusted printed SLOs from the diagnostic questions in each area."""
    scoped = [dict(area) for area in areas]
    if not attempt or not scoped:
        return scoped
    from app.models.lms_models import AttemptAnswer, Question

    question_ids = {
        int(question_id)
        for area in scoped
        for question_id in (area.get("question_ids") or [])
        if question_id
    }
    if not question_ids:
        return scoped
    db = get_db()
    try:
        questions = {
            question.id: question
            for question in db.query(Question).filter(Question.id.in_(question_ids)).all()
        }
        answer_map = {
            answer.question_id: answer
            for answer in db.query(AttemptAnswer)
            .filter(
                AttemptAnswer.attempt_id == attempt.id,
                AttemptAnswer.question_id.in_(question_ids),
            )
            .all()
        }
    except Exception as exc:  # noqa: BLE001 - broad topic remains the safe fallback
        logger.warning("Could not resolve printed diagnostic SLOs: %s", exc)
        return scoped

    for area in scoped:
        slos: List[str] = []
        seen: set[str] = set()
        for raw_id in area.get("question_ids") or []:
            question_id = int(raw_id)
            answer = answer_map.get(question_id)
            if incorrect_only and answer is not None and answer.is_correct is True:
                continue
            slo = _printed_slo(questions.get(question_id))
            key = (slo or "").casefold()
            if slo and key not in seen:
                seen.add(key)
                slos.append(slo)
        if slos:
            area["slo_names"] = slos
    return scoped


def _enrichment_areas(student_id: int, diag_id: Optional[int]) -> List[dict]:
    """Topics to stretch a student who has no weak areas: their strongest
    diagnostic topics, so the challenge builds on what they already know."""
    if diag_id:
        attempt = _latest_diagnostic_attempt(student_id, diag_id)
        if attempt:
            try:
                analysis = analyze_attempt(attempt.id)
            except Exception:  # noqa: BLE001
                analysis = {}
            areas = (analysis.get("strong_topics") or []) or (analysis.get("all_topics") or [])
            if areas:
                return _attach_approved_slos(list(areas)[:3], attempt, incorrect_only=False)

    from app.services.lms import performance_service

    rows = performance_service.get_student_mastery(student_id) or []
    strong = sorted(
        (r for r in rows if (r.get("score_percent") or 0) >= 100 and r.get("topic_id")),
        key=lambda r: -(r.get("score_percent") or 0),
    )[:3]
    if strong:
        return [
            {
                "topic_id": row["topic_id"],
                "topic_name": row.get("topic_name") or f"Topic #{row['topic_id']}",
                "score_percent": row.get("score_percent") or 0,
            }
            for row in strong
        ]
    return []


def _build_enrichment_queue(
    areas: List[dict],
    target_thread_ids: List[str],
    rag_owner_id: int,
    grade_level: Optional[str],
) -> List[dict]:
    """Grounded challenge questions from the same approved PDF/SLOs."""
    challenge_areas = [
        {**area, "score_percent": 80, "min_questions": 2}
        for area in areas
    ]
    queue = _build_question_queue(
        challenge_areas,
        target_thread_ids,
        rag_owner_id,
        grade_level,
    )
    for question in queue:
        question["learning_chat_mode"] = "enrichment"
    return queue


def _practice_weak_areas(student_id: int, diag_id: Optional[int]) -> List[dict]:
    """Weak areas a practice Learning Chat should cover right now.

    The authority is the student's LIVE mastery after every Baseline and
    Follow-up (and Learning Chat) - never the frozen diagnostic list:

    * every live weak Domain is included, so a weakness that only a Follow-up
      quiz revealed is practised too;
    * a Domain whose own score is fine but which has a weak Subtopic is included
      (a Follow-up can re-test only part of a Domain);
    * inside a Domain the scopes are the currently weak Subtopics, weakest first.

    Only a Domain with no Subtopic evidence at all (legacy papers with no printed
    Topic) falls back to the diagnostic's printed SLOs or, failing that, to the
    Domain itself - and says so with ``scope_level`` instead of doing it silently.
    """
    from app.services.lms import subtopic_mastery_service

    live = [dict(w) for w in get_weak_topics(student_id) if w.get("topic_id")]
    weak_subs = subtopic_mastery_service.get_weak_subtopics(student_id)
    tracked_domains = subtopic_mastery_service.domains_with_subtopic_data(student_id)
    if not live and not weak_subs:
        raise LMSValidationError(
            "No weak areas found. Complete your diagnostic assessment first."
        )

    attempt = None
    ai_by_topic: Dict[int, dict] = {}
    if diag_id:
        attempt = _latest_diagnostic_attempt(student_id, diag_id)
        if attempt:
            try:
                analysis = analyze_attempt(attempt.id)
            except Exception as exc:  # noqa: BLE001 - live mastery is enough
                logger.warning("Learning Chat: diagnostic analysis unavailable: %s", exc)
                analysis = {}
            for area in analysis.get("all_topics") or analysis.get("weak_topics") or []:
                if area.get("topic_id"):
                    ai_by_topic.setdefault(int(area["topic_id"]), area)

    areas: Dict[int, dict] = {}
    for w in live:
        tid = int(w["topic_id"])
        ai = ai_by_topic.get(tid) or {}
        areas[tid] = {
            "topic_id": tid,
            "topic_name": w.get("topic_name") or ai.get("topic_name") or "Practice area",
            "score_percent": w.get("score_percent", ai.get("score_percent", 0)),
            "question_ids": ai.get("question_ids") or [],
        }
    for sub in weak_subs:
        tid = int(sub.get("domain_topic_id") or 0)
        if not tid:
            continue
        area = areas.setdefault(
            tid,
            {
                "topic_id": tid,
                "topic_name": sub.get("domain_name") or "Practice area",
                # Domain itself is not weak: size the queue from its weakest subtopic.
                "score_percent": sub.get("score_percent", 0),
                "question_ids": (ai_by_topic.get(tid) or {}).get("question_ids") or [],
            },
        )
        area.setdefault("subtopics", []).append(
            {
                "subtopic_id": sub["subtopic_id"],
                "subtopic_name": sub["subtopic_name"],
                "score_percent": sub["score_percent"],
            }
        )

    legacy = []
    for tid, area in areas.items():
        if area.get("subtopics"):
            area["slo_names"] = [s["subtopic_name"] for s in area["subtopics"]]
            area["scope_level"] = "subtopic"
        elif tid in tracked_domains:
            # Every tracked subtopic here is mastered; the Domain is weak only
            # through questions with no printed Topic. Practise the Domain.
            area["scope_level"] = "domain"
        else:
            legacy.append(area)
    if legacy:
        for area in _attach_approved_slos(legacy, attempt, incorrect_only=True):
            target = areas[int(area["topic_id"])]
            if area.get("slo_names"):
                target["slo_names"] = area["slo_names"]
                target["scope_level"] = "legacy_diagnostic_slo"
            else:
                target["scope_level"] = "domain"
    for area in areas.values():
        if area.get("scope_level") == "domain":
            logger.info(
                "Learning Chat for student %s: Domain %s has no weak Subtopic evidence; "
                "practising at Domain level", student_id, area.get("topic_name"),
            )
    return sorted(areas.values(), key=lambda a: float(a.get("score_percent") or 0))


def _session_topic_ids(session: DeficiencyChatSession) -> set:
    try:
        weak = json.loads(session.weak_topics_json or "[]")
    except json.JSONDecodeError:
        return set()
    return {int(w.get("topic_id") or 0) for w in weak if isinstance(w, dict)}


def _start_topic_session(
    student_id: int,
    topic_id: int,
    force_new: bool,
    target_thread_ids: List[str],
    rag_owner_id: Optional[int],
    diag_id: Optional[int],
) -> dict:
    """Learning Chat for ONE weak area. Each weak topic has its own session, so
    pausing one topic and opening another keeps both where the student left them."""
    db = get_db()
    target_thread_id = target_thread_ids[0] if target_thread_ids else None
    q = db.query(DeficiencyChatSession).filter(
        DeficiencyChatSession.student_id == student_id,
        DeficiencyChatSession.status.in_(("active", "paused")),
    )
    if diag_id:
        q = q.filter(DeficiencyChatSession.diagnostic_assessment_id == diag_id)
    open_sessions = [
        s
        for s in q.order_by(DeficiencyChatSession.updated_at.desc()).all()
        if s.rag_thread_id == target_thread_id  # practice only; enrichment has no thread
    ]

    mine = [s for s in open_sessions if _session_topic_ids(s) == {topic_id}]
    if force_new and mine:
        for s in mine:
            s.status = "completed"
        db.commit()
        open_sessions = [s for s in open_sessions if s not in mine]
        mine = []
    if mine:
        for s in mine:
            _drop_non_pdf_pending(s, target_thread_ids)
        mine = [s for s in mine if s.status in ("active", "paused")]
    if mine:
        existing = mine[0]
        if existing.status == "paused":
            existing.status = "active"
            db.commit()
        return _session_state(existing)

    def _is_topic(entry: dict) -> bool:
        return int(entry.get("topic_id") or 0) == topic_id

    weak = [w for w in _practice_weak_areas(student_id, diag_id) if _is_topic(w)]
    if not weak:
        weak = [w for w in get_weak_topics(student_id) if _is_topic(w)]
    if not weak:
        raise LMSValidationError("This topic is not one of your weak areas any more.")
    weak = _note_outstanding_misses(student_id, weak[:1])

    # Questions already generated for this topic (the session pre-built right after
    # the diagnostic) are reused, so opening a topic costs no new generation.
    from app.services.quiz.mcq_math_verify import check_answer_key

    queue: List[dict] = []
    for s in open_sessions:
        _drop_non_pdf_pending(s, target_thread_ids)
        if s.status not in ("active", "paused"):
            continue
        topic_qs = [x for x in _load_questions(s) if _is_topic(x)]
        if topic_qs and not any(x.get("answered") for x in topic_qs):
            # A pre-built queue may predate the fail-closed key check, and the
            # student may already have been served its stems in a session copied
            # from it - those stay out so the same question is not shown again.
            served = _served_question_keys(student_id, exclude_session_id=s.id)
            queue = [
                dict(x)
                for x in topic_qs
                if (x.get("key_verified") or not check_answer_key(x).disproved)
                and _question_key(x.get("question_text") or "") not in served
            ]
            break
    if not queue:
        from app.services.lms import class_service

        queue = _build_question_queue(
            weak,
            target_thread_ids,
            rag_owner_id,
            class_service.get_student_grade(student_id),
            used_question_texts_by_topic=_prior_question_texts_by_topic(student_id),
        )
    if not queue:
        raise LMSValidationError(
            "Could not generate questions from the target PDF(s) for this weak area. "
            "Ask your admin to check the target content PDFs cover this topic."
        )

    w = weak[0]
    payload = [
        {
            "topic_id": topic_id,
            "topic_name": w.get("topic_name") or "Practice area",
            "score_percent": w.get("score_percent", 0),
            "question_ids": w.get("question_ids") or [],
            "slo_names": w.get("slo_names") or [],
            "subtopics": w.get("subtopics") or [],
            "scope_level": w.get("scope_level"),
        }
    ]
    session = DeficiencyChatSession(
        student_id=student_id,
        diagnostic_assessment_id=diag_id,
        rag_thread_id=target_thread_id,
        rag_owner_id=rag_owner_id,
        weak_topics_json=json.dumps(payload, ensure_ascii=False),
        questions_json=json.dumps(queue, ensure_ascii=False),
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_state(session)


def start_session(
    student_id: int,
    force_new: bool = False,
    mode: str = "practice",
    topic_id: Optional[int] = None,
) -> dict:
    """Start Learning Chat.

    topic_id (practice only) -> a chat for that one weak area.
    mode="practice"   -> difficulty-laddered questions on weak areas
    mode="enrichment" -> above-level challenge questions when there are no
                         weak areas (DIL: the practice section must never be
                         empty after a clean diagnostic).
    """
    enrichment = mode == "enrichment"
    target_thread_ids, rag_owner_id, diag_id = _resolve_all_target_threads(student_id)
    target_thread_id = target_thread_ids[0] if target_thread_ids else None

    if not target_thread_id or not rag_owner_id:
        raise LMSValidationError(
            "No target content PDF has been uploaded yet. "
            "Learning Chat uses study material PDFs for weak areas — ask your admin."
        )

    if topic_id and not enrichment:
        return _start_topic_session(
            student_id, int(topic_id), force_new, target_thread_ids, rag_owner_id, diag_id
        )

    if force_new:
        _close_old_sessions(student_id)

    existing = None if force_new else get_active_session(student_id, diag_id)
    existing_questions = _load_questions(existing) if existing else []
    existing_mode = (
        "enrichment"
        if existing_questions
        and (
            existing_questions[0].get("learning_chat_mode") == "enrichment"
            or existing_questions[0].get("source") == "enrichment_ai"
        )
        else "practice"
    )
    requested_mode = "enrichment" if enrichment else "practice"
    if existing and existing_mode == requested_mode and (
        enrichment or existing.rag_thread_id == target_thread_id
    ):
        _drop_non_pdf_pending(existing, target_thread_ids)
        if existing.status in ("active", "paused"):
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
        queue = _build_enrichment_queue(
            areas,
            target_thread_ids,
            rag_owner_id,
            grade_level,
        )
        if not queue:
            raise LMSValidationError(
                "Could not generate challenge questions right now — please try again shortly."
            )
        payload = [
            {
                "topic_id": a.get("topic_id", 0),
                "topic_name": a.get("topic_name") or "Challenge area",
                "score_percent": a.get("score_percent", 0),
                "question_ids": a.get("question_ids") or [],
                "slo_names": a.get("slo_names") or [],
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

    weak = _note_outstanding_misses(student_id, _practice_weak_areas(student_id, diag_id))

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
            "slo_names": w.get("slo_names") or [],
            "subtopics": w.get("subtopics") or [],
            "scope_level": w.get("scope_level"),
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
    warmed = False
    try:
        state = start_session(student_id, force_new=False)
        warmed = bool(state.get("total_questions"))
    except LMSValidationError as exc:
        logger.info("Learning Chat prewarm skipped for student %s: %s", student_id, exc)
    except Exception as exc:  # noqa: BLE001 — prewarm must never break submit
        logger.warning("Learning Chat prewarm failed for student %s: %s", student_id, exc)
    # The Learning Path opens one chat per weak Domain. After a Follow-up the
    # combined session may be an older, refreshed one that does not cover newly
    # weak subtopics, so warm each Domain's own session too (an open one, or
    # unanswered pre-built questions, are reused - nothing is generated twice).
    try:
        _, _, diag_id = _resolve_all_target_threads(student_id)
        domains = [int(a["topic_id"]) for a in _practice_weak_areas(student_id, diag_id) if a.get("topic_id")]
    except Exception as exc:  # noqa: BLE001
        logger.info("Learning Chat per-topic prewarm skipped for student %s: %s", student_id, exc)
        domains = []
    for topic_id in domains:
        try:
            if start_session(student_id, topic_id=topic_id).get("total_questions"):
                warmed = True
        except LMSValidationError as exc:
            logger.info("Learning Chat prewarm for topic %s skipped: %s", topic_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Learning Chat prewarm for topic %s failed: %s", topic_id, exc)
            get_db().rollback()
    return warmed


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
            "messages": [],
        },
        ensure_ascii=False,
    )


def _merge_question_dwell(q: dict, time_spent_seconds) -> None:
    """Store absolute per-question dwell on the Learning Chat question blob."""
    if time_spent_seconds is None:
        return
    try:
        sec = int(time_spent_seconds)
    except (TypeError, ValueError):
        return
    if sec < 0:
        sec = 0
    sec = min(sec, 2 * 60 * 60)
    q["time_spent_seconds"] = max(int(q.get("time_spent_seconds") or 0), sec)


def submit_answer(
    session_id: int,
    student_id: int,
    selected_option_index: int,
    time_spent_seconds=None,
) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")
    if _drop_non_pdf_pending(session) and session.status == "completed":
        raise LMSValidationError(_PDF_COVERAGE_ERROR)

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        raise LMSValidationError("No more questions in this session")

    q = questions[idx]
    _merge_question_dwell(q, time_spent_seconds)
    # Correct answers stay on this MCQ for Mode B review; Next/advance moves on.
    if q.get("solved_review") and q.get("correct") is True:
        _save_questions(session, questions)
        db.commit()
        result = _session_state(session)
        result["last_answer"] = {
            "correct": True,
            "explanation_available": True,
            "topic_name": q.get("topic_name"),
            "stay_on_question": True,
            "solved": True,
        }
        return result

    option_count = len(q.get("options") or [])
    if selected_option_index < 0 or (option_count and selected_option_index >= option_count):
        raise LMSValidationError("That option does not exist for this question")
    is_correct = selected_option_index == q.get("correct_option_index")
    already_correct = q.get("correct") is True
    if getattr(session, "close_reason", None) == "refreshed_after_assessment":
        session.close_reason = None  # the student has seen the refresh notice
    # Keep the student's latest choice server-side so the tutor can respond to
    # that misconception (Mode A) or confirm it (Mode B). The answer key is
    # still never copied into the public session payload.
    q["last_selected_option_index"] = selected_option_index

    if is_correct:
        q["answered"] = True
        q["correct"] = True
        q["solved_review"] = True
        if not already_correct:
            session.correct_count += 1
        # Stay on this question for the Mode B worked-solution window.
        # Advance / reorder / complete / tutor reset happen in advance_session.
    else:
        q["answered"] = True
        q["correct"] = False
        q.pop("solved_review", None)

    _save_questions(session, questions)
    db.commit()

    result = _session_state(session)
    result["last_answer"] = {
        "correct": is_correct,
        "explanation_available": True,
        "topic_name": q.get("topic_name"),
        "stay_on_question": True,
        "solved": bool(is_correct),
    }
    return result


def advance_session(session_id: int, student_id: int, time_spent_seconds=None) -> dict:
    """Leave the current question (after wrong attempt or Mode B solved review)."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")
    if _drop_non_pdf_pending(session) and session.status == "completed":
        raise LMSValidationError(_PDF_COVERAGE_ERROR)

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        session.status = "completed"
        db.commit()
        _on_session_completed(session_id, student_id)
        return _session_state(session)

    q = questions[idx]
    _merge_question_dwell(q, time_spent_seconds)
    was_solved_review = bool(q.get("solved_review") and q.get("correct") is True)
    if not q.get("answered"):
        q["answered"] = True
        q["correct"] = False
    q.pop("solved_review", None)
    _save_questions(session, questions)

    session.current_index = idx + 1
    _reset_tutor_state(session)
    just_completed = session.current_index >= len(questions)
    if just_completed:
        session.status = "completed"
    else:
        # Adaptive reorder after a graded item (correct review or skip-after-wrong).
        _reorder_pending(session, questions)
        _save_questions(session, questions)
    db.commit()

    if just_completed:
        # Must run after the commit above so questions_json (with the skipped
        # or reviewed question) is what gets read back.
        _on_session_completed(session_id, student_id)
    result = _session_state(session)
    if was_solved_review:
        result["advanced_from_solved"] = True
    return result


def pause_session(session_id: int, student_id: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status == "active":  # a completed session stays completed
        session.status = "paused"
        db.commit()
    return _session_state(session)


def _load_tutor_state(session: DeficiencyChatSession) -> dict:
    """Per-question tutor history; legacy assistance levels are ignored."""
    try:
        raw = json.loads(session.chat_history_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if isinstance(raw, list):
        raw = {"question_index": session.current_index, "messages": raw}
    if raw.get("question_index") != session.current_index:
        return {"question_index": session.current_index, "messages": []}
    return {
        "question_index": session.current_index,
        "messages": raw.get("messages") or [],
    }


def _save_tutor_state(session: DeficiencyChatSession, state: dict) -> None:
    session.chat_history_json = json.dumps(
        {
            "question_index": state.get("question_index", session.current_index),
            "messages": (state.get("messages") or [])[-20:],
        },
        ensure_ascii=False,
    )


_ANSWER_GUARD_REPLY = (
    "Let's work it out without naming the answer. "
    "What rule from the PDF could you use to test your choice?"
)


def _reveals_current_mcq_answer(reply: str, current_question: Optional[dict]) -> bool:
    """Catch explicit answer-key leaks before a tutor reply reaches the student.

    The model never receives the key, but it can still solve a simple MCQ from
    the visible stem/options. This narrow guard catches direct disclosures while
    allowing an option to be discussed as the student's incorrect choice.
    """
    if not reply or not current_question:
        return False
    options = current_question.get("options") or []
    correct_index = current_question.get("correct_option_index")
    if not isinstance(correct_index, int) or not (0 <= correct_index < len(options)):
        return False

    correct = options[correct_index]
    label = (correct.get("label") or chr(65 + correct_index)).strip()
    text = (correct.get("text") or "").strip()
    escaped_label = re.escape(label)
    disclosure_patterns = [
        rf"\b(?:correct\s+answer\s*(?:is|:|=)?|answer\s*(?:is|:|=))\s*"
        rf"(?:option\s+)?{escaped_label}\b",
        rf"\b(?:correct\s+)?(?:option|choice)\s*(?:is|:|=)?\s*{escaped_label}\b",
        rf"\b{escaped_label}\s+is\s+(?:the\s+)?(?:correct|right)\b",
        rf"\b(?:choose|select|pick)\s+(?:option\s+)?{escaped_label}\b",
    ]
    if any(re.search(pattern, reply, re.IGNORECASE) for pattern in disclosure_patterns):
        return True
    if not text:
        return False
    escaped_text = re.escape(text)
    return bool(
        re.search(
            rf"\b(?:correct\s+answer|answer|result|value)\s*(?:is|:|=)\s*{escaped_text}(?:\b|$)",
            reply,
            re.IGNORECASE,
        )
    )


def explain_with_tutor(
    session_id: int,
    student_id: int,
    message: str,
    api_key: str = "",
) -> dict:
    """PDF-grounded, answer-aware Socratic tutor for the current MCQ."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")
    if _drop_non_pdf_pending(session) and session.status == "completed":
        raise LMSValidationError(_PDF_COVERAGE_ERROR)

    questions = _load_questions(session)
    current_q = questions[session.current_index] if session.current_index < len(questions) else None

    pdf_excerpt = ""
    pdf_section = (current_q or {}).get("pdf_section") or (current_q or {}).get("topic_name") or ""
    thread_for_excerpt = (current_q or {}).get("rag_thread_id") or session.rag_thread_id
    if current_q and current_q.get("subtopic_name"):
        from app.services.lms import class_service as _cs

        pdf_excerpt, _ = _bank_source_text(
            int(current_q.get("topic_id") or 0),
            current_q["subtopic_name"],
            _cs.get_student_grade(student_id),
            [thread_for_excerpt] if thread_for_excerpt else [],
            topic_name=(current_q.get("topic_name") or ""),
        )
        pdf_excerpt = pdf_excerpt[:4000]
    if not pdf_excerpt and thread_for_excerpt and session.rag_owner_id and pdf_section:
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
    if _looks_like_answer_key_table(pdf_excerpt):
        pdf_excerpt = ""  # an answer-key table would hand the tutor the answers

    tutor_state = _load_tutor_state(session)
    history = tutor_state.get("messages") or []
    prior_turns = sum(1 for m in history if m.get("role") == "assistant")
    solved = bool(
        current_q
        and current_q.get("solved_review")
        and current_q.get("correct") is True
    )

    from app.services.lms import class_service

    grade_level = class_service.get_student_grade(student_id)

    context = tutor_service.build_deficiency_context(
        weak_topics_json=session.weak_topics_json,
        current_question=current_q,
        pdf_excerpt=pdf_excerpt,
        grade_level=grade_level,
        prior_turns=prior_turns,
        message=message,
        solved=solved,
    )
    reply = tutor_service.tutor_chat(
        message,
        api_key=api_key,
        mode="deficiency",
        context=context,
        history=history,
        solved=solved,
    )
    # Mode A only: block answer-key leaks. Mode B must name the correct option.
    if not solved and _reveals_current_mcq_answer(reply, current_q):
        logger.warning("Learning Chat tutor answer leak blocked for session %s", session_id)
        # Let the model rewrite its own reply (so the student still gets a natural,
        # on-point answer); the fixed sentence is only the last resort.
        retry_context = (
            f"{context}\n\nIMPORTANT: Your previous draft named the correct option/final "
            "answer of the Current practice question, which is not allowed. Reply to the "
            "student's latest message again, helping as much as possible, but stop before "
            "the final answer and do not say which option is correct."
        )
        try:
            reply = tutor_service.tutor_chat(
                message,
                api_key=api_key,
                mode="deficiency",
                context=retry_context,
                history=history,
                solved=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Learning Chat leak-rewrite failed for session %s: %s", session_id, exc)
            reply = ""
        if not (reply or "").strip() or _reveals_current_mcq_answer(reply, current_q):
            reply = _ANSWER_GUARD_REPLY

    off_tag = tutor_service.DEFICIENCY_OFF_TOPIC_TAG
    refuse = tutor_service.DEFICIENCY_OFF_TOPIC_REFUSE
    cleaned = (reply or "").strip()
    # Model-judged off-topic: optional <<OFF_TOPIC>> prefix (stripped before UI).
    if cleaned.startswith(off_tag):
        cleaned = cleaned[len(off_tag) :].strip() or refuse
        reply = cleaned
    elif refuse in cleaned and cleaned != refuse:
        # Legacy exact-sentence refuse — keep the model's friendly redirect.
        pass

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    tutor_state["messages"] = history
    tutor_state["question_index"] = session.current_index
    _save_tutor_state(session, tutor_state)
    db.commit()

    return {
        "reply": reply,
        "session_id": session_id,
    }


def _on_session_completed(session_id: int, student_id: int) -> None:
    """A student finished a Learning Chat session: feed Domain + Subtopic mastery,
    then update the learning path. Runs after the session's final commit."""
    from app.services.lms import performance_service, subtopic_mastery_service

    performance_service.update_topic_scores_from_deficiency_session(session_id)
    try:
        subtopic_mastery_service.record_learning_chat_session(session_id)
    except Exception as exc:  # noqa: BLE001 - Domain mastery is already saved
        logger.warning("Subtopic mastery update failed for session %s: %s", session_id, exc)
        get_db().rollback()
    _mark_learning_path_chat_complete(student_id)


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
    if not still_weak:
        from app.services.lms import subtopic_mastery_service

        still_weak = subtopic_mastery_service.get_weak_subtopics(student_id)

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
