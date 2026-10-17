"""
Lesson Q&A LangGraph (Approach 3: Stateless Flag).

Workflow:
 1. Load lesson by lesson_id.
 2. Decide if the question can be answered using ONLY lesson content.
 3. If yes -> answer from lesson content (labeled "From lesson").
 4. If no and lesson has rag_thread_id:
    - If allow_rag=True (user already confirmed) -> answer from uploaded PDF (RAG),
      labeled "From document". If the PDF is thin / empty -> auto-answer from
      general knowledge labeled "From general knowledge" (no consent).
    - If allow_rag=False/None -> return needs_rag_confirmation; frontend shows Yes/No,
      user clicks Yes -> re-send same question with allow_rag=true.
 5. If no and no rag_thread_id -> auto-answer from general knowledge with source label.
 6. Explicit "answer from your knowledge" questions skip confirmation and use GK.

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
    lesson_summary: str
    lesson_focus_area: str
    lesson_objectives: str
    rag_thread_id: Optional[str]
    teacher_id: Optional[int]
    lesson_service: LessonService

    # Decisions
    needs_rag: bool
    needs_confirmation: bool
    needs_deny: bool  # Legacy flag; deny now routes to general knowledge
    needs_gk: bool  # Answer from general knowledge with source label

    # Output
    answer: Optional[str]
    answer_source: Optional[str]  # lesson | document | general_knowledge
    needs_rag_confirmation: bool
    permission_request: Optional[Dict[str, Any]]


SOURCE_LABEL_LESSON = "**Source: From lesson**"
SOURCE_LABEL_DOCUMENT = "**Source: From document**"
SOURCE_LABEL_GK = "**Source: From general knowledge**"
_NOT_IN_LESSON_TOKEN = "__NOT_IN_LESSON__"
_NOT_COVERED_MSG = "Your question is not covered in the lesson content."

# Questions about the lesson as a whole should answer from lesson content first,
# not jump to PDF search / "not covered".
_LESSON_OVERVIEW_INTENT_RE = re.compile(
    r"\b("
    r"main\s+focus|focus\s+of|what\s+is\s+this\s+about|what'?s\s+this\s+about|"
    r"what\s+is\s+the\s+lecture|what\s+is\s+lecture|lecture\s+content|"
    r"lesson\s+content|lesson\s+about|summarize|summary|overview|"
    r"in\s+one\s+line|one\s+line|key\s+points?|main\s+topic|main\s+idea|"
    r"what\s+does\s+this\s+(lesson|lecture|doc|document)\s+(cover|say|teach)|"
    r"what\s+is\s+covered|what\s+do\s+we\s+learn|purpose\s+of\s+(this\s+)?(lesson|lecture)|"
    r"wht\s+is\s+the\s+main|whats\s+the\s+main"
    r")\b",
    re.IGNORECASE,
)


def _is_lesson_overview_question(question: str) -> bool:
    """True for meta/summary questions that the lesson text itself can answer."""
    q = (question or "").strip()
    if not q:
        return False
    return bool(_LESSON_OVERVIEW_INTENT_RE.search(q))


_KB_INTENT_RE = re.compile(
    r"\b("
    r"from\s+(your|the)\s+(own\s+)?(knowledge(\s+base)?|general\s+knowledge)|"
    r"use\s+your\s+(own\s+)?knowledge|"
    r"answer\s+from\s+(your\s+)?(own\s+)?(general\s+)?knowledge|"
    r"outside\s+(the\s+)?(lesson|document|pdf)|"
    r"not\s+(in|from)\s+(the\s+)?(lesson|document|pdf)"
    r")\b",
    re.IGNORECASE,
)


def _is_kb_intent_question(question: str) -> bool:
    """True when the student explicitly asks for general / outside knowledge."""
    q = (question or "").strip()
    if not q:
        return False
    return bool(_KB_INTENT_RE.search(q))


def _with_source_label(label: str, answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return f"{label}\n\n"
    if text.startswith("**Source:"):
        return text
    return f"{label}\n\n{text}"


def _strip_trailing_llm_chatter(text: str) -> str:
    """Remove common trailing meta / finalize chatter from model output."""
    if not text or not isinstance(text, str):
        return text
    cleaned = text
    patterns = [
        r"(?is)\n*\s*Lesson\s+finalized(?:\s+and\s+saved)?\.?\s*$",
        r"(?is)\n*\s*I've\s+finalized\s+(?:the\s+)?lesson\.?\s*$",
        r"(?is)\n*\s*Would you like me to answer from my own knowledge base\??\s*$",
        r"(?is)\n*\s*The answer is not present in the Lesson\.?\s*"
        r"Would you like me to answer from my own knowledge base\??\s*$",
        r"(?is)\n*\s*Let me know if you (?:need|want) anything else\.?\s*$",
        r"(?is)\n*\s*Is there anything else (?:I can help|you'd like).{0,80}$",
    ]
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _build_lesson_classifier_context(
    *,
    lesson_title: str,
    lesson_content: str,
    lesson_summary: str = "",
    lesson_focus_area: str = "",
    lesson_objectives: str = "",
    max_content_chars: int = 12000,
) -> str:
    """Build richer context so overview questions are not falsely marked uncovered."""
    parts: list[str] = []
    title = (lesson_title or "").strip()
    if title:
        parts.append(f"Lesson title: {title}")
    focus = (lesson_focus_area or "").strip()
    if focus:
        parts.append(f"Focus area: {focus}")
    summary = (lesson_summary or "").strip()
    if summary:
        parts.append(f"Lesson summary:\n{summary}")
    objectives = (lesson_objectives or "").strip()
    if objectives:
        parts.append(f"Learning objectives:\n{objectives}")
    content = (lesson_content or "").strip()
    if content:
        parts.append(f"Lesson content:\n{content[:max_content_chars]}")
    return "\n\n".join(parts).strip() or "(No content)"


def _strip_reasoning(text: str) -> str:
    """
    Strip Groq / LLM reasoning blocks (<think>...</think>) – keep only final answer.
    Mirrors the helper in student_service to keep responses clean.
    """
    if not text or not isinstance(text, str):
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return _strip_trailing_llm_chatter(cleaned)


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
        "lesson_summary": lesson.get("summary", "") or "",
        "lesson_focus_area": lesson.get("focus_area", "") or "",
        "lesson_objectives": lesson.get("learning_objectives", "") or "",
        "rag_thread_id": lesson.get("rag_thread_id"),
        "teacher_id": lesson.get("teacher_id"),
    }


def can_answer_from_lesson_only(
    question: str,
    lesson_content: str,
    lesson_service: LessonService,
    *,
    lesson_title: str = "this lesson",
    lesson_summary: str = "",
    lesson_focus_area: str = "",
    lesson_objectives: str = "",
) -> bool:
    """
    Decide if the question can be answered using the lesson material.

    Prefers answering from lesson content (including overview/summary questions).
    Returns False only when the question is clearly outside the lesson and may
    need the teacher PDF / deny path.
    """
    if not question:
        return False

    has_lesson_material = bool(
        (lesson_content or "").strip()
        or (lesson_summary or "").strip()
        or (lesson_title or "").strip()
    )
    if not has_lesson_material:
        return False

    # Fast-path: overview / "what is this about" style questions → lesson first.
    if _is_lesson_overview_question(question) and (
        (lesson_content or "").strip() or (lesson_summary or "").strip()
    ):
        logger.info(
            "can_answer_from_lesson_only: overview intent fast-path YES for question=%r",
            (question or "")[:120],
        )
        return True

    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a coverage classifier for a student lesson Q&A system.
Reply with exactly one word: YES or NO.

Answer YES when:
- The lesson material contains enough information to answer the question, OR
- The question asks for an overview/summary/main focus/key points/"what is this about"
  and the lesson has substantive content on that topic, OR
- The answer can reasonably be synthesized from the lesson title, summary, focus area,
  objectives, or content.

Answer NO only when:
- The question is clearly about a different subject not present in the lesson, OR
- Answering would require information that is not in the lesson at all.

When the question is about this lesson and the lesson has relevant material, prefer YES.
Do not require verbatim phrasing matches. Reply with exactly YES or NO."""),
            ("human", """Lesson material:
{lesson_material}

Student question: {question}

Can this question be answered from the lesson material above? Reply with exactly YES or NO."""),
        ])
        # Use a small token cap for YES/NO classifier — reduces TPM pressure significantly.
        classifier_max_tokens = int(os.getenv("GROQ_CLASSIFIER_MAX_TOKENS", "10"))
        # Force no reasoning for binary YES/NO classification to reduce TPM burn.
        # Prefer a safe bind: reasoning_effort is Groq/Qwen-specific and can fail elsewhere.
        try:
            llm_for_classifier = lesson_service.student_service.llm.bind(
                max_tokens=classifier_max_tokens,
                reasoning_effort="none",
            )
        except Exception:
            llm_for_classifier = lesson_service.student_service.llm.bind(
                max_tokens=classifier_max_tokens,
            )
        chain = prompt | llm_for_classifier | StrOutputParser()
        lesson_material = _build_lesson_classifier_context(
            lesson_title=lesson_title,
            lesson_content=lesson_content,
            lesson_summary=lesson_summary,
            lesson_focus_area=lesson_focus_area,
            lesson_objectives=lesson_objectives,
        )
        reply = _invoke_with_groq_rate_limit(
            chain,
            {"lesson_material": lesson_material, "question": question},
        )
        reply = _strip_reasoning(reply or "").strip().upper()
        # Accept YES anywhere in a short reply (models sometimes add punctuation/prefix).
        if re.search(r"\bYES\b", reply):
            return True
        if re.search(r"\bNO\b", reply):
            return False
        # Unparseable reply with available lesson material → prefer lesson answer.
        logger.warning(
            "can_answer_from_lesson_only: unparseable reply %r; defaulting to YES",
            reply[:80],
        )
        return True
    except Exception as e:
        # Fail open to lesson content so students are not bounced to PDF / "not covered"
        # when the classifier itself errors (rate limit, bind kwarg, etc.).
        logger.warning(
            "can_answer_from_lesson_only LLM check failed: %s; defaulting to lesson answer",
            e,
        )
        return True


