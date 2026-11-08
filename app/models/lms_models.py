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


class LessonTopic(Base):
    """Join table linking lessons to curriculum topics."""

    __tablename__ = "lesson_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lesson_id = Column(
        Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_id = Column(
        Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("lesson_id", "topic_id", name="uq_lesson_topic"),
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
    # Canonical cognitive level printed on the source paper (Knowledge /
    # Understanding / Application / Analysis/Reasoning); NULL when not printed.
    cognitive_level = Column(String(64), nullable=True)
    # Difficulty label exactly as printed ("Intermediate"). NULL means the paper
    # printed none and ``difficulty`` is only the default - never a real match.
    printed_difficulty = Column(String(32), nullable=True)
    time_limit_seconds = Column(Integer, nullable=True)
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
    grade_level = Column(String(100), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    time_limit_minutes = Column(Integer, nullable=True)
    overall_confidence = Column(Float, nullable=True)
    requires_review = Column(Boolean, default=False, server_default="0")
    # Diagnostics only: set when a PUBLISHED diagnostic is removed / replaced by the admin.
    # Students who already existed at superseded_at still take it first (if not submitted);
    # brand-new accounts only see the current published diagnostic.
    superseded_at = Column(DateTime, nullable=True)
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
    target_pdfs = relationship(
        "DiagnosticTargetPdf",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="DiagnosticTargetPdf.sort_order",
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
    # Scheduled release (naive UTC). NULL = visible as soon as the assignment is published.
    # Same convention as LessonAssignment.publish_at.
    publish_at = Column(DateTime, nullable=True)
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


class LessonAssignment(Base):
    """Assign a lesson to a specific class (quiz-assignment parity)."""
    __tablename__ = "lesson_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    instructions = Column(Text, nullable=True)
    due_date = Column(DateTime, nullable=True)
    # Scheduled release (naive UTC). NULL = visible as soon as the assignment is published.
    # Distinct from due_date, which is only the deadline shown to students.
    publish_at = Column(DateTime, nullable=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','closed')",
            name="check_lesson_assignment_status",
        ),
        UniqueConstraint("lesson_id", "class_id", name="uq_lesson_assignment_lesson_class"),
        Index("idx_lesson_assignments_class_status", "class_id", "status"),
    )


class QuizPdfSource(Base):
    __tablename__ = "quiz_pdf_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    rag_thread_id = Column(String(255), nullable=True, index=True)
    original_filename = Column(String(512), nullable=True)
    target_rag_thread_id = Column(String(255), nullable=True, index=True)
    target_original_filename = Column(String(512), nullable=True)
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


class DiagnosticTargetPdf(Base):
    """Multiple target content PDFs per diagnostic (Learning Chat source)."""

    __tablename__ = "diagnostic_target_pdfs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rag_thread_id = Column(String(255), nullable=False, index=True)
    original_filename = Column(String(512), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    assessment = relationship("Assessment", back_populates="target_pdfs")


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
    expires_at = Column(DateTime, nullable=True)
    timed_out = Column(Boolean, nullable=False, default=False, server_default="false")
    # JSON list of question ids for THIS attempt. NULL = use the assessment's
    # own question list (every attempt before diagnostic retakes existed).
    # A retake stores a fresh, order-shuffled / AI-variant set here.
    question_ids_json = Column(Text, nullable=True)
    # Last time the student's quiz page pinged the server, and how many seconds of
    # offline time were already added back to expires_at (diagnostics only, capped).
    last_seen_at = Column(DateTime, nullable=True)
    offline_credit_seconds = Column(Integer, nullable=False, default=0, server_default="0")

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


class StudentStudyGroupAssignment(Base):
    """Effective-dated Controlled/Uncontrolled membership for one study cohort."""

    __tablename__ = "student_study_group_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cohort_key = Column(String(100), nullable=False, default="default", server_default="default")
    group_name = Column(String(20), nullable=False)
    effective_from = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    effective_to = Column(DateTime, nullable=True)
    assigned_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source = Column(String(32), nullable=False, default="manual", server_default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "group_name IN ('Controlled','Uncontrolled')",
            name="check_student_study_group_name",
        ),
        CheckConstraint(
            "source IN ('manual','import')",
            name="check_student_study_group_source",
        ),
        Index(
            "idx_student_group_effective",
            "student_id",
            "cohort_key",
            "effective_from",
            "effective_to",
        ),
    )


class StudentStudyGroupAudit(Base):
    """Append-only history of every assignment, reassignment and unassignment."""

    __tablename__ = "student_study_group_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cohort_key = Column(String(100), nullable=False, default="default", server_default="default")
    old_group_name = Column(String(20), nullable=True)
    new_group_name = Column(String(20), nullable=True)
    changed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    source = Column(String(32), nullable=False, default="manual", server_default="manual")
    note = Column(String(512), nullable=True)

    __table_args__ = (
        Index("idx_student_group_audit_student", "student_id", "cohort_key", "changed_at"),
    )


class AssessmentReportClassification(Base):
    """Admin-owned study classification; assessment_type is intentionally not reused."""

    __tablename__ = "assessment_report_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    cohort_key = Column(String(100), nullable=False, default="default", server_default="default")
    test_type = Column(String(20), nullable=False)
    followup_order = Column(Integer, nullable=True)
    classified_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "test_type IN ('Baseline','FollowUp')",
            name="check_assessment_report_test_type",
        ),
        CheckConstraint(
            "followup_order IS NULL OR followup_order > 0",
            name="check_assessment_report_followup_order",
        ),
        UniqueConstraint("assessment_id", "cohort_key", name="uq_assessment_report_cohort"),
        Index("idx_assessment_report_type", "cohort_key", "test_type", "followup_order"),
    )


