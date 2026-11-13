"""Live Groq + supplied-PDF acceptance check, exclusively in a disposable DB.

Usage: python scripts/verify_adaptive_pdf.py --bank PATH --diagnostic PATH
Uses the existing ignored .env credentials. Never connects to the application DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", required=True)
    parser.add_argument("--diagnostic", required=True)
    parser.add_argument("--output", default=".local/adaptive-pdf-report.json")
    parser.add_argument("--reuse-extraction", action="store_true", help="Reuse this PDF's cached Groq extraction if present")
    args = parser.parse_args()

    from dotenv import dotenv_values
    settings = dotenv_values(ROOT / ".env")
    for name in ("GROQ_API_KEY", "GROQ_MODEL"):
        if settings.get(name):
            os.environ[name] = settings[name]
    if not os.environ.get("GROQ_API_KEY"):
        raise RuntimeError("Groq credentials are missing")
    os.environ["LLM_PROVIDER"] = "groq"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["SKIP_EXTRA_STARTUP"] = "true"
    os.environ["SKIP_DB_INIT"] = "true"

    from flask import Flask
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import pymupdf
    from app.models.lms_models import Base, Question, Assessment, AssessmentQuestion, AssessmentAttempt, AttemptAnswer
    import app.utils.db as db_module
    from app.services.lms.target_question_service import index_target_questions
    from app.services.lms import target_question_service
    from app.services.lms.adaptive_practice_service import build_queue, analyze_metadata_attempt
    from app.services.lms.question_bank_service import create_question
    from app.services.lms.deficiency_chat_service import _question_count_for_score
    from app.services.quiz.pdf_extractor import try_native_mcq_harvest
    from app.services.quiz.question_metadata import complete_metadata
    from app.services.quiz.mcq_converter import mcq_to_question_fields

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    db_module.get_session_factory = lambda: lambda: session
    app = Flask(__name__)
    report = {}
    # Preserve expensive sample extraction for repeat runs, independently of DB
    # insertion. The cache contains public sample content, never credentials.
    import hashlib
    from types import SimpleNamespace
    original_extract = target_question_service.extract_target_questions
    def cached_extract(text, pdf_bytes=None):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        cache = ROOT / ".local" / f"adaptive-extraction-{digest}.json"
        if args.reuse_extraction and cache.exists():
            result = []
            for item in json.loads(cache.read_text(encoding="utf-8")):
                number = item.pop("source_number")
                item["options"] = [SimpleNamespace(**o) for o in item["options"]]
                result.append((SimpleNamespace(**item), number))
            complete_metadata([q for q, n in result], [n for q, n in result], text)
            return result
        result = original_extract(text, pdf_bytes)
        values = []
        for q, number in result:
            item = {k: getattr(q, k, None) for k in (
                "question_text", "question_latex", "correct_option_label", "explanation", "domain", "topic",
                "cognitive_level", "difficulty", "conversion_confidence", "preserve_option_order", "requires_review")}
            item["source_number"] = number
            item["options"] = [dict(label=o.label, text=o.text, latex=getattr(o, "latex", None)) for o in q.options]
            values.append(item)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        return result
    target_question_service.extract_target_questions = cached_extract
    with app.app_context():
        with pymupdf.open(args.bank) as doc:
            bank_text = "\n".join(page.get_text() for page in doc)
        with pymupdf.open(args.diagnostic) as doc:
            diagnostic_text = "\n".join(page.get_text() for page in doc)
        print("Indexing supplied bank with Groq metadata classification...", flush=True)
        bank = index_target_questions("qa-target", 9, bank_text, Path(args.bank).read_bytes())
        assert bank and all(q.domain and q.topic_label and q.cognitive_level and q.difficulty for q in bank)
        report["bank_questions"] = len(bank)
        report["bank_active"] = sum(q.is_active for q in bank)
        report["bank_requires_review"] = [{"number": q.source_question_number, "reason": q.explanation} for q in bank if not q.is_active]
        report["bank_domains"] = dict(Counter(q.domain for q in bank))
        print(f"Stored {len(bank)} target questions with all four metadata fields", flush=True)
        # Cache only public sample questions, for repeatable local checks without
        # spending additional API requests reclassifying the same teacher PDF.
        artifact = ROOT / ".local" / "adaptive-bank-fixture.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        from app.services.lms.question_bank_service import question_to_dict
        artifact.write_text(json.dumps([question_to_dict(q) for q in bank], ensure_ascii=False, indent=2), encoding="utf-8")

        diagnostic_items = cached_extract(diagnostic_text, Path(args.diagnostic).read_bytes())
        questions, numbers = map(list, zip(*diagnostic_items))
        assert len(questions) == 20, "Expected all 20 supplied diagnostic questions"
        complete_metadata(questions, numbers, diagnostic_text)
        report["diagnostic_questions_checked"] = len(questions)
        saved = [create_question(created_by=9, **mcq_to_question_fields(q)) for q in questions]
        analyses = []
        for kind in ("diagnostic", "quiz"):
            paper = Assessment(title="Disposable PDF QA", assessment_type=kind, status="published", creation_mode="manual")
            session.add(paper)
            session.flush()
            attempt = AssessmentAttempt(student_id=1, assessment_id=paper.id, status="submitted")
            session.add(attempt)
            session.flush()
            for i, q in enumerate(saved):
                session.add(AssessmentQuestion(assessment_id=paper.id, question_id=q.id, sort_order=i))
                session.add(AttemptAnswer(attempt_id=attempt.id, question_id=q.id, selected_option_index=(q.correct_option_index + 1) % 4,
                                         is_correct=False))
            session.commit()
            analyses.append(analyze_metadata_attempt(attempt.id))
        assert [(g["domain"], g["topic_name"], g["wrong_metadata"]) for g in analyses[0]["weak_topics"]] == [
            (g["domain"], g["topic_name"], g["wrong_metadata"]) for g in analyses[1]["weak_topics"]]
        report["diagnostic_quiz_shared_analysis"] = True

        group = next(g for g in analyses[0]["weak_topics"] if g["domain"] == "Numbers")
        # 33.3% retains the existing seven-question allocation.
        group["score_percent"] = 33.3
        count = _question_count_for_score(group["score_percent"])
        print(f"Selecting and independently verifying {count} target questions...", flush=True)
        queue = build_queue(group, ["qa-target"], 9, count, "9")
        assert len(queue) == 7
        assert all(q["domain"].casefold() == group["domain"].casefold() for q in queue)
        report["target_queue"] = dict(count=len(queue), sources=dict(Counter(q["source"] for q in queue)))

        print("Checking live Groq shortfall generation and independent answer verification...", flush=True)
        # Exclude every bank question to exercise actual generation, not a mock.
        queue = build_queue(group, ["qa-target"], 9, 2, "9", [q.question_text for q in bank])
        assert len(queue) == 2 and all(q["source"] == "grade_fit_ai" for q in queue)
        report["groq_fallback"] = dict(count=len(queue), metadata=[{k: q[k] for k in ("domain", "topic", "cognitive_level", "difficulty")} for q in queue])
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    session.close()
    engine.dispose()


if __name__ == "__main__":
    main()
