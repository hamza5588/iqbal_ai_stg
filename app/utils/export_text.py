"""Text clean-up for the Word / PowerPoint lesson exports.

Lesson content is stored as markdown with LaTeX math and, for PDF-sourced
lessons, the odd control character. Word and PowerPoint show LaTeX as raw
code, and python-docx refuses control characters outright, so the exporters
run every string through ``clean_for_export`` first.
"""
from __future__ import annotations

import re

# XML 1.0 forbids these; python-docx raises "All strings must be XML compatible".
_XML_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")

_SUPERSCRIPTS = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPTS = str.maketrans("0123456789+-=()aeoxhklmnpstijruv", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ")
_SUPER_OK = set("0123456789+-=()ni")
_SUB_OK = set("0123456789+-=()aeoxhklmnpstijruv")

_SYMBOLS = {
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "mp": "∓", "ast": "*",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝", "infty": "∞",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔", "implies": "⇒",
    "iff": "⇔", "therefore": "∴", "because": "∵", "angle": "∠", "triangle": "△",
    "perp": "⊥", "parallel": "∥", "degree": "°", "circ": "°", "prime": "′",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "sum": "Σ", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
    "ldots": "…", "cdots": "…", "dots": "…", "vdots": "⋮",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "quad": " ", "qquad": "  ", "left": "", "right": "", "big": "", "Big": "",
    "bigg": "", "Bigg": "", "displaystyle": "", "limits": "", "nolimits": "",
    "lbrace": "{", "rbrace": "}", "langle": "⟨", "rangle": "⟩", "mid": "|",
    "backslash": "\\", "textbackslash": "\\",
}
# Commands whose single argument is kept as-is (\text{abc} -> abc).
_UNWRAP = {
    "text", "textrm", "textbf", "textit", "mathrm", "mathbf", "mathit", "mathsf",
    "mathbb", "mathcal", "operatorname", "boxed", "underline", "mbox", "hbox",
}
_ACCENTS = {"overline": "\u0305", "bar": "\u0304", "hat": "\u0302", "vec": "\u20d7", "tilde": "\u0303", "dot": "\u0307"}

_MATH_SPAN_RE = re.compile(
    r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)",
    re.S,
)
_BARE_COMMAND_RE = re.compile(r"\\(?:frac|sqrt|times|div|cdot|pm|leq|geq|neq|approx|pi|theta|alpha|beta|log|ln|sin|cos|tan|text)\b|\^\{|_\{")


def strip_xml_illegal(text: str) -> str:
    """Drop characters Word / PowerPoint XML cannot hold (keeps tab and newline)."""
    return _XML_ILLEGAL_RE.sub("", text or "")


def _read_group(s: str, i: int) -> tuple[str, int]:
    """Read one TeX argument starting at ``i``: a {group}, a \\command or a single character."""
    n = len(s)
    while i < n and s[i] == " ":
        i += 1
    if i >= n:
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < n:
            if s[j] == "\\" and j + 1 < n:
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], n
    if s[i] == "\\":
        j = i + 1
        while j < n and s[j].isalpha():
            j += 1
        return s[i:max(j, i + 2)], max(j, i + 2)
    return s[i], i + 1


def _is_simple(text: str) -> bool:
    return bool(re.fullmatch(r"[\w.°′%π√∞αβγθ]+", text or ""))


def _wrap(text: str) -> str:
    return text if _is_simple(text) else f"({text})"


def _wrap_fraction_part(text: str) -> str:
    """A numerator/denominator needs brackets unless it is one number or one symbol: 3/(2a), not 3/2a."""
    return text if re.fullmatch(r"\d+(?:\.\d+)?|[^\W\d_]|[°′π∞αβγθ]", text or "") else f"({text})"


def _script(body: str, table: dict, allowed: set, marker: str) -> str:
    body = body.strip()
    if body and all(ch in allowed for ch in body):
        return body.translate(table)
    return marker + _wrap(body)


