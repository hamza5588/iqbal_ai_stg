"""Target PDF question bank: import, independent verification, admin review.

Pipeline: upload -> format/type check -> span extraction (target_bank_extractor)
-> answer-key join -> Domain/Subtopic mapping -> independent verification ->
``needs_review`` / ``auto_verified`` -> admin approval -> Guided Practice.

Nothing reaches a student until an admin approves it: ``auto_verified`` only means
every automated check passed and the item may be bulk-approved; ``needs_review``
items must be approved one by one after looking at the page preview (or
corrected first). Approved questions are never rewritten silently - a correction
returns the item to ``needs_review`` and keeps the original in its history.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from app.models.lms_models import (
    Question,
    QuestionTaxonomy,
    TargetBankDocument,
    TargetBankItem,
)
from app.services.lms import question_attributes as qa
from app.services.lms import taxonomy_service
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json
from app.services.quiz import target_bank_extractor as tbe
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_LABELS = "ABCD"
_DIFFICULTY = {"easy": "easy", "basic": "easy", "intermediate": "medium", "medium": "medium",
               "hard": "hard", "advanced": "hard", "difficult": "hard"}
LAYOUT_FLAG_TEXT = {
    "stacked_math_layout": "Stacked layout (fraction or radical index) cannot be represented from the text stream",
    "drawn_math_rule": "Drawn fraction bar / radical vinculum near this question",
    "nested_script": "Script inside a script (e.g. x^(3y²)) - check against the printed page",
    "metadata_line_unparsed": "Domain/Topic line could not be read",
}


# ---------------------------------------------------------------------------
# Deterministic rendering (no heuristic re-guessing of maths)
# ---------------------------------------------------------------------------

_SUP_TO_ASCII = {v: k for k, v in zip("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")}
_SUB_TO_ASCII = {v: k for k, v in zip("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")}
_FUNC_NAMES = ("log", "ln", "sin", "cos", "tan")


def _base_start(text: str, end: int) -> int:
    """Start index of the atom a script attaches to (char, number, (group), log)."""
    if end <= 0:
        return end
    ch = text[end - 1]
    if ch == ")":
        depth, i = 0, end - 1
        while i >= 0:
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    return i
            i -= 1
        return end - 1
    for name in _FUNC_NAMES:
        if text[:end].endswith(name):
            return end - len(name)
    if ch.isdigit():
        i = end - 1
        while i > 0 and (text[i - 1].isdigit() or (text[i - 1] == "." and i > 1 and text[i - 2].isdigit())):
            i -= 1
        return i
    if ch.isalpha():
        return end - 1
    return end


def _latex_escape(s: str) -> str:
    return s.replace("\\", "\\backslash ").replace("{", "\\{").replace("}", "\\}")


def bank_render(text: str) -> str:
    """HTML for MathJax: prose escaped, each base+script wrapped as ``\\(b^{s}\\)``."""
    text = text or ""
    out: List[str] = []
    i = 0
    plain_from = 0
    while i < len(text):
        ch = text[i]
        if ch in _SUP_TO_ASCII or ch in _SUB_TO_ASCII:
            table, op = (_SUP_TO_ASCII, "^") if ch in _SUP_TO_ASCII else (_SUB_TO_ASCII, "_")
            j = i
            while j < len(text) and text[j] in table:
                j += 1
            script = "".join(table[c] for c in text[i:j])
            start = max(_base_start(text, i), plain_from)
            base = text[start:i]
            out.append(html.escape(text[plain_from:start]))
            base_tex = "\\" + base if base in _FUNC_NAMES else _latex_escape(base)
            out.append(f"\\({base_tex}{op}{{{_latex_escape(script)}}}\\)")
            i = j
            plain_from = j
            continue
        i += 1
    out.append(html.escape(text[plain_from:]))
    return "".join(out)


def render_script_counts(rendered: str) -> Tuple[int, int]:
    sup = sum(len(m) for m in re.findall(r"\^\{([^}]*)\}", rendered))
    sub = sum(len(m) for m in re.findall(r"_\{([^}]*)\}", rendered))
    return sup, sub


def _glyph_script_counts(text: str) -> Tuple[int, int]:
    return (sum(1 for c in text if c in _SUP_TO_ASCII), sum(1 for c in text if c in _SUB_TO_ASCII))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _norm(s: Optional[str]) -> str:
    return taxonomy_service.normalize_key(tbe.normalize_glyphs(s or ""))


def verify_question(
    q: tbe.ExtractedMCQ,
    key: Optional[tbe.AnswerKeyEntry],
    *,
    plumber_scripts: Optional[Tuple[int, int]],
    duplicate_of: Optional[int] = None,
    conflicting_duplicate_of: Optional[int] = None,
) -> dict:
    """Deterministic checks. ``blocking`` must be empty for ``auto_verified``."""
    checks: List[dict] = []
    blocking: List[str] = []
    warnings: List[str] = []

    def check(name: str, ok: bool, detail: str = "", block: bool = True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            (blocking if block else warnings).append(f"{name}: {detail}" if detail else name)

    stem = (q.stem or "").strip()
    check("question_text_complete", len(stem) >= 8 and bool(re.search(r"[A-Za-z]", stem)),
          "question text missing or too short")
    check("option_count", len(q.options) == 4 and all(o.strip() for o in q.options),
          f"{len(q.options)} options, {sum(1 for o in q.options if not o.strip())} empty")
    norm_opts = [re.sub(r"\s+", "", o).casefold() for o in q.options]
    check("options_distinct", len(set(norm_opts)) == len(norm_opts), "two options are identical")
    check("answer_key_present", bool(key and key.answer in _LABELS),
          "no answer-key entry for this question number")
    check("printed_domain_topic", bool(q.printed_domain and q.printed_topic),
          "Domain/Topic not printed with the question")
    if key and (key.domain or key.topic):
        same = _norm(key.domain) == _norm(q.printed_domain) and _norm(key.topic) == _norm(q.printed_topic)
        check("answer_key_mapping_agrees", same,
              f"inline {q.printed_domain} / {q.printed_topic} vs key {key.domain} / {key.topic}")
    for flag in q.flags:
        check("math_layout", False, LAYOUT_FLAG_TEXT.get(flag, flag))
    full_text = " ".join([stem] + list(q.options))
    check("no_page_margin_text", not re.search(r"\|\s*Page\s+\d+|\bPage\s+\d+\s+of\s+\d+", full_text),
          "page header/footer text inside the question or an option")
    bad_glyph = any(c == "�" or 0xE000 <= ord(c) <= 0xF8FF for c in full_text)
    check("glyphs_mapped", not bad_glyph, "unmapped/private-use glyph in the text")
    check("scripts_have_unicode_form", "^(" not in full_text and "_(" not in full_text,
          "a superscript/subscript has no faithful plain-text form - needs an admin LaTeX check")
    if plumber_scripts is not None:
        native = (q.sup_chars_native, q.sub_chars_native)
        check("scripts_match_second_engine", tuple(plumber_scripts) == native,
              f"PyMuPDF sup/sub chars {native} vs pdfplumber {tuple(plumber_scripts)}")
    rendered = [bank_render(stem)] + [bank_render(o) for o in q.options]
    glyphs = [_glyph_script_counts(t) for t in [stem] + list(q.options)]
    lost = [i for i, (r, g) in enumerate(zip(rendered, glyphs)) if render_script_counts(r) != g]
    check("render_preserves_scripts", not lost, f"rendering changed scripts in part(s) {lost}")
    check("provenance", bool(q.pages) and q.number > 0, "missing source page or question number")
    if conflicting_duplicate_of:
        check("duplicate_conflict", False,
              f"same question text as Q{conflicting_duplicate_of} but different options/answer")
    if duplicate_of:
        check("duplicate", False, f"identical to Q{duplicate_of}", block=False)
    return {"checks": checks, "blocking": blocking, "warnings": warnings}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _storage_dir() -> str:
    configured = os.environ.get("TARGET_BANK_STORAGE_DIR")
    if configured:
        return configured
    try:
        from flask import current_app

        return os.path.join(current_app.instance_path, "target_bank")
    except Exception:  # noqa: BLE001
        return os.path.join(os.getcwd(), "instance", "target_bank")


def _bank_options_json(options: List[dict]) -> str:
    """Store printed options verbatim. The shared MCQ validator rejects one-letter
    option texts as "label only", but a variable such as ``a`` is real source
    content here; verify_question already checks count, emptiness and duplicates."""
    return json.dumps([{"label": o.get("label"), "text": o.get("text") or ""} for o in options], ensure_ascii=False)


def _discard_incomplete_document(db, doc: TargetBankDocument) -> None:
    """An import that crashed midway (no stats) is removed so a retry starts clean."""
    q_ids = [qid for (qid,) in db.query(TargetBankItem.question_id).filter(TargetBankItem.document_id == doc.id).all()]
    db.query(TargetBankItem).filter(TargetBankItem.document_id == doc.id).delete(synchronize_session=False)
    if q_ids:
        db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(q_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.id.in_(q_ids), Question.is_active.is_(False)).delete(synchronize_session=False)
    db.delete(doc)
    db.commit()


def _difficulty(value: Optional[str]) -> str:
    return _DIFFICULTY.get((value or "").strip().lower(), "medium")


def import_target_bank(
    pdf_bytes: bytes,
    filename: str,
    grade_level: str,
    *,
    assessment_id: Optional[int] = None,
    user_id: Optional[int] = None,
    subject: str = "Mathematics",
) -> dict:
    """Extract, verify and store a Target PDF's printed MCQs (idempotent per file+grade)."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise LMSValidationError("Target bank upload must be a PDF file")
    grade = taxonomy_service.normalize_grade(grade_level)
    if not grade:
        raise LMSValidationError("Grade level is required for a Target PDF question bank")
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    db = get_db()
    existing = (
        db.query(TargetBankDocument)
        .filter(TargetBankDocument.sha256 == sha, TargetBankDocument.grade_level == grade)
        .first()
    )
    if existing and existing.stats_json is None:
        logger.warning("Target bank %s: discarding an incomplete earlier import", filename)
        _discard_incomplete_document(db, existing)
        existing = None
    if existing:
        # Re-attached to a newer diagnostic (e.g. the old one was archived): follow it.
        if assessment_id and existing.assessment_id != assessment_id:
            existing.assessment_id = assessment_id
            db.commit()
        return document_summary(existing.id)

    os.makedirs(_storage_dir(), exist_ok=True)
    path = os.path.join(_storage_dir(), f"{sha}.pdf")
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(pdf_bytes)

    extraction = tbe.extract_bank(pdf_bytes)
    doc = TargetBankDocument(
        sha256=sha,
        original_filename=(filename or "target.pdf")[:512],
        grade_level=grade,
        subject=subject,
        assessment_id=assessment_id,
        storage_path=path,
        page_count=extraction.page_count,
        extractor_version=extraction.extractor_version,
        created_by=user_id,
    )
    db.add(doc)
    db.commit()  # stats_json stays NULL until every question is stored = "incomplete"

    stems: Dict[str, Tuple[int, str]] = {}
    plumber_cache: dict = {}
    taxonomy_ctx = SimpleNamespace(
        grade_level=grade, description=None, assessment_type="target_bank",
        creation_mode="pdf_qa_auto", title="", id=None,
    )
    counts = {"auto_verified": 0, "needs_review": 0}
    failed: List[dict] = []
    for q in extraction.questions:
        try:
            status = _store_question(db, doc, q, extraction, stems, plumber_cache, taxonomy_ctx, pdf_bytes, user_id)
            counts[status] += 1
        except Exception as exc:  # noqa: BLE001 - one bad question never sinks the bank
            logger.exception("Target bank %s: Q%s could not be stored", filename, q.number)
            db.rollback()
            failed.append({"question_number": q.number, "error": str(exc)[:200]})
    missing_key = sorted(set(extraction.answer_key) - {q.number for q in extraction.questions})
    stats = {
        "pdf_type": extraction.pdf_type,
        "questions_extracted": len(extraction.questions),
        "answer_key_entries": len(extraction.answer_key),
        "answer_key_without_question": missing_key,
        "failed_questions": failed,
        "warnings": extraction.warnings,
        **counts,
    }
    doc.stats_json = json.dumps(stats, ensure_ascii=False)
    db.commit()
    logger.info("Target bank %s imported: %s", filename, stats)
    return document_summary(doc.id)


