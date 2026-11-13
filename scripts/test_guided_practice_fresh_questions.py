"""Guided Practice serves fresh, verified questions across repeat visits.

Business rule changed (subtopic learning system): Guided Practice no longer
generates AI questions. It serves only admin-approved MCQs extracted from the
Target PDF, for the student's currently weak Subtopics. The guarantees this
file used to check for the AI batches are kept for the bank:

* each verified question is used once before any repeat (fresh across visits);
* unverified (needs_review / rejected) questions are never served;
* an unanswered question is resumed on the next visit instead of piling up;
* topic practice can no longer create AI questions at all.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.lms_models import Base, Question, StudentSubtopicScore, TargetBankDocument, TargetBankItem
from app.services.lms.exceptions import LMSValidationError

STUDENT = 42


@pytest.fixture()
def bank(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    import app.utils.db as db_mod
    from app.services.lms import class_service

    monkeypatch.setattr(db_mod, "get_session_factory", lambda: (lambda: db))
    monkeypatch.setattr(class_service, "get_student_grade", lambda sid: "9")
    monkeypatch.setattr(class_service, "get_student_diagnostic_grade", lambda sid: "9")
    with Flask(__name__).app_context():
        from app.services.lms import taxonomy_service

        doc = TargetBankDocument(sha256="f" * 64, grade_level="9", storage_path="bank.pdf")
        db.add(doc)
        db.flush()
        ctx = SimpleNamespace(grade_level="9", description=None, assessment_type="target_bank",
                              creation_mode="pdf_qa_auto", title="", id=None)
        sub_id = None
        for n, status in enumerate(["approved"] * 5 + ["needs_review", "rejected"], start=1):
            q = Question(question_text=f"Practice item {n}", options_json=json.dumps(
                [{"label": c, "text": f"{c}{n}"} for c in "ABCD"]), correct_option_index=1, correct_answer_raw="B",
                source_type="pdf_qa_converted", is_active=status == "approved")
            db.add(q)
            db.flush()
            tax = taxonomy_service.classify_question(q, ctx, printed_domain="Algebra",
                                                     printed_topic="Factorization", refresh=True)
            sub_id = tax.subtopic_id
            db.add(TargetBankItem(document_id=doc.id, question_id=q.id, source_question_number=n,
                                  verification_status=status, source_page=1))
        db.add(StudentSubtopicScore(student_id=STUDENT, subtopic_id=sub_id, domain_topic_id=tax.domain_topic_id,
                                    score_percent=35.0, sample_size=3, last_assessed_at=datetime(2026, 11, 2)))
        db.commit()
        yield db, sub_id
    db.close()


def _practise(n):
    from app.services.lms import guided_practice_service

    out = []
    for _ in range(n):
        nxt = guided_practice_service.next_question(STUDENT)
        out.append((nxt["question"]["question_text"], nxt["bank_exhausted"]))
        guided_practice_service.submit_answer(STUDENT, nxt["attempt_id"], 0)
    return out


def test_each_verified_question_is_used_once_before_any_repeat(bank):
    first = _practise(5)
    assert sorted(t for t, _ in first) == [f"Practice item {n}" for n in range(1, 6)]
    assert not any(exhausted for _, exhausted in first)
    repeat = _practise(2)
    assert all(exhausted for _, exhausted in repeat)  # bank is finite: repeats are flagged, not invented


def test_unverified_or_rejected_questions_are_never_served(bank):
    served = {t for t, _ in _practise(12)}
    assert "Practice item 6" not in served and "Practice item 7" not in served


def test_unanswered_question_is_resumed_on_the_next_visit(bank):
    from app.services.lms import guided_practice_service

    first = guided_practice_service.next_question(STUDENT)
    again = guided_practice_service.next_question(STUDENT)
    assert again["resumed"] and again["attempt_id"] == first["attempt_id"]


def test_topic_practice_cannot_generate_ai_questions(bank):
    db, _ = bank
    from app.services.lms import practice_service

    before = db.query(Question).count()
    assert not hasattr(practice_service, "_create_ai_practice_batch")
    with pytest.raises(LMSValidationError):
        practice_service.start_session(STUDENT, topic_id=7)
    assert db.query(Question).count() == before
