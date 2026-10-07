"""Recover exam-PDF math (exponents, fractions) into LaTeX without inventing answers."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

# Some LLM structured-output round trips mis-escape a literal backslash
# before a LaTeX command as a JSON control-character escape - e.g. the model
# writes `\frac{1}{2}` inside a JSON string field, but a spec-compliant JSON
# parser reads "\f" as U+000C (form feed) per the JSON escape table, eating
# the backslash and the f: `\frac` -> "\x0crac". Found live via E2E testing
# an AI-generated diagnostic variant question ("\frac{3}{7} \times \frac{2}
# {5}" arrived as "\x0crac{3}{7} \x09imes \x0crac{2}{5}"). Restore the
# handful of common math commands this happens to; every other control
# character is left alone since it's far more likely a real backslash never
# belonged there.
# Longest-first: \begin / \biggl must win over shorter \big / \beta prefixes.
_EATEN_BACKSLASH_COMMANDS = (
    ("\x08egin", "\\begin"),     # \b + egin <- \begin
    ("\x08iggl", "\\biggl"),     # \b + iggl <- \biggl
    ("\x08iggr", "\\biggr"),
    ("\x08igl", "\\bigl"),
    ("\x08igr", "\\bigr"),
    ("\x08igg", "\\bigg"),
    ("\x08ig", "\\big"),         # \b + ig   <- \big  (MathJax "Math input error" on stems)
    ("\x08eta", "\\beta"),       # \b + eta  <- \beta
    ("\x0crac", "\\frac"),       # \f + rac  <- \frac
    ("\x09imes", "\\times"),     # \t + imes <- \times
    ("\x09heta", "\\theta"),     # \t + heta <- \theta
    ("\x09ext", "\\text"),       # \t + ext  <- \text
    ("\x0bec", "\\vec"),         # \v + ec   <- \vec
)


def recover_eaten_backslash_commands(text: str) -> str:
    if not text or not any(c in text for c in "\x08\x09\x0b\x0c"):
        return text
    for bad, good in _EATEN_BACKSLASH_COMMANDS:
        text = text.replace(bad, good)
    return text


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
    """Turn flattened PDF exponents (x2, a3b2, (x-1)2) into TeX
    (x^{2}, a^{3}b^{2}, (x-1)^{2})."""
    if not text:
        return text

    def repl(match: re.Match[str]) -> str:
        base, digits = match.group(1), match.group(2)
        if base.isalpha():
            # The letters may be a TeX command (the math editor writes "\neq1", "\pm5", "\times10"
            # with no space): that digit is an operand, never a power - "a \neq^{1}" was saved.
            start = match.start(1)
            while start > 0 and match.string[start - 1].isalpha():
                start -= 1
            if start > 0 and match.string[start - 1] == "\\":
                return f"{base} {digits}"
            if _is_function_name(match.string, match.start(1)):
                return match.group(0)
        return f"{base}^{{{digits}}}"

    # letter/paren/brace/bracket followed directly by digits and NOT already
    # an exponent - e.g. "x2", "(3x-2)2", "}2"
    return re.sub(r"([A-Za-z\)\]\}])(?!\^)(\d+)", repl, text)


# NOTE: The emergency helpers ``recover_spaced_power``,
# ``recover_scientific_notation`` and ``recover_log_subscripts`` used to live
# here as a temporary patch for flattened PDF superscripts/subscripts. They
# have been removed - the new ``document_parser`` layer reconstructs
# exponents / subscripts / scientific notation from PDF font geometry (span
# size + baseline origin) or from PP-FormulaNet output, not from text-level
# regex guesses. See report.txt (Phase 3) and the ``NativePyMuPDFParser``
# implementation for the replacement.


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
        # A "denominator" of a bare 1 is a strong signal this isn't really a
        # stacked fraction at all (nobody typesets "expression / 1" as a
        # fraction) - more likely a radical/vinculum artifact (e.g. a sqrt's
        # bar or an unrelated stray "1" glyph) from PDF text extraction
        # getting misread as a fraction denominator. Found live: a
        # distance-formula sqrt((4-1)^2+(6-2)^2) coming out as a bare
        # "\frac{(4-1)^2+(6-2)^2}{1}". Leave such lines unmerged rather than
        # guess what the real numerator/denominator relationship should be.
        if den.strip() == "1" or num.strip() == "1":
            return text
        if "\\frac" not in num and "\\frac" not in den:
            frac = f"\\frac{{{num}}}{{{den}}}"
            if prefix_parts:
                return prefix_parts[0] + " " + frac
            return frac
    return text


def promote_log_underscore(text: str) -> str:
    """``log_a 8`` → ``\\log_{a} 8`` so the base typesets as a subscript.

    Does not rematch already-TeX ``\\log`` (that would double the backslash).
    """

    def _repl(m: re.Match[str]) -> str:
        fn = (m.group(2) or "log").lower()
        base = m.group(3)
        cmd = "\\ln" if fn == "ln" else "\\log"
        return f"{m.group(1)}{cmd}_{{{base}}}"

    return re.sub(
        r"(^|[^\\A-Za-z0-9_])(log|ln)\s*_\{?([A-Za-z0-9]+)\}?",
        _repl,
        text or "",
        flags=re.IGNORECASE,
    )


def promote_slash_fractions(text: str) -> str:
    """Convert printable ``a/(b)`` and ``3/2`` into ``\\frac`` (horizontal bar).

    Leaves mixed percents like ``16 2/3%`` alone.
    """
    s = text or ""
    if _PLAIN_PERCENT_RE.match(s.strip()):
        return s
    if re.search(r"\d+\s+\d+\s*/\s*\d+\s*%", s) or re.match(r"^\s*\d+\s*/\s*\d+\s*%", s):
        return s

    def _paren_paren(m: re.Match[str]) -> str:
        if "%" in m.group(0):
            return m.group(0)
        return f"\\frac{{{m.group(1).strip()}}}{{{m.group(2).strip()}}}"

    def _atom_paren(m: re.Match[str]) -> str:
        if "%" in m.group(0):
            return m.group(0)
        return f"\\frac{{{m.group(1).strip()}}}{{{m.group(2).strip()}}}"

    def _simple(m: re.Match[str]) -> str:
        return f"{m.group(1)}\\frac{{{m.group(2)}}}{{{m.group(3)}}}"

    s = re.sub(r"\(([^()]{1,80})\)\s*/\s*\(([^()]{1,80})\)", _paren_paren, s)
    s = re.sub(
        r"((?:-?\d*[A-Za-z](?:\^\{[^}]+\}|\^[A-Za-z0-9]+|\d+)*|-?\d+(?:\.\d+)?))"
        r"\s*/\s*\(([^()]{1,80})\)",
        _atom_paren,
        s,
    )
    s = re.sub(
        r"(^|[^0-9A-Za-z./])(\d+)\s*/\s*(\d+)(?!\s*%)(?![0-9A-Za-z])",
        _simple,
        s,
    )
    return s


def promote_inline_math_notation(text: str) -> str:
    """Promote slash fractions + log_base so students see stacked frac / subscripts."""
    return promote_slash_fractions(promote_log_underscore(text or ""))


def recover_latex(text: Optional[str]) -> str:
    """Best-effort LaTeX body (no delimiters) from flattened PDF / stored MCQ text."""
    # Repair BEFORE strip(): a form-feed/tab/etc. artifact at the very start
    # or end of the string is whitespace as far as str.strip() is concerned,
    # so stripping first would delete the very character the repair needs.
    s = recover_eaten_backslash_commands(text or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = unwrap_outer_math_if_prose(s)
    s = unsquash_english(s)
    s = s.replace("−", "-").replace("×", "\\times ").replace("÷", "\\div ")
    s = _unicode_supers_to_latex(s)
    s = implicit_exponents_to_latex(s)
    # Explicit powers on groups: (3x-2)^2 → (3x-2)^{2}
    s = re.sub(r"(\))\s*\^\s*(\d+)\b", r"\1^{\2}", s)
    s = recover_stacked_fraction(s)
    s = promote_inline_math_notation(s)
    s = strip_inner_math_delims(s)
    s = strip_english_dollar_spans(s)
    s = normalize_mixed_percents(s)
    s = normalize_plain_ellipsis(s)
    s = normalize_latex_spacing(s)
    s = normalize_tex_set_braces(s)
    s = dedupe_repeated_math(s)
    s = re.sub(r"([0-9√π∞°}%])([A-Za-z]{3,})", r"\1 \2", s)
    s = re.sub(r"(\})([A-Za-z]{3,})", r"\1 \2", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


_MIXED_FRAC_PCT_RE = re.compile(
    r"(\d+)\s*\\(?:t|d|c)?frac\s*\{(\d+)\}\s*\{(\d+)\}\s*\\?%"
)
_MIXED_SPACED_PCT_RE = re.compile(r"(\d+)\s+(\d+)\s+(\d+)\s*%")
# Flattened PDF / OCR form: "162/3%" → "16 2/3%", "331/3%" → "33 1/3%"
_SMASHED_MIXED_PCT_RE = re.compile(r"(?<![\d/])(\d{2,})/(\d+)\s*%")
_PLAIN_PERCENT_RE = re.compile(r"^\d+(?:\s+\d+\s*/\s*\d+)?\s*%$")
_UNICODE_VULGAR_FRAC = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def _unsquash_mixed_percent(match: re.Match[str]) -> str:
    """Split smashed whole+num/den percents into '16 2/3%' style."""
    left, den = match.group(1), match.group(2)
    try:
        den_i = int(den)
    except ValueError:
        return match.group(0)
    if den_i <= 0:
        return match.group(0)
    for num_digits in (1, 2):
        if len(left) <= num_digits:
            continue
        whole, num = left[:-num_digits], left[-num_digits:]
        if not whole or whole.startswith("0"):
            continue
        try:
            num_i = int(num)
        except ValueError:
            continue
        if num_i <= 0:
            continue
        # Prefer a proper fraction (num < den); allow single-digit nums with
        # small dens even when equal (rare) so 8/8-style stays smashed.
        if num_i < den_i:
            return f"{whole} {num}/{den}%"
    return match.group(0)


def normalize_mixed_percents(text: str) -> str:
    """Keep 16 2/3% as a visible slash, not a stacked fraction or 162/3%.

    Handles extractor variants: \\frac, \\tfrac, \\dfrac, \\%, smashed 162/3%.
    """
    s = text or ""
    for glyph, ascii_frac in _UNICODE_VULGAR_FRAC.items():
        s = s.replace(glyph, f" {ascii_frac}")
    s = s.replace("\\%", "%")
    # Drop optional \\mathrm / \\text wrappers around digits in percent latex
    s = re.sub(r"\\(?:mathrm|text|textrm|mathsf)\s*\{([^{}]+)\}", r"\1", s)
    s = _MIXED_FRAC_PCT_RE.sub(r"\1 \2/\3%", s)
    s = _MIXED_SPACED_PCT_RE.sub(r"\1 \2/\3%", s)
    s = _SMASHED_MIXED_PCT_RE.sub(_unsquash_mixed_percent, s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


# PDF prints plain "..." ; extractors often emit \ldots which MathJax never
# typesets when the stem is prose (no \(...\) wrappers).
_LATEX_ELLIPSIS_RE = re.compile(r"\\(?:ldots|cdots|dots)\b")
# "$non - terminating$" etc. — English wrapped in $ $ renders italic + spaced hyphens.
_ENGLISH_DOLLAR_SPAN_RE = re.compile(
    r"(?<![\\$])\$([A-Za-z][A-Za-z]*(?:\s*-\s*[A-Za-z]+)+)\$"
)


def normalize_plain_ellipsis(text: str) -> str:
    """Turn \\ldots / … into plain '...' so decimals match the printed PDF."""
    s = text or ""
    s = _LATEX_ELLIPSIS_RE.sub("...", s)
    s = s.replace("…", "...")
    # "18181818..." → "18181818 ..." (PDF spacing before the dots)
    s = re.sub(r"(\d)\.\.\.(?=\s|$|[^\d.])", r"\1 ...", s)
    # Collapse "5. 1818 ... ..." style doubles
    s = re.sub(r"(?:\.\.\.\s*){2,}", "... ", s)
    return re.sub(r"[ \t]+", " ", s).strip() if s else s


def strip_english_dollar_spans(text: str) -> str:
    """Unwrap $non - terminating$ style false math around hyphenated English."""

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        # Real math keeps digits / TeX; English hyphen compounds collapse.
        if re.search(r"[0-9\\^=_+]", inner):
            return match.group(0)
        return re.sub(r"\s*-\s*", "-", inner)

    return _ENGLISH_DOLLAR_SPAN_RE.sub(repl, text or "")


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
    """True when a string is an English sentence, not a pure math expression.

    TeX command names (``\\frac``, ``\\log``, …) are stripped before the word
    count so a stem like ``Why is \\frac{x+1}{x^2+4} a rational expression?``
    is not mistaken for pure math (which would wrap the *whole* sentence in
    ``\\[...\\]`` and glue words together in MathJax).
    """
    expanded = unsquash_english(text or "")
    # Drop TeX control words so "\frac" does not contribute the token "frac".
    stripped = re.sub(r"\\[A-Za-z]+\*?", " ", expanded)
    words = _ENGLISH_WORD_RE.findall(stripped)
    # Also accept 3-letter English words ("Why", "the", …) — the 4+ regex alone
    # under-counts short but clearly prosaic stems.
    short_words = re.findall(r"\b[A-Za-z]{3}\b", stripped)
    real = [w for w in words if w.lower() not in _MATH_VOCAB]
    real_short = [
        w for w in short_words
        if w.lower() not in _MATH_VOCAB and w.lower() not in {"and", "or", "for", "not"}
    ]
    return len(real) >= 2 or (len(real) >= 1 and len(real_short) >= 1) or len(real_short) >= 3


def _compact_math_key(s: str) -> str:
    """Normalize for containment checks (ignore spaces / $ / \\( \\) / spacing cmds)."""
    t = (s or "").strip()
    t = re.sub(r"\\\(|\\\)|\\\[|\\\]|\$+", "", t)
    # \, \; \: \! \quad \qquad ~ — must not block "already in stem" matches
    t = re.sub(r"\\(?:,|;|:|!|quad|qquad)\b|~", "", t)
    return re.sub(r"\s+", "", t)


def normalize_latex_spacing(text: str) -> str:
    """Turn TeX spacing into plain spaces so \\, / \\quad never show as raw text."""
    s = text or ""
    if not s:
        return s
    s = re.sub(r"\\,", " ", s)
    s = re.sub(r"\\;", " ", s)
    s = re.sub(r"\\:", " ", s)
    s = re.sub(r"\\!", "", s)
    s = re.sub(r"\\quad\b", " ", s)
    s = re.sub(r"\\qquad\b", "  ", s)
    s = s.replace("~", " ")
    s = re.sub(r",\s+", ", ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def normalize_tex_set_braces(text: str) -> str:
    """Convert TeX set braces \\{ \\} to plain { } for student display.

    Outside MathJax delimiters, ``\\{1, 2, 3\\}`` otherwise shows a visible
    backslash. Plain braces match printed set notation.
    """
    s = text or ""
    if "\\{" not in s and "\\}" not in s:
        return s
    # Leave content already inside \\( \\) / \\[ \\] / $...$ alone — MathJax
    # needs the backslash there. Process outside those spans.
    parts: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s.startswith("\\(", i) or s.startswith("\\[", i):
            end = s.find("\\)", i + 2) if s.startswith("\\(", i) else s.find("\\]", i + 2)
            if end < 0:
                parts.append(s[i:].replace("\\{", "{").replace("\\}", "}"))
                break
            close_len = 2
            parts.append(s[i : end + close_len])
            i = end + close_len
            continue
        if s[i] == "$":
            end = s.find("$", i + 1)
            if end < 0:
                parts.append(s[i:].replace("\\{", "{").replace("\\}", "}"))
                break
            parts.append(s[i : end + 1])
            i = end + 1
            continue
        # Plain segment until next math opener
        next_ops = [p for p in (s.find("\\(", i), s.find("\\[", i), s.find("$", i)) if p >= 0]
        j = min(next_ops) if next_ops else n
        parts.append(s[i:j].replace("\\{", "{").replace("\\}", "}"))
        i = j
    return "".join(parts)


def _first_half_matching_compact(chunk: str, target_compact: str) -> Optional[str]:
    """Grow a prefix of chunk until its compact math key equals target_compact."""
    if not chunk or not target_compact:
        return None
    acc: list[str] = []
    for ch in chunk:
        acc.append(ch)
        if _compact_math_key("".join(acc)) == target_compact:
            result = "".join(acc).strip().rstrip(",").strip()
            # Compact key ignores delimiters, so the cut may land before a
            # closing \) / \] / $ — close any we opened.
            if result.count("\\(") > result.count("\\)"):
                result += "\\)"
            if result.count("\\[") > result.count("\\]"):
                result += "\\]"
            if result.count("$") % 2 == 1:
                result += "$"
            return result
    return None


_SPOKEN_MATH_CUES = re.compile(
    r"\b(?:of x equals|x squared|squared plus|squared minus|log base|"
    r"a squared|b squared|find P of|plus 2x|minus 3)\b",
    re.I,
)
_SYMBOLIC_MATH_CUES = re.compile(
    r"P\s*\(|p\s*\(|\\log|[A-Za-z]\^\d|[A-Za-z]\^\{|\\frac|[A-Za-z]\^\d"
)


def collapse_spoken_symbolic_duplicate(text: str) -> str:
    """Drop spoken-English restatements when the stem already has the symbolic form.

    Hybrid vision sometimes emits both, e.g.
    ``If P of x equals x squared plus 2x minus 3, find P of 2.
    P(x) = x^2 + 2x - 3, find P(2)``
    while the PDF only has the symbolic sentence. Prefer the symbolic form.
    """
    s = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not s or not _SPOKEN_MATH_CUES.search(s):
        return s

    if ". " in s:
        head, _, tail = s.rpartition(". ")
        tail = tail.strip().rstrip(".")
        head_spoken = bool(_SPOKEN_MATH_CUES.search(head))
        tail_symbolic = bool(_SYMBOLIC_MATH_CUES.search(tail)) and not _SPOKEN_MATH_CUES.search(tail)
        if head_spoken and tail_symbolic:
            # Full symbolic question restatement (P(x)=..., find P(2))
            if re.search(r"P\s*\([^)]*\)\s*=", tail, re.I) and re.search(r"find\s*P\s*\(", tail, re.I):
                out = tail if re.match(r"if\b", tail, re.I) else f"If {tail}"
                return out.rstrip(".") + "."
            # Trailing expression only: "… a squared plus b squared. a^2 + b^2"
            if len(tail) <= 48:
                head2 = re.sub(
                    r"\bDetermine\s+a squared plus b squared\b",
                    f"Determine {tail}",
                    head,
                    flags=re.I,
                )
                head2 = re.sub(
                    r"\ba squared plus b squared\b",
                    tail,
                    head2,
                    flags=re.I,
                )
                if head2 != head:
                    return head2.rstrip(".") + "."
            # log base … equals … . \log_a 8 = ...
            if "log base" in head.lower() and "\\log" in tail:
                head2 = re.sub(
                    r"log base\s+[A-Za-z0-9' ]+?\s+of\s+[A-Za-z0-9]+\s+equals\s*"
                    r"(?:\\frac\{[^{}]+\}\{[^{}]+\}|[^\s,]+)",
                    tail.split(",")[0].strip(),
                    head,
                    flags=re.I,
                )
                if head2 != head:
                    return head2.rstrip(".") + "."
    return s


def dedupe_repeated_math(text: str) -> str:
    """Collapse back-to-back duplicate equation blocks already stuck in stored stems.

    e.g. ``...equations? x+y=9, x-y=3 x+y=9, x-y=3`` → one copy.
    """
    s = collapse_spoken_symbolic_duplicate((text or "").strip())
    if s.count("=") < 2:
        return s

    # Prefer splitting after the question's "?".
    m = re.search(r"^(.*\?)\s*(.+)$", s, re.S)
    if m:
        prefix, rest = m.group(1), m.group(2).strip()
        deduped = _dedupe_self_repeated_chunk(rest)
        if deduped != rest:
            return f"{prefix} {deduped}".strip()
        return s

    return _dedupe_self_repeated_chunk(s)


def _dedupe_self_repeated_chunk(chunk: str) -> str:
    compact = _compact_math_key(chunk)
    n = len(compact)
    if n < 6 or "=" not in compact:
        return chunk
    # Exact doubled compact key (AA).
    if n % 2 == 0:
        half = n // 2
        if compact[:half] == compact[half:] and "=" in compact[:half]:
            first = _first_half_matching_compact(chunk, compact[:half])
            if first:
                return first
    # Near-half (comma / spacing drift between copies).
    for half in range(max(3, n // 2 - 8), n // 2 + 9):
        if half * 2 > n:
            break
        if compact[:half] == compact[half : half * 2] and "=" in compact[:half]:
            if half * 2 == n or not compact[half * 2 :]:
                first = _first_half_matching_compact(chunk, compact[:half])
                if first:
                    return first
    return chunk


def math_already_in_stem(stem: str, math: str) -> bool:
    """True when the latex body is already present in the English stem."""
    key = _compact_math_key(math)
    if not key:
        return True
    hay = _compact_math_key(stem)
    if key in hay:
        return True
    # Flattened vs TeX: (3x-2)^2 vs (3x-2)^{2}
    flat = re.sub(r"\^\{([^{}]+)\}", r"^\1", key)
    return flat != key and flat in hay


def merge_prose_and_math(text: Optional[str], latex: Optional[str]) -> str:
    """Join an English stem with a separate math latex field.

    Extraction often stores ``text="Which expression is equal to"`` and
    ``latex="(3x-2)^{2}"``. Display must show both — never drop the math.
    """
    text_s = (text or "").strip()
    latex_s = (latex or "").strip()
    if not latex_s:
        return text_s
    if not text_s:
        return latex_s
    if looks_like_prose(latex_s):
        return text_s
    if math_already_in_stem(text_s, latex_s):
        return text_s
    stem_like = (
        looks_like_prose(text_s)
        or bool(_INSTRUCTION_RE.match(text_s))
        or text_s.rstrip().endswith((":", "?"))
    )
    if stem_like:
        return f"{text_s} {latex_s}".strip()
    # Pure-math text vs richer latex: prefer latex (options / short expressions).
    if "\\frac" in latex_s or "^{" in latex_s or "^" in latex_s:
        return latex_s
    return text_s or latex_s


def recover_fields(text: Optional[str], latex: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return (display_text, latex) with reconstructed math for diagnostic MCQs."""
    # Repair BEFORE strip() - see recover_latex() for why the order matters.
    text_s = recover_eaten_backslash_commands(text or "").strip()
    text_s = unsquash_english(unicodedata.normalize("NFKC", text_s))
    text_s = unwrap_outer_math_if_prose(text_s)
    latex_s = recover_eaten_backslash_commands(latex or "").strip() or None
    if latex_s:
        latex_s = unwrap_outer_math_if_prose(unsquash_english(unicodedata.normalize("NFKC", latex_s)))

    # Short instruction + math in latex ("Simplify:" + fraction).
    if text_s and latex_s and not looks_like_prose(latex_s):
        if _INSTRUCTION_RE.match(text_s) or (
            looks_like_prose(text_s) and not math_already_in_stem(text_s, latex_s)
        ):
            recovered_l = recover_latex(latex_s) or latex_s
            recovered_t = recover_latex(text_s) or text_s
            merged = merge_prose_and_math(recovered_t, recovered_l)
            return merged, recovered_l

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


