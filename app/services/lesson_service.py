"""
Main lesson service - now uses structured approach with separate teacher and student services
"""
import logging
from typing import Any, Dict, Optional, List
from werkzeug.datastructures import FileStorage

from .lesson.teacher_service import TeacherLessonService
from .lesson.student_service import StudentLessonService

# Set up logging
logger = logging.getLogger(__name__)


class LessonService:
    """
    Main lesson service that delegates to specialized teacher and student services.
    This maintains backward compatibility while providing a structured approach.
    """
    def __init__(self, api_key: str):
        """Initialize the LessonService with API key from database."""
        self.api_key = api_key
        self.teacher_service = TeacherLessonService(api_key)
        self.student_service = StudentLessonService(api_key)

    # Delegate methods to appropriate services
    
    def allowed_file(self, filename: str) -> bool:
        """Check if file extension is supported"""
        return self.teacher_service.allowed_file(filename)

    def process_file(self, file: FileStorage, lesson_details: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Process an uploaded file and return structured lesson content with DOCX bytes."""
        return self.teacher_service.process_file(file, lesson_details)

    def create_ppt(self, lesson_data: dict) -> bytes:
        """Generate a basic PPTX file from the lesson structure using python-pptx."""
        return self.teacher_service.create_ppt(lesson_data)

    def edit_lesson_with_prompt(self, lesson_text: str, user_prompt: str, filename: str = "") -> str:
        """Use a FAISS vector database for semantic chunk retrieval and editing."""
        return self.teacher_service.edit_lesson_with_prompt(lesson_text, user_prompt, filename)

    def improve_lesson_content(self, lesson_id: int, current_content: str, improvement_prompt: str = "") -> str:
        """Improve lesson content using AI based on user prompt"""
        return self.teacher_service.improve_lesson_content(lesson_id, current_content, improvement_prompt)

    def interactive_chat(
        self, 
        lesson_id: int, 
        user_query: str,
        session_id: str = None,
        subject: str = None,
        grade_level: str = None,
        focus_area: str = None,
        document_uploaded: bool = False,
        document_filename: str = None
    ):
        """Interactive chat with the lesson"""
        return self.teacher_service.interactive_chat(
            lesson_id=lesson_id,
            user_query=user_query,
            session_id=session_id,
            subject=subject,
            grade_level=grade_level,
            focus_area=focus_area,
            document_uploaded=document_uploaded,
            document_filename=document_filename
        )
    
    def interactive_chat_stream(
        self, 
        lesson_id: int, 
        user_query: str,
        session_id: str = None,
        subject: str = None,
        grade_level: str = None,
        focus_area: str = None,
        document_uploaded: bool = False,
        document_filename: str = None
    ):
        """Interactive chat with streaming response"""
        return self.teacher_service.interactive_chat_stream(
            lesson_id=lesson_id,
            user_query=user_query,
            session_id=session_id,
            subject=subject,
            grade_level=grade_level,
            focus_area=focus_area,
            document_uploaded=document_uploaded,
            document_filename=document_filename
        )

    def review_lesson_with_rag(self, lesson_content: str, user_prompt: str, filename: str = "") -> str:
        """Review lesson content using RAG to retrieve relevant information from vector database"""
        return self.teacher_service.review_lesson_with_rag(lesson_content, user_prompt, filename)

    # Student-focused methods
    def answer_lesson_question(self, lesson_id: int, question: str, conversation_history: list = None) -> Dict[str, str]:
        """Answer a student's question about a specific lesson"""
        return self.student_service.answer_lesson_question(lesson_id, question, conversation_history)

    def get_lesson_faqs(self, lesson_id: int, limit: int = 5) -> list:
        """Get frequently asked questions for a lesson"""
        return self.student_service.get_lesson_faqs(lesson_id, limit)

    def get_lesson_summary(self, lesson_id: int) -> Dict[str, str]:
        """Get a summary of the lesson for students"""
        return self.student_service.get_lesson_summary(lesson_id)

    def get_lesson_key_points(self, lesson_id: int) -> list:
        """Extract key learning points from a lesson"""
        return self.student_service.get_lesson_key_points(lesson_id)

    # Legacy methods for backward compatibility
    def llm_answer(self, lesson_content: str, question: str, lesson_title: str = "this lesson") -> str:
        """Generate an answer using the LLM with lesson-specific context"""
        return self.student_service.llm_answer(lesson_content, question, lesson_title)

    def canonicalize_question(self, lesson_id: int, question: str) -> str:
        """Return a canonical phrasing for the question using semantic similarity"""
        return self.student_service.canonicalize_question(lesson_id, question)

    # Additional methods for backward compatibility
    
    def analyze_user_query(self, query: str) -> Dict[str, Any]:
        """Analyze user query to determine intent and type"""
        try:
            # Simple keyword-based analysis
            query_lower = query.lower()
            
            # Check for question indicators
            question_keywords = ['what', 'how', 'why', 'when', 'where', 'which', 'who', 'explain', 'tell me', 'describe']
            is_question = any(keyword in query_lower for keyword in question_keywords)
            
            # Check for lesson generation indicators
            lesson_keywords = ['generate', 'create', 'make', 'lesson', 'plan', 'teach', 'curriculum']
            wants_lesson = any(keyword in query_lower for keyword in lesson_keywords)
            
            if is_question and not wants_lesson:
                return {
                    'query_type': 'QUESTION',
                    'intent': 'answer_question',
                    'confidence': 0.8
                }
            elif wants_lesson:
                return {
                    'query_type': 'LESSON_GENERATION',
                    'intent': 'generate_lesson',
                    'confidence': 0.9
                }
            else:
                return {
                    'query_type': 'GENERAL',
                    'intent': 'general_inquiry',
                    'confidence': 0.5
                }
                
        except Exception as e:
            logger.error(f"Error analyzing user query: {str(e)}")
            return {
                'query_type': 'GENERAL',
                'intent': 'general_inquiry',
                'confidence': 0.3
            }

    def answer_general_question(self, question: str, available_lessons: List[int], conversation_history: List[Dict] = None) -> Dict[str, str]:
        """Answer general questions using available lesson content"""
        try:
            if not available_lessons:
                return {
                    "answer": "I don't have any lesson content available to answer your question. Please upload a lesson first or ask your teacher to make some lessons available.",
                    "lesson_id": None,
                    "question": question,
                    "source": "no_lessons",
                    "confidence": 0.0,
                    "lesson_context": "",
                    "relevant_lessons": []
                }
            
            # Try to answer using the first available lesson
            lesson_id = available_lessons[0]
            result = self.answer_lesson_question(lesson_id, question)
            
            # Add additional context about available lessons
            result["source"] = "lesson_content"
            result["confidence"] = result.get("confidence", 0.8)
            result["lesson_context"] = f"Based on lesson {lesson_id}"
            result["relevant_lessons"] = available_lessons[:3]  # Show first 3 relevant lessons
            
            return result
                
        except Exception as e:
            logger.error(f"Error answering general question: {str(e)}")
            return {
                "answer": f"I apologize, but I encountered an error while processing your question: {str(e)}",
                "lesson_id": None,
                "question": question,
                "source": "error",
                "confidence": 0.0,
                "lesson_context": "",
                "relevant_lessons": []
            }

    def _get_available_lesson_ids(self) -> List[int]:
        """Get list of available lesson IDs for the current user"""
        try:
            from app.models.models import LessonModel, UserModel
            from flask import session
            
            # Get current user ID from session
            user_id = session.get('user_id')
            if not user_id:
                logger.warning("No user_id in session, returning empty lesson list")
                return []
            
            lesson_ids = []
            
            # Get user role to determine what lessons they can access
            user = UserModel.get_user_by_id(user_id)
            if not user:
                logger.warning(f"User {user_id} not found")
                return []
            
            user_role = user.get('role', 'student')
            
            if user_role == 'teacher':
                # Teachers can access their own lessons
                teacher_lessons = LessonModel.get_lessons_by_teacher(user_id)
                lesson_ids.extend([lesson.get('id') for lesson in teacher_lessons if lesson.get('id')])
                logger.info(f"Found {len(teacher_lessons)} teacher lessons for user {user_id}")
            
            # All users (teachers and students) can access public lessons
            public_lessons = LessonModel.get_public_lessons()
            public_lesson_ids = [lesson.get('id') for lesson in public_lessons if lesson.get('id')]
            lesson_ids.extend(public_lesson_ids)
            logger.info(f"Found {len(public_lessons)} public lessons")
            
            # Remove duplicates while preserving order
            lesson_ids = list(dict.fromkeys(lesson_ids))
            
            logger.info(f"Total available lessons for user {user_id} ({user_role}): {len(lesson_ids)} lessons")
            return lesson_ids
            
        except Exception as e:
            logger.error(f"Error getting available lesson IDs: {str(e)}")
            return []

    def _create_docx(self, lesson_data: dict) -> bytes:
        """Create DOCX from lesson data"""
        return self.teacher_service._create_docx(lesson_data)

    def _delete_faiss_index(self, lesson_id: int):
        """Delete FAISS index for a lesson (placeholder)"""
        # This would need to be implemented if you're using persistent FAISS indices
        logger.info(f"FAISS index deletion requested for lesson {lesson_id}")

    def review_by_ai(self, lesson_id: int, user_query: str) -> str:
        """AI review of lesson content"""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return "Lesson not found"
            
            lesson_content = lesson.get('content', '')
            return self.review_lesson_with_rag(lesson_content, user_query)
            
        except Exception as e:
            logger.error(f"Error in AI review: {str(e)}")
            return f"Error during AI review: {str(e)}"
