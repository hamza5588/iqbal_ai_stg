"""SQLAlchemy models for the LMS (classes, quizzes, mastery, assignments)."""
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import relationship
from datetime import datetime

from app.models.database_models import Base


class Topic(Base):
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    parent_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    subject = Column(String(100), nullable=False, index=True)
    grade_level = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, server_default="0")
    is_active = Column(Boolean, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    parent = relationship("Topic", remote_side=[id], backref="children")
    prerequisites = relationship(
        "TopicPrerequisite",
        foreign_keys="TopicPrerequisite.topic_id",
        back_populates="topic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("subject", "slug", name="uq_topics_subject_slug"),
        Index("idx_topics_parent_id", "parent_id"),
    )


class TopicPrerequisite(Base):
    __tablename__ = "topic_prerequisites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    prerequisite_topic_id = Column(
        Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )

    topic = relationship("Topic", foreign_keys=[topic_id], back_populates="prerequisites")
    prerequisite = relationship("Topic", foreign_keys=[prerequisite_topic_id])

    __table_args__ = (
        UniqueConstraint("topic_id", "prerequisite_topic_id", name="uq_topic_prerequisite"),
    )


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    question_text = Column(Text, nullable=False)
    question_latex = Column(Text, nullable=True)
    options_json = Column(Text, nullable=False)  # JSON array of {label, text, latex?}
    correct_option_index = Column(Integer, nullable=False)
    correct_answer_raw = Column(Text, nullable=True)
    explanation = Column(Text, nullable=True)
    difficulty = Column(String(20), nullable=False, default="medium", server_default="medium")
    source_type = Column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    source_pdf_thread_id = Column(String(255), nullable=True, index=True)
    source_question_number = Column(Integer, nullable=True)
    extraction_confidence = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    topic = relationship("Topic")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('manual','pdf_qa_converted','pdf_ai','mixed')",
            name="check_question_source_type",
        ),
        CheckConstraint(
            "difficulty IN ('easy','medium','hard')",
            name="check_question_difficulty",
        ),
        CheckConstraint(
            "correct_option_index >= 0 AND correct_option_index <= 3",
            name="check_correct_option_index",
        ),
    )


class SchoolClass(Base):
    __tablename__ = "classes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    join_code = Column(String(16), nullable=False, unique=True, index=True)
    grade_level = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    enrollments = relationship(
        "ClassEnrollment", back_populates="school_class", cascade="all, delete-orphan"
    )


class ClassEnrollment(Base):
    __tablename__ = "class_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    enrolled_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    school_class = relationship("SchoolClass", back_populates="enrollments")

    __table_args__ = (
        UniqueConstraint("class_id", "student_id", name="uq_class_enrollment"),
        CheckConstraint(
            "status IN ('active','inactive','removed')",
            name="check_enrollment_status",
        ),
        Index("idx_class_enrollments_student_id", "student_id"),
    )


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=True)
    assessment_type = Column(String(32), nullable=False)  # diagnostic | quiz
    creation_mode = Column(String(32), nullable=False, default="manual", server_default="manual")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    time_limit_minutes = Column(Integer, nullable=True)
    overall_confidence = Column(Float, nullable=True)
    requires_review = Column(Boolean, default=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    questions = relationship(
        "AssessmentQuestion",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="AssessmentQuestion.sort_order",
    )
    pdf_source = relationship(
        "QuizPdfSource", back_populates="assessment", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "assessment_type IN ('diagnostic','quiz')",
            name="check_assessment_type",
        ),
        CheckConstraint(
            "creation_mode IN ('manual','pdf_qa_auto','pdf_ai','mixed')",
            name="check_creation_mode",
        ),
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="check_assessment_status",
        ),
    )


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")

    assessment = relationship("Assessment", back_populates="questions")
    question = relationship("Question")

    __table_args__ = (
        UniqueConstraint("assessment_id", "question_id", name="uq_assessment_question"),
        Index("idx_assessment_questions_assessment_id", "assessment_id"),
    )