class StudentTopicScore(Base):
    __tablename__ = "student_topic_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False)
    score_percent = Column(Float, nullable=False, default=0.0, server_default="0")
    # How many questions the current score_percent was computed from - a
    # diagnostic/quiz can resolve very unevenly many questions per topic
    # (topic assignment is a text/PDF-label heuristic, not a fixed column -
    # e.g. one real diagnostic resolved 25 questions into topics with as
    # few as 1-2 each). Without this, get_overall_progress()'s per-topic
    # average weighs a 1-question topic the same as a 10-question one,
    # which can make "Overall Progress" diverge sharply (in either
    # direction) from the student's actual raw score. See #: Overall
    # Progress not matching real marks.
    sample_size = Column(Integer, nullable=False, default=1, server_default="1")
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
    # Fingerprint of the weak Domains + Subtopics the path was built for, so a
    # refresh with an unchanged weak set keeps the path instead of duplicating it.
    weakness_signature = Column(Text, nullable=True)
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
    label = Column(String(512), nullable=True)

    learning_path = relationship("LearningPath", back_populates="items")

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('lesson','quiz','practice','reassessment','enrichment')",
            name="check_path_item_type",
        ),
        CheckConstraint(
            "status IN ('pending','in_progress','completed','skipped')",
            name="check_path_item_status",
        ),
    )


class LearningPathTemplate(Base):
    """Remediation template for a weak topic (P-401)."""

    __tablename__ = "learning_path_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    topic = relationship("Topic")
    items = relationship(
        "LearningPathTemplateItem",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="LearningPathTemplateItem.sort_order",
    )


class LearningPathTemplateItem(Base):
    __tablename__ = "learning_path_template_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(
        Integer, ForeignKey("learning_path_templates.id", ondelete="CASCADE"), nullable=False
    )
    item_type = Column(String(32), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    label = Column(String(512), nullable=True)

    template = relationship("LearningPathTemplate", back_populates="items")

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('lesson','quiz','practice','reassessment')",
            name="check_template_item_type",
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


class PracticeSession(Base):
    """Guided practice session (Phase 5)."""

    __tablename__ = "practice_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    hint_level = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    attempts = relationship("PracticeAttempt", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','completed','abandoned')",
            name="check_practice_session_status",
        ),
    )


class PracticeAttempt(Base):
    __tablename__ = "practice_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("practice_sessions.id", ondelete="CASCADE"), nullable=False
    )
    selected_option_index = Column(Integer, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    hint_level_used = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    session = relationship("PracticeSession", back_populates="attempts")


class DeficiencyChatSession(Base):
    """Post-diagnostic chat: one-by-one weak-area questions + PDF-grounded tutor help."""

    __tablename__ = "deficiency_chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    diagnostic_assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True
    )
    rag_thread_id = Column(String(255), nullable=True)
    rag_owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    weak_topics_json = Column(Text, nullable=True)
    questions_json = Column(Text, nullable=False, default="[]")
    current_index = Column(Integer, nullable=False, default=0, server_default="0")
    correct_count = Column(Integer, nullable=False, default=0, server_default="0")
    chat_history_json = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    # Why a session ended without the student finishing it, e.g. "superseded"
    # when a newer assessment cleared every subtopic it was practising.
    close_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed')",
            name="check_deficiency_chat_status",
        ),
    )


