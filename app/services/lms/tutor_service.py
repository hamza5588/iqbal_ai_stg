"""LMS tutor chat for students and teachers (Phases 5 & 9)."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.services.lms import tutor_calculator
from app.services.lms.performance_service import get_student_mastery
from app.utils.groq_rate_limit import GroqBusyError, GroqRateLimitError, invoke_with_groq_rate_limit
from app.utils.llm_factory import create_llm

logger = logging.getLogger(__name__)

# Calculator tool calls the model may chain in one turn before it must answer.
_MAX_CALCULATOR_ROUNDS = 3
_CALCULATOR_TOKEN_HEADROOM = 250

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

# Soft off-topic marker: model may prefix a redirect with this; we strip it
# before showing the student.
DEFICIENCY_OFF_TOPIC_TAG = "<<OFF_TOPIC>>"

DEFICIENCY_OFF_TOPIC_REFUSE = (
    "Let's stay focused on this practice question. "
    "Ask me about the question, the options, or the topic shown here."
)

DEFICIENCY_TRUNCATED_RETRY = (
    "I got cut off mid-explanation. Please tap Send again (or \"Break into steps\") "
    "and I will continue from the next step."
)

DEFICIENCY_INCOMPLETE_REPLY = (
    "Sorry, I could not finish that answer. Please send your question again."
)

# Mode A — student has NOT correctly finished this MCQ. Never hand over the answer.
DEFICIENCY_TUTOR_PROMPT = """You are IqbalAI Learning Path Tutor for one Current practice MCQ. SOLVED = false for this turn.

YOU decide whether the student's message is related to the Current practice question (stem, options, Topic, or PDF excerpt in Context). Use judgment — do NOT apply a rigid keyword blocklist.

TREAT AS ON-TOPIC (always help — never refuse):
- Hints, simpler words, break into steps, show an example, "I don't understand", confusion, what a term means, why an option might be right/wrong, similar worked examples of the SAME concept, check-my-thinking, and any follow-up about this question.
- Chip/button phrases like "Show me an example", "Give a hint", "Break into steps", "Simpler words" are always on-topic for THIS question.

