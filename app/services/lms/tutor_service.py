"""LMS tutor chat for students and teachers (Phases 5 & 9)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.services.lms.performance_service import get_student_mastery
from app.utils.llm_factory import create_llm

STUDENT_TUTOR_PROMPT = """You are IqbalAI — a friendly, general academic tutor for students (same spirit as the main IqbalAI chatbot).

- Answer questions across ALL subjects: math, science, English, history, geography, computers, study skills, and more. You are NOT limited to mathematics.
- When a student asks a factual or conceptual question, give a clear, direct, helpful answer.
- When they are working through a problem or assignment, you may guide step-by-step — but still help them reach the answer; do not refuse non-math topics.
- Use warm, encouraging language. Keep replies concise unless they ask for more detail.
- If the question is unclear, ask one short clarifying question.
- If you truly do not know, say so honestly — do not invent facts."""

TEACHER_TUTOR_PROMPT = """You are IqbalAI, an AI teaching assistant for educators (same helpful tone as the main IqbalAI chatbot).
- Help with any subject: lesson ideas, explanations, practice questions, rubrics, and classroom strategies.
- Do not include student PII.
- Suggest MCQ distractors when asked.
- Be practical, accurate, and concise."""

DEFICIENCY_TUTOR_PROMPT = """You are a supportive tutor helping a student practice weak areas after a diagnostic test.
- Use the teacher's target PDF content when explaining.
- Be encouraging, clear, and concise — same friendly tone as the main IqbalAI tutor.
- NEVER give the final MCQ letter answer or full solution on early assistance levels.
- Follow the ASSISTANCE LEVEL instruction exactly for this turn."""

DEFICIENCY_ASSISTANCE_LEVELS = {
    1: {
        "label": "Level 1 – Prompt",
        "instruction": (
            "Level 1 – Prompt: Do NOT reveal the answer or solve the problem. "
            "Encourage the student to think about the FIRST step only. Ask 1–2 guiding questions. "
            "End with something like 'What would you try first?'"
        ),
    },
    2: {
        "label": "Level 2 – Hint",
        "instruction": (
            "Level 2 – Hint: Give a SMALL clue about the concept or formula needed. "
            "Do NOT show calculation steps or the final answer. Keep it to 2–4 sentences."
        ),
    },
    3: {
        "label": "Level 3 – Strong Hint",
        "instruction": (
            "Level 3 – Strong Hint: Provide structured guidance — outline the steps to solve "
            "but leave at least the final calculation or conclusion for the student. "
            "Do NOT state which MCQ option is correct."
        ),
    },
    4: {
        "label": "Level 4 – Similar Worked Example",
        "instruction": (
            "Level 4 – Similar Worked Example: Walk through a SIMILAR problem with different numbers "
            "or context (from the PDF material). Show each step clearly. "
            "Then connect it back to their question without giving the exact MCQ answer."
        ),
    },
    5: {
        "label": "Level 5 – Full Explanation",
        "instruction": (
            "Level 5 – Full Explanation: The student has tried several times. "
            "Explain the concept fully, show how to solve THIS question step by step, "
            "and state which option is correct and why — grounded in the PDF content."
        ),
    },
}

HINT_LEVELS = [
    "Offer a gentle nudge — ask what concept might apply.",
    "Suggest a strategy without solving.",
    "Give a partial step but not the final answer.",
]


def build_student_context(
    student_id: int,
    topic_id: Optional[int] = None,
    question_text: Optional[str] = None,
    attempt_count: int = 0,
) -> str:
    mastery = get_student_mastery(student_id)
    weak = [m for m in mastery if m.get("mastery_status") == "weak"]
    parts = [f"Weak topics: {len(weak)}", f"Prior attempts on this item: {attempt_count}"]
    if topic_id:
        parts.append(f"Current topic_id: {topic_id}")
    if question_text:
        parts.append(f"Question context: {question_text[:500]}")
    return "\n".join(parts)


def get_deficiency_assist_level_label(level: int) -> str:
    entry = DEFICIENCY_ASSISTANCE_LEVELS.get(min(max(level, 1), 5), DEFICIENCY_ASSISTANCE_LEVELS[1])
    return entry["label"]


def get_deficiency_assist_instruction(level: int) -> str:
    entry = DEFICIENCY_ASSISTANCE_LEVELS.get(min(max(level, 1), 5), DEFICIENCY_ASSISTANCE_LEVELS[5])
    return entry["instruction"]


def build_deficiency_context(
    weak_topics_json: Optional[str] = None,
    current_question: Optional[dict] = None,
    pdf_excerpt: Optional[str] = None,
    assist_level: int = 1,
) -> str:
    parts = []
    parts.append(get_deficiency_assist_instruction(assist_level))
    if weak_topics_json:
        parts.append(f"Weak areas (from diagnostic): {weak_topics_json[:800]}")
    if current_question:
        parts.append(f"Current practice question: {current_question.get('question_text', '')[:500]}")
        parts.append(f"Topic: {current_question.get('topic_name', '')}")
    if pdf_excerpt:
        parts.append(f"Teacher target PDF excerpt:\n{pdf_excerpt[:3500]}")
    return "\n\n".join(parts)


def tutor_chat(
    message: str,
    api_key: str,
    mode: str = "student",
    context: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    assist_level: Optional[int] = None,
) -> str:
    if not api_key:
        api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    if not api_key:
        from app.utils.llm_factory import get_chat_model
        try:
            get_chat_model(max_tokens=512)
            api_key = "__admin__"
        except Exception:
            return "Tutor unavailable: no API key configured."

    if mode == "teacher":
        system = TEACHER_TUTOR_PROMPT
    elif mode == "deficiency":
        system = DEFICIENCY_TUTOR_PROMPT
        if assist_level is not None and not context:
            system += f"\n\n{get_deficiency_assist_instruction(assist_level)}"
    else:
        system = STUDENT_TUTOR_PROMPT
    if context:
        system += f"\n\nContext:\n{context}"

    if api_key == "__admin__":
        from app.utils.llm_factory import get_chat_model
        llm = get_chat_model(temperature=0.4, max_tokens=1024)
    else:
        llm = create_llm(api_key=api_key)
    messages: List[Any] = [{"role": "system", "content": system}]
    for h in history or []:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})

    try:
        resp = llm.invoke(messages)
        content = getattr(resp, "content", str(resp))
        return content if isinstance(content, str) else str(content)
    except Exception as e:
        return f"Tutor error: {e}"


def get_hint(level: int = 0, question_text: Optional[str] = None) -> str:
    idx = min(max(level, 0), len(HINT_LEVELS) - 1)
    hint = HINT_LEVELS[idx]
    if question_text:
        return f"{hint}\n\nQuestion: {question_text[:300]}"
    return hint