# --- Text saved by the teacher's math editor (mirrors lms-core.js) ---

_TEXT_BLOCK_RE = re.compile(r"\\(?:text|textrm|textnormal|mbox)\s*\{")
_TEX_COMMAND_RE = re.compile(r"\\(?:[a-zA-Z]+|.)", re.S)
_ESCAPED_LATEX_RE = re.compile(r"\\textbrace(?:left|right)")


_TEXT_MODE_NAMED = {
    "textbackslash": "\\", "textasciicircum": "^", "textasciitilde": "~",
    "lbrack": "[", "rbrack": "]", "lbrace": "{", "rbrace": "}",
}
# The editor puts one space after a command word whether it is a separator ("\lbrack note") or real
# text ("\rbrack One"). After an opening bracket it is a separator; after a closing one it is kept.
_TEXT_MODE_UNESCAPE_RE = re.compile(
    r"\\(textbackslash|textasciicircum|textasciitilde|lbrack|lbrace)(?![A-Za-z])(?:\{\}|\s)?"
    r"|\\(rbrack|rbrace)(?![A-Za-z])(?:\{\})?"
    r"|\\\^\{\}|\\([{}#%&_])"
)


def _unescape_text_mode(s: str) -> str:
    """One pass (mirrors lmsUnescapeTextMode); the math editor writes "[" as \\lbrack."""

    def repl(m: re.Match[str]) -> str:
        if m.group(1) or m.group(2):
            return _TEXT_MODE_NAMED[m.group(1) or m.group(2)]
        return m.group(3) or "^"

    return _TEXT_MODE_UNESCAPE_RE.sub(repl, s or "")