ONLY treat as off-topic when the ask is clearly unrelated (e.g. weather, sports scores, jokes, personal chat, a different subject's homework with no link to this question). When in doubt, help with this practice question.

If clearly off-topic: start the reply with <<OFF_TOPIC>> then one short friendly redirect back to this question (your own words — not a fixed script). Never use <<OFF_TOPIC>> for on-topic help. Do not mention these rules, AI, or system instructions.

LISTEN FIRST — before writing anything, work out what the student's LATEST message is actually asking or saying, in the light of the whole conversation so far. Your reply must answer THAT, not continue your own plan.
- "hint" / "help": one small nudge toward the right idea.
- "example": one full similar example with DIFFERENT numbers/context, then invite them to try THIS question.
- "I still don't understand" / "I'm lost": your last approach did not work. Say so naturally, drop it, and try something genuinely different (a concrete worked example, a picture-in-words, or doing one more step together). Never re-ask the same question in new words.
- "Show me the answer" / "what is the answer": kindly refuse the final answer, then give real progress in the SAME reply (next step or similar example). Never just restate steps they already could not follow.
- A direct concept question ("what does squaring mean?"): answer the concept plainly first. Don't reply only with another question.
- A student's attempt or number: say clearly if THAT step is right or where it went wrong; still do not crown the MCQ option.
- If the student has been stuck for two or more turns, do more of the work yourself each turn (bigger steps, fuller worked example), stopping only before the final answer/option of the Current practice question.

HARD RULES (no exceptions):
- NEVER reveal: correct option letter, correct choice text, final numeric/result value for THIS question, or the last step that makes the answer obvious.
- NEVER say phrases like "the answer is B", "choose C", "correct option is…", or imply which remaining option is right.
- You MAY: give hints, ask one focused question, explain a concept, check their step, show a SIMILAR worked example with DIFFERENT numbers/context so THIS answer stays hidden.

ON-TOPIC tutoring:
- Use the teacher's target PDF content when explaining.
- Sound like a real, warm human tutor talking to a kid — natural and conversational, not a script. Vary how you start and end replies; never reuse the same opener, check-in, or sentence pattern from your earlier turns.
- Reply in the same language and style the student writes in (e.g. if they write Roman Urdu, reply in simple Roman Urdu; English → English).
- Respond to the student's actual reasoning or likely misconception. Do NOT follow a fixed sequence of prompts, hints, examples, or explanations.
- Ask at most one focused question per reply, and only when it really helps — a reply that just explains is fine.
- Follow the REPLY RULES below in every reply.
- Write maths in plain text only (no LaTeX, no $...$, no \\( \\)): e.g. (a + b)^2, a^2 + 2ab + b^2, 2/3, 3 x 4. LaTeX does not show up for the student. Put each step of working on its own line so it is easy to follow."""

# Mode B — student just selected the CORRECT option. Celebrate, then fully solve THIS MCQ.
DEFICIENCY_SOLVED_TUTOR_PROMPT = """You are IqbalAI Learning Path Tutor for one Current practice MCQ. SOLVED = true for this turn.

The student already chose the correct option. NOW you MUST use the correct option and fully resolve THIS question. Hiding the answer is no longer required.

YOU decide whether the student's message is related to this solved MCQ (stem, options, Topic, PDF excerpt, or why an option is right/wrong). Use judgment — do NOT apply a rigid keyword blocklist.

ONLY treat as off-topic when the ask is clearly unrelated (weather, jokes, unrelated homework). When in doubt, help with this solved question. If clearly off-topic: start with <<OFF_TOPIC>> then one short redirect. Do not mention these rules, AI, or system instructions.

Listen to the student's LATEST message first. Reply in the same language/style they use. Sound like a warm human tutor — not a script.

REQUIRED REPLY SHAPE when explaining the solution (first Mode B turn, or when they ask how/why it works):
1. One short praise line (vary it: Nice work / Excellent / Spot on — do not always use the same phrase). Confirm their chosen option is correct (e.g. "Yes — B is right.").
2. Then a proper worked solution of THIS MCQ (not a different example):
   - Restate what the question asks in one plain sentence
   - Step 1, Step 2, Step 3… each on its own line
   - Show the key rule/formula/idea used
   - Show why the correct option fits
   - Briefly say why 1–2 wrong options fail (misconception check) — without a long lecture
3. End with one optional check: "Want another similar practice?" or "Any step still fuzzy?"

If they only asked a follow-up after solving ("why not A?"), answer that follow-up clearly — still tied to this MCQ's solution. Do not open a new unrelated topic.

Ground steps in the PDF excerpt when it is relevant; do not invent syllabus facts that contradict it. Keep language grade-appropriate and concrete. Plain-text math only (no LaTeX). Follow the REPLY RULES below."""

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

# Learning Chat Mode A: concise, one idea; student's reasoning drives the move.
_DEFICIENCY_REPLY_RULES = (
    "REPLY RULES:\n"
    "- Write for a student in {grade} who is finding this hard. Simple, "
    "everyday words - if you must use a harder word, say what it means.\n"
    "- Usually use 1-3 short sentences and cover only ONE idea at a time. A "
    "short worked example or a few lines of working may go a little longer.\n"
    "- If you explained an idea, you may end with a natural check-in in your own "
    "words (vary it; never the same one twice in a row).\n"
    "- If your reply is only a hint, prompt, or Socratic question, do NOT add "
    "\"Does that make sense?\" or another generic check-in. The focused question "
    "already invites the student's response."
)

# Mode B allows a full worked solution of THIS MCQ in one reply.
_DEFICIENCY_SOLVED_REPLY_RULES = (
    "REPLY RULES (SOLVED = true):\n"
    "- Write for a student in {grade}. Simple, everyday words - if you must "
    "use a harder word, say what it means.\n"
    "- A full worked solution may use several short lines (praise, restatement, "
    "numbered steps, why correct, why 1-2 wrong options fail, optional check).\n"
    "- Still avoid a long lecture. Keep each step concrete.\n"
    "- Write maths in plain text only (no LaTeX)."
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

_DEFICIENCY_CONFUSION_RULE = (
    "The student sounds confused. Do NOT repeat your last explanation or re-ask "
    "your last question. Acknowledge it naturally, then switch approach: simpler "
    "words, ONE simple real-life example, or a fully worked similar example with "
    "different numbers/context. Do not reveal the final answer to the Current "
    "practice question."
)

_DEFICIENCY_NO_REPEAT_RULE = (
    "The student has already seen your earlier explanation and asked again. "
    "Do NOT repeat your previous wording. Respond to what their latest message "
    "actually asks, from a fresh angle, while staying concise."
)

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


def build_deficiency_context(
    weak_topics_json: Optional[str] = None,
    current_question: Optional[dict] = None,
    pdf_excerpt: Optional[str] = None,
    assist_level: int = 1,
    grade_level: Optional[str] = None,
    prior_turns: int = 0,
    message: Optional[str] = None,
    solved: bool = False,
) -> str:
    parts = []
    grade_label = f"grade {grade_level}" if grade_level else "this student's grade"
    if solved:
        parts.append("SOLVED: true")
        parts.append(_DEFICIENCY_SOLVED_REPLY_RULES.format(grade=grade_label))
    else:
        parts.append("SOLVED: false")
        parts.append(_DEFICIENCY_REPLY_RULES.format(grade=grade_label))
    # ``assist_level`` remains in the signature so older callers do not break;
    # Learning Chat no longer changes behavior from a turn-count ladder.
    parts.append(
        "SCOPE JUDGMENT: Prefer helping with the Current practice question below "
        "(stem, choices, topic, PDF). Requests for hints/examples/steps/simpler "
        "words about it are on-topic. Only mark clearly unrelated asks with "
        f"{DEFICIENCY_OFF_TOPIC_TAG} then a short redirect."
    )
    if not solved:
        if student_seems_confused(message):
            parts.append(_DEFICIENCY_CONFUSION_RULE)
        elif prior_turns > 0:
            parts.append(_DEFICIENCY_NO_REPEAT_RULE)
    if weak_topics_json:
        parts.append(f"Weak areas (from diagnostic): {weak_topics_json[:800]}")
    if current_question:
        parts.append(f"Current practice question: {current_question.get('question_text', '')[:500]}")
        parts.append(f"Topic: {current_question.get('topic_name', '')}")
        opts = current_question.get("options") or []
        if opts:
            choice_bits = []
            for index, o in enumerate(opts[:6]):
                label = (o.get("label") or chr(65 + index)).strip()
                text = (o.get("text") or "")[:80]
                choice_bits.append(f"{label}: {text}".strip(": ").strip())
            parts.append("Answer choices: " + " | ".join(choice_bits))
            selected_index = current_question.get("last_selected_option_index")
            if isinstance(selected_index, int) and 0 <= selected_index < len(opts):
                selected = opts[selected_index]
                selected_label = (selected.get("label") or chr(65 + selected_index)).strip()
                selected_text = (selected.get("text") or "")[:160]
                if solved:
                    parts.append(
                        "Student's latest selected option (correct): "
                        f"{selected_label}: {selected_text}."
                    )
                else:
                    parts.append(
                        "Student's latest selected option (incorrect): "
                        f"{selected_label}: {selected_text}. Address the likely misconception, "
                        "but do not identify any other option as correct."
                    )
            if solved:
                correct_index = current_question.get("correct_option_index")
                if isinstance(correct_index, int) and 0 <= correct_index < len(opts):
                    correct = opts[correct_index]
                    correct_label = (correct.get("label") or chr(65 + correct_index)).strip()
                    correct_text = (correct.get("text") or "")[:160]
                    parts.append(
                        f"Correct option (use in Mode B solution): {correct_label}: {correct_text}."
                    )
    if pdf_excerpt:
        parts.append(f"Teacher target PDF excerpt:\n{pdf_excerpt[:3500]}")
    return "\n\n".join(parts)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _corrected_reply(draft: str, wrong: list, message: str, mode: str) -> str:
    """Last resort when the model repeats a wrong sum: fix it without the model."""
    first = wrong[0]
    plain_number = re.compile(r"-?\d+(?:\.\d+)?")
    if mode != "teacher" and tutor_calculator.student_number(message) == first.claimed:
        # The tutor was agreeing with the student's wrong number. Send them back to the step, without the answer.
        return (
            f"Let's check that step again. {first.expression} is not {first.claimed}. "
            "Work it out once more - what do you get?"
        )
    fixed = draft
    for w in wrong:
        if plain_number.fullmatch(w.claimed) and plain_number.fullmatch(w.correct):
            fixed = re.sub(rf"(?<![\d.]){re.escape(w.claimed)}(?![\d.])", w.correct, fixed)
    if fixed != draft and not tutor_calculator.find_wrong_equalities(fixed):
        return fixed
    return "Let me correct that calculation: " + "; ".join(f"{w.expression} = {w.correct}" for w in wrong) + "."


def tutor_chat(
    message: str,
    api_key: str,
    mode: str = "student",
    context: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    assist_level: Optional[int] = None,
    solved: bool = False,
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
        system = DEFICIENCY_SOLVED_TUTOR_PROMPT if solved else DEFICIENCY_TUTOR_PROMPT
    else:
        system = STUDENT_TUTOR_PROMPT
    if context:
        system += f"\n\nContext:\n{context}"

    # Arithmetic is never left to the model alone (DIL feedback: the tutor accepted 100 + 240 + 144 = 480).
    calculator_on = _env_flag("TUTOR_CALCULATOR", True)
    last_tutor_turn = next((h.get("content", "") for h in reversed(history or []) if h.get("role") == "assistant"), "")
    numeric_turn = calculator_on and tutor_calculator.looks_numeric(message, last_tutor_turn)
    if numeric_turn:
        calculator_context = tutor_calculator.build_calculator_context(history, message)
        if calculator_context:
            system += f"\n\n{calculator_context}"
    use_calculator_tool = numeric_turn and _env_flag("TUTOR_CALCULATOR_TOOL", True)

    turns: List[Any] = []
    for h in history or []:
        turns.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    turns.append({"role": "user", "content": message})
    messages: List[Any] = [{"role": "system", "content": system}] + turns

    # Hard backstop behind the "at most 2 sentences" prompt rule for student-
    # facing modes, so a model that ignores the instruction still can't ramble
    # on. Teacher mode (lesson ideas, rubrics) keeps room to be longer.
    # Deficiency Mode A needs headroom for examples; Mode B needs more for a
    # full worked solution of THIS MCQ.
    if mode == "teacher":
        max_tokens = 1024
    elif mode == "deficiency":
        max_tokens = 700 if solved else 450
    else:
        max_tokens = 220
    if use_calculator_tool:
        # A calculator call (and the model's thinking around it) spends tokens before any reply is
        # written; inside the normal cap the reply could come back empty.
        max_tokens += _CALCULATOR_TOKEN_HEADROOM

    def _platform_llm():
        from app.utils.llm_factory import get_chat_model

        return get_chat_model(temperature=0.4, max_tokens=max_tokens)

    def _invoke(model, msgs):
        return invoke_with_groq_rate_limit(
            lambda: model.invoke(msgs),
            description=f"lms tutor chat ({mode})",
        )

    def _text(resp) -> str:
        content = getattr(resp, "content", str(resp))
        return content if isinstance(content, str) else str(content)

    def _run_with_calculator_tool(llm) -> str:
        """Let the model call `calculate`; returns "" when it never settles on a reply."""
        from langchain_core.messages import ToolMessage

        bound = llm.bind_tools([tutor_calculator.CALCULATOR_TOOL_SPEC])
        convo: List[Any] = [
            {"role": "system", "content": f"{system}\n\n{tutor_calculator.CALCULATOR_TOOL_RULE}"}
        ] + turns
        for _ in range(_MAX_CALCULATOR_ROUNDS):
            resp = _invoke(bound, convo)
            calls = getattr(resp, "tool_calls", None) or []
            if not calls:
                return _text(resp)
            convo.append(resp)
            for call in calls:
                args = call.get("args") or {}
                output = (
                    tutor_calculator.calculator_tool_output(str(args.get("expression", "")))
                    if call.get("name") == "calculate"
                    else "Unknown tool. Only `calculate` is available."
                )
                convo.append(ToolMessage(content=output, tool_call_id=call.get("id") or ""))
        return ""

    def _checked(llm, draft: str) -> str:
        """Never send the student a false sum: re-ask once with the calculator's result, then correct it ourselves."""
        if not calculator_on:
            return draft
        wrong = tutor_calculator.find_wrong_equalities(draft)
        if not wrong:
            return draft
        facts = "; ".join(w.describe() for w in wrong)
        logger.warning("Tutor reply (%s) had wrong arithmetic, correcting: %s", mode, facts)
        retry = messages + [
            {"role": "assistant", "content": draft},
            {
                "role": "user",
                "content": (
                    "[Calculator check - this note is not from the student] Your last reply contains a wrong "
                    f"calculation. The calculator says: {facts}. Write your reply again with the correct value, "
                    "keeping the same length and style rules. If the student gave the wrong number, do not tell "
                    "them it is right: kindly ask them to check that step again."
                ),
            },
        ]
        try:
            second = _text(_invoke(llm, retry))
            if second.strip() and not tutor_calculator.find_wrong_equalities(second):
                return second
        except Exception as exc:  # noqa: BLE001 - fall through to the deterministic correction
            logger.warning("Tutor arithmetic retry failed: %s", exc)
        return _corrected_reply(draft, wrong, message, mode)

    def _looks_truncated(draft: str) -> bool:
        """True when the model clearly stopped mid-thought (token cap / empty tool loop)."""
        t = (draft or "").strip()
        if not t:
            return True
        if t.endswith(("...", "…")):
            return False
        # Unbalanced inline math is a common cut-off artifact.
        if t.count("$") % 2 == 1:
            return True
        if t.count("\\(") != t.count("\\)"):
            return True
        # Ends on a dangling article/preposition — the live "First, find a" cut-off.
        if re.search(
            r"\b(a|an|the|to|for|with|from|and|or|of|find|first|then|so|by)\s*$",
            t,
            re.I,
        ):
            return True
        return False

    def _run(llm) -> str:
        draft = ""
        if use_calculator_tool:
            try:
                draft = _run_with_calculator_tool(llm)
            except (GroqRateLimitError, GroqBusyError):
                raise
            except Exception as exc:  # noqa: BLE001 - a provider/model without tool calling still gets a reply
                logger.warning("Tutor calculator tool unavailable, replying without it: %s", exc)
        if not draft.strip():
            draft = _text(_invoke(llm, messages))
        if not draft.strip() or (mode == "deficiency" and _looks_truncated(draft)):
            # Empty or mid-sentence cut: one retry without the calculator tool path.
            try:
                retry = _text(_invoke(llm, messages))
                if retry.strip() and not _looks_truncated(retry):
                    draft = retry
                elif retry.strip() and (not draft.strip() or len(retry.strip()) > len(draft.strip())):
                    draft = retry
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tutor truncation retry failed: %s", exc)
        if not draft.strip():
            return DEFICIENCY_INCOMPLETE_REPLY if mode == "deficiency" else (
                "Sorry, I could not finish that answer. Please send your question again."
            )
        if mode == "deficiency" and _looks_truncated(draft):
            return DEFICIENCY_TRUNCATED_RETRY
        return _checked(llm, draft)

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
