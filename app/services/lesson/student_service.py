"""
Student-focused lesson service for learning and Q&A
"""
import logging
import re
from typing import Dict, List, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from .base_service import BaseLessonService

logger = logging.getLogger(__name__)


def _strip_reasoning(text: str) -> str:
    """Strip Groq reasoning/thinking blocks (<think>...</think>) - keep only the final answer."""
    if not text or not isinstance(text, str):
        return text
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


class StudentLessonService(BaseLessonService):
    """
    Student-focused lesson service for:
    - Answering questions about lessons
    - Getting lesson summaries
    - Extracting key points
    - FAQ generation
    """
    
    def __init__(self, groq_api_key: str):
        super().__init__(groq_api_key)
        logger.info("Student lesson service initialized")

    def answer_lesson_question(self, lesson_id: int, question: str, conversation_history: list = None) -> Dict[str, str]:
        """Answer a student's question about a specific lesson, letting the AI infer affirmatives."""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            if not lesson:
                return {"error": "Lesson not found"}

            lesson_content = lesson.get('content', '')
            lesson_title = lesson.get('title', 'this lesson')

            # Limit history for clarity but include enough for context
            history = (conversation_history or [])[-3:]
            formatted_history = "\n".join(
                f"Student Question: {h.get('question', '')}\nAI Answer: {h.get('answer', '')}"
                for h in history if h
            ) or "No previous conversation."

            # Debug prints
            print("\n" + "="*80)
            print("CONVERSATION HISTORY SENT TO PROMPT:")
            print("="*80)
            print(formatted_history)
            print("="*80)
            print(f"Current Question: {question}")
            print("="*80 + "\n")

            # ---- AI prompt (NO hard-coded logic, LLM interprets affirmatives) ----
            prompt = ChatPromptTemplate.from_template("""
        You are a helpful teaching assistant.

        Your job is to answer the student's question based on the *lesson content* when possible.

        Follow these rules:

        1. If the student's current message indicates they want you to answer the previous question (e.g., "yes", "yes please", "sure"), then simply answer the **previous question** directly using your general knowledge.
        - Do NOT explain your reasoning.
        - Do NOT restate the previous conversation.

        2. If the student's question is related to the lesson:
        - Provide a clear, educational answer using the lesson content.

        3. If the student's question is NOT related to the lesson:
        - Respond exactly with two sentences:
            "<question> is not related to the lesson. Would you like me to answer it using my own knowledge?"
        - Do NOT provide any additional explanation.

        4. If no relevant lesson info exists:
        - Say: "No relevant information about this topic is found in the lesson content. Would you like me to answer it using my own knowledge?"
        - If the student's next message implies consent, answer using your general knowledge — directly, without explaining your decision.

        Lesson Title: {lesson_title}
        Lesson Content: {lesson_content}

        Current Student Question: {question}

        Conversation History:
        {formatted_history}
        """)


            chain = prompt | self.llm | StrOutputParser()

            answer = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content,
                "question": question,
                "formatted_history": formatted_history,
            })

            answer = _strip_reasoning(answer)
            return {
                "answer": answer.strip(),
                "lesson_id": lesson_id,
                "question": question
            }

        except Exception as e:
            logger.exception("Error answering lesson question")
            return {"error": str(e)}


    def get_lesson_faqs(self, lesson_id: int, limit: int = 5) -> List[Dict[str, str]]:
        """Get frequently asked questions for a lesson"""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return []
            
            lesson_content = lesson.get('content', '')
            lesson_title = lesson.get('title', '')
            
            # Create prompt for FAQ generation
            prompt = ChatPromptTemplate.from_template("""
            Based on the following lesson content, generate {limit} frequently asked questions that students might have.
            
            Lesson Title: {lesson_title}
            Lesson Content: {lesson_content}
            
            For each question, provide:
            1. The question
            2. A clear, educational answer
            
            Format as JSON with this structure:
            [
                {{"question": "Question text", "answer": "Answer text"}},
                ...
            ]
            """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            faq_response = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content,
                "limit": limit
            })
            faq_response = _strip_reasoning(faq_response)
            
            # Parse JSON response
            import json
            try:
                faqs = json.loads(faq_response)
                return faqs[:limit]  # Ensure we don't exceed the limit
            except json.JSONDecodeError:
                # Fallback if JSON parsing fails
                return [{"question": "What is this lesson about?", "answer": "This lesson covers important concepts and topics."}]
            
        except Exception as e:
            logger.error(f"Error generating lesson FAQs: {str(e)}")
            return []

    def get_lesson_summary(self, lesson_id: int) -> Dict[str, str]:
        """Get a summary of the lesson for students"""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return {"error": "Lesson not found"}
            
            lesson_content = lesson.get('content', '')
            lesson_title = lesson.get('title', '')
            
            # Create prompt for lesson summary
            prompt = ChatPromptTemplate.from_template("""
            Create a student-friendly summary of this lesson.
            
            Lesson Title: {lesson_title}
            Lesson Content: {lesson_content}
            
            Provide:
            1. A brief overview of what students will learn
            2. Key concepts they should understand
            3. Why this lesson is important
            
            Keep it concise and engaging for students.
            """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            summary = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content
            })
            summary = _strip_reasoning(summary)
            
            return {
                "summary": summary,
                "lesson_id": lesson_id,
                "title": lesson_title
            }
            
        except Exception as e:
            logger.error(f"Error generating lesson summary: {str(e)}")
            return {"error": f"Failed to generate summary: {str(e)}"}

    def get_lesson_key_points(self, lesson_id: int) -> List[str]:
        """Extract key learning points from a lesson"""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return []
            
            lesson_content = lesson.get('content', '')
            lesson_title = lesson.get('title', '')
            
            # Create prompt for key points extraction
            prompt = ChatPromptTemplate.from_template("""
            Extract the key learning points from this lesson.
            
            Lesson Title: {lesson_title}
            Lesson Content: {lesson_content}
            
            Provide a list of 5-10 key points that students should remember.
            Each point should be clear and concise.
            
            Format as a simple list, one point per line.
            """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            key_points_response = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content
            })
            key_points_response = _strip_reasoning(key_points_response)
            
            # Split response into list
            key_points = [point.strip() for point in key_points_response.split('\n') if point.strip()]
            return key_points[:10]  # Limit to 10 points
            
        except Exception as e:
            logger.error(f"Error extracting lesson key points: {str(e)}")
            return []

    def llm_answer(self, lesson_content: str, question: str, lesson_title: str = "this lesson") -> str:
        """Generate an answer using the LLM with lesson-specific context"""
        try:
            prompt = ChatPromptTemplate.from_template("""
            You are a helpful teaching assistant. Answer the student's question based on the lesson content.
            
            Lesson Title: {lesson_title}
            Lesson Content: {lesson_content}
            
            Student Question: {question}
            
            Provide a clear, educational answer that helps the student understand.
            Use information from the lesson content to support your answer.
            """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            answer = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content,
                "question": question
            })
            answer = _strip_reasoning(answer)
            return answer
            
        except Exception as e:
            logger.error(f"Error generating LLM answer: {str(e)}")
            return f"I apologize, but I encountered an error while processing your question: {str(e)}"

    def canonicalize_question(self, lesson_id: int, question: str) -> str:
        """Return a canonical phrasing for the question using semantic similarity"""
        try:
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return question
            
            lesson_content = lesson.get('content', '')
            
            # Create prompt for question canonicalization
            prompt = ChatPromptTemplate.from_template("""
            Based on the lesson content, rephrase the student's question to be more specific and clear.
            
            Lesson Content: {lesson_content}
            Student Question: {question}
            
            Return a clearer, more specific version of the question that would be easier to answer based on the lesson content.
            If the question is already clear, return it as is.
            """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            canonical_question = chain.invoke({
                "lesson_content": lesson_content,
                "question": question
            })
            canonical_question = _strip_reasoning(canonical_question)
            return canonical_question.strip()
            
        except Exception as e:
            logger.error(f"Error canonicalizing question: {str(e)}")
            return question
