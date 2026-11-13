"""One metadata selection flow for diagnostic and quiz weaknesses."""
from __future__ import annotations

from collections import Counter
import re

from app.models.lms_models import AssessmentAttempt, AttemptAnswer, Question
from app.services.quiz.question_metadata import normalized
from app.utils.db import get_db


def metadata_of(question):
    return {"domain": getattr(question, "domain", None), "topic": getattr(question, "topic_label", None),
            "cognitive_level": getattr(question, "cognitive_level", None), "difficulty": getattr(question, "difficulty", None)}


def analyze_metadata_attempt(attempt_id):
    """Shared diagnostic/quiz grouping, or None for an older untagged assessment."""
    from app.services.lms.attempt_service import attempt_question_ids
    from app.services.lms.performance_service import _resolve_question_topic_id
    from app.services.lms.assessment_service import get_assessment

    db = get_db()
    attempt = db.query(AssessmentAttempt).filter_by(id=attempt_id).first()
    if not attempt:
        return None
    questions = db.query(Question).filter(Question.id.in_(attempt_question_ids(attempt))).order_by(Question.id).all()
    if not questions or any(not all(metadata_of(q).values()) for q in questions):
        return None
    assessment = get_assessment(attempt.assessment_id)
    answers = {a.question_id: a for a in db.query(AttemptAnswer).filter_by(attempt_id=attempt_id)}
    groups = {}
    for question in questions:
        meta = metadata_of(question)
        key = (normalized(meta["domain"]), normalized(meta["topic"]))
        group = groups.setdefault(key, dict(domain=meta["domain"], topic_name=meta["topic"],
                                           topic_id=_resolve_question_topic_id(question, assessment),
                                           question_ids=[], wrong_question_ids=[], wrong_metadata=[], correct=0, total=0))
        group["question_ids"].append(question.id)
        group["total"] += 1
        answer = answers.get(question.id)
        if answer and answer.is_correct:
            group["correct"] += 1
        else:
            group["wrong_question_ids"].append(question.id)
            group["wrong_metadata"].append(meta)
    entries = list(groups.values())
    for group in entries:
        group.update(score_percent=100 * group["correct"] / group["total"],
                     wrong_count=group["total"] - group["correct"], attempt_id=attempt_id)
    return dict(all_topics=entries, weak_topics=[g for g in entries if g["wrong_count"]],
                strong_topics=[g for g in entries if not g["wrong_count"]])


def weak_groups_for_student(student_id, live_weak):
    """Newest evidence per domain/topic, retaining every missed level combination.

    A quiz replaces evidence for the topics it assessed; unrelated weaknesses
    remain. Live mastery still removes topics cleared by practice.
    """
    from app.services.lms.attempt_service import attempt_question_ids
    from app.services.lms.performance_service import _resolve_question_topic_id
    from app.services.lms.assessment_service import get_assessment

    db = get_db()
    live_ids = {w["topic_id"] for w in live_weak}
    seen = set()
    groups = []
    attempts = db.query(AssessmentAttempt).filter(
        AssessmentAttempt.student_id == student_id, AssessmentAttempt.status == "submitted",
    ).order_by(AssessmentAttempt.submitted_at.desc(), AssessmentAttempt.id.desc()).all()
    for attempt in attempts:
        assessment = get_assessment(attempt.assessment_id)
        answers = {a.question_id: a for a in db.query(AttemptAnswer).filter_by(attempt_id=attempt.id)}
        questions = db.query(Question).filter(Question.id.in_(attempt_question_ids(attempt))).order_by(Question.id).all()
        buckets = {}
        for question in questions:
            metadata = metadata_of(question)
            if not all(metadata.values()):
                continue  # Legacy metadata is handled by the existing path until reimported.
            key = (normalized(metadata["domain"]), normalized(metadata["topic"]))
            if key in seen:
                continue
            bucket = buckets.setdefault(key, {"domain": metadata["domain"], "topic_name": metadata["topic"],
                                               "questions": [], "wrong_metadata": [], "wrong_question_ids": []})
            bucket["questions"].append(question)
            answer = answers.get(question.id)
            if not answer or not answer.is_correct:
                bucket["wrong_metadata"].append(metadata)
                bucket["wrong_question_ids"].append(question.id)
        for key, bucket in buckets.items():
            seen.add(key)
            questions = bucket.pop("questions")
            wrong = len(bucket["wrong_metadata"])
            if not wrong:
                continue
            topic_id = _resolve_question_topic_id(questions[0], assessment)
            if topic_id not in live_ids:
                continue
            bucket.update(topic_id=topic_id, question_ids=[q.id for q in questions],
                          wrong_count=wrong, min_questions=wrong,
                          score_percent=100 * (len(questions) - wrong) / len(questions),
                          attempt_id=attempt.id)
            groups.append(bucket)
    covered = {g["topic_id"] for g in groups}
    # Do not discard pre-existing topics whose old imports lack metadata.
    return groups + [w for w in live_weak if w["topic_id"] not in covered]


