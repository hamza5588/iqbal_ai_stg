"""
Lesson Q&A LangGraph (Approach 3: Stateless Flag).

Workflow:
 1. Load lesson by lesson_id.
 2. Decide if the question can be answered using ONLY lesson content.
 3. If yes -> answer from lesson content.
 4. If no and lesson has rag_thread_id:
    - If allow_rag=True (user already confirmed) -> answer from uploaded PDF (RAG).
    - If allow_rag=False/None -> return needs_rag_confirmation; frontend shows Yes/No,
      user clicks Yes -> re-send same question with allow_rag=true.
 5. If no and no rag_thread_id -> "Your question is not covered in the lesson content."

No interrupt/checkpoint: stateless, single endpoint. All user-facing answers in ENGLISH.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional, TypedDict, Literal, Dict, Any

from langgraph.graph import StateGraph, START, END

from app.models.models import LessonModel
from app.services.lesson_service import LessonService
from app.utils.rag_service import groq_rate_limiter
from app.utils.groq_rate_limit import (
    parse_groq_error,
    compute_retry_delay,
    GroqRateLimitError,
    GroqBusyError,
)
from app.utils.constants import (
    is_stress_test_mode,
    STRESS_TEST_MAX_TOKENS_CLASSIFIER,
    STRESS_TEST_MAX_TOKENS_ANSWER,
)

logger = logging.getLogger(__name__)


class LessonQAState(TypedDict, total=False):
    # Inputs
    lesson_id: int
    question: str
    user_id: int
    allow_rag: Optional[bool]  # When True, skip confirmation and go to RAG

    # Loaded from DB
    lesson_content: str
    lesson_title: str
    rag_thread_id: Optional[str]
    teacher_id: Optional[int]
    lesson_service: LessonService

    # Decisions
    needs_rag: bool
    needs_confirmation: bool
    needs_deny: bool  # Not covered and no RAG

    # Output
    answer: Optional[str]
    needs_rag_confirmation: bool
    permission_request: Optional[Dict[str, Any]]


def _strip_reasoning(text: str) -> str:
    """
    Strip Groq / LLM reasoning blocks (<think>...</think>) – keep only final answer.
    Mirrors the helper in student_service to keep responses clean.
    """
    if not text or not isinstance(text, str):
        return text
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _invoke_with_groq_rate_limit(chain, payload: Dict[str, Any]) -> str:
    """
    Wrap chain.invoke with shared Groq limiter + header-aware retry (mirrors student_service).

    Retry strategy:
    - TPM_RATE_LIMITED / TRANSIENT_SERVER_ERROR: retry up to GROQ_MAX_RETRIES times.
    - RPD_EXHAUSTED / PROVIDER_ERROR: fail fast.
    - After retries exhausted for rate-limit: raise GroqRateLimitError (caught at route level).
    """
    max_retries = max(0, int(os.getenv("GROQ_MAX_RETRIES", "3")))
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        groq_rate_limiter.wait_if_needed()  # may raise GroqBusyError
        try:
            response = chain.invoke(payload)
            groq_rate_limiter.record_success()
            return response
        except Exception as exc:
            groq_rate_limiter.release_slot()
            info = parse_groq_error(exc)
            last_exc = exc

            if info.kind in ("TPM_RATE_LIMITED", "TRANSIENT_SERVER_ERROR") and info.is_retryable:
                groq_rate_limiter.record_429_error()
                if attempt < max_retries:
                    delay = compute_retry_delay(info, attempt)
                    logger.warning(
                        "lesson_qa_graph._invoke: %s (attempt %d/%d), retrying in %.1fs",
                        info.kind, attempt + 1, max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                raise GroqRateLimitError(info) from exc

            if info.kind == "RPD_EXHAUSTED":
                groq_rate_limiter.record_429_error()
                raise GroqRateLimitError(info) from exc

            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("lesson_qa_graph._invoke_with_groq_rate_limit: unexpected exit")


def load_lesson(state: LessonQAState) -> LessonQAState:
    """Load lesson content, title, rag_thread_id, teacher_id from LessonModel."""
    lesson_id = state["lesson_id"]
    lesson = LessonModel.get_lesson_by_id(lesson_id)
    if not lesson:
        # Surface clear English error; caller will usually treat as failure.
        raise ValueError(f"Lesson {lesson_id} not found")

    return {
        **state,
        "lesson_content": lesson.get("content", "") or "",
        "lesson_title": lesson.get("title", "this lesson") or "this lesson",
        "rag_thread_id": lesson.get("rag_thread_id"),
        "teacher_id": lesson.get("teacher_id"),
    }


def can_answer_from_lesson_only(
    question: str,
    lesson_content: str,
    lesson_service: LessonService,
) -> bool:
    """
    Decide if the question can be answered using ONLY the lesson content.

    Uses an LLM so that questions not actually covered (e.g. "Deep Research Module"
    or "real estate" when the lesson is about frontend-backend) get False, which
    triggers the interrupt and asks the student whether to search the uploaded PDF.

    Returns True only when the lesson explicitly and clearly contains enough
    information to answer the question. Otherwise False → needs_rag or needs_confirmation.
    """
    if not lesson_content or not question:
        return False

    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a strict classifier. Answer with exactly one word: YES or NO.
- YES only if the lesson content explicitly and clearly contains enough information to answer the student's question.
- NO if the question is about something not mentioned in the lesson, or only vaguely related, or requires information not present in the lesson. When in doubt, answer NO."""),
            ("human", """Lesson content:
{lesson_content}

Student question: {question}

Can this question be answered using ONLY the lesson content above? Reply with exactly YES or NO."""),
        ])
        # Use a small token cap for YES/NO classifier — reduces TPM pressure significantly.
        classifier_max_tokens = int(os.getenv("GROQ_CLASSIFIER_MAX_TOKENS", "10"))
        llm_for_classifier = lesson_service.student_service.llm.bind(max_tokens=classifier_max_tokens)
        chain = prompt | llm_for_classifier | StrOutputParser()
        # Truncate very long lesson content to avoid token limits; keep enough for classification
        content_snippet = (lesson_content or "")[:8000].strip() or "(No content)"
        reply = _invoke_with_groq_rate_limit(chain, {"lesson_content": content_snippet, "question": question})
        reply = _strip_reasoning(reply or "").strip().upper()
        return reply.startswith("YES")
    except Exception as e:
        logger.warning("can_answer_from_lesson_only LLM check failed: %s; defaulting to needs_rag=True", e)
        return False