def _store_question(db, doc, q, extraction, stems, plumber_cache, taxonomy_ctx, pdf_bytes, user_id) -> str:
    """Verify + persist one extracted MCQ; returns its verification status."""
    key = extraction.answer_key.get(q.number)
    stem_key = re.sub(r"\s+", "", q.stem).casefold()
    full_key = stem_key + "|" + "|".join(re.sub(r"\s+", "", o).casefold() for o in q.options) + (
        f"|{key.answer}" if key else "")
    dup = conflict = None
    if stem_key in stems:
        prev_no, prev_full = stems[stem_key]
        dup, conflict = (prev_no, None) if prev_full == full_key else (None, prev_no)
    else:
        stems[stem_key] = (q.number, full_key)
    try:
        plumber = tbe.script_char_counts(pdf_bytes, q.regions, plumber_cache)
    except Exception as exc:  # noqa: BLE001 - a missing second opinion blocks auto-verification
        logger.warning("Second-engine check failed for Q%s: %s", q.number, exc)
        plumber = None
    report = verify_question(q, key, plumber_scripts=plumber, duplicate_of=dup, conflicting_duplicate_of=conflict)
    if plumber is None:
        report["blocking"].append("scripts_match_second_engine: second engine unavailable")

    correct_idx = _LABELS.index(key.answer) if key and key.answer in _LABELS else 0
    options = [{"label": _LABELS[i], "text": text} for i, text in enumerate(q.options[:4])]
    while len(options) < 4:
        options.append({"label": _LABELS[len(options)], "text": ""})
    meta_bits = []
    if q.cognitive_level:
        meta_bits.append(f"Cognitive level: {q.cognitive_level}")
    if q.printed_topic:
        meta_bits.append(f"Topic: {q.printed_topic}")
    question = Question(
        topic_id=None,
        created_by=user_id,
        question_text=q.stem or f"Q{q.number}",
        question_latex=None,
        options_json=_bank_options_json(options),
        correct_option_index=correct_idx,
        correct_answer_raw=key.answer if key else None,
        explanation="; ".join(meta_bits) or None,
        difficulty=_difficulty(q.difficulty),
        cognitive_level=qa.normalize_cognitive_level(q.cognitive_level),
        printed_difficulty=(q.difficulty.strip()[:32] if qa.normalize_difficulty(q.difficulty) else None),
        source_type="pdf_qa_converted",
        source_question_number=q.number,
        extraction_confidence=None,
        is_active=False,  # becomes True only on admin approval
    )
    db.add(question)
    db.flush()
    tax = taxonomy_service.classify_question(
        question, taxonomy_ctx, printed_domain=q.printed_domain or (key.domain if key else None),
        printed_topic=q.printed_topic or (key.topic if key else None), refresh=True,
    )
    question.topic_id = tax.domain_topic_id
    if tax.mapping_status != "resolved":
        report["blocking"].append(f"taxonomy: {tax.mapping_status} ({tax.mapping_note or ''})")
        report["checks"].append({"name": "taxonomy_resolved", "ok": False, "detail": tax.mapping_status})
    else:
        report["checks"].append({"name": "taxonomy_resolved", "ok": True, "detail": ""})
    status = "auto_verified" if not report["blocking"] else "needs_review"
    db.add(
        TargetBankItem(
            document_id=doc.id,
            question_id=question.id,
            source_page=q.pages[0] if q.pages else None,
            source_question_number=q.number,
            verification_status=status,
            verification_report_json=json.dumps(report, ensure_ascii=False),
            extraction_metadata_json=json.dumps(
                {
                    "extractor": extraction.extractor_version,
                    "regions": q.regions,
                    "flags": q.flags,
                    "printed_domain": q.printed_domain,
                    "printed_topic": q.printed_topic,
                    "cognitive_level": q.cognitive_level,
                    "printed_difficulty": q.difficulty,
                    "answer_key": vars(key) if key else None,
                    "native_scripts": [q.sup_chars_native, q.sub_chars_native],
                    "second_engine_scripts": list(plumber) if plumber else None,
                    "history": [],
                },
                ensure_ascii=False,
            ),
        )
    )
    db.flush()
    return status