def _skip_brace_group(s: str, start: int) -> int:
    """Index just past the brace group opening at ``start``."""
    depth, k = 0, start
    while k < len(s):
        c = s[k]
        if c == "\\":
            k += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return len(s)


def mathlive_to_storage(latex: Optional[str]) -> str:
    """Math-editor LaTeX -> stored quiz text: words as plain prose, math in $...$.

    ``\\text{Simplify }563.71\\times10^{-3}`` -> ``Simplify $563.71\\times10^{-3}$``.
    """
    latex = (latex or "").strip()
    # A field whose default mode is text hands its whole value back as "$ ... $": unwrap that one pair.
    outer = re.fullmatch(r"\$\s*([^$]*?)\s*\$", latex)
    if outer:
        latex = outer.group(1)
    if not latex:
        return ""
    # Anything else that already carries delimiters is in stored form; leave it alone.
    if latex.startswith(("\\(", "\\[")) or re.search(r"(?:^|[^\\])\$", latex):
        return latex
    parts: list[tuple[bool, str]] = []  # (is_text, value)
    math = ""
    i = 0
    while i < len(latex):
        ch = latex[i]
        if ch == "\\":
            m = _TEXT_BLOCK_RE.match(latex, i)
            if m:
                close = _skip_brace_group(latex, m.end() - 1)
                if math.strip():
                    parts.append((False, math.strip()))
                math = ""
                parts.append((True, _unescape_text_mode(latex[m.end():close - 1])))
                i = close
                continue
            m = _TEX_COMMAND_RE.match(latex, i)
            math += m.group(0)
            i = m.end()
            continue
        if ch == "{":
            past = _skip_brace_group(latex, i)
            math += latex[i:past]
            i = past
            continue
        math += ch
        i += 1
    if math.strip():
        parts.append((False, math.strip()))
    if not any(is_text and value.strip() for is_text, value in parts):
        return f"${latex}$"
    out = ""
    last_was_math = False
    for is_text, value in parts:
        if is_text:
            if last_was_math and value and not re.match(r"[\s?.!,;:)\]]", value):
                out += " "
            out += value
            last_was_math = False
            continue
        if re.fullmatch(r"[?.!,;:]+", value):
            out = out.rstrip() + value
            last_was_math = False
            continue
        if out and not re.search(r"[\s(\[]$", out):
            out += " "
        out += f"${value}$"
        last_was_math = True
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def repair_escaped_latex(text: Optional[str]) -> Optional[str]:
    """Undo a stem saved by the old quiz-editor bug, which stored the LaTeX code as text:
    ``\\textbackslash text\\textbraceleft What is ...\\textbraceright\\textbackslash log 5289``."""
    if not text or "\\textbackslash" not in text or not _ESCAPED_LATEX_RE.search(text):
        return text
    body = re.sub(r"^\$+|\$+$", "", text.strip())
    body = re.sub(r"\\textbackslash(?:\{\})?\s?", lambda _m: "\\", body)
    body = re.sub(r"\\textbraceleft(?:\{\})?\s?", "{", body)
    body = re.sub(r"\\textbraceright(?:\{\})?\s?", "}", body).strip()
    # The editor stored the escaped code inside one outer \text{...}; drop that wrapper.
    wrapped = re.match(r"\\text\s*\{", body)
    if wrapped and _skip_brace_group(body, wrapped.end() - 1) == len(body):
        body = body[wrapped.end():-1]
    return mathlive_to_storage(body)