def latex_math_to_text(tex: str) -> str:
    """Render one LaTeX math body as plain Unicode text: \\frac{a}{b} -> a/b, x^{2} -> x²."""
    s = tex or ""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            j = i + 1
            while j < n and s[j].isalpha():
                j += 1
            name = s[i + 1:j]
            if not name:  # \\ , \{ , \% , \, ...
                nxt = s[i + 1] if i + 1 < n else ""
                out.append({"\\": "\n", ",": " ", ";": " ", ":": " ", "!": "", " ": " "}.get(nxt, nxt))
                i += 2
                continue
            i = j
            if name in ("frac", "dfrac", "tfrac", "cfrac"):
                num, i = _read_group(s, i)
                den, i = _read_group(s, i)
                out.append(f"{_wrap_fraction_part(latex_math_to_text(num))}/{_wrap_fraction_part(latex_math_to_text(den))}")
            elif name == "sqrt":
                index = ""
                if i < n and s[i] == "[":
                    end = s.find("]", i)
                    if end > 0:
                        index, i = s[i + 1:end], end + 1
                body, i = _read_group(s, i)
                root = _script(latex_math_to_text(index), _SUPERSCRIPTS, _SUPER_OK, "") if index else ""
                out.append(f"{root}√{_wrap(latex_math_to_text(body))}")
            elif name in _UNWRAP:
                body, i = _read_group(s, i)
                out.append(body if name.startswith("text") or name in ("mbox", "hbox") else latex_math_to_text(body))
            elif name in _ACCENTS:
                body, i = _read_group(s, i)
                inner = latex_math_to_text(body)
                out.append(inner + _ACCENTS[name] if len(inner) == 1 else inner)
            elif name in ("begin", "end"):
                _, i = _read_group(s, i)
            elif name in _SYMBOLS:
                out.append(_SYMBOLS[name])
            else:  # \log, \sin, \lim ... and anything unknown: keep the word
                out.append(name)
                if i < n and s[i].isalnum():
                    out.append(" ")
            continue
        if ch in "^_":
            body, i = _read_group(s, i + 1)
            inner = latex_math_to_text(body)
            if ch == "^" and inner in ("°", "′"):
                out.append(inner)
            elif ch == "^":
                out.append(_script(inner, _SUPERSCRIPTS, _SUPER_OK, "^"))
            else:
                out.append(_script(inner, _SUBSCRIPTS, _SUB_OK, "_"))
            continue
        if ch in "{}":
            i += 1
            continue
        if ch == "&":
            out.append("  ")
            i += 1
            continue
        if ch == "~":
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" ?\n ?", "\n", text).strip()


def latex_to_readable(text: str) -> str:
    """Replace every math span in mixed prose with readable Unicode text."""
    if not text or ("\\" not in text and "$" not in text and "^" not in text):
        return text or ""

    def repl(m: re.Match[str]) -> str:
        body = next(g for g in m.groups() if g is not None)
        rendered = latex_math_to_text(body)
        # Display math sits on its own line; keep it there.
        return f"\n{rendered}\n" if (m.group(1) is not None or m.group(2) is not None) else rendered

    out = _MATH_SPAN_RE.sub(repl, text)
    # Lines that still carry bare LaTeX (no delimiters) are converted whole.
    lines = [latex_math_to_text(line) if _BARE_COMMAND_RE.search(line) else line for line in out.split("\n")]
    return "\n".join(lines)


def clean_for_export(text) -> str:
    """Full pipeline for one exported string: repair, drop illegal characters, make math readable."""
    # Imported here: app.services imports the lesson service, which imports this module.
    from app.services.quiz.math_text import recover_eaten_backslash_commands

    s = recover_eaten_backslash_commands(str(text or ""))
    s = strip_xml_illegal(s).replace("\r\n", "\n").replace("\r", "\n")
    return latex_to_readable(s)