def decide_route(state: LessonQAState) -> LessonQAState:
    """
    Set needs_rag / needs_confirmation based on:
    - If lesson covers the question -> answer from lesson.
    - If not covered and allow_rag=True -> go to RAG.
    - If not covered and has rag_thread_id but allow_rag not True -> needs_confirmation.
    - If not covered and no rag -> deny.
    """
    lesson_content = state.get("lesson_content") or ""
    question = state["question"]
    rag_thread_id = state.get("rag_thread_id")
    allow_rag = state.get("allow_rag")

    covered = can_answer_from_lesson_only(
        question=question,
        lesson_content=lesson_content,
        lesson_service=state["lesson_service"],
    )
    has_rag = bool(rag_thread_id)
    not_covered = not covered

    needs_rag = not_covered and has_rag and allow_rag is True
    needs_confirmation = not_covered and has_rag and allow_rag is not True
    needs_deny = not_covered and not has_rag
    return {
        **state,
        "needs_rag": needs_rag,
        "needs_confirmation": needs_confirmation,
        "needs_deny": needs_deny,
    }


def answer_from_lesson(state: LessonQAState) -> LessonQAState:
    """
    Answer using ONLY the lesson content.

    Uses existing LessonService.llm_answer to stay consistent with your stack.
    The underlying prompts SHOULD be English-only and lesson-focused.
    """
    lesson_service = state["lesson_service"]  # central LLM provider
    answer = lesson_service.llm_answer(
        lesson_content=state.get("lesson_content") or "",
        question=state["question"],
        lesson_title=state.get("lesson_title", "this lesson"),
    )
    answer = _strip_reasoning(answer)
    return {
        **state,
        "answer": (answer or "").strip(),
    }


def needs_confirmation_node(state: LessonQAState) -> LessonQAState:
    """
    Return needs_rag_confirmation so the route handler can tell the frontend
    to show Yes/No. User will re-send with allow_rag=true on Yes.
    """
    payload: Dict[str, Any] = {
        "type": "rag_permission",
        "lesson_id": state["lesson_id"],
        "question": state["question"],
        "message": (
            "This question may require searching the uploaded PDF attached to this lesson. "
            "Do you want me to search that PDF to answer your question?"
        ),
    }
    return {
        **state,
        "needs_rag_confirmation": True,
        "permission_request": payload,
    }


def _answer_from_rag_impl(
    *,
    question: str,
    rag_thread_id: str,
    teacher_id: Optional[int],
    lesson_service: LessonService,
) -> str:
    """
    Internal helper: retrieve from RAG and answer from PDF chunks.

    This mirrors the RAG branch from StudentLessonService but is isolated so we
    can control behavior:
      - If RAG yields no usable context, we fall back to the fixed message,
        not to lesson content or general knowledge.
    """
    if not rag_thread_id or not teacher_id:
        return "Your question is not covered in the lesson content."

    try:
        from app.utils.rag_service import _get_retriever
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        retriever = _get_retriever(rag_thread_id, user_id=teacher_id)
        if not retriever:
            return "Your question is not covered in the lesson content."

        docs = retriever.invoke(question)
        page_contents = [
            (getattr(d, "page_content", "") or "").strip()
            for d in (docs or [])[:8]
        ]
        page_contents = [c for c in page_contents if c]
        if not page_contents:
            return "Your question is not covered in the lesson content."

        context = "\n\n".join(page_contents)
        rag_prompt = ChatPromptTemplate.from_template(
            """You are a helpful teaching assistant.
Answer the student's question using ONLY the following excerpts from the lecture PDF.
Do not mention external knowledge or sources.

Lecture excerpts:
{context}

Student question: {question}

Answer in clear, professional English:"""
        )
        llm = lesson_service.student_service.llm  # reuse configured LLM
        if is_stress_test_mode():
            llm = llm.bind(max_tokens=STRESS_TEST_MAX_TOKENS_ANSWER)
        chain = rag_prompt | llm | StrOutputParser()
        answer = _invoke_with_groq_rate_limit(chain, {"context": context, "question": question})
        answer = _strip_reasoning(answer or "")
        answer = answer.strip()
        if not answer:
            return "Your question is not covered in the lesson content."
        return answer
    except (GroqRateLimitError, GroqBusyError):
        # Propagate rate-limit/busy errors to the route — do NOT silently fall back.
        raise
    except Exception as e:
        logger.warning("Lesson RAG answer failed (non-rate-limit): %s", e)
        return "Your question is not covered in the lesson content."