# --- Delimiter wrapping so MathJax actually typesets the recovered math ---

_INSTRUCTION_WORDS = r"Simplify|Find|Solve|Evaluate|Compute|Calculate|Expand|Factorize|Factorise|Factor"
_INSTRUCTION_BARE_RE = re.compile(rf"^({_INSTRUCTION_WORDS})\s+(.+)$", re.I | re.S)
_INSTRUCTION_IN_MATH_RE = re.compile(
    rf"^(\$|\\\()\s*({_INSTRUCTION_WORDS})(?![A-Za-z])\s*:?\s*(.+?)\s*(\$|\\\))$", re.I | re.S
)
_FUNC_WORD_RE = re.compile(r"\b(?:" + "|".join(_FUNC_NAMES) + r")\b", re.I)


def _is_pure_math_body(body: str) -> bool:
    """Math with no English words in it (TeX commands and function names aside)."""
    words = _FUNC_WORD_RE.sub(" ", re.sub(r"\\[A-Za-z]+", " ", body or ""))
    if re.search(r"[A-Za-z]{3,}", words):
        return False
    return bool(_BARE_MATH_RE.search(body) or re.search(r"\d\s*[=+\-*/]\s*\d|[A-Za-z]\s*=", body))


def lift_instruction_out_of_math(text: str) -> str:
    """``$Simplify 563.71 \\times 10^{-3}$`` -> ``Simplify $563.71 \\times 10^{-3}$``.

    Inside math the instruction word renders italic and glued to the number ("Simplify563.71").
    """
    m = _INSTRUCTION_IN_MATH_RE.match((text or "").strip())
    if not m or (m.group(1) == "$") != (m.group(4) == "$"):
        return text
    body = m.group(3)
    if "$" in body or "\\(" in body or "\\)" in body or not _is_pure_math_body(body):
        return text
    return f"{m.group(2)} ${body}$"