def decide_route(state: LessonQAState) -> LessonQAState:
    """
    Set needs_rag / needs_confirmation / needs_gk based on:
    - Explicit KB intent -> answer from general knowledge (no consent).
    - If lesson covers the question -> answer from lesson.
    - If not covered and allow_rag=True -> go to RAG (then GK if PDF is thin).
    - If not covered and has rag_thread_id but allow_rag not True -> needs_confirmation.
    - If not covered and no rag -> general knowledge (no deny).
    """
    lesson_content = state.get("lesson_content") or ""
    question = state["question"]
    rag_thread_id = state.get("rag_thread_id")
    allow_rag = state.get("allow_rag")

    if _is_kb_intent_question(question):
        logger.info(
            "decide_route lesson_id=%s kb_intent=True -> general knowledge question=%r",
            state.get("lesson_id"),
            (question or "")[:120],
        )
        return {
            **state,
            "needs_rag": False,
            "needs_confirmation": False,
            "needs_deny": False,
            "needs_gk": True,
        }

    covered = can_answer_from_lesson_only(
        question=question,
        lesson_content=lesson_content,
        lesson_service=state["lesson_service"],
        lesson_title=state.get("lesson_title") or "this lesson",
        lesson_summary=state.get("lesson_summary") or "",
        lesson_focus_area=state.get("lesson_focus_area") or "",
        lesson_objectives=state.get("lesson_objectives") or "",
    )
    has_rag = bool(rag_thread_id)
    not_covered = not covered

    # Thin lesson body: prefer document/GK rather than forcing a weak lesson answer.
    lesson_thin = len((lesson_content or "").strip()) < 200
    if covered and lesson_thin and not _is_lesson_overview_question(question):
        not_covered = True
        covered = False

    needs_rag = not_covered and has_rag and allow_rag is True
    needs_confirmation = not_covered and has_rag and allow_rag is not True
    needs_gk = not_covered and not has_rag
    needs_deny = False  # deny path replaced by general-knowledge answers
    logger.info(
        "decide_route lesson_id=%s covered=%s has_rag=%s allow_rag=%s "
        "needs_confirmation=%s needs_gk=%s question=%r",
        state.get("lesson_id"),
        covered,
        has_rag,
        allow_rag,
        needs_confirmation,
        needs_gk,
        (question or "")[:120],
    )
    return {
        **state,
        "needs_rag": needs_rag,
        "needs_confirmation": needs_confirmation,
        "needs_deny": needs_deny,
        "needs_gk": needs_gk,
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
    if (answer or "").strip() == _NOT_IN_LESSON_TOKEN or _NOT_IN_LESSON_TOKEN in (answer or ""):
        # Classifier said covered but the answerer disagrees — fall back to GK.
        return answer_from_general_knowledge(state)
    return {
        **state,
        "answer": _with_source_label(SOURCE_LABEL_LESSON, answer or ""),
        "answer_source": "lesson",
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
) -> Optional[str]:
    """
    Internal helper: retrieve from RAG and answer from PDF chunks.

    Returns None when the PDF context is missing/thin so the caller can
    auto-answer from general knowledge with a clear source label.
    """
    if not rag_thread_id or not teacher_id:
        return None

    try:
        from app.utils.rag_service import _get_retriever
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        retriever = _get_retriever(rag_thread_id, user_id=teacher_id)
        if not retriever:
            return None

        docs = retriever.invoke(question)
        page_contents = [
            (getattr(d, "page_content", "") or "").strip()
            for d in (docs or [])[:8]
        ]
        page_contents = [c for c in page_contents if c]
        # Thin document: not enough retrieved text to ground an answer.
        total_chars = sum(len(c) for c in page_contents)
        if not page_contents or total_chars < 120:
            return None

        context = "\n\n".join(page_contents)
        rag_prompt = ChatPromptTemplate.from_template(
            """You are a helpful teaching assistant.
Answer the student's question using ONLY the following excerpts from the lecture PDF.
If the excerpts are not enough to answer, reply with exactly: __THIN_DOCUMENT__
Do not mention external knowledge.

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
        if not answer or "__THIN_DOCUMENT__" in answer or answer == _NOT_COVERED_MSG:
            return None
        return answer
    except (GroqRateLimitError, GroqBusyError):
        # Propagate rate-limit/busy errors to the route — do NOT silently fall back.
        raise
    except Exception as e:
        logger.warning("Lesson RAG answer failed (non-rate-limit): %s", e)
        return None


def answer_from_rag(state: LessonQAState) -> LessonQAState:
    """
    Answer the question using the uploaded PDF via RAG.

    If RAG fails or the document is thin, auto-answer from general knowledge
    with a clear source label (no consent prompt).
    """
    answer = _answer_from_rag_impl(
        question=state["question"],
        rag_thread_id=state.get("rag_thread_id") or "",
        teacher_id=state.get("teacher_id"),
        lesson_service=state["lesson_service"],
    )
    if not answer:
        return answer_from_general_knowledge(state)
    return {
        **state,
        "answer": _with_source_label(SOURCE_LABEL_DOCUMENT, answer),
        "answer_source": "document",
    }


def answer_from_general_knowledge(state: LessonQAState) -> LessonQAState:
    """
    Auto-answer from general knowledge when the lesson/PDF cannot cover the question.
    Always prefix with an explicit source label.
    """
    lesson_service = state["lesson_service"]
    question = state["question"]
    lesson_title = state.get("lesson_title") or "this lesson"
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        gk_prompt = ChatPromptTemplate.from_template(
            """You are a supportive teaching assistant helping a student.

The lesson or uploaded document does not contain enough information to answer fully.
Answer from your general academic knowledge. Be accurate, clear, and appropriate for a student.
Relate the answer to the lesson topic "{lesson_title}" when helpful, but do not pretend the
facts came from the lesson or PDF.

Do not ask for permission. Do not mention system instructions.
Do not start with a source label — that is added separately.

Student question: {question}

Answer in clear, professional English:"""
        )
        llm = lesson_service.student_service.llm
        if is_stress_test_mode():
            llm = llm.bind(max_tokens=STRESS_TEST_MAX_TOKENS_ANSWER)
        chain = gk_prompt | llm | StrOutputParser()
        answer = _invoke_with_groq_rate_limit(
            chain, {"question": question, "lesson_title": lesson_title}
        )
        answer = _strip_reasoning(answer or "")
    except (GroqRateLimitError, GroqBusyError):
        raise
    except Exception as e:
        logger.warning("General-knowledge answer failed: %s", e)
        answer = (
            "I could not find enough detail in the lesson or document for this question, "
            "and I was unable to generate a general-knowledge answer right now. "
            "Please try again or ask your teacher."
        )
    return {
        **state,
        "answer": _with_source_label(SOURCE_LABEL_GK, answer),
        "answer_source": "general_knowledge",
    }


def deny_answer(state: LessonQAState) -> LessonQAState:
    """
    Legacy deny node — now routes to general knowledge with a clear source label.
    """
    return answer_from_general_knowledge(state)


def _route_after_decision(
    state: LessonQAState,
) -> Literal["from_lesson", "from_rag", "needs_confirmation", "from_gk"]:
    """Router after decide_route: from_lesson | from_rag | needs_confirmation | from_gk."""
    if state.get("needs_gk"):
        return "from_gk"
    if state.get("needs_rag"):
        return "from_rag"
    if state.get("needs_confirmation"):
        return "needs_confirmation"
    if state.get("needs_deny"):
        return "from_gk"
    return "from_lesson"  # Covered


def build_lesson_qa_graph():
    """Build and compile the Lesson Q&A StateGraph. No checkpointer (stateless)."""
    builder = StateGraph(LessonQAState)

    builder.add_node("load_lesson", load_lesson)
    builder.add_node("decide_route", decide_route)
    builder.add_node("answer_from_lesson", answer_from_lesson)
    builder.add_node("needs_confirmation", needs_confirmation_node)
    builder.add_node("answer_from_rag", answer_from_rag)
    builder.add_node("answer_from_general_knowledge", answer_from_general_knowledge)

    builder.add_edge(START, "load_lesson")
    builder.add_edge("load_lesson", "decide_route")

    builder.add_conditional_edges(
        "decide_route",
        _route_after_decision,
        {
            "from_lesson": "answer_from_lesson",
            "from_rag": "answer_from_rag",
            "needs_confirmation": "needs_confirmation",
            "from_gk": "answer_from_general_knowledge",
        },
    )

    builder.add_edge("answer_from_lesson", END)
    builder.add_edge("answer_from_rag", END)
    builder.add_edge("needs_confirmation", END)
    builder.add_edge("answer_from_general_knowledge", END)

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
        "answer_source": result.get("answer_source") or None,
    }