def answer_from_rag(state: LessonQAState) -> LessonQAState:
    """
    Answer the question using the uploaded PDF via RAG.

    If RAG fails or cannot provide context, we fall back to the fixed message.
    """
    answer = _answer_from_rag_impl(
        question=state["question"],
        rag_thread_id=state.get("rag_thread_id") or "",
        teacher_id=state.get("teacher_id"),
        lesson_service=state["lesson_service"],
    )
    return {
        **state,
        "answer": answer,
    }


def deny_answer(state: LessonQAState) -> LessonQAState:
    """
    User declined RAG permission or RAG is not allowed.

    MUST always set the answer to exactly the required English message.
    """
    return {
        **state,
        "answer": "Your question is not covered in the lesson content.",
    }


def _route_after_decision(state: LessonQAState) -> Literal["from_lesson", "from_rag", "needs_confirmation", "deny"]:
    """Router after decide_route: from_lesson | from_rag | needs_confirmation | deny."""
    if state.get("needs_rag"):
        return "from_rag"
    if state.get("needs_confirmation"):
        return "needs_confirmation"
    if state.get("needs_deny"):
        return "deny"
    return "from_lesson"  # Covered


def build_lesson_qa_graph():
    """Build and compile the Lesson Q&A StateGraph. No checkpointer (stateless)."""
    builder = StateGraph(LessonQAState)

    builder.add_node("load_lesson", load_lesson)
    builder.add_node("decide_route", decide_route)
    builder.add_node("answer_from_lesson", answer_from_lesson)
    builder.add_node("needs_confirmation", needs_confirmation_node)
    builder.add_node("answer_from_rag", answer_from_rag)
    builder.add_node("deny_answer", deny_answer)

    builder.add_edge(START, "load_lesson")
    builder.add_edge("load_lesson", "decide_route")

    builder.add_conditional_edges(
        "decide_route",
        _route_after_decision,
        {
            "from_lesson": "answer_from_lesson",
            "from_rag": "answer_from_rag",
            "needs_confirmation": "needs_confirmation",
            "deny": "deny_answer",
        },
    )

    builder.add_edge("answer_from_lesson", END)
    builder.add_edge("answer_from_rag", END)
    builder.add_edge("needs_confirmation", END)
    builder.add_edge("deny_answer", END)

    return builder.compile()


LESSON_QA_GRAPH = None


def init_lesson_qa_graph() -> None:
    """Initialize the global LESSON_QA_GRAPH."""
    global LESSON_QA_GRAPH
    LESSON_QA_GRAPH = build_lesson_qa_graph()
    logger.info("Lesson Q&A LangGraph initialized (Approach 3: stateless)")


def invoke_lesson_qa(
    *,
    lesson_id: int,
    question: str,
    user_id: int,
    allow_rag: bool = False,
) -> Dict[str, Any]:
    """
    Run the graph for a lesson question.

    When allow_rag=False and the lesson doesn't cover the question but has a PDF,
    returns needs_rag_confirmation. Frontend shows Yes/No; on Yes, call again with allow_rag=True.

    Returns:
      - {"status": "needs_rag_confirmation", "permission_request": <payload_dict>}
      - {"status": "completed", "answer": <str>}
    """
    if LESSON_QA_GRAPH is None:
        raise RuntimeError("LESSON_QA_GRAPH is not initialized. Call init_lesson_qa_graph() at startup.")

    lesson_service = LessonService(api_key=None)
    result = LESSON_QA_GRAPH.invoke({
        "lesson_id": lesson_id,
        "question": question,
        "user_id": user_id,
        "allow_rag": allow_rag,
        "lesson_service": lesson_service,
    })

    if result.get("needs_rag_confirmation") and result.get("permission_request"):
        return {
            "status": "needs_rag_confirmation",
            "permission_request": result["permission_request"],
        }

    return {
        "status": "completed",
        "answer": result.get("answer", "") or "",
    }

