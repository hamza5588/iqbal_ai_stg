"""
Pydantic models for structured lesson data
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class CreativeActivity(BaseModel):
    """Model for creative activities in lessons"""
    name: str = Field(..., description="Name of the activity")
    description: str = Field(..., description="Description of the activity")
    duration: Optional[str] = Field(None, description="Duration of the activity")
    learning_purpose: Optional[str] = Field(None, description="Learning purpose of the activity")


class STEMEquation(BaseModel):
    """Model for STEM equations in lessons"""
    equation: str = Field(..., description="The mathematical equation")
    term_explanations: Optional[List[str]] = Field(None, description="Explanations of terms in the equation")
    mathematical_operations: Optional[str] = Field(None, description="Mathematical operations involved")
    complete_equation_significance: Optional[str] = Field(None, description="Significance of the complete equation")


class QuizQuestion(BaseModel):
    """Model for quiz questions in lessons"""
    question: str = Field(..., description="The question text")
    options: List[str] = Field(..., description="Answer options")
    answer: str = Field(..., description="Correct answer")
    explanation: Optional[str] = Field(None, description="Explanation of the answer")


class LessonSection(BaseModel):
    """Model for lesson sections"""
    heading: str = Field(..., description="Section heading")
    content: str = Field(..., description="Section content")


class LessonPlan(BaseModel):
    """Model for structured lesson plan"""
    title: str = Field(..., description="Lesson title")
    summary: str = Field(..., description="Lesson summary")
    learning_objectives: List[str] = Field(..., description="Learning objectives")
    key_concepts: List[str] = Field(..., description="Key concepts")
    background_prerequisites: List[str] = Field(..., description="Background prerequisites")
    sections: List[LessonSection] = Field(..., description="Lesson sections")
    creative_activities: List[CreativeActivity] = Field(..., description="Creative activities")
    stem_equations: List[STEMEquation] = Field(..., description="STEM equations")
    assessment_quiz: List[QuizQuestion] = Field(..., description="Assessment quiz")
    teacher_notes: List[str] = Field(..., description="Teacher notes")


class LessonResponse(BaseModel):
    """Model for lesson response from AI"""
    response_type: str = Field(..., description="Type of response (lesson_plan or direct_answer)")
    answer: Optional[LessonPlan] = Field(None, description="Structured lesson plan")
    user_question: Optional[str] = Field(None, description="User's question or prompt")