def import_target_bank_safely(pdf_bytes: bytes, filename: str, grade_level: str, **kwargs) -> Optional[dict]:
    """Best-effort hook for the diagnostic upload flow: never fails the upload."""
    try:
        return import_target_bank(pdf_bytes, filename, grade_level, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Target bank extraction skipped for %s: %s", filename, exc)
        try:
            get_db().rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


# ---------------------------------------------------------------------------
# Review API
# ---------------------------------------------------------------------------


def document_summary(document_id: int) -> dict:
    from sqlalchemy import func

    db = get_db()
    doc = db.query(TargetBankDocument).filter(TargetBankDocument.id == document_id).first()
    if not doc:
        raise LMSNotFoundError("Target bank document not found")
    by_status = dict(
        db.query(TargetBankItem.verification_status, func.count(TargetBankItem.id))
        .filter(TargetBankItem.document_id == doc.id)
        .group_by(TargetBankItem.verification_status)
        .all()
    )
    return {
        "document_id": doc.id,
        "filename": doc.original_filename,
        "grade_level": doc.grade_level,
        "assessment_id": doc.assessment_id,
        "page_count": doc.page_count,
        "extractor_version": doc.extractor_version,
        "stats": json.loads(doc.stats_json or "{}"),
        "status_counts": {k: int(v) for k, v in by_status.items()},
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def list_documents(grade_level: Optional[str] = None) -> List[dict]:
    db = get_db()
    q = db.query(TargetBankDocument)
    if grade_level:
        q = q.filter(TargetBankDocument.grade_level == taxonomy_service.normalize_grade(grade_level))
    return [document_summary(d.id) for d in q.order_by(TargetBankDocument.id.desc()).all()]


def _item_dict(item: TargetBankItem, question: Question, tax: Optional[QuestionTaxonomy], names: dict) -> dict:
    options = options_from_json(question.options_json)
    meta = json.loads(item.extraction_metadata_json or "{}")
    sub = names.get(tax.subtopic_id) if tax and tax.subtopic_id else None
    return {
        "item_id": item.id,
        "question_id": question.id,
        "document_id": item.document_id,
        "source_page": item.source_page,
        "source_question_number": item.source_question_number,
        "regions": meta.get("regions") or [],
        "verification_status": item.verification_status,
        "report": json.loads(item.verification_report_json or "{}"),
        "question_text": question.question_text,
        "question_render": bank_render(question.question_text),
        "options": [
            {"label": o.get("label"), "text": o.get("text"), "render": bank_render(o.get("text") or "")}
            for o in options
        ],
        "correct_label": _LABELS[question.correct_option_index] if question.correct_option_index is not None else None,
        "printed_domain": tax.printed_domain if tax else meta.get("printed_domain"),
        "printed_topic": tax.printed_topic if tax else meta.get("printed_topic"),
        "domain_topic_id": tax.domain_topic_id if tax else None,
        "subtopic_id": tax.subtopic_id if tax else None,
        "subtopic_name": sub["subtopic_name"] if sub else None,
        "domain_name": sub["domain_name"] if sub else None,
        "mapping_status": tax.mapping_status if tax else "unresolved",
        "cognitive_level": qa.cognitive_level_of(question),
        "printed_difficulty": question.printed_difficulty,
        "difficulty": qa.known_difficulty(question),
        "history": meta.get("history") or [],
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
    }


def list_items(document_id: int, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    db = get_db()
    q = (
        db.query(TargetBankItem, Question, QuestionTaxonomy)
        .join(Question, Question.id == TargetBankItem.question_id)
        .outerjoin(QuestionTaxonomy, QuestionTaxonomy.question_id == Question.id)
        .filter(TargetBankItem.document_id == document_id)
    )
    if status:
        q = q.filter(TargetBankItem.verification_status == status)
    total = q.count()
    rows = q.order_by(TargetBankItem.source_question_number).offset(offset).limit(min(limit, 200)).all()
    names = taxonomy_service.subtopic_names(t.subtopic_id for _, _, t in rows if t and t.subtopic_id)
    return {"total": total, "items": [_item_dict(i, qu, t, names) for i, qu, t in rows]}


def _load_item(item_id: int) -> Tuple[TargetBankItem, Question]:
    db = get_db()
    item = db.query(TargetBankItem).filter(TargetBankItem.id == item_id).first()
    if not item:
        raise LMSNotFoundError("Question bank item not found")
    question = db.query(Question).filter(Question.id == item.question_id).first()
    return item, question


def approve_items(item_ids: List[int], admin_id: int, *, reviewed_individually: bool = False) -> dict:
    """Publish items. ``needs_review`` items need ``reviewed_individually`` (one by one
    after looking at the page preview); bulk approval only takes ``auto_verified``."""
    db = get_db()
    approved, skipped = [], []
    for item_id in item_ids:
        item, question = _load_item(int(item_id))
        if item.verification_status == "approved":
            continue
        if item.verification_status == "rejected":
            skipped.append({"item_id": item.id, "reason": "rejected - correct it first"})
            continue
        if item.verification_status == "needs_review" and not (reviewed_individually and len(item_ids) == 1):
            skipped.append({"item_id": item.id, "reason": "needs individual review"})
            continue
        tax = db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id == question.id).first()
        if not tax or not tax.subtopic_id:
            skipped.append({"item_id": item.id, "reason": "no Subtopic mapping"})
            continue
        if not question.correct_answer_raw:
            skipped.append({"item_id": item.id, "reason": "no answer key"})
            continue
        item.verification_status = "approved"
        item.reviewed_by = admin_id
        item.reviewed_at = datetime.utcnow()
        question.is_active = True
        approved.append(item.id)
    db.commit()
    return {"approved": approved, "skipped": skipped}


def reject_items(item_ids: List[int], admin_id: int, reason: str = "") -> dict:
    db = get_db()
    done = []
    for item_id in item_ids:
        item, question = _load_item(int(item_id))
        item.verification_status = "rejected"
        item.reviewed_by = admin_id
        item.reviewed_at = datetime.utcnow()
        question.is_active = False
        meta = json.loads(item.extraction_metadata_json or "{}")
        meta.setdefault("history", []).append(
            {"action": "rejected", "by": admin_id, "at": datetime.utcnow().isoformat(), "reason": reason[:500]}
        )
        item.extraction_metadata_json = json.dumps(meta, ensure_ascii=False)
        done.append(item.id)
    db.commit()
    return {"rejected": done}


def correct_item(item_id: int, admin_id: int, changes: dict) -> dict:
    """Admin correction. The original stays in history; the item returns to
    ``needs_review`` (unpublished) until it is approved again."""
    db = get_db()
    item, question = _load_item(item_id)
    options = options_from_json(question.options_json)
    before = {
        "question_text": question.question_text,
        "options": [o.get("text") for o in options],
        "correct_label": _LABELS[question.correct_option_index],
    }
    tax = db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id == question.id).first()
    if tax:
        before.update({"printed_domain": tax.printed_domain, "printed_topic": tax.printed_topic})

    stem = changes.get("question_text")
    if stem is not None:
        if len(stem.strip()) < 3:
            raise LMSValidationError("Question text is required")
        question.question_text = stem.strip()
    new_opts = changes.get("options")
    if new_opts is not None:
        if len(new_opts) != 4 or not all(str(o).strip() for o in new_opts):
            raise LMSValidationError("Exactly four non-empty options are required")
        options = [{"label": _LABELS[i], "text": str(t).strip()} for i, t in enumerate(new_opts)]
    label = (changes.get("correct_label") or "").strip().upper()
    if label:
        if label not in _LABELS:
            raise LMSValidationError("Correct answer must be A, B, C or D")
        question.correct_option_index = _LABELS.index(label)
        question.correct_answer_raw = label
    question.options_json = _bank_options_json(options)
    if changes.get("printed_domain") or changes.get("printed_topic"):
        domain = changes.get("printed_domain") or (tax.printed_domain if tax else None)
        topic = changes.get("printed_topic") or (tax.printed_topic if tax else None)
        doc = db.query(TargetBankDocument).filter(TargetBankDocument.id == item.document_id).first()
        ctx = SimpleNamespace(grade_level=doc.grade_level if doc else "", description=None,
                              assessment_type="target_bank", creation_mode="pdf_qa_auto", title="", id=None)
        bits = [b for b in (question.explanation or "").split("; ") if not b.lower().startswith("topic:")]
        question.explanation = "; ".join(bits + [f"Topic: {topic}"])
        tax = taxonomy_service.classify_question(question, ctx, printed_domain=domain, printed_topic=topic, refresh=True)
        question.topic_id = tax.domain_topic_id

    meta = json.loads(item.extraction_metadata_json or "{}")
    meta.setdefault("history", []).append(
        {"action": "corrected", "by": admin_id, "at": datetime.utcnow().isoformat(), "before": before}
    )
    item.extraction_metadata_json = json.dumps(meta, ensure_ascii=False)
    report = json.loads(item.verification_report_json or "{}")
    report.setdefault("warnings", []).append("admin_corrected: verify against the page preview before approving")
    item.verification_report_json = json.dumps(report, ensure_ascii=False)
    item.verification_status = "needs_review"
    item.reviewed_by = admin_id
    item.reviewed_at = datetime.utcnow()
    question.is_active = False
    db.commit()
    names = taxonomy_service.subtopic_names([tax.subtopic_id] if tax and tax.subtopic_id else [])
    return _item_dict(item, question, tax, names)


def ai_edit_item(item_id: int, admin_id: int, instruction: str, *, apply: bool = True) -> dict:
    """Rewrite a bank MCQ from a short admin prompt (no math keyboard).

    Uses the platform LLM. Math that PDF text flattened (e.g. ``√a n`` instead of
    an nth-root index) is restored with ``$...$`` LaTeX so MathJax can render it.
    When ``apply`` is true the rewrite is saved via ``correct_item`` (back to
    needs_review); otherwise only a draft is returned for the admin to review.
    """
    from pydantic import BaseModel, Field

    from app.services.quiz.retry_utils import invoke_structured
    from app.utils.llm_factory import get_chat_model
    from app.utils.llm_gateway import llm_workflow

    instruction = (instruction or "").strip()
    if len(instruction) < 3:
        raise LMSValidationError("Describe what to fix (e.g. 'put n as the index of the radical').")
    if len(instruction) > 2000:
        raise LMSValidationError("Instruction is too long (max 2000 characters).")

    item, question = _load_item(item_id)
    options = options_from_json(question.options_json)
    db = get_db()
    tax = db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id == question.id).first()

    class _AiBankEdit(BaseModel):
        question_text: str = Field(..., description="Full stem; math in $...$ LaTeX when needed")
        options: List[str] = Field(..., description="Exactly four option texts A–D order")
        correct_label: str = Field(..., description="A, B, C, or D")
        printed_domain: Optional[str] = None
        printed_topic: Optional[str] = None
        notes: Optional[str] = Field(None, description="One-line what you changed")

    current = {
        "question_text": question.question_text,
        "options": [o.get("text") for o in options],
        "correct_label": _LABELS[question.correct_option_index]
        if question.correct_option_index is not None
        else "A",
        "printed_domain": (tax.printed_domain if tax else None) or "",
        "printed_topic": (tax.printed_topic if tax else None) or "",
        "verification_status": item.verification_status,
        "report": json.loads(item.verification_report_json or "{}"),
    }

    prompt = (
        "You are editing ONE extracted Target-PDF MCQ for a Grade school math bank.\n"
        "The admin instruction describes what is wrong. Fix ONLY what they ask for.\n"
        "Keep the same meaning, answer key, and four options unless the instruction says otherwise.\n"
        "Math rules:\n"
        "- Prefer clear LaTeX inside single $...$ for radicals, fractions, exponents, subscripts.\n"
        "- Nth root with index n of a must be written as $\\sqrt[n]{a}$ (index on the radical), "
        "never as plain '√a n' or '√an'.\n"
        "- Square root of a: $\\sqrt{a}$. Fraction a/b: $\\frac{a}{b}$.\n"
        "- Keep surrounding English prose as plain text outside the $...$ spans.\n"
        "- Do not invent new topics or change Domain/Topic unless asked.\n"
        "- correct_label must be A, B, C, or D.\n"
        "- options must be exactly four non-empty strings in order A–D.\n\n"
        f"ADMIN INSTRUCTION:\n{instruction}\n\n"
        f"CURRENT ITEM JSON:\n{json.dumps(current, ensure_ascii=False)}\n"
    )

    with llm_workflow("target_bank_ai_edit", user_id=admin_id, user_role="admin"):
        llm = get_chat_model(temperature=0.2, max_tokens=2500)
        draft: _AiBankEdit = invoke_structured(llm, _AiBankEdit, prompt)

    proposed = {
        "question_text": draft.question_text.strip(),
        "options": [str(o).strip() for o in draft.options],
        "correct_label": draft.correct_label.strip().upper(),
        "printed_domain": (draft.printed_domain or current["printed_domain"] or "").strip(),
        "printed_topic": (draft.printed_topic or current["printed_topic"] or "").strip(),
        "notes": (draft.notes or "").strip() or None,
    }
    if len(proposed["options"]) != 4 or not all(proposed["options"]):
        raise LMSValidationError("AI returned invalid options; try again with a clearer instruction.")

    if not apply:
        return {"applied": False, "proposed": proposed, "before": current}

    saved = correct_item(item_id, admin_id, proposed)
    return {"applied": True, "proposed": proposed, "item": saved, "notes": proposed.get("notes")}


def page_preview_png(document_id: int, page: int, bbox: Optional[List[float]] = None, zoom: float = 2.0) -> bytes:
    """PNG of a source page (optionally clipped to a question region) for review."""
    import pymupdf

    db = get_db()
    doc = db.query(TargetBankDocument).filter(TargetBankDocument.id == document_id).first()
    if not doc or not os.path.exists(doc.storage_path):
        raise LMSNotFoundError("Source PDF is not available")
    pdf = pymupdf.open(doc.storage_path)
    try:
        if page < 1 or page > pdf.page_count:
            raise LMSValidationError("Page out of range")
        pg = pdf[page - 1]
        clip = None
        if bbox and len(bbox) == 4:
            x0, y0, x1, y1 = bbox
            clip = pymupdf.Rect(max(0, x0 - 12), max(0, y0 - 10), min(pg.rect.width, x1 + 60), min(pg.rect.height, y1 + 10))
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        return pix.tobytes("png")
    finally:
        pdf.close()
