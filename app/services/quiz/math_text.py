"""Recover exam-PDF math (exponents, fractions) into LaTeX without inventing answers."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

_UNICODE_SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾", "0123456789+-()")
_SUPER_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾"
_FUNC_NAMES = (
    "log", "ln", "sin", "cos", "tan", "sec", "csc", "cot",
    "exp", "lim", "max", "min", "gcd", "lcm", "mod", "abs",
)
_INSTRUCTION_RE = re.compile(
    r"^(simplify|find|solve|evaluate|compute|expand|factor|simplify\s+fully)\s*:?\s*$",
    re.I,
)
_INSTRUCTION_PREFIX_RE = re.compile(
    r"^((?:simplify|find|solve|evaluate|compute|expand|factor)\s*:)\s*(.*)$",
    re.I,
)
_ALREADY_LATEX_RE = re.compile(r"\\(frac|sqrt|cdot|times|left|right|overline)|[\^_]")
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_MATH_TOKEN_RE = re.compile(r"[A-Za-z0-9]")
_LONG_WORD_RE = re.compile(r"[A-Za-z]{8,}")
_SMASHED_BLOB_RE = re.compile(r"[A-Za-z]{10,}")

# Longest-first exam English used to restore spaces PDF extraction smashed.
_EXAM_WORDS = tuple(
    sorted(
        {
            "factorization", "factorisation", "polynomials", "polynomial",
            "expressions", "expression", "statements", "statement",
            "coefficients", "coefficient", "identities", "identity",
            "equations", "equation", "fractions", "fraction", "decimals",
            "decimal", "integers", "integer", "numbers", "number",
            "incorrect", "correct", "following", "repeating", "terminating",
            "irrational", "rational", "quadratic", "standard", "simplify",
            "simplified", "evaluate", "compute", "expand", "factor",
            "degree", "product", "difference", "quotient", "remainder",
            "equivalent", "positive", "negative", "greatest", "greater",
            "choose", "select", "between", "without", "linear", "cubic",
            "prime", "composite", "complex", "constant", "variable",
            "which", "what", "where", "when", "this", "that", "these",
            "those", "each", "both", "only", "also", "true", "false",
            "none", "find", "solve", "given", "below", "above", "after",
            "before", "over", "under", "into", "onto", "from", "with",
            "than", "then", "such", "must", "does", "have", "has",
            "was", "were", "are", "the", "and", "for", "not", "its",
            "of", "is", "in", "or", "an", "to", "if",
        },
        key=lambda w: (-len(w), w),
    )
)


def _unicode_supers_to_latex(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] in _SUPER_CHARS:
            j = i
            while j < n and text[j] in _SUPER_CHARS:
                j += 1
            body = text[i:j].translate(_UNICODE_SUPER)
            out.append("^{" + body + "}")
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _is_function_name(text: str, letter_index: int) -> bool:
    start = letter_index
    while start > 0 and text[start - 1].isalpha():
        start -= 1
    word = text[start : letter_index + 1].lower()
    return word in _FUNC_NAMES or any(word.endswith(fn) for fn in _FUNC_NAMES)


def implicit_exponents_to_latex(text: str) -> str:
    """Turn flattened PDF exponents (x2, a3b2) into TeX (x^{2}, a^{3}b^{2})."""
    if not text:
        return text

    def repl(match: re.Match[str]) -> str:
        letter, digits = match.group(1), match.group(2)
        if _is_function_name(match.string, match.start(1)):
            return match.group(0)
        return f"{letter}^{{{digits}}}"

    return re.sub(r"([A-Za-z])(?!\^)(\d+)", repl, text)


def unsquash_english(text: str) -> str:
    """Restore spaces in smashed exam English: Whichisthecorrect... → Which is the correct..."""

    def segment_blob(blob: str) -> str:
        lower = blob.lower()
        n = len(lower)
        parts: list[str] = []
        i = 0
        while i < n:
            matched = None
            for word in _EXAM_WORDS:
                if lower.startswith(word, i):
                    matched = word
                    break
            if matched is None:
                rest = blob[i:]
                if not parts:
                    return blob
                return " ".join(parts) + " " + rest
            parts.append(blob[i : i + len(matched)])
            i += len(matched)
        return " ".join(parts)

    spaced = _SMASHED_BLOB_RE.sub(lambda m: segment_blob(m.group(0)), text or "")
    return re.sub(r"([A-Za-z]{4,})(\d)", r"\1 \2", spaced)


def unwrap_outer_math_if_prose(text: str) -> str:
    """Drop wrapping \\( \\) / $ $ around an English sentence (math mode eats spaces)."""
    s = (text or "").strip()
    inner = None
    if s.startswith("\\(") and s.endswith("\\)") and len(s) > 4:
        inner = s[2:-2].strip()
    elif s.startswith("\\[") and s.endswith("\\]") and len(s) > 4:
        inner = s[2:-2].strip()
    elif s.startswith("$$") and s.endswith("$$") and len(s) > 4:
        inner = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$") and len(s) > 2 and not s.startswith("$$"):
        inner = s[1:-1].strip()
    if inner is None:
        return s
    expanded = unsquash_english(inner)
    if looks_like_prose(expanded):
        return expanded
    return s


def looks_like_math_line(text: str) -> bool:
    s = unsquash_english((text or "").strip())
    if not s or len(s) > 120:
        return False
    if _INSTRUCTION_RE.match(s):
        return False
    if looks_like_prose(s):
        return False
    if _LONG_WORD_RE.search(s) and not _ALREADY_LATEX_RE.search(s):
        return False
    words = _ENGLISH_WORD_RE.findall(s)
    math_words = {"frac", "sqrt", "cdot", "left", "right", "text", "over", "times"}
    if words and not all(w.lower() in math_words or w.lower() in _FUNC_NAMES for w in words):
        if len(words) >= 2:
            return False
    return bool(_MATH_TOKEN_RE.search(s)) and (
        bool(re.search(r"[=\+\-×÷·/^_\\()]", s))
        or bool(re.search(r"[A-Za-z]\d|\d[A-Za-z]|[A-Za-z]\^", s))
        or bool(re.search(r"[A-Za-z]{1,3}\d+[A-Za-z]{0,3}", s))
    )


def recover_stacked_fraction(text: str) -> str:
    """Turn a numerator/denominator split across lines into \\frac{num}{den}."""
    raw_lines = [ln.strip() for ln in (text or "").replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in raw_lines if ln]
    if len(lines) < 2:
        return text

    prefix_parts = []
    math_lines = []
    for ln in lines:
        if _INSTRUCTION_RE.match(ln) and not math_lines:
            prefix_parts.append(ln.rstrip(":") + ":")
            continue
        prefixed = _INSTRUCTION_PREFIX_RE.match(ln)
        if prefixed and not math_lines:
            prefix_parts.append(prefixed.group(1).rstrip() + ("" if prefixed.group(1).endswith(":") else ":"))
            rest = (prefixed.group(2) or "").strip()
            if rest:
                math_lines.append(rest)
            continue
        math_lines.append(ln)

    if len(math_lines) == 2 and looks_like_math_line(math_lines[0]) and looks_like_math_line(math_lines[1]):
        num, den = math_lines[0], math_lines[1]
        if "\\frac" not in num and "\\frac" not in den:
            frac = f"\\frac{{{num}}}{{{den}}}"
            if prefix_parts:
                return prefix_parts[0] + " " + frac
            return frac
    return text


def recover_latex(text: Optional[str]) -> str:
    """Best-effort LaTeX body (no delimiters) from flattened PDF / stored MCQ text."""
    s = (text or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = unwrap_outer_math_if_prose(s)
    s = unsquash_english(s)
    s = s.replace("−", "-").replace("×", "\\times ").replace("÷", "\\div ")
    s = _unicode_supers_to_latex(s)
    s = implicit_exponents_to_latex(s)
    s = recover_stacked_fraction(s)
    s = strip_inner_math_delims(s)
    s = normalize_mixed_percents(s)
    s = re.sub(r"([0-9√π∞°}%])([A-Za-z]{3,})", r"\1 \2", s)
    s = re.sub(r"(\})([A-Za-z]{3,})", r"\1 \2", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


_MIXED_FRAC_PCT_RE = re.compile(r"(\d+)\s*\\frac\{(\d+)\}\{(\d+)\}\s*\\?%")
_MIXED_SPACED_PCT_RE = re.compile(r"(\d+)\s+(\d+)\s+(\d+)\s*%")


def normalize_mixed_percents(text: str) -> str:
    """Keep 16 2/3% as a visible slash, not a stacked fraction or 16 2 3 %."""
    s = _MIXED_FRAC_PCT_RE.sub(r"\1 \2/\3%", text or "")
    s = _MIXED_SPACED_PCT_RE.sub(r"\1 \2/\3%", s)
    return s


def _strip_math_delims(s: str) -> str:
    for tok in ("\\(", "\\)", "\\[", "\\]"):
        s = s.replace(tok, "")
    return s


def _read_brace_group(s: str, start: int) -> Tuple[str, int]:
    """Read `{...}` starting at start. Returns (inner, index after closing brace)."""
    depth = 0
    i = start
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i + 1
        i += 1
    return s[start + 1 :], n


def strip_inner_math_delims(text: str) -> str:
    """Remove nested \\( \\) inside \\frac{...} that render as red leftover symbols."""
    s = text or ""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s.startswith("\\frac", i):
            j = i + 5
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j] == "{":
                num, j = _read_brace_group(s, j)
                while j < n and s[j].isspace():
                    j += 1
                if j < n and s[j] == "{":
                    den, j = _read_brace_group(s, j)
                    num = strip_inner_math_delims(_strip_math_delims(num))
                    den = strip_inner_math_delims(_strip_math_delims(den))
                    out.append(f"\\frac{{{num}}}{{{den}}}")
                    i = j
                    continue
        out.append(s[i])
        i += 1
    return "".join(out)


_MATH_VOCAB = {
    "frac", "sqrt", "cdot", "left", "right", "text", "over", "times",
    "log", "ln", "sin", "cos", "tan", "sec", "csc", "cot", "exp", "lim",
    "max", "min", "gcd", "lcm", "mod", "abs", "simplify",
}


def looks_like_prose(text: str) -> bool:
    """True when a string is an English sentence, not a pure math expression."""
    expanded = unsquash_english(text or "")
    words = _ENGLISH_WORD_RE.findall(expanded)
    real = [w for w in words if w.lower() not in _MATH_VOCAB]
    return len(real) >= 3


def recover_fields(text: Optional[str], latex: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return (display_text, latex) with reconstructed math for diagnostic MCQs."""
    text_s = unsquash_english(unicodedata.normalize("NFKC", (text or "").strip()))
    text_s = unwrap_outer_math_if_prose(text_s)
    latex_s = (latex or "").strip() or None
    if latex_s:
        latex_s = unwrap_outer_math_if_prose(unsquash_english(unicodedata.normalize("NFKC", latex_s)))
    if looks_like_prose(text_s):
        recovered_text = recover_latex(text_s)
        return recovered_text or text_s, None

    recovered = recover_latex(latex_s or text_s)
    if not recovered:
        recovered = recover_latex(text_s)
    if not recovered:
        return text_s, latex_s

    has_tex = "\\frac" in recovered or "^{" in recovered or "\\times" in recovered
    if has_tex and not looks_like_prose(recovered):
        return recovered, recovered
    if latex_s and ("\\frac" in latex_s or "^{" in latex_s or "^" in latex_s) and not looks_like_prose(text_s):
        return text_s or latex_s, latex_s
    return recovered or text_s, latex_s


def option_needs_math(text: str) -> bool:
    recovered = recover_latex(text)
    return bool(recovered) and ("^{" in recovered or "\\frac" in recovered or looks_like_math_line(recovered))