def desired_profiles(wrong_metadata, count):
    """Largest-remainder allocation preserves the distribution of missed levels."""
    counts = Counter(tuple(normalized(m[k]) for k in ("domain", "topic", "cognitive_level", "difficulty"))
                     for m in wrong_metadata)
    if not counts:
        return []
    total = sum(counts.values())
    quotas = {k: count * n // total for k, n in counts.items()}
    remainder = sorted(counts, key=lambda k: (-(count * counts[k] % total), k))
    for key in remainder[:count - sum(quotas.values())]:
        quotas[key] += 1
    return [dict(zip(("domain", "topic", "cognitive_level", "difficulty"), key))
            for key in counts for _ in range(quotas[key])]


def rank_candidate(candidate, profile):
    """Exact, same topic/near levels, then same-domain fallback. Never cross domain."""
    if normalized(candidate.get("domain")) != normalized(profile.get("domain")) or not profile.get("domain"):
        return None
    same_topic = normalized(candidate.get("topic")) == normalized(profile.get("topic"))
    same_cognitive = normalized(candidate.get("cognitive_level")) == normalized(profile.get("cognitive_level"))
    same_difficulty = normalized(candidate.get("difficulty")) == normalized(profile.get("difficulty"))
    words = lambda value: set(re.findall(r"\w+", normalized(value))) - {"the", "of", "and", "a", "on"}
    candidate_words, target_words = words(candidate.get("topic")), words(profile.get("topic"))
    overlap = len(candidate_words & target_words) / max(1, len(candidate_words | target_words))
    rank = {"easy": 0, "medium": 1, "hard": 2}
    distance = abs(rank.get(normalized(candidate.get("difficulty")), 1) - rank.get(normalized(profile.get("difficulty")), 1))
    return (not same_topic, 0 if same_topic else 1 - overlap,
            not (same_cognitive and same_difficulty), not same_cognitive, distance)


def select_candidates(candidates, profiles):
    """Reserve exact matches for ALL profiles before relaxing any of them."""
    available = list(candidates)
    selected = {}
    for exact_only in (True, False):
        for index, profile in enumerate(profiles):
            if index in selected:
                continue
            ranked = [(rank_candidate(c, profile), i, c) for i, c in enumerate(available)]
            ranked = [r for r in ranked if r[0] is not None and (not exact_only or not any(r[0]))]
            if not ranked:
                continue
            _, i, candidate = min(ranked, key=lambda r: (r[0], r[2].get("question_id", 0)))
            selected[index] = candidate
            available.pop(i)
    return selected


def build_queue(entry, threads, owner_id, count, grade_level=None, exclude_texts=None):
    from app.services.lms.deficiency_chat_service import _mcq_to_queue_item, _question_key
    from app.services.lms.mcq_utils import options_from_json
    from app.services.lms.target_question_service import ensure_target_questions
    from app.services.quiz.models import MCQQuestion
    from app.services.quiz.mcq_answer_check import verify_mcq_keys
    from app.services.quiz.remediation_generator import generate_remediation_mcqs
    from app.services.lms.exceptions import LMSValidationError

    profiles = desired_profiles(entry["wrong_metadata"], count)
    excluded = {_question_key(t) for t in exclude_texts or []}
    candidates = []
    for thread in threads:
        ensure_target_questions(thread, owner_id)
    rows = get_db().query(Question).filter(
        Question.source_pdf_thread_id.in_(threads), Question.created_by == owner_id,
        Question.is_active.is_(True),
    ).order_by(Question.id).all()
    for row in rows:
        if row.id in entry.get("question_ids", []):
            continue
        meta = metadata_of(row)
        if not all(meta.values()) or not any(rank_candidate(meta, p) is not None for p in profiles):
            continue
        key = _question_key(row.question_text)
        if key in excluded:
            continue
        options = options_from_json(row.options_json)
        mcq = MCQQuestion(question_text=row.question_text, question_latex=row.question_latex,
                          options=options, correct_option_label=options[row.correct_option_index]["label"],
                          **meta)
        item = _mcq_to_queue_item(mcq, entry["topic_id"], entry["topic_name"], entry["topic_name"],
                                  row.source_pdf_thread_id, row.difficulty)
        if item:
            item.update(meta, question_id=row.id, source="target_pdf")
            candidates.append(item)
            excluded.add(key)
    # Exhaust matching target questions (including alternatives rejected by the
    # answer-key gate) before asking AI to fill any slots.
    selected = {}
    remaining = list(candidates)
    while remaining and len(selected) < count:
        open_indices = [i for i in range(count) if i not in selected]
        proposed = select_candidates(remaining, [profiles[i] for i in open_indices])
        if not proposed:
            break
        batch = list(proposed.values())
        verified_ids = {q["question_id"] for q in verify_mcq_keys(batch)}
        for local_index, item in proposed.items():
            if item["question_id"] in verified_ids:
                selected[open_indices[local_index]] = item
        tried = {q["question_id"] for q in batch}
        remaining = [q for q in remaining if q["question_id"] not in tried]
    used = list(exclude_texts or []) + [q["question_text"] for q in selected.values()]
    used_keys = {_question_key(t) for t in used}
    for _round in range(2):
        missing = [i for i in range(count) if i not in selected]
        if not missing:
            break
        grouped = {}
        for index in missing:
            grouped.setdefault(tuple(profiles[index].items()), []).append(index)
        for profile_items, indices in grouped.items():
            profile = dict(profile_items)
            generated = generate_remediation_mcqs(
                topic_name=profile["topic"], topic_description=profile["topic"], count=len(indices),
                score_percent=entry["score_percent"], difficulty=profile["difficulty"],
                grade_level=grade_level, exclude_question_texts=used,
                domain=profile["domain"], cognitive_level=profile["cognitive_level"],
            )
            items = []
            for mcq in generated:
                meta = {k: getattr(mcq, k, None) for k in profile}
                if any(normalized(meta[k]) != normalized(profile[k]) for k in profile):
                    continue  # Do not relabel irrelevant generated content to make it fit.
                item = _mcq_to_queue_item(mcq, entry["topic_id"], entry["topic_name"], entry["topic_name"], "", mcq.difficulty)
                key = _question_key((item or {}).get("question_text"))
                if item and key not in used_keys:
                    item.update(meta, source="grade_fit_ai")
                    items.append(item)
                    used_keys.add(key)
                    used.append(item["question_text"])
            for index, item in zip(indices, verify_mcq_keys(items)):
                selected[index] = item
    if len(selected) != count:
        raise LMSValidationError("Not enough verified practice questions are available yet. Please retry shortly.")
    return [selected[i] for i in range(count)]
