"""Scenario-Based Assessment (SBA) tables.

A teacher-owned assessment linked to one of the teacher's classes. Three document
types feed it (learning content, sample questions, general reference answers);
every document is versioned and chunked into its own retrieval rows so the three
sources are never mixed. AI-generated questions start as drafts; students only
see approved questions of a published assessment. Each submission keeps a full
history of AI evaluations (with the rubric and document versions used) plus an
optional teacher override.

Kept separate from the MCQ quiz / guided-practice tables on purpose: SBA scoring
never touches existing first-attempt or mastery scoring.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.models.database_models import Base

DOC_TYPES = ("learning", "sample_questions", "reference_answers")


class ScenarioAssessment(Base):
    __tablename__ = "sba_assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(512), nullable=False)
    topic = Column(String(512), nullable=True)
    instructions = Column(Text, nullable=True)
    learning_objectives_json = Column(Text, nullable=True)
    question_count = Column(Integer, nullable=False, default=5, server_default="5")
    difficulty = Column(String(16), nullable=False, default="medium", server_default="medium")
    max_score_per_question = Column(Integer, nullable=False, default=10, server_default="10")
    rubric_json = Column(Text, nullable=False)
    rubric_version = Column(Integer, nullable=False, default=1, server_default="1")
    release_mode = Column(String(16), nullable=False, default="immediate", server_default="immediate")
    results_released = Column(Boolean, nullable=False, default=False, server_default="0")
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    generation_status = Column(String(16), nullable=False, default="idle", server_default="idle")
    generation_error = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    documents = relationship("ScenarioDocument", back_populates="assessment", cascade="all, delete-orphan")
    questions = relationship(
        "ScenarioQuestion",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="ScenarioQuestion.sort_order",
    )

    __table_args__ = (
        CheckConstraint("status IN ('draft','published','archived')", name="check_sba_status"),
        CheckConstraint("difficulty IN ('easy','medium','hard')", name="check_sba_difficulty"),
        CheckConstraint("release_mode IN ('immediate','manual')", name="check_sba_release_mode"),
        CheckConstraint(
            "generation_status IN ('idle','running','failed')", name="check_sba_generation_status"
        ),
    )


class ScenarioDocument(Base):
    __tablename__ = "sba_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("sba_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type = Column(String(32), nullable=False)
    filename = Column(String(512), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    page_count = Column(Integer, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    # Lineage: every replacement points at the document it replaced.
    replaces_id = Column(Integer, ForeignKey("sba_documents.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    error_message = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    chunk_count = Column(Integer, nullable=False, default=0, server_default="0")
    embedded = Column(Boolean, nullable=False, default=False, server_default="0")
    uploaded_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    processed_at = Column(DateTime, nullable=True)

    assessment = relationship("ScenarioAssessment", back_populates="documents")

    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('learning','sample_questions','reference_answers')", name="check_sba_doc_type"
        ),
        CheckConstraint(
            "status IN ('pending','processing','ready','failed')", name="check_sba_doc_status"
        ),
        Index("idx_sba_documents_assessment_type", "assessment_id", "doc_type"),
    )


class ScenarioChunk(Base):
    __tablename__ = "sba_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("sba_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id = Column(Integer, ForeignKey("sba_assessments.id", ondelete="CASCADE"), nullable=False)
    doc_type = Column(String(32), nullable=False)
    doc_version = Column(Integer, nullable=False, default=1)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    heading = Column(String(512), nullable=True)
    text = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=True)

    __table_args__ = (Index("idx_sba_chunks_scope", "assessment_id", "doc_type"),)


class ScenarioSampleQuestion(Base):
    __tablename__ = "sba_sample_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("sba_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id = Column(Integer, ForeignKey("sba_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    page = Column(Integer, nullable=True)
    question_type = Column(String(64), nullable=True)
    objective = Column(Text, nullable=True)
    difficulty = Column(String(16), nullable=True)
    dedupe_key = Column(String(64), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "dedupe_key", name="uq_sba_sample_doc_key"),
    )


class ScenarioQuestion(Base):
    __tablename__ = "sba_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("sba_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    scenario = Column(Text, nullable=False)
    question_text = Column(Text, nullable=False)
    difficulty = Column(String(16), nullable=False, default="medium")
    learning_objectives_json = Column(Text, nullable=True)
    concepts_json = Column(Text, nullable=True)
    source_refs_json = Column(Text, nullable=True)
    # Teacher-only grading guidance: never sent to students.
    evaluation_criteria_json = Column(Text, nullable=True)
    generation_meta_json = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    edited_by_teacher = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    assessment = relationship("ScenarioAssessment", back_populates="questions")

    __table_args__ = (
        CheckConstraint("status IN ('draft','approved','rejected')", name="check_sba_question_status"),
    )


class ScenarioSubmission(Base):
    __tablename__ = "sba_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("sba_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("sba_questions.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    answer_text = Column(Text, nullable=False, default="")
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    evaluation_error = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, server_default=func.now())

    __table_args__ = (
        # One answer per student per question: re-saving a draft updates it; a
        # second submit is refused.
        UniqueConstraint("question_id", "student_id", name="uq_sba_submission_student_question"),
        CheckConstraint(
            "status IN ('draft','submitted','evaluating','evaluated','failed')",
            name="check_sba_submission_status",
        ),
    )


class ScenarioEvaluation(Base):
    __tablename__ = "sba_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(Integer, ForeignKey("sba_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    is_current = Column(Boolean, nullable=False, default=True, server_default="1")
    total_score = Column(Float, nullable=False, default=0.0)
    max_score = Column(Float, nullable=False, default=10.0)
    criteria_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    review_required = Column(Boolean, nullable=False, default=False, server_default="0")
    review_reasons_json = Column(Text, nullable=True)
    rubric_snapshot_json = Column(Text, nullable=False)
    doc_versions_json = Column(Text, nullable=True)
    prompt_version = Column(String(32), nullable=True)
    model_name = Column(String(128), nullable=True)
    teacher_score = Column(Float, nullable=True)
    teacher_feedback = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