class StudentProfile(Base):
    """Extended LMS state for students (onboarding, diagnostic, learning path)."""

    __tablename__ = "student_profiles"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    diagnostic_completed = Column(Boolean, nullable=False, default=False, server_default="false")
    diagnostic_completed_at = Column(DateTime, nullable=True)
    diagnostic_assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True
    )
    current_learning_path_id = Column(
        Integer, ForeignKey("learning_paths.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )


class DiagnosticRetakeRequest(Base):
    """A student's request to retake a diagnostic they already submitted.

    pending -> approved (by a teacher of the student, or an admin) -> used (when the
    retake attempt is started), or pending -> denied.
    """

    __tablename__ = "diagnostic_retake_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id = Column(
        Integer, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    requested_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','denied','used')",
            name="check_retake_request_status",
        ),
    )


class TutorChatSession(Base):
    """Persistent AI Tutor conversation per user (student or teacher mode)."""

    __tablename__ = "tutor_chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    mode = Column(String(16), nullable=False, default="student", server_default="student")
    summary_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        server_onupdate=func.now(),
    )

    messages = relationship(
        "TutorChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TutorChatMessage.created_at",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "mode", name="uq_tutor_chat_user_mode"),
        CheckConstraint("mode IN ('student','teacher')", name="check_tutor_chat_mode"),
    )


class TutorChatMessage(Base):
    """Individual message in an AI Tutor session."""

    __tablename__ = "tutor_chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("tutor_chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    session = relationship("TutorChatSession", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="check_tutor_chat_msg_role"),
    )


# ---------------------------------------------------------------------------
# Domain -> Subtopic taxonomy, subtopic mastery and the Target PDF question bank.
#
# Domains stay the existing ``topics`` rows (subject "Content") so every
# Domain-level score, analytic and Learning Chat payload keeps its ids.
# Subtopics hang off a Domain topic and are scoped by grade, so the same
# printed name in two grades never shares mastery.
# ---------------------------------------------------------------------------


class Subtopic(Base):
    __tablename__ = "lms_subtopics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain_topic_id = Column(
        Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grade_level = Column(String(100), nullable=False, default="", server_default="")
    subject = Column(String(100), nullable=False, default="Mathematics", server_default="Mathematics")
    canonical_name = Column(String(255), nullable=False)
    name_key = Column(String(255), nullable=False)
    # Normalised alternative names an admin confirmed map here (JSON list of keys).
    aliases_json = Column(Text, nullable=False, default="[]", server_default="[]")
    status = Column(String(32), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    domain = relationship("Topic")

    __table_args__ = (
        UniqueConstraint("domain_topic_id", "grade_level", "name_key", name="uq_subtopic_domain_grade_key"),
        CheckConstraint("status IN ('active','retired')", name="check_subtopic_status"),
    )


class QuestionTaxonomy(Base):
    """Canonical Domain/Subtopic of one question plus the labels printed on its paper."""

    __tablename__ = "lms_question_taxonomy"

    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    domain_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True)
    subtopic_id = Column(Integer, ForeignKey("lms_subtopics.id", ondelete="SET NULL"), nullable=True, index=True)
    printed_domain = Column(String(255), nullable=True)
    printed_topic = Column(String(255), nullable=True)
    grade_level = Column(String(100), nullable=True)
    # resolved: Domain + Subtopic ids; domain_only: no printed Topic (legacy or
    # AI-labelled question); unresolved: the Domain itself could not be determined.
    mapping_status = Column(String(32), nullable=False, default="unresolved", server_default="unresolved")
    mapping_note = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "mapping_status IN ('resolved','domain_only','unresolved')",
            name="check_question_taxonomy_status",
        ),
    )


class SubtopicEvidence(Base):
    """One graded answer on a subtopic: the audit trail subtopic mastery is replayed from."""

    __tablename__ = "lms_subtopic_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subtopic_id = Column(Integer, ForeignKey("lms_subtopics.id", ondelete="CASCADE"), nullable=False, index=True)
    domain_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    source_type = Column(String(32), nullable=False)
    source_id = Column(Integer, nullable=False)
    question_ref = Column(String(64), nullable=False)
    is_correct = Column(Boolean, nullable=False)
    counts_toward_mastery = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "student_id", "source_type", "source_id", "question_ref", name="uq_subtopic_evidence_answer"
        ),
        CheckConstraint(
            "source_type IN ('diagnostic','quiz','learning_chat','guided_practice')",
            name="check_subtopic_evidence_source",
        ),
        Index("ix_subtopic_evidence_student_subtopic", "student_id", "subtopic_id"),
    )


