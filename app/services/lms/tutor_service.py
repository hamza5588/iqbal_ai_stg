"""LMS tutor chat for students and teachers (Phases 5 & 9)."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from app.services.lms.performance_service import get_student_mastery
from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
from app.utils.llm_factory import create_llm

STUDENT_TUTOR_PROMPT = """You are IqbalAI — a friendly, general academic tutor for students (same spirit as the main IqbalAI chatbot).

- Answer questions across ALL subjects: math, science, English, history, geography, computers, study skills, and more. You are NOT limited to mathematics.
- Default to SHORT, step-by-step replies (see REPLY RULES below) — give one
  step or one idea, then check the student is following, rather than a long
  answer up front. Only go longer if the student clearly asks for more detail.
- When they are working through a problem or assignment, guide step-by-step — but still help them reach the answer; do not refuse non-math topics.
- Use warm, encouraging language.
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
- Follow the ASSISTANCE LEVEL instruction exactly for this turn.
- Follow the READING LEVEL rules below in every reply."""

# DIL feedback: the tutor's default answers were too hard and too long.
# Default to short, one-step-at-a-time replies for every student turn.
_READING_LEVEL_RULES = (
    "REPLY RULES - follow every single turn, no exceptions:\n"
    "- Write for a student in {grade} who is finding this hard. Simple, "
    "everyday words - if you must use a harder word, say what it means.\n"
    "- AT MOST 2 short sentences (about 12 words each). Never write more, "
    "even for a full explanation - break it across turns instead.\n"
    "- Explain only ONE step at a time. Never the whole method or solution "
    "in a single reply.\n"
    "- End with a short check-in, e.g. \"Does that make sense so far?\" or "
    "\"Want to try the next step?\" - then stop and wait for their answer."
)

_CONFUSION_RE = re.compile(
    r"(i\s*don.?t\s*get\s*it|don.?t\s*understand|i.?m\s*confused|"
    r"still\s*confused|not\s*clear|makes?\s*no\s*sense|(you.?ve\s*)?lost\s*me|"
    r"no\s*idea|huh\??$|what\??$)",
    re.I,
)


def student_seems_confused(message: Optional[str]) -> bool:
    """True when the student's message signals they didn't follow the last
    explanation ("I don't get it", "I'm confused", ...) - the trigger for
    switching to a real-life example instead of rephrasing."""
    return bool(_CONFUSION_RE.search((message or "").strip()))


_REAL_LIFE_EXAMPLE_RULE = (
    "The student just said they don't get it. Do NOT repeat your last "
    "explanation, even reworded. Instead give ONE simple real-life example "
    "of the idea - something they could see or do, not another maths line - "
    "in at most 2 sentences, then ask if that helped."
)

_NO_REPEAT_RULE = (
    "The student has already seen your earlier explanation and asked again. "
    "Do NOT repeat your previous wording. Explain the SAME single step a "
    "different way - simpler words or a fresh angle - still at most 2 sentences."
)

DEFICIENCY_ASSISTANCE_LEVELS = {
    1: {
        "label": "Level 1 – Prompt",
        "instruction": (
            "Level 1 – Prompt: Do NOT reveal the answer or solve the problem. "
            "Encourage the student to think about the FIRST step only, in at "
            "most 2 short sentences. End with something like 'What would you try first?'"
        ),
    },
    2: {
        "label": "Level 2 – Hint",
        "instruction": (
            "Level 2 – Hint: Give ONE small clue about the concept or formula "
            "needed, in at most 2 short sentences. Do NOT show calculation "
            "steps or the final answer."
        ),
    },
    3: {
        "label": "Level 3 – Strong Hint",
        "instruction": (
            "Level 3 – Strong Hint: In at most 2 short sentences, name the "
            "FIRST step to solve it - not the whole method, and leave the "
            "calculation and conclusion for the student. "
            "Do NOT state which MCQ option is correct."
        ),
    },
    4: {
        "label": "Level 4 – Similar Worked Example",
        "instruction": (
            "Level 4 – Similar Worked Example: In at most 2 short sentences, "
            "show ONE step of a SIMILAR problem with different numbers (from "
            "the PDF material) - not the whole worked example at once. End by "
            "asking if they want the next step. Never give the exact MCQ answer."
        ),
    },
    5: {
        "label": "Level 5 – Full Explanation",
        "instruction": (
            "Level 5 – Full Explanation: The student has tried several times, "
            "so this turn is the ONE exception to the 2-sentence rule. Explain "
            "the concept, show how to solve THIS question step by step (one "
            "short line per step), and state which option is correct and why "
            "— grounded in the PDF content. Still simple words, still short lines."
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
    message: Optional[str] = None,
) -> str:
    mastery = get_student_mastery(student_id)
    weak = [m for m in mastery if m.get("mastery_status") == "weak"]
    parts = [f"Weak topics: {len(weak)}", f"Prior attempts on this item: {attempt_count}"]
    try:
        from app.services.lms import class_service

        grade = class_service.get_student_grade(student_id)
    except Exception:
        grade = None
    parts.append(_READING_LEVEL_RULES.format(grade=f"grade {grade}" if grade else "this student's grade"))
    if student_seems_confused(message):
        parts.append(_REAL_LIFE_EXAMPLE_RULE)
    elif attempt_count > 0:
        parts.append(_NO_REPEAT_RULE)
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
    grade_level: Optional[str] = None,
    prior_turns: int = 0,
    message: Optional[str] = None,
) -> str:
    parts = []
    grade_label = f"grade {grade_level}" if grade_level else "this student's grade"
    parts.append(_READING_LEVEL_RULES.format(grade=grade_label))
    parts.append(get_deficiency_assist_instruction(assist_level))
    if student_seems_confused(message):
        parts.append(_REAL_LIFE_EXAMPLE_RULE)
    elif prior_turns > 0:
        parts.append(_NO_REPEAT_RULE)
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

    messages: List[Any] = [{"role": "system", "content": system}]
    for h in history or []:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})

    # Hard backstop behind the "at most 2 sentences" prompt rule for student-
    # facing modes, so a model that ignores the instruction still can't ramble
    # on. Teacher mode (lesson ideas, rubrics) keeps room to be longer, and so
    # does deficiency Level 5 - the one deliberate full-explanation exception.
    if mode == "teacher":
        max_tokens = 1024
    elif mode == "deficiency" and (assist_level or 0) >= 5:
        max_tokens = 500
    else:
        max_tokens = 220

    def _platform_llm():
        from app.utils.llm_factory import get_chat_model

        return get_chat_model(temperature=0.4, max_tokens=max_tokens)

    def _run(llm) -> str:
        resp = invoke_with_groq_rate_limit(
            lambda: llm.invoke(messages),
            description=f"lms tutor chat ({mode})",
        )
        content = getattr(resp, "content", str(resp))
        return content if isinstance(content, str) else str(content)

    # A student's saved Groq key can be stale or invalid mid-session (the
    # "Ask a Tutor stopped working until I logged out and back in" report).
    # Always fall back to the platform model instead of surfacing an error.
    if api_key == "__admin__":
        try:
            return _run(_platform_llm())
        except Exception as e:  # noqa: BLE001
            return f"Tutor error: {e}"

    try:
        return _run(create_llm(api_key=api_key, max_tokens=max_tokens))
    except Exception:
        try:
            return _run(_platform_llm())
        except Exception as e:  # noqa: BLE001
            return f"Tutor error: {e}"


def get_hint(level: int = 0, question_text: Optional[str] = None) -> str:
    idx = min(max(level, 0), len(HINT_LEVELS) - 1)
    hint = HINT_LEVELS[idx]
    if question_text:
        return f"{hint}\n\nQuestion: {question_text[:300]}"
    return hint
