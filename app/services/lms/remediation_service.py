"""Personalized remediation quizzes for learning paths (weak topics)."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from app.models.lms_models import Assessment, AssessmentAttempt, AssessmentQuestion, Question
from app.services.lms import assessment_service, curriculum_service, question_bank_service
from app.services.lms.exceptions import LMSNotFoundError
from app.services.lms.path_generator import suggest_difficulty
from app.services.quiz.mcq_converter import mcq_to_question_fields
from app.services.quiz.remediation_generator import generate_remediation_mcqs
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_QUESTIONS_PER_QUIZ = 4


def _exclude_texts_for_student(student_id: int, topic_id: int) -> List[str]:
    """Collect question texts to avoid (diagnostic + prior remediation for this topic)."""
    db = get_db()
    texts: List[str] = []

    diag_attempts = (
        db.query(AssessmentAttempt)
        .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "submitted",
            Assessment.assessment_type == "diagnostic",
        )
        .all()
    )
    for attempt in diag_attempts:
        assessment = assessment_service.get_assessment(attempt.assessment_id)
        for aq in assessment.questions:
            q = db.query(Question).filter(Question.id == aq.question_id).first()
            if q and q.question_text:
                texts.append(q.question_text.strip())

    remediation_assessments = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_type == "quiz",
            Assessment.creation_mode == "mixed",
            Assessment.description.like(f'%"student_id": {student_id}%'),
            Assessment.description.like(f'%"topic_id": {topic_id}%'),
        )
        .all()
    )
    for assessment in remediation_assessments:
        for aq in assessment.questions:
            q = db.query(Question).filter(Question.id == aq.question_id).first()
            if q and q.question_text:
                texts.append(q.question_text.strip())

    return list(dict.fromkeys(texts))


def _find_existing_remediation_quiz(
    student_id: int, topic_id: int, purpose: str
) -> Optional[int]:
    db = get_db()
    rows = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_type == "quiz",
            Assessment.creation_mode == "mixed",
            Assessment.status == "published",
            Assessment.description.like(f'%"student_id": {student_id}%'),
            Assessment.description.like(f'%"topic_id": {topic_id}%'),
            Assessment.description.like(f'%"purpose": "{purpose}"%'),
        )
        .order_by(Assessment.updated_at.desc())
        .all()
    )
    for row in rows:
        if row.questions:
            return row.id
    return None


def get_or_create_remediation_quiz(
    student_id: int,
    topic_id: int,
    score_percent: float = 0.0,
    difficulty: Optional[str] = None,
    purpose: str = "practice",
) -> Optional[int]:
    """
    Create (or reuse) a published quiz with fresh AI questions for one weak topic.
    Questions are topic-related but NOT copied from the student's diagnostic.
    """
    existing = _find_existing_remediation_quiz(student_id, topic_id, purpose)
    if existing:
        return existing

    try:
        topic = curriculum_service.get_topic_by_id(topic_id)
    except LMSNotFoundError:
        logger.warning("remediation: topic %s not found", topic_id)
        return None

    diff = difficulty or suggest_difficulty(score_percent)
    exclude = _exclude_texts_for_student(student_id, topic_id)

    try:
        mcqs = generate_remediation_mcqs(
            topic_name=topic.name,
            topic_description=topic.description or topic.name,
            count=_QUESTIONS_PER_QUIZ,
            score_percent=score_percent,
            difficulty=diff,
            purpose=purpose,
            exclude_question_texts=exclude,
        )
    except Exception as exc:
        logger.warning("remediation MCQ generation failed for topic %s: %s", topic_id, exc)
        return None

    if not mcqs:
        return None

    purpose_label = "Reassessment" if purpose == "reassessment" else "Practice"
    title = f"{purpose_label}: {topic.name}"
    meta = {
        "remediation": True,
        "student_id": student_id,
        "topic_id": topic_id,
        "purpose": purpose,
    }

    assessment = assessment_service.create_assessment(
        created_by=student_id,
        title=title,
        assessment_type="quiz",
        creation_mode="mixed",
        description=json.dumps(meta, ensure_ascii=False),
    )

    question_ids: List[int] = []
    for mcq in mcqs:
        fields = mcq_to_question_fields(mcq)
        q = question_bank_service.create_question(
            created_by=student_id,
            question_text=fields["question_text"],
            options=fields["options"],
            correct_option_index=fields["correct_option_index"],
            topic_id=topic_id,
            question_latex=fields.get("question_latex"),
            explanation=fields.get("explanation"),
            difficulty=diff,
            source_type="mixed",
            extraction_confidence=fields.get("extraction_confidence"),
        )
        question_ids.append(q.id)

    assessment_service.add_questions(assessment.id, question_ids)
    assessment_service.publish_assessment(assessment.id)
    return assessment.id