class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    quiz_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(512), nullable=False)
    instructions = Column(Text, nullable=True)
    due_date = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    submissions = relationship(
        "AssignmentSubmission", back_populates="assignment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','closed')",
            name="check_assignment_status",
        ),
    )


class QuizPdfSource(Base):
    __tablename__ = "quiz_pdf_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    rag_thread_id = Column(String(255), nullable=True, index=True)
    original_filename = Column(String(512), nullable=True)
    extraction_status = Column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    overall_confidence = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    assessment = relationship("Assessment", back_populates="pdf_source")
    extractions = relationship(
        "PdfQaExtraction", back_populates="pdf_source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "extraction_status IN ('pending','processing','completed','failed')",
            name="check_extraction_status",
        ),
    )


class PdfQaExtraction(Base):
    __tablename__ = "pdf_qa_extractions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quiz_pdf_source_id = Column(
        Integer, ForeignKey("quiz_pdf_sources.id", ondelete="CASCADE"), nullable=False
    )
    raw_extraction_json = Column(Text, nullable=True)
    pair_count = Column(Integer, nullable=True)
    warnings_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    pdf_source = relationship("QuizPdfSource", back_populates="extractions")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_id = Column(Integer, ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(32), nullable=False, default="in_progress", server_default="in_progress")
    score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    submitted_at = Column(DateTime, nullable=True)

    answers = relationship(
        "AttemptAnswer", back_populates="attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress','submitted','abandoned')",
            name="check_attempt_status",
        ),
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attempt_id = Column(
        Integer, ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    selected_option_index = Column(Integer, nullable=True)
    is_correct = Column(Boolean, nullable=True)

    attempt = relationship("AssessmentAttempt", back_populates="answers")

    __table_args__ = (
        UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question"),
    )


class StudentTopicScore(Base):
    __tablename__ = "student_topic_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    score_percent = Column(Float, nullable=False, default=0.0, server_default="0")
    mastery_status = Column(String(32), nullable=False, default="needs_practice", server_default="needs_practice")
    last_assessed_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    topic = relationship("Topic")

    __table_args__ = (
        UniqueConstraint("student_id", "topic_id", name="uq_student_topic_score"),
        CheckConstraint(
            "mastery_status IN ('mastered','improving','needs_practice','weak')",
            name="check_mastery_status",
        ),
    )


class MasterySnapshot(Base):
    __tablename__ = "mastery_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    items = relationship(
        "LearningPathItem",
        back_populates="learning_path",
        cascade="all, delete-orphan",
        order_by="LearningPathItem.sort_order",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','archived')",
            name="check_learning_path_status",
        ),
    )


class LearningPathItem(Base):
    __tablename__ = "learning_path_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    learning_path_id = Column(
        Integer, ForeignKey("learning_paths.id", ondelete="CASCADE"), nullable=False
    )
    item_type = Column(String(32), nullable=False)
    item_id = Column(Integer, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    completed_at = Column(DateTime, nullable=True)

    learning_path = relationship("LearningPath", back_populates="items")

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('lesson','quiz','practice','reassessment')",
            name="check_path_item_type",
        ),
        CheckConstraint(
            "status IN ('pending','in_progress','completed','skipped')",
            name="check_path_item_status",
        ),
    )


class AssignmentSubmission(Base):
    __tablename__ = "assignment_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assignment_id = Column(
        Integer, ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    attempt_id = Column(
        Integer, ForeignKey("assessment_attempts.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String(32), nullable=False, default="not_started", server_default="not_started")
    submitted_at = Column(DateTime, nullable=True)

    assignment = relationship("Assignment", back_populates="submissions")

    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_assignment_submission"),
        CheckConstraint(
            "status IN ('not_started','in_progress','submitted','overdue')",
            name="check_assignment_submission_status",
        ),
    )