_HAS_DELIM_RE = re.compile(r"\\\(|\\\)|\\\[|\\\]|\$")
# A plain English connective ("or", "and", ...) between two math bits, e.g.
# "x = 3 or x = -3". The whole line must not be one math span - math mode
# eats the spaces and renders the word as italic letters ("3orx").
_MATH_CONNECTIVE_RE = re.compile(
    r"[0-9A-Za-z)}\]]\s+(?:or|and|nor|where|when|then)\s+[-(\\0-9A-Za-z]", re.I
)
_BARE_MATH_RE = re.compile(
    r"\\(?:frac|sqrt|times|div|cdot|pm|mp|leq|geq|neq|le|ge|ne|sum|int|"
    r"alpha|beta|gamma|theta|pi|infty|circ|approx|left|right|overline|vec|log|ln)\b"
    r"|\^\{|_\{|[A-Za-z]\^\d|[A-Za-z]\^\{|[A-Za-z]\^[A-Za-z]"
    r"|\\frac|\d+\s*/\s*\d+|\w\s*/\s*\("
)
_BRACE_1 = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
# A single math token: number, one variable letter (not part of a word),
# an operator, a \command, an exponent, a subscript, or a brace group.
_MATH_TOKEN = (
    r"(?:\d+(?:\.\d+)?"
    r"|[A-Za-z](?![A-Za-z])"
    r"|[()\[\]+\-=*/·×÷]"
    r"|\\[A-Za-z]+"
    r"|\^\{[^{}]+\}|\^-?\d+|\^[A-Za-z]"
    r"|_\{[^{}]+\}|_\d+"
    r"|" + _BRACE_1 + r")"
)
# After a closed \\frac{..}{..} / \\sqrt{..}, only continue with operators /
# numbers / more TeX — not a lone English letter ("a rational…").
_MATH_TAIL_NO_LETTER = (
    r"(?:\s*(?:\d+(?:\.\d+)?"
    r"|[()\[\]+\-=*/·×÷]"
    r"|\\[A-Za-z]+(?:\s*" + _BRACE_1 + r"){0,2}"
    r"|\^\{[^{}]+\}|\^-?\d+|\^[A-Za-z]"
    r"|_\{[^{}]+\}|_\d+"
    r"|" + _BRACE_1 + r"))*"
)
_MATH_RUN_RE = re.compile(
    "(?:"
    r"\\frac\s*" + _BRACE_1 + r"\s*" + _BRACE_1 + _MATH_TAIL_NO_LETTER
    + r"|\\sqrt\s*" + _BRACE_1 + _MATH_TAIL_NO_LETTER
    # log / ln with optional base and argument: \log_a 8, \log_{a}8, \log(2\times5)
    + r"|\\(?:log|ln)(?:\s*(?:_" + _BRACE_1 + r"|_[A-Za-z0-9]+))?"
    + r"(?:\s*(?:\([^()]{0,60}\)|[A-Za-z0-9]+))?"
    + r"(?:\s*" + _MATH_TOKEN + r")*"
    + r"|(?:\([^()]{0,40}\)|[A-Za-z0-9\]]{1,20})\s*\^\s*(?:\{[^{}]+\}|-?\d+|[A-Za-z])"
    + r"(?:\s*" + _MATH_TOKEN + r")*"
    + ")"
)


