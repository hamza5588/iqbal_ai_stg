"""Pydantic models for PDF extraction and MCQ conversion."""
from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from app.services.lms.mcq_utils import (
    is_broken_math_blob,
    is_label_only,
    pick_display_fields,
    strip_option_label_prefix,
)

OptionLabel = Literal["A", "B", "C", "D"]


class ExtractedOption(BaseModel):
    label: Optional[str] = Field(None, description="A, B, C, or D when present")
    text: str = Field("", description="Full option text without the A/B/C/D prefix")
    latex: Optional[str] = Field(None, description="LaTeX for math notation if present")


class ExtractedQuestion(BaseModel):
    number: Union[int, str] = Field(..., description="Question number or label from PDF")
    text: str = Field(..., description="Question stem only — do not include A/B/C/D option lines")
    latex: Optional[str] = Field(None, description="LaTeX for math notation if present")
    options: List[ExtractedOption] = Field(
        default_factory=list,
        description="If the PDF already has MCQ choices, the 4 full option texts",
    )


class ExtractedAnswer(BaseModel):
    number: Union[int, str] = Field(..., description="Answer number or label from PDF")
    text: str = Field(..., description="Answer text or answer-key letter (A–D)")
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
    options: List[ExtractedOption] = Field(default_factory=list)
    correct_option_label: Optional[OptionLabel] = None


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
    learning_concept: Optional[str] = Field(
        None,
        description="Short student-friendly concept this question tests (3-8 words, not a document title)",
    )
    conversion_confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_mcq_structure(self) -> "MCQQuestion":
        if len(self.options) != 4:
            raise ValueError("MCQ must have exactly 4 options")
        labels = {opt.label for opt in self.options}
        if labels != {"A", "B", "C", "D"}:
            raise ValueError("Options must have labels A, B, C, and D")
        cleaned: List[MCQOption] = []
        for opt in self.options:
            text, latex = pick_display_fields(opt.text, opt.latex)
            if is_label_only(text):
                raise ValueError(
                    f"Option {opt.label} text cannot be only a letter — use the full choice text"
                )
            if not text.strip():
                raise ValueError(f"Option {opt.label} text cannot be empty")
            cleaned.append(MCQOption(label=opt.label, text=text, latex=latex))
        self.options = cleaned
        texts = [opt.text.strip().lower() for opt in self.options]
        if len(set(texts)) != 4:
            raise ValueError("All 4 options must be unique")
        if self.correct_option_label not in labels:
            raise ValueError("correct_option_label must match one of the option labels")
        q_text, q_latex = pick_display_fields(self.question_text, self.question_latex)
        self.question_text = q_text or strip_option_label_prefix(self.question_text or "")
        self.question_latex = q_latex
        if self.question_latex and is_broken_math_blob(self.question_latex):
            self.question_latex = None
        return self


class MCQBatchResult(BaseModel):
    quiz_title: Optional[str] = None
    questions: List[MCQQuestion] = Field(default_factory=list)
    failed_conversions: List[str] = Field(default_factory=list)


class PairingResult(BaseModel):
    pairs: List[QuestionAnswerPair] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
