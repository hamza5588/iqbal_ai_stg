"""Pydantic schemas for LMS API and validation."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class MCQOptionSchema(BaseModel):
    label: str
    text: str
    latex: Optional[str] = None


class QuestionCreate(BaseModel):
    topic_id: Optional[int] = None
    question_text: str
    question_latex: Optional[str] = None
    options: List[MCQOptionSchema]
    correct_option_index: int
    correct_answer_raw: Optional[str] = None
    explanation: Optional[str] = None
    difficulty: str = "medium"
    source_type: str = "manual"
    source_pdf_thread_id: Optional[str] = None
    source_question_number: Optional[int] = None
    extraction_confidence: Optional[float] = None


class QuestionRead(BaseModel):
    id: int
    topic_id: Optional[int] = None
    question_text: str
    question_latex: Optional[str] = None
    options: List[MCQOptionSchema]
    correct_option_index: int
    correct_answer_raw: Optional[str] = None
    explanation: Optional[str] = None
    difficulty: str
    source_type: str
    is_active: bool

    model_config = {"from_attributes": True}


class TopicCreate(BaseModel):
    name: str
    slug: str
    subject: str
    parent_id: Optional[int] = None
    grade_level: Optional[str] = None
    description: Optional[str] = None
    sort_order: int = 0


class TopicRead(BaseModel):
    id: int
    name: str
    slug: str
    subject: str
    parent_id: Optional[int] = None
    grade_level: Optional[str] = None
    description: Optional[str] = None
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}


class AssessmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    assessment_type: str = Field(..., pattern="^(diagnostic|quiz)$")
    creation_mode: str = "manual"
    time_limit_minutes: Optional[int] = None


class ClassCreate(BaseModel):
    name: str
    description: Optional[str] = None
    grade_level: Optional[str] = None


class AssignmentCreate(BaseModel):
    class_id: int
    quiz_id: int
    title: str
    instructions: Optional[str] = None
    due_date: Optional[str] = None


class LearningPathItemCreate(BaseModel):
    item_type: str
    item_id: int
    sort_order: int = 0

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, v: str) -> str:
        allowed = {"lesson", "quiz", "practice", "reassessment"}
        if v not in allowed:
            raise ValueError(f"item_type must be one of {allowed}")
        return v
