"""Rule-based learning path generator (P-402)."""

from __future__ import annotations



from typing import List, Optional



from app.models.database_models import Lesson as DBLesson

from app.models.lms_models import (

    Assessment,

    AssessmentQuestion,

    LearningPathTemplate,

    LessonTopic,

    Question,

    StudentTopicScore,

)

from app.services.lms import curriculum_service, performance_service

from app.services.lms.exceptions import LMSNotFoundError

from app.utils.db import get_db



WEAK_THRESHOLD = performance_service.WEAK_THRESHOLD





def get_weak_topics(student_id: int) -> List[dict]:
    return performance_service.get_weak_topics_for_student(student_id)





def _get_template_for_topic(topic_id: int) -> Optional[LearningPathTemplate]:

    db = get_db()

    return (

        db.query(LearningPathTemplate)

        .filter(

            LearningPathTemplate.topic_id == topic_id,

            LearningPathTemplate.is_active.is_(True),

        )

        .first()

    )





def _find_lesson_for_topic(topic_id: int) -> Optional[int]:

    db = get_db()

    row = (

        db.query(LessonTopic.lesson_id)

        .join(DBLesson, DBLesson.id == LessonTopic.lesson_id)

        .filter(LessonTopic.topic_id == topic_id)

        .order_by(DBLesson.id)

        .first()

    )

    return row[0] if row else None





def _find_quiz_for_topic(topic_id: int, difficulty: Optional[str] = None) -> Optional[int]:

    db = get_db()

    q = (

        db.query(Assessment.id)

        .join(AssessmentQuestion, AssessmentQuestion.assessment_id == Assessment.id)

        .join(Question, Question.id == AssessmentQuestion.question_id)

        .filter(

            Question.topic_id == topic_id,

            Assessment.assessment_type == "quiz",

            Assessment.status == "published",

        )

    )

    if difficulty:

        q = q.filter(Question.difficulty == difficulty)

    row = q.order_by(Assessment.updated_at.desc()).first()

    return row[0] if row else None





def suggest_difficulty(recent_score_percent: float) -> str:

    """P-407: adaptive difficulty from recent performance."""

    if recent_score_percent >= 85:

        return "hard"

    if recent_score_percent >= 60:

        return "medium"

    return "easy"





def _prerequisite_topic_ids(topic_id: int) -> List[int]:

    try:

        prereqs = curriculum_service.get_prerequisites(topic_id)

        return [p.id for p in prereqs]

    except Exception:

        return []





def _build_steps_for_topic(topic_id: int) -> List[tuple]:

    template = _get_template_for_topic(topic_id)

    if template and template.items:

        return [(i.item_type, i.label or i.item_type.title()) for i in template.items]

    return []





def build_path_items(student_id: int) -> List[dict]:

    """

    Build learning path from weak topics.

    Single chat step — weak-area practice happens in deficiency chat (not listed per topic).

    """

    weak = get_weak_topics(student_id)

    if not weak:

        return []



    return [

        {

            "item_type": "practice",

            "item_id": 0,

            "sort_order": 0,

            "label": "Learning Chat — practice weak areas",

        }

    ]





def has_mastery_data(student_id: int) -> bool:

    db = get_db()

    return (

        db.query(StudentTopicScore.id)

        .filter(StudentTopicScore.student_id == student_id)

        .first()

        is not None

    )

