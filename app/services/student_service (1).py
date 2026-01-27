"""
Student-focused lesson service for learning and Q&A
"""
import logging
from typing import Dict, List, Any, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from .base_service import BaseLessonService

logger = logging.getLogger(__name__)


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
        """Answer a student's question about a specific lesson"""
        try:
            # Get lesson content from database
            from app.models.models import LessonModel
            lesson = LessonModel.get_lesson_by_id(lesson_id)
            
            if not lesson:
                return {"error": "Lesson not found"}
            
            lesson_content = lesson.get('content', '')
            lesson_title = lesson.get('title', 'this lesson')
            
            # Handle conversation history safely
            history = (conversation_history or [])[-2:]
            formatted_history = "\n".join([f"Student Question: {h.get('question', '')}\nAI Answer: {h.get('answer', '')}" for h in history if h])
            

            
            # Print history to terminal for debugging
            print("\n" + "="*80)
            print("CONVERSATION HISTORY BEING SENT TO PROMPT:")
            print("="*80)
            print(formatted_history if formatted_history else "No previous conversation.")
            print("="*80)
            print(f"Current Question: {question}")
            print("="*80 + "\n")
            logger.info(f"Conversation history: {formatted_history if formatted_history else 'No previous conversation.'}")

            
            # Create prompt for answering questions
            prompt = ChatPromptTemplate.from_template(f"""
        # You are a helpful teaching assistant. Answer the student's question based on the lesson content. If the Question is Not In the Content. Just Say Question Not related to The Content 
        # If the Student Says Yes Answer From your Information, Then Answer the Above Question.
        RULES:
 
        # 1. If the Student says "Yes" or something like Yes to your Response then Answer using your general Knowledge (IMPORTANT)
        #   - Answer the PREVIOUS question (Last Question) asked by the Student using your own knowledge. (IMPORTANT)
        
        2. If question is related to lesson content:
           - Provide clear, educational answer using lesson content. Use simple language and examples.

        3. If question is NOT related to lesson content:
           - Response must be EXACTLY two sentences: "{{question}} is not related to the lesson. Would you like me to answer it using my own knowledge?"
           - Do NOT provide any additional information.

           *if the question in the last conversation was not related to the lesson, and then student say's yes in this query. answer the last question using your own knowledge.*

        4. If no relevant info found:
           - "No relevant information about this topic is found in the lesson content. Would you like me to answer it using my own knowledge?"
           -"If Question is "Yes". Than Answer using your own KnowledgeBase

        ## IMPORTANT: If Student Says "Yes Please" or "Yes" Answer the Previous Question Then (CRITICAL)

        # *Current Question: {question}*
        Lesson Title: {lesson_title}
        Lesson Content: {lesson_content}

        # Previous History: (Top Question: First Question, Bottom Question: Last Question Asked)
        Previous History: {formatted_history if formatted_history else "None"} 
        #IMPORTANT: History is Only For Context and Guidence

        """)
            
            chain = prompt | self.llm | StrOutputParser()
            
            answer = chain.invoke({
                "lesson_title": lesson_title,
                "lesson_content": lesson_content,
                "question": question
            })
            
            return {
                "answer": answer,
                "lesson_id": lesson_id,
                "question": question
            }
            
        except Exception as e:
            logger.error(f"Error answering lesson question: {str(e)}")
            return {"error": f"Failed to answer question: {str(e)}"}

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
            
            return canonical_question.strip()
            
        except Exception as e:
            logger.error(f"Error canonicalizing question: {str(e)}")
            return question