def _wrap_math_islands(text: str, inline: bool) -> str:
    """Wrap \\frac{..}{..}, \\sqrt{..}, \\log… and exponent runs in \\( \\), leaving
    the surrounding English words untouched.

    Islands inside prose always use inline ``\\( \\)`` even for ``\\frac``. Display
    ``\\[ \\]`` on a mid-sentence fraction makes MathJax eat neighboring spaces
    ("Whyis … arationalexpression").
    """
    def repl(m: re.Match[str]) -> str:
        body = m.group(0).strip()
        # Only use display math when the *entire* text is this one math island.
        block = (not inline) and "\\frac" in body and m.start() == 0 and m.end() == len(text or "")
        return (f" \\[{body}\\] " if block else f" \\({body}\\) ")

    wrapped = _MATH_RUN_RE.sub(repl, text or "")
    # merge islands separated only by whitespace: \(a\) \(b\) -> \(a b\)
    wrapped = re.sub(r"\\\)\s+\\\(", " ", wrapped)
    wrapped = re.sub(r"\\\]\s+\\\[", " ", wrapped)
    return re.sub(r"[ \t]{2,}", " ", wrapped).strip()


def wrap_for_mathjax(text: Optional[str], inline: bool = True) -> str:
    """Return a string MathJax/KaTeX will typeset.

    Prose stays prose; a bare LaTeX body (``\\frac{a}{b}``, ``x^{2}+1``) or
    an ``Instruction: <math>`` line gets wrapped in ``\\( \\)`` (or ``\\[ \\]``
    for a display fraction). No-ops when the text already carries
    delimiters or has no math at all - so it is safe to run on every
    delivered question and option.
    """
    s = (text or "").strip()
    if not s:
        return text or ""
    # Plain percents ("15%", "16 2/3%") must stay literal — wrapping them
    # turns % into \% and leaves a visible backslash when MathJax skips.
    plain_pct = normalize_mixed_percents(s)
    if _PLAIN_PERCENT_RE.match(plain_pct):
        return plain_pct
    # Prose stems must not keep bare \ldots (shows as literal backslash-text).
    s = dedupe_repeated_math(
        normalize_tex_set_braces(
            normalize_latex_spacing(normalize_plain_ellipsis(strip_english_dollar_spans(s)))
        )
    )
    s = promote_inline_math_notation(s)
    s = lift_instruction_out_of_math(s)
    if _HAS_DELIM_RE.search(s):
        return s
    # "Simplify 563.71 \times 10^{-3}" (no colon): keep the word as prose, wrap only the math.
    bare = _INSTRUCTION_BARE_RE.match(s)
    if bare and _is_pure_math_body(bare.group(2)):
        return f"{bare.group(1)} \\({bare.group(2).strip()}\\)"
    prefixed = _INSTRUCTION_PREFIX_RE.match(s)
    if prefixed and (prefixed.group(2) or "").strip():
        body = prefixed.group(2).strip()
        if looks_like_prose(body) and not re.search(r"\\frac|\^\{", body):
            return s
        block = (not inline) and "\\frac" in body
        prefix = prefixed.group(1).rstrip()
        return f"{prefix} " + (f"\\[{body}\\]" if block else f"\\({body}\\)")
    if not _BARE_MATH_RE.search(s):
        return s
    # Any English around TeX must keep words outside math mode (island wrap).
    if looks_like_prose(s) or _MATH_CONNECTIVE_RE.search(s) or re.search(r"[A-Za-z]{3,}", re.sub(r"\\[A-Za-z]+\*?", " ", s)):
        return _wrap_math_islands(s, inline)
    block = (not inline) and "\\frac" in s
    return f"\\[{s}\\]" if block else f"\\({s}\\)"


def to_render_string(text: Optional[str], latex: Optional[str] = None, *, inline: bool = True) -> str:
    """Full pipeline for a diagnostic stem/option: recover the math, then
    guarantee it is delimited for MathJax. This is what delivery sends as
    the ``render`` field so the client does not have to re-guess."""
    display, recovered_latex = recover_fields(text, latex)
    # Prefer the merged stem (prose + math). Using latex alone drops
    # "Which expression is equal to" when math lives in the latex field.
    body = merge_prose_and_math(display, recovered_latex)
    return wrap_for_mathjax(body, inline=inline)
