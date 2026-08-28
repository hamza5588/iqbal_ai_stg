"""Pydantic models for PDF extraction and MCQ conversion."""
from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

OptionLabel = Literal["A", "B", "C", "D"]


class ExtractedQuestion(BaseModel):
    number: Union[int, str] = Field(..., description="Question number or label from PDF")
    text: str = Field(..., description="Question text")
    latex: Optional[str] = Field(None, description="LaTeX for math notation if present")


class ExtractedAnswer(BaseModel):
    number: Union[int, str] = Field(..., description="Answer number or label from PDF")
    text: str = Field(..., description="Answer text")
    latex: Optional[str] = Field(None, description="LaTeX for math notation if present")


class PDFExtractionResult(BaseModel):
    title: Optional[str] = None
    questions: List[ExtractedQuestion] = Field(default_factory=list)
    answers: List[ExtractedAnswer] = Field(default_factory=list)
    format_detected: str = Field(default="unknown", description="Detected PDF layout")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    warnings: List[str] = Field(default_factory=list)


class QuestionAnswerPair(BaseModel):
    question_number: Union[int, str]
    question_text: str
    question_latex: Optional[str] = None
    answer_text: str
    answer_latex: Optional[str] = None
    match_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_matched: bool = True


class MCQOption(BaseModel):
    label: OptionLabel
    text: str
    latex: Optional[str] = None


class MCQQuestion(BaseModel):
    question_text: str
    question_latex: Optional[str] = None
    options: List[MCQOption]
    correct_option_label: OptionLabel
    explanation: Optional[str] = None
    conversion_confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_mcq_structure(self) -> "MCQQuestion":
        if len(self.options) != 4:
            raise ValueError("MCQ must have exactly 4 options")
        labels = {opt.label for opt in self.options}
        if labels != {"A", "B", "C", "D"}:
            raise ValueError("Options must have labels A, B, C, and D")
        texts = [opt.text.strip().lower() for opt in self.options]
        if len(set(texts)) != 4:
            raise ValueError("All 4 options must be unique")
        if self.correct_option_label not in labels:
            raise ValueError("correct_option_label must match one of the option labels")
        return self


class MCQBatchResult(BaseModel):
    quiz_title: Optional[str] = None
    questions: List[MCQQuestion] = Field(default_factory=list)
    failed_conversions: List[str] = Field(default_factory=list)


class PairingResult(BaseModel):
    pairs: List[QuestionAnswerPair] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