class StudentSubtopicScore(Base):
    """Current subtopic mastery, derived from SubtopicEvidence (always rebuildable)."""

    __tablename__ = "lms_student_subtopic_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subtopic_id = Column(Integer, ForeignKey("lms_subtopics.id", ondelete="CASCADE"), nullable=False, index=True)
    domain_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True)
    score_percent = Column(Float, nullable=False, default=0.0, server_default="0")
    sample_size = Column(Integer, nullable=False, default=0, server_default="0")
    correct_count = Column(Integer, nullable=False, default=0, server_default="0")
    total_count = Column(Integer, nullable=False, default=0, server_default="0")
    mastery_status = Column(String(32), nullable=False, default="needs_practice", server_default="needs_practice")
    baseline_percent = Column(Float, nullable=True)
    latest_assessment_percent = Column(Float, nullable=True)
    last_source = Column(String(32), nullable=True)
    last_assessed_at = Column(DateTime, nullable=True)
    calc_version = Column(String(32), nullable=False, default="subtopic_v1", server_default="subtopic_v1")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("student_id", "subtopic_id", name="uq_student_subtopic_score"),
    )


class TargetBankDocument(Base):
    """A Target PDF whose printed MCQs were extracted into the Guided Practice bank."""

    __tablename__ = "lms_target_bank_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sha256 = Column(String(64), nullable=False)
    original_filename = Column(String(512), nullable=True)
    grade_level = Column(String(100), nullable=False)
    subject = Column(String(100), nullable=False, default="Mathematics", server_default="Mathematics")
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True, index=True)
    storage_path = Column(String(1024), nullable=False)
    page_count = Column(Integer, nullable=True)
    extractor_version = Column(String(32), nullable=True)
    stats_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sha256", "grade_level", name="uq_bank_document_sha_grade"),
    )


class TargetBankItem(Base):
    """One printed Target PDF MCQ. Students only ever see ``approved`` items."""

    __tablename__ = "lms_target_bank_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(
        Integer, ForeignKey("lms_target_bank_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, unique=True)
    source_page = Column(Integer, nullable=True)
    source_question_number = Column(Integer, nullable=False)
    verification_status = Column(String(32), nullable=False, default="needs_review", server_default="needs_review")
    verification_report_json = Column(Text, nullable=True)
    extraction_metadata_json = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "source_question_number", name="uq_bank_item_doc_qno"),
        CheckConstraint(
            "verification_status IN ('auto_verified','needs_review','approved','rejected')",
            name="check_bank_item_status",
        ),
    )


class GuidedPracticeAttempt(Base):
    """A student's answer to a Target PDF bank question in Guided Practice."""

    __tablename__ = "lms_guided_practice_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    bank_item_id = Column(
        Integer, ForeignKey("lms_target_bank_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subtopic_id = Column(Integer, ForeignKey("lms_subtopics.id", ondelete="SET NULL"), nullable=True)
    # Display order of the options shown (JSON list of original option indexes).
    option_order_json = Column(Text, nullable=False, default="[]", server_default="[]")
    selected_original_index = Column(Integer, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String(32), nullable=False, default="served", server_default="served")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    answered_at = Column(DateTime, nullable=True)
    # Four-parameter match that chose this item: 1 = Domain+Topic+Cognitive+Difficulty,
    # 2 = +Cognitive, 3 = +Difficulty, 4 = Domain+Topic only. NULL for legacy rows.
    match_priority = Column(Integer, nullable=True)
    # The incorrectly answered question this item was matched against (if any).
    target_question_id = Column(Integer, ForeignKey("questions.id", ondelete="SET NULL"), nullable=True)
    match_json = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('served','answered','abandoned')", name="check_guided_attempt_status"),
    )


class IncorrectAnswerRecord(Base):
    """One incorrect answer with its four matching parameters (insert-only log).

    Snapshot of Domain / Subtopic / Cognitive level / Difficulty at answer time,
    so later question edits never rewrite what the student actually got wrong.
    """

    __tablename__ = "lms_incorrect_answers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    domain_topic_id = Column(Integer, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True)
    subtopic_id = Column(Integer, ForeignKey("lms_subtopics.id", ondelete="SET NULL"), nullable=True, index=True)
    cognitive_level = Column(String(64), nullable=True)
    difficulty = Column(String(20), nullable=True)  # known (printed) difficulty only
    selected_option_index = Column(Integer, nullable=True)  # NULL = left blank
    correct_option_index = Column(Integer, nullable=True)
    # diagnostic | quiz (Baseline / Follow-up attempts) | guided_practice
    source_type = Column(String(32), nullable=False)
    source_id = Column(Integer, nullable=False)  # attempt id in that source
    assessment_id = Column(Integer, ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("student_id", "source_type", "source_id", "question_id", name="uq_incorrect_answer"),
        CheckConstraint(
            "source_type IN ('diagnostic','quiz','guided_practice')", name="check_incorrect_answer_source"
        ),
        Index("ix_incorrect_answer_student_subtopic", "student_id", "subtopic_id"),
    )
