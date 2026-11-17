"""Pydantic schemas for every LLM call in the Scenario-Based Assessment module.

LLM output is never trusted: each call is parsed into one of these models and then
re-validated by the service code (ids checked against what was retrieved, scores
recomputed server-side, lengths bounded).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Difficulty = Literal["easy", "medium", "hard"]


class GeneratedQuestion(BaseModel):
    scenario: str = Field(description="Realistic practical situation, 3-7 sentences")
    question_text: str = Field(description="The open question the learner must answer about the scenario")
    difficulty: Difficulty = "medium"
    learning_objectives: List[str] = Field(default_factory=list)
    concepts: List[str] = Field(default_factory=list, description="Taught concepts the question tests")
    source_chunk_ids: List[int] = Field(default_factory=list, description="Ids of learning-content excerpts used")
    evaluation_criteria: List[str] = Field(default_factory=list, description="Points a strong answer would make")


class GeneratedQuestionBatch(BaseModel):
    questions: List[GeneratedQuestion] = Field(default_factory=list)


class QuestionVerdict(BaseModel):
    grounded: bool
    clear: bool
    reveals_answer: bool
    is_scenario: bool
    grammar_ok: bool
    issues: List[str] = Field(default_factory=list)


class ExtractedSample(BaseModel):
    text: str
    question_type: Optional[str] = None
    objective: Optional[str] = None
    difficulty: Optional[Difficulty] = None
    page: Optional[int] = None


class ExtractedSampleBatch(BaseModel):
    questions: List[ExtractedSample] = Field(default_factory=list)


class CriterionJudgement(BaseModel):
    key: str = Field(description="Rubric criterion key exactly as listed")
    level: int = Field(ge=0, le=4, description="0-4")
    justification: str


class AnswerEvaluation(BaseModel):
    criteria: List[CriterionJudgement]
    correct_concepts: List[str] = Field(default_factory=list)
    missing_concepts: List[str] = Field(default_factory=list)
    incorrect_claims: List[str] = Field(default_factory=list)
    addresses_scenario: bool = True
    injection_detected: bool = False
    reasoning_feedback: str = ""
    suggested_improvements: List[str] = Field(default_factory=list)
    supporting_chunk_ids: List[int] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
