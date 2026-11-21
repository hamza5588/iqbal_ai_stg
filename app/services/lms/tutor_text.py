"""Text helpers for the LMS practice tutor (Learning Chat / AI Guided Practice).

DIL feedback 17-11-2026:
* Urdu: "explain it in urdu" got "Sorry, I could not finish that answer", and Urdu
  replies rendered without spaces in a sideways-scrolling box.
* Math: replies showed raw ``\\(`` / ``$`` (nested or unbalanced delimiters), stopped
  mid-sentence ("try this tiny version: if (x"), and talked about "the PDF".

``reply_language`` works out which language the student wants (and keeps it across
turns until they switch back); ``tidy_reply`` makes the model's math delimiters
well-formed and removes internal wording before a reply is stored or shown.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional

# Arabic-script letters (Urdu uses the Arabic block plus presentation forms).
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_WANTS_URDU_RE = re.compile(
    r"\burdu\b|اردو|\burdo\b", re.I
)
_WANTS_ENGLISH_RE = re.compile(r"\benglish\b|\bangrezi\b|انگریزی|انگلش", re.I)
# Common Roman-Urdu words; two or more distinct hits = the student writes Roman Urdu.
_ROMAN_URDU_WORDS = {
    "kya", "kia", "hai", "hain", "nahi", "nahin", "mujhe", "muje", "samajh", "samjh", "samaj",
    "kaise", "kaisay", "kyun", "kyon", "kiun", "batao", "bataen", "bataein", "btao", "ap", "aap",
    "mein", "main", "ko", "ka", "ki", "ke", "yeh", "ye", "woh", "wo", "karo", "kro", "karen",
    "kesay", "kese", "ho", "hota", "hoti", "sawal", "jawab", "tareeqa", "tarika", "phir", "abhi",
    "thora", "thoda", "aur", "lekin", "agar", "matlab", "galat", "sahi", "theek", "acha", "achha",
}


def arabic_letter_count(text: Optional[str]) -> int:
    return len(_ARABIC_RE.findall(text or ""))


def is_urdu_script(text: Optional[str]) -> bool:
    t = text or ""
    ar = arabic_letter_count(t)
    return ar >= 3 and ar >= len(re.findall(r"[A-Za-z]", t)) * 0.5


def _message_language(text: Optional[str]) -> Optional[str]:
    """Language this one message asks for, or None when it does not say."""
    t = (text or "").strip()
    if not t:
        return None
    wants_en = bool(_WANTS_ENGLISH_RE.search(t))
    wants_ur = bool(_WANTS_URDU_RE.search(t))
    if wants_en and not wants_ur:
        return "english"
    if wants_ur and not wants_en:
        # "roman urdu mein samjhao" -> Roman Urdu, otherwise Urdu script.
        return "roman_urdu" if re.search(r"\broman\b", t, re.I) else "urdu"
    if is_urdu_script(t):
        return "urdu"
    words = set(re.findall(r"[a-z]+", t.lower()))
    if len(words & _ROMAN_URDU_WORDS) >= 2:
        return "roman_urdu"
    return None


def reply_language(message: Optional[str], history: Optional[Iterable[Dict[str, str]]] = None) -> str:
    """'urdu' | 'roman_urdu' | 'english' for the next tutor reply.

    The latest message wins when it states or shows a language; otherwise the last
    language the student chose stays (DIL: after "explain it in urdu", tapping the
    English "Simpler words" chip must not switch the tutor back to English).
    """
    lang = _message_language(message)
    if lang:
        return lang
    for turn in reversed(list(history or [])):
        if (turn.get("role") or "user") != "user":
            continue
        lang = _message_language(turn.get("content"))
        if lang:
            return lang
    return "english"


LANGUAGE_RULES = {
    "urdu": (
        "LANGUAGE: Reply ONLY in clear, simple Urdu written in Urdu script (اردو), even if the "
        "student's latest message is an English button such as \"Simpler words\" - they asked for "
        "Urdu earlier. Use easy everyday Urdu a grade 9 student in Pakistan understands; for a "
        "maths term give the common Urdu word and, once, the English term in brackets, e.g. "
        "اجزائے ضربی (factors), مساوات (equation), قوت نما (exponent). Keep every maths expression, "
        "number and variable in normal maths notation inside \\( \\), e.g. \\(a^3 + b^3\\) - never "
        "write the Urdu words inside the math. Finish every sentence with ۔ or ؟"
    ),
    "roman_urdu": (
        "LANGUAGE: Reply in simple Roman Urdu (Urdu words in English letters, e.g. \"Pehle dono "
        "terms ka common factor nikalo\"), the way the student writes. Keep maths in normal notation."
    ),
    "english": "LANGUAGE: Reply in simple English.",
}

MATH_FORMAT_RULE = (
    "MATH FORMAT: Write each maths expression inside \\( and \\), e.g. \\(x^2 + 2x + 1\\) or "
    "\\(\\frac{1}{x}\\). Never use $ signs. Never put ordinary words inside the math - write "
    "\"multiply the coefficient of \\(x^2\\) by the constant term\", not the words inside \\( \\). "
    "Never put \\( inside another \\( \\); use plain ( ) brackets inside maths. Close every "
    "\\( you open in the same sentence."
)

STUDENT_WORDING_RULE = (
    "WORDING: The student never sees the source text you were given - never mention "
    "\"the PDF\", \"the excerpt\", \"the context\", \"the target\" or question numbers from it; "
    "say \"your book\" or just explain. Use short sentences and everyday words; explain a "
    "maths term the first time you use it."
)


# -- reply clean-up ---------------------------------------------------------------

_MATH_COMMAND_WORDS = {
    "frac", "dfrac", "tfrac", "sqrt", "times", "cdot", "div", "pm", "mp", "left", "right", "big",
    "bigl", "bigr", "text", "mathrm", "log", "ln", "sin", "cos", "tan", "neq", "leq", "geq",
    "quad", "qquad", "infty", "alpha", "beta", "theta", "pi", "over", "approx", "equiv", "ldots",
    "cdots", "dots", "mathbb", "boldsymbol", "mathbf", "operatorname", "begin", "end", "aligned",
}
# Words that may legitimately sit inside math (function names, "and" in sets ...).
_MATH_OK_WORDS = {"log", "ln", "sin", "cos", "tan", "lim", "max", "min", "mod", "and", "or"}


def _body_has_prose(body: str) -> bool:
    """True when a math span holds ordinary words (they render as squashed italics)."""
    if _ARABIC_RE.search(body):
        return True
    stripped = re.sub(r"\\text\{[^{}]*\}", " ", body)  # \text{...} is fine inside math
    stripped = re.sub(r"\\[A-Za-z]+", " ", stripped)
    words = [w for w in re.findall(r"[A-Za-z]{4,}", stripped) if w.lower() not in _MATH_OK_WORDS]
    return bool(words)


# A whitespace token that is maths ("x^2", "\times", "2x", "=", "(a+b)^{3}") vs prose.
_STRONG_MATH_TOKEN = re.compile(r"[\^_]|\\[A-Za-z]+")
_MATH_TOKEN = re.compile(r"^[()\[\]]*(?:[A-Za-z]|\d+(?:\.\d+)?[A-Za-z]?|[=+\-−×÷<>≤≥±*/·]|\\[A-Za-z]+(?:\{[^\s{}]*\})*"
                         r"|[A-Za-z\d()]*[\^_](?:\{[^\s{}]*\}|[A-Za-z\d]+)[A-Za-z\d()^_{}]*)[()\[\].,;:?!]*$")


def _rewrap_math_runs(text: str) -> str:
    """Wrap each run of maths tokens that has a ^, _ or \\command in \\( \\)."""
    tokens = re.split(r"(\s+)", text)
    out: List[str] = []
    run: List[str] = []

    def flush():
        if not run:
            return
        words = [t for t in run if not t.isspace()]
        if any(_STRONG_MATH_TOKEN.search(w) for w in words):
            core = "".join(run).strip()
            trail = re.search(r"[.,;:?!]+$", core)
            tail = trail.group(0) if trail else ""
            core = core[: len(core) - len(tail)] if tail else core
            out.append("\\(" + core + "\\)" + tail + ("".join(run)[len("".join(run).rstrip()):]))
        else:
            out.append("".join(run))
        run.clear()

    for tok in tokens:
        if tok.isspace():
            (run if run else out).append(tok)
        elif _MATH_TOKEN.match(tok) and not _ARABIC_RE.search(tok) and not (
            re.fullmatch(r"[A-Za-z]", tok) and not run and tok.lower() in ("a", "i")
        ):
            run.append(tok)
        else:
            flush()
            out.append(tok)
    flush()
    return "".join(out)


def _unwrap_body(body: str) -> str:
    body = re.sub(r"\\(?:text|mathrm)\{([^{}]*)\}", r"\1", body).replace("$", "")
    # Spacing commands mean nothing outside maths (live: "a+b = 7 \qquadاور\qquad a-b = 3").
    body = re.sub(r"\s*\\(?:qquad|quad|[,;:!])\s*", " ", body)
    return _rewrap_math_runs(body).strip()


def fix_math_delimiters(text: str) -> str:
    """Make inline/display math well-formed for MathJax.

    * ``$...$`` -> ``\\(...\\)`` and ``$$...$$`` -> ``\\[...\\]`` (one delimiter style);
    * ``\\(`` inside an open ``\\(`` becomes ``(`` (live: "Think of \\(x - 1/x\\)^2");
    * a stray closer becomes ``)``; an unclosed span is closed at the end;
    * a math span holding prose/Urdu words is unwrapped to plain text.
    """
    if not text:
        return text
    s = text
    # $$..$$ then $..$ (only balanced pairs on one logical span).
    s = re.sub(r"\$\$([\s\S]+?)\$\$", lambda m: "\\[" + m.group(1) + "\\]", s)
    s = re.sub(r"(?<![\\\w])\$([^$\n]+?)\$", lambda m: "\\(" + m.group(1) + "\\)", s)

    out: List[str] = []
    i, n = 0, len(s)
    mode: Optional[str] = None  # None | "(" | "["
    depth = 0
    start = 0
    while i < n:
        two = s[i:i + 2]
        if two in ("\\(", "\\["):
            if mode is None:
                out.append(s[start:i])
                mode, depth, start = two[1], 1, i + 2
            else:  # nested opener (either kind) -> plain bracket
                if two[1] == mode:
                    depth += 1
                s = s[:i] + two[1] + s[i + 2:]
                n = len(s)
                continue
            i += 2
            continue
        if two in ("\\)", "\\]"):
            want = ")" if mode == "(" else "]"
            if mode is not None and two[1] != want:  # other kind inside math -> plain bracket
                s = s[:i] + two[1] + s[i + 2:]
                n = len(s)
                continue
            if mode is not None and two[1] == want:
                if depth > 1:  # closing a nested opener -> plain bracket
                    s = s[:i] + want + s[i + 2:]
                    n = len(s)
                    depth -= 1
                    continue
                body = s[start:i]
                end = i + 2
                # "\(x - 1/x\)^2": the model used the delimiters as brackets; keep the power.
                power = re.match(r"\s*\^\s*(\{[^{}]*\}|-?\d+|[A-Za-z])", s[end:]) if mode == "(" else None
                if power and not _body_has_prose(body):
                    body = "(" + body + ")^" + power.group(1)
                    end += power.end()
                if _body_has_prose(body):
                    out.append(_unwrap_body(body))
                elif body.strip():
                    out.append(("\\(" if mode == "(" else "\\[") + body.replace("$", "") + two)
                mode, depth, start = None, 0, end
                i = end
                continue
            if mode is None:  # stray closer
                out.append(s[start:i] + two[1])
                start = i + 2
            i += 2
            continue
        i += 1
    tail = s[start:]
    if mode is not None:
        body = tail
        if _body_has_prose(body) or not body.strip():
            out.append(_unwrap_body(body))
        else:
            out.append(("\\(" if mode == "(" else "\\[") + body.replace("$", "") + ("\\)" if mode == "(" else "\\]"))
    else:
        out.append(tail)
    return "".join(out)


_PDF_PHRASES = [
    (re.compile(r"\b(?:from|in|on)\s+(?:the|your)\s+(?:target\s+)?pdf(?:\s+excerpt)?(?:\s*\([^)]*\))?", re.I), "in your book"),
    (re.compile(r"\b(?:the|your)\s+(?:teacher'?s\s+)?(?:target\s+)?pdf(?:\s+excerpt)?\b", re.I), "your book"),
    (re.compile(r"\bthe\s+(?:pdf\s+)?excerpt\b", re.I), "your book"),
    (re.compile(r"\bpdf\b", re.I), "book"),
]


def tidy_reply(text: Optional[str]) -> str:
    """Student-facing clean-up of a tutor reply (idempotent)."""
    t = (text or "").strip()
    if not t:
        return t
    # JSON/tool-call residue at the end of a reply (live: '... in the PDF?"}').
    t = re.sub(r'\s*"\s*\}+\s*$', "", t)
    for rx, repl in _PDF_PHRASES:
        t = rx.sub(repl, t)
    t = fix_math_delimiters(t)
    return t.strip()


# -- truncation ---------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?۔؟](?:[\"'”’)\]*]*)(?=\s|$)")


def unbalanced_brackets(text: str) -> bool:
    """More "(" than ")" outside LaTeX delimiters - e.g. "try this tiny version: if (x"."""
    t = re.sub(r"\\[()\[\]]", "", text or "")
    return t.count("(") > t.count(")")


def trim_to_last_sentence(text: str, min_ratio: float = 0.4) -> Optional[str]:
    """The reply up to its last complete sentence, when that keeps most of it."""
    t = (text or "").rstrip()
    ends = list(_SENTENCE_END_RE.finditer(t))
    if not ends:
        return None
    cut = t[: ends[-1].end()].rstrip()
    if len(cut) < max(30, int(len(t) * min_ratio)):
        return None
    if cut.count("\\(") != cut.count("\\)") or cut.count("\\[") != cut.count("\\]"):
        return None
    return cut


# -- answer leak (math-aware) --------------------------------------------------------

_SUPERS = str.maketrans({"⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5",
                         "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9", "⁻": "^-"})


def compact_math(text: Optional[str]) -> str:
    """Comparable form of a maths string: "(a² − ab + b²)" == "(a^{2}-ab+b^{2})" == "(𝑎2 −𝑎𝑏+ 𝑏2)".

    PDF-extracted bank options use maths-italic letters (𝑎) and lose the "^" ("𝑎2"), so
    the comparison drops "^" and braces after NFKC (live E2E: an Urdu reply wrote
    "(a-b)(a^2+ab+b^2)" for option "(𝑎−𝑏)(𝑎2 + 𝑎𝑏+ 𝑏2)" and was not caught).
    """
    t = (text or "").translate(_SUPERS)
    t = unicodedata.normalize("NFKC", t)
    # Longest names first: "big" before "bigl" left "l(" behind and hid a live leak.
    t = re.sub(r"\\(?:left|right|[Bb]igg?[lr]?|quad|qquad|[,;:!]|\s)", "", t)
    t = re.sub(r"\\[()\[\]]|\$", "", t)
    t = t.replace("−", "-").replace("–", "-").replace("×", "*").replace("\\times", "*").replace("\\cdot", "*")
    t = re.sub(r"[\^{}]", "", t)
    return re.sub(r"\s+", "", t).lower()


def _paren_groups(text: str) -> List[str]:
    out, stack = [], []
    for i, ch in enumerate(text):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            j = stack.pop()
            out.append(text[j:i + 1])
    return out


def reveals_option_math(reply: str, options: List[dict], correct_index: Optional[int], stem: str = "") -> bool:
    """True when the reply writes out the correct option's distinctive maths.

    The phrase-based guard misses leaks such as an Urdu reply that builds
    "(a² - ab + b²)" - the factor that only the correct option has (live AIGP msg 3460).
    """
    if not reply or not isinstance(correct_index, int) or not (0 <= correct_index < len(options)):
        return False
    texts = [compact_math(o.get("text") or "") for o in options]
    correct = texts[correct_index]
    if re.fullmatch(r"-?\d+(?:[./]\d+)?", correct):
        # Short numeric key ("81"): a leak is the reply stating a result equal to it
        # ("3^4 = 81", "x is 81", "... برابر 81"), unless that number is in the stem.
        stem_numbers = set(re.findall(r"\d+(?:[./]\d+)?", compact_math(stem)))
        if correct.lstrip("-") in stem_numbers:
            return False
        num = re.escape(correct)
        plain = unicodedata.normalize("NFKC", reply)
        return bool(
            re.search(rf"(?:=|equals?|\bis\b|\bbe\b|برابر)\s*(?:\\[()\[\]]|\$|\{{|\s)*{num}(?!\d|[./]\d)", plain, re.I)
            or re.search(rf"(?<![\d./^]){num}(?:\\[()\[\]]|\$|\}}|\s)*(?:ہے|ہوگا|بنتا|آتا)", plain)
        )
    if len(correct) < 4:
        return False
    others = [t for k, t in enumerate(texts) if k != correct_index]
    stem_c = compact_math(stem)
    body = compact_math(reply)
    if len(correct) >= 6 and correct in body and correct not in stem_c and not any(correct in o for o in others):
        return True
    for g in _paren_groups(correct):
        if len(g) < 7 or g in stem_c or any(g in o for o in others):
            continue
        if g in body:
            return True
    return False


ANSWER_GUARD_REPLIES = {
    "english": "Let's work it out without naming the answer. Which rule from your book could you use to test your choice?",
    "urdu": "آئیے جواب بتائے بغیر اسے خود حل کریں۔ اپنی کتاب کا کون سا اصول اپنے انتخاب کو جانچنے میں مدد دے سکتا ہے؟",
    "roman_urdu": "Chaliye jawab bataye baghair isay khud hal karte hain. Apni kitab ka kaunsa usool apna jawab check karne mein madad dega?",
}


def answer_guard_reply(message: Optional[str], history: Optional[Iterable[Dict[str, str]]] = None) -> str:
    """The last-resort "won't name the answer" reply, in the student's language."""
    return ANSWER_GUARD_REPLIES[reply_language(message, history)]


_REFUSAL_RE = re.compile(
    r"^\W*(?:i['’]?m\s+sorry|i\s+am\s+sorry|sorry|unfortunately|i\s+(?:can['’]?t|cannot|won['’]?t|am\s+unable|'m\s+unable)"
    r"|معذرت|معاف\s+کیجیے|میں\s+یہ\s+نہیں)",
    re.I,
)


def is_bare_refusal(text: Optional[str]) -> bool:
    """A short reply that only refuses ("I'm sorry, but I can't provide that.") with no help in it."""
    t = (text or "").strip()
    if not t or len(t) > 160:
        return False
    if not _REFUSAL_RE.search(t):
        return False
    # a refusal that also carries a hint (maths, a question back, "hint") is fine
    return not re.search(r"\\\(|\^|=|\bhint\b|\btry\b|\bthink\b|\?|؟|اشارہ", t, re.I)
