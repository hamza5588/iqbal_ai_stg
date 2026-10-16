"""Deterministic (no LLM) checks for maths MCQs before a student sees them.

Two layers, both conservative:

* ``structural_problems`` - things that make a question unusable whatever the
  answer is: wrong option count, empty / duplicate / label-only options, a key
  that points nowhere, a garbled stem (``\\begin{cases}^{2}y``, unbalanced
  braces), or an instruction ("Solve the quadratic equation by factorization.")
  with no maths left in it.
* ``solve_options`` - for the question shapes it can read with certainty
  (one-variable equations, 2x2 linear systems, f(x) evaluation, numeric or
  algebraic simplification, factorising, scientific notation) it works out
  which options are actually correct with sympy.

Anything it cannot read is reported as unknown, never as wrong: a correct
question must not be dropped because the parser did not understand it.
Seen in production (Oct 2026): AI practice questions keyed ``3(2x-5)=4x+7`` ->
"x = 4" (true answer 11, not among the options) and ``-3 + 5(2-7)`` -> "-22".
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

OK = "ok"
WRONG_KEY = "wrong_key"
NO_CORRECT = "no_correct_option"
MULTIPLE = "multiple_correct"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class KeyVerdict:
    status: str
    correct_indexes: Tuple[int, ...] = ()
    reason: str = ""

    @property
    def disproved(self) -> bool:
        return self.status in (WRONG_KEY, NO_CORRECT, MULTIPLE)


# ── structural checks ────────────────────────────────────────────────────────

_LABEL_ONLY_RE = re.compile(r"^\(?[A-Da-d]\)?[.)]?$")
_GARBLED_RE = re.compile(r"\\begin\{cases\}\s*\^|(?:^|[\s(=,])\^\{")
# Only an instruction that opens the stem ("Solve the quadratic equation by factorization.");
# "Which method can be used to solve ...?" is a concept question with no maths by design.
_ACTION_RE = re.compile(r"^\W*(solve|simplify|factori[sz]e|factor|evaluate|calculate|compute|expand)\b", re.I)


def _option_text(o: dict) -> str:
    return re.sub(r"\s+", " ", str(o.get("text") or o.get("latex") or "")).strip()


def _brace_balance_ok(s: str) -> bool:
    s = s.replace("\\{", "").replace("\\}", "")
    depth = 0
    for ch in s:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _has_math_content(stem: str) -> bool:
    return bool(re.search(r"\d|\\\(|\$|\\frac|\\sqrt|[a-z]\s*[\^=+\-*/]|[=+*/^]", stem))


def structural_problems(item: dict) -> List[str]:
    """Reasons this queue item / question cannot be shown, or [] when it is well-formed."""
    problems: List[str] = []
    stem = str(item.get("question_text") or item.get("question_latex") or "").strip()
    opts = item.get("options") or []
    idx = item.get("correct_option_index")
    texts = [_option_text(o) if isinstance(o, dict) else str(o).strip() for o in opts]

    if len(re.sub(r"[^0-9A-Za-z]", "", stem)) < 2:  # "2+3=?" is a real (short) stem
        problems.append("empty or too-short stem")
    if len(opts) != 4:
        problems.append(f"{len(opts)} options (need 4)")
    if not isinstance(idx, int) or not 0 <= idx < len(opts):
        problems.append("answer key points to no option")
    if any(not t for t in texts):
        problems.append("empty option")
    if any(_LABEL_ONLY_RE.match(t) for t in texts if t):
        problems.append("option is only a letter")
    norm = [re.sub(r"[\s$]|\\[(),;!]", "", t).lower() for t in texts]
    if len(set(n for n in norm if n)) < len([n for n in norm if n]):
        problems.append("duplicate options")
    if _GARBLED_RE.search(stem) or not _brace_balance_ok(stem) or any(not _brace_balance_ok(t) for t in texts):
        problems.append("malformed maths in stem or options")
    if stem.count("\\(") != stem.count("\\)") or stem.count("$") % 2:
        problems.append("unbalanced maths delimiters")
    if _ACTION_RE.search(stem) and not _has_math_content(stem):
        problems.append("instruction with no maths to work on")
    return problems


# ── TeX -> sympy text ────────────────────────────────────────────────────────

class _Unsupported(Exception):
    pass


_SIMPLE_REPL = [
    (r"\\left|\\right|\\displaystyle|\\,|\\;|\\!|\\quad|\\qquad|\\text\{\s*\}", " "),
    (r"\\[dt]frac", r"\\frac"),
    (r"\\times|×|\\cdot|·|∙", "*"),
    (r"\\div|÷", "/"),
    (r"[−–‑]", "-"),
    (r"\\pi\b|π", "pi"),
    (r"\\lbrace|\\\{", "("),
    (r"\\rbrace|\\\}", ")"),
    (r"\\lbrack", "("),
    (r"\\rbrack", ")"),
]
_UNIT_RE = re.compile(
    r"\s*(?:\\text\{\s*)?(?:cm|mm|km|m|kg|g|s|sec|units?|sq\.?\s*units?)\s*(?:\^\{?[23]\}?|[²³])?\}?\s*$"
)
_DEGREE_RE = re.compile(r"\s*(?:\^\{?\\circ\}?|°|\\degree)\s*")


def _take_group(s: str, i: int) -> Tuple[str, int]:
    """Return (contents, end) of a {...} group starting at s[i] (or one char)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        raise _Unsupported("missing argument")
    if s[i] != "{":
        return s[i], i + 1
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
    raise _Unsupported("unbalanced braces")


def _expand_commands(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s.startswith("\\frac", i):
            num, j = _take_group(s, i + 5)
            den, j = _take_group(s, j)
            out.append(f"(({_expand_commands(num)})/({_expand_commands(den)}))")
            i = j
        elif s.startswith("\\sqrt", i):
            j = i + 5
            root = None
            if j < len(s) and s[j] == "[":
                k = s.index("]", j)
                root, j = s[j + 1 : k], k + 1
            arg, j = _take_group(s, j)
            arg = _expand_commands(arg)
            out.append(f"(({arg})**(1/({root})))" if root else f"sqrt({arg})")
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _unicode_sqrt(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return f"sqrt({m.group(1)})"
    s = re.sub(r"√\s*\(([^()]+)\)", repl, s)
    return re.sub(r"√\s*(\d+(?:\.\d+)?|[a-z])", repl, s)


def _abs_bars(s: str) -> str:
    if s.count("|") % 2:
        raise _Unsupported("odd number of | bars")
    while "|" in s:
        s, n = re.subn(r"\|([^|]*)\|", r"Abs(\1)", s, count=1)
        if not n:
            raise _Unsupported("nested | bars")
    return s


def tex_to_sympy_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    if re.search(r"[<>≤≥≠]|\\[lg]eq?\b|\\neq|\\ne\b|\\l?t\b|\\gt\b", s):
        raise _Unsupported("inequality")
    if re.search(r"\d\s+\d", s):
        raise _Unsupported("numbers separated by a space (flattened fraction?)")
    if re.search(r"[a-zA-Z]\d", re.sub(r"\\[a-zA-Z]+|_\{?\w+\}?", " ", s)):
        raise _Unsupported("letter followed by digit (flattened exponent?)")
    s = re.sub(r"^\\\(|\\\)$|^\\\[|\\\]$|^\$+|\$+$", "", s).strip()
    s = s.replace("\\(", " ").replace("\\)", " ").replace("$", " ")
    for pat, rep in _SIMPLE_REPL:
        s = re.sub(pat, rep, s)
    s = _expand_commands(s)
    s = _unicode_sqrt(s)
    s = _abs_bars(s)
    s = s.replace("²", "^2").replace("³", "^3")
    if "\\" in s:
        raise _Unsupported("unhandled TeX command")
    s = s.replace("{", "(").replace("}", ")").replace("[", "(").replace("]", ")")
    s = re.sub(r"\^\s*(-?\d+(?:\.\d+)?|[a-zA-Z])", r"**(\1)", s)
    s = s.replace("^", "**")
    # Words are never maths: allow only known functions and short variable runs.
    for word in re.findall(r"[A-Za-z]+", s):
        if word not in ("sqrt", "pi", "Abs") and len(word) > 2:
            raise _Unsupported(f"word in maths: {word}")
    return s


@lru_cache(maxsize=4096)
def _parse(text: str, func_defs: Tuple[Tuple[str, str, str], ...] = ()):
    import sympy
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        split_symbols,
        standard_transformations,
    )

    local = {"sqrt": sympy.sqrt, "pi": sympy.pi, "Abs": sympy.Abs, "E": sympy.Symbol("E")}
    for name, var, body in func_defs:
        v = sympy.Symbol(var)
        local[name] = sympy.Lambda(v, _parse(body))
    transformations = standard_transformations + (split_symbols, implicit_multiplication_application, convert_xor)
    return parse_expr(text, local_dict=local, transformations=transformations, evaluate=True)


def _expr(s: str, func_defs: Tuple[Tuple[str, str, str], ...] = ()):
    try:
        expr = _parse(tex_to_sympy_text(s), func_defs)
        # i (imaginary unit) and e (Euler) read as plain variables would give wrong verdicts
        if {str(v) for v in expr.free_symbols} & {"i", "e", "E"}:
            raise _Unsupported("i / e is ambiguous")
        return expr
    except _Unsupported:
        raise
    except Exception as exc:  # noqa: BLE001 - any sympy failure means "cannot read"
        raise _Unsupported(f"parse failed: {exc}") from exc


def _same(a, b) -> bool:
    import sympy

    try:
        diff = sympy.simplify(sympy.expand(a - b))
        if diff == 0:
            return True
        if diff.free_symbols:
            return False
        return abs(complex(sympy.N(diff))) < 1e-9
    except Exception:  # noqa: BLE001
        return False


# ── reading options ──────────────────────────────────────────────────────────

_NO_SOLUTION_RE = re.compile(r"\bno\s+(real\s+)?(solution|value|roots?)\b", re.I)
_CANNOT_RE = re.compile(r"cannot be simplified|not possible|none of (these|the above)", re.I)


def _clean_option(t: str) -> str:
    t = _option_text({"text": t}) if not isinstance(t, str) else re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^\$|\$$", "", t).strip()
    t = re.sub(r"^\\\(|\\\)$", "", t).strip()
    t = _DEGREE_RE.sub("", t)
    t = _UNIT_RE.sub("", t)
    return t.strip()


def _option_values(t: str, variables: Sequence[str]):
    """Parse an option into ('scalar', expr) | ('set', [exprs]) | ('tuple', {var: expr})."""
    t = _clean_option(t)
    if not t:
        raise _Unsupported("empty option")
    pairs = re.findall(r"\b([a-z])\s*=\s*([^,;]+?)(?=,\s*[a-z]\s*=|\)?\s*$|\s+(?:and|or)\s+)", t.strip("() "))
    m_tuple = re.match(r"^\(?\s*([a-z])\s*,\s*([a-z])\s*\)?\s*=\s*\((.+),(.+)\)$", t)
    if m_tuple:
        return "tuple", {m_tuple.group(1): _expr(m_tuple.group(3)), m_tuple.group(2): _expr(m_tuple.group(4))}
    if len(pairs) >= 2 and len({p[0] for p in pairs}) == len(pairs):  # "(x = 3, y = 5)"
        return "tuple", {v: _expr(e) for v, e in pairs}
    m_bare = re.match(r"^\(\s*([^,()]+(?:\([^()]*\))?[^,()]*)\s*,\s*([^,()]+(?:\([^()]*\))?[^,()]*)\s*\)$", t)
    if m_bare and len(variables) == 2:
        return "tuple", {variables[0]: _expr(m_bare.group(1)), variables[1]: _expr(m_bare.group(2))}
    if re.search(r"\s(or|and)\s", t):
        parts = re.split(r"\s+(?:or|and)\s+", t)
        vals = []
        for p in parts:
            p = re.sub(r"^\s*[a-z]\s*=\s*", "", p.strip())
            vals.append(_expr(p))
        return "set", vals
    if len(pairs) >= 2 and len({p[0] for p in pairs}) == len(pairs):
        return "tuple", {v: _expr(e) for v, e in pairs}
    t = re.sub(r"^\s*[a-z]\s*=\s*(?!.*=)", "", t)
    if "=" in t:
        # Worked-answer style "12/18 = 2/3": the value is the last part, but only when every
        # part is a plain number ("\log_x a = y", "Sum = 5, ..." and equations like
        # "x^2 - 5 = 0" are not values). A chain whose parts disagree ("12/18 = 3/2") is a
        # false statement: it matches nothing.
        parts = [_expr(p) for p in t.split("=")]
        if any(p.free_symbols for p in parts):
            raise _Unsupported("equation, not a value")
        if not all(_same(parts[0], p) for p in parts[1:]):
            return "false", None
        return "scalar", parts[-1]
    return "scalar", _expr(t)


# ── reading the stem ─────────────────────────────────────────────────────────

_SEGMENT_RE = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$|\$(.+?)\$", re.S)
# Wording that means the question asks for something this checker does not compute.
_UNSUPPORTED_WORDS = re.compile(
    r"area|perimeter|length|width|rectangle|triangle|circle|angle|consecutive|integers?\b|"
    r"slope|intercept|line\b|inequalit|solution set|how many|conjugate|\blog|property|statement|"
    r"true|false|digit|percent|%|average|mean|median|remainder|divided|lcm|gcd|hcf|multiple|"
    r"factor of|\bnot\b|least|greatest|smallest|largest|prime|rational|irrational|number line|"
    r"real solutions|roots?\b|sum|difference|product|discriminant|domain|range|graph|which of the following|"
    r"which (one|value|option)|coefficient|degree|term|ratio|proportion|percentage|probability|"
    r"interest|speed|age|cost|price|profit|claims?|correct|incorrect|reason|why|explain|inverse|"
    r"infinite|parallel|perpendicular|between|order|compare|estimate|round|approximately|nearest",
    re.I,
)
_SOLVE_WORDS = re.compile(r"solve|value of|find|what is|determine|calculate|if\b", re.I)
_EXPR_WORDS = re.compile(
    r"simplify|evaluate|calculate|compute|value of|what is|find|equivalent|factori[sz]e|factor|expand|"
    r"reduce|lowest form|simplest form|express",
    re.I,
)
_LEAD_IN_RE = re.compile(
    r"^\s*(?P<lead>what is(?: the value of)?|evaluate|calculate|compute|simplify|find(?: the value of)?)\s+"
    r"(?P<math>[^A-Za-z]*[0-9][^A-Za-z]*)\s*[?.]?\s*$",
    re.I,
)
_FUNC_DEF_RE = re.compile(r"^\s*([fgh])\s*\(\s*([a-z])\s*\)\s*=\s*(.+)$")


def _stem_parts(stem: str) -> Tuple[str, List[str]]:
    """(wording with maths removed, maths segments)."""
    segs = [next(g for g in m.groups() if g is not None).strip() for m in _SEGMENT_RE.finditer(stem)]
    words = _SEGMENT_RE.sub(" ", stem)
    if not segs:
        lead = _LEAD_IN_RE.match(stem)
        if ":" in stem:
            # "Solve for x: 3(2x - 5) = 4x + 7"
            head, tail = stem.rsplit(":", 1)
            segs, words = [tail.strip().rstrip(".?")], head
        elif lead:
            # "What is 2 + 2?", "Evaluate 3 + 5 * 2."
            segs, words = [lead.group("math").strip().rstrip(".?")], lead.group("lead")
        else:
            # a bare equation / expression with no delimiters: "3(2x - 5) = 4x + 1", "3+2=?"
            segs, words = [stem.strip().rstrip(".?")], ""
    # "3+2=?" / "3+2 =" asks for the value of the left side, it is not an equation
    segs = [re.sub(r"\s*=\s*\?*\s*$", "", s) for s in segs]
    return words, [s for s in segs if s]


def _variables(expr) -> List[str]:
    return sorted(str(s) for s in expr.free_symbols)


def _match(kind_values, target) -> bool:
    kind, val = kind_values
    if kind == "scalar":
        return not isinstance(target, (list, dict)) and _same(val, target)
    if kind == "set":
        if not isinstance(target, list) or len(val) != len(target):
            return False
        return all(any(_same(v, t) for t in target) for v in val) and all(any(_same(v, t) for v in val) for t in target)
    if kind == "tuple":
        return isinstance(target, dict) and set(val) == set(target) and all(_same(val[k], target[k]) for k in val)
    return False


def _verdict_from_matches(opts: List[str], matcher, allow_no_solution: bool = False) -> Optional[Tuple[int, ...]]:
    """Indexes of options equal to the computed answer, or None when an option cannot be read."""
    hits, unreadable = [], []
    for i, t in enumerate(opts):
        if allow_no_solution and _NO_SOLUTION_RE.search(t):
            if matcher(None):
                hits.append(i)
            continue
        if _CANNOT_RE.search(t):
            unreadable.append(i)
            continue
        try:
            if matcher(t):
                hits.append(i)
        except _Unsupported:
            unreadable.append(i)
    if unreadable and not hits:
        return None  # the answer might be the option we could not read
    if unreadable and len(hits) == 1 and len(unreadable) > 1:
        return None
    return tuple(hits)


def _solve_equations(eq_texts: List[str], func_defs, target_vars: Optional[List[str]]):
    import sympy

    eqs, symbols = [], set()
    for t in eq_texts:
        lhs, rhs = t.split("=")
        e = _expr(lhs, func_defs) - _expr(rhs, func_defs)
        eqs.append(e)
        symbols |= e.free_symbols
    syms = sorted(symbols, key=str)
    if len(eqs) == 1 and not syms:
        # the unknown cancelled out: "2x - 3 = 2x + 6" has no solution (an identity is unsupported)
        if _same(eqs[0], sympy.Integer(0)):
            raise _Unsupported("identity")
        return None, []
    if len(eqs) == 1 and len(syms) == 1:
        sols = sympy.solve(eqs[0], syms[0], dict=False)
        real = [s for s in sols if s.is_real is not False]
        if any(s.has(sympy.I) for s in real):
            raise _Unsupported("complex roots")
        # verify each root (drops extraneous roots of radical equations)
        good = [s for s in real if _same(eqs[0].subs(syms[0], s), sympy.Integer(0))]
        return str(syms[0]), good
    if len(eqs) == 2 and len(syms) == 2 and all(sympy.Poly(e, *syms).total_degree() == 1 for e in eqs):
        sol = sympy.solve(eqs, syms, dict=True)
        if len(sol) != 1:
            raise _Unsupported("system without a unique solution")
        return [str(s) for s in syms], {str(k): v for k, v in sol[0].items()}
    raise _Unsupported("equation shape not supported")


def _sci_ok(t: str, value) -> bool:
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:\\times|×|\*|x)\s*10\s*\^\s*\{?\s*(-?\d+)\s*\}?\s*$", _clean_option(t))
    if not m:
        raise _Unsupported("not a scientific-notation option")
    mant = float(m.group(1))
    if not 1 <= abs(mant) < 10:
        return False
    import sympy

    return _same(sympy.Rational(m.group(1)) * sympy.Integer(10) ** int(m.group(2)), value)


def solve_options(item: dict) -> Optional[Tuple[int, ...]]:
    """Indexes of the options that are actually correct, or None when the question is not readable."""
    stem = str(item.get("question_text") or "").strip()
    if item.get("question_latex") and not _SEGMENT_RE.search(stem) and "=" not in stem:
        stem = f"{stem} \\({item['question_latex']}\\)"
    opts = [_option_text(o) if isinstance(o, dict) else str(o) for o in item.get("options") or []]
    if not stem or len(opts) < 2:
        return None
    try:
        return _solve(stem, opts)
    except _Unsupported as exc:
        logger.debug("mcq verify: unsupported (%s): %s", exc, stem[:80])
        return None
    except Exception as exc:  # noqa: BLE001 - a checker bug must never look like a verdict
        logger.info("mcq verify: error (%s): %s", exc, stem[:80])
        return None


def _solve(stem: str, opts: List[str]) -> Optional[Tuple[int, ...]]:
    import sympy

    words, segs = _stem_parts(stem)
    if _UNSUPPORTED_WORDS.search(words):
        return None

    # scientific notation: value of the one number in the stem, mantissa in [1, 10)
    if re.search(r"scientific notation", stem, re.I):
        nums = re.findall(r"-?\d+\.\d+|-?\d+", stem)
        if len(nums) != 1:
            return None
        value = sympy.Rational(nums[0])
        return _verdict_from_matches(opts, lambda t: _sci_ok(t, value))

    # f(x) = ... definitions; every other segment must use them
    func_defs: List[Tuple[str, str, str]] = []
    rest: List[str] = []
    for s in segs:
        m = _FUNC_DEF_RE.match(s)
        if m and not func_defs and re.search(r"[a-z]", m.group(3)) and not re.match(r"^\s*-?\d+(\.\d+)?\s*$", m.group(3)):
            func_defs.append((m.group(1), m.group(2), tex_to_sympy_text(m.group(3))))
        else:
            rest.append(s)
    fdefs = tuple(func_defs)
    bare_vars = [s for s in rest if re.fullmatch(r"[a-z]", s)]
    maths = [s for s in rest if not re.fullmatch(r"[a-z]", s)]
    if "\\begin{cases}" in " ".join(maths):
        if len(maths) != 1:
            return None
        inner = re.search(r"\\begin\{cases\}(.*)\\end\{cases\}", maths[0], re.S)
        if not inner:
            return None
        maths = [e.strip() for e in re.split(r"\\\\|\\\s", inner.group(1)) if e.strip()]
    equations = [s for s in maths if s.count("=") == 1]
    if any(s.count("=") > 1 for s in maths):
        return None
    if len(maths) != len(equations) and equations:
        return None  # extra expressions besides the equations: not a plain solve

    if equations:
        if not _SOLVE_WORDS.search(words) and words.strip(" .:?"):
            return None
        if fdefs and any(re.search(rf"\b{fdefs[0][0]}\s*\(", e) for e in equations):
            pass
        var, answer = _solve_equations(equations, fdefs, None)
        if isinstance(var, list):  # system
            asked = [v for v in bare_vars if v in var]
            if len(asked) == 1 and re.search(r"value of", words, re.I):
                target = answer[asked[0]]
                return _verdict_from_matches(opts, lambda t: _match(_option_values(t, var), target))
            if bare_vars and not asked:
                return None
            return _verdict_from_matches(opts, lambda t: _match(_option_values(t, var), answer))
        if var is None:
            var = bare_vars[0] if bare_vars else "x"
        if bare_vars and any(v != var for v in bare_vars):
            return None  # asks for a different quantity than the unknown
        if not answer:
            return _verdict_from_matches(opts, lambda t: t is None or False, allow_no_solution=True)

        def m_eq(t):
            if t is None:
                return False
            kind_vals = _option_values(t, [var])
            target = answer[0] if len(answer) == 1 else list(answer)
            if kind_vals[0] == "scalar" and len(answer) > 1:
                return False
            return _match(kind_vals, target)

        hits = _verdict_from_matches(opts, m_eq, allow_no_solution=True)
        if hits == () and not words.strip(" .:?"):
            # A bare equation with no instruction may be asking for something other than
            # its roots (the discriminant, say): no match is not proof of a bad key.
            return None
        return hits

    if len(maths) != 1 or bare_vars:
        return None
    if not _EXPR_WORDS.search(words) and words.strip(" .:?"):
        return None
    target = _expr(maths[0], fdefs)
    variables = _variables(target)
    if re.search(r"factori[sz]e|factor", words, re.I) and not variables:
        return None
    return _verdict_from_matches(opts, lambda t: _match(_option_values(t, variables), target))


# ── verdict ──────────────────────────────────────────────────────────────────

_EQUIVALENCE_OK = re.compile(r"lowest (terms|form)|simplest form|scientific notation|standard form|fraction", re.I)


def equivalent_option_pairs(item: dict) -> List[Tuple[int, int]]:
    """Pairs of options with the same mathematical value (two answers that are both right)."""
    stem = str(item.get("question_text") or "")
    if _EQUIVALENCE_OK.search(stem):
        return []
    opts = [_option_text(o) if isinstance(o, dict) else str(o) for o in item.get("options") or []]
    vals: Dict[int, object] = {}
    for i, t in enumerate(opts):
        try:
            kind, v = _option_values(t, [])
        except _Unsupported:
            continue
        except Exception:  # noqa: BLE001
            continue
        if kind == "scalar":
            vals[i] = v
    pairs = []
    keys = sorted(vals)
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            if _same(vals[keys[a]], vals[keys[b]]):
                pairs.append((keys[a], keys[b]))
    return pairs


def _verdict_key(item: dict) -> str:
    opts = tuple(_option_text(o) if isinstance(o, dict) else str(o) for o in item.get("options") or [])
    return repr((item.get("question_text"), item.get("question_latex"), opts, item.get("correct_option_index")))


_VERDICT_CACHE: Dict[str, KeyVerdict] = {}


def check_answer_key(item: dict) -> KeyVerdict:
    """Deterministic verdict on the stored key: ok / wrong_key / no_correct_option /
    multiple_correct / unknown."""
    cache_key = _verdict_key(item)
    cached = _VERDICT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    verdict = _check(item)
    if len(_VERDICT_CACHE) > 4096:
        _VERDICT_CACHE.clear()
    _VERDICT_CACHE[cache_key] = verdict
    return verdict


def _check(item: dict) -> KeyVerdict:
    key = item.get("correct_option_index")
    pairs = equivalent_option_pairs(item)
    if pairs and any(key in p for p in pairs):
        return KeyVerdict(MULTIPLE, tuple(sorted({i for p in pairs for i in p})), "options with the same value")
    hits = solve_options(item)
    if hits is None:
        return KeyVerdict(UNKNOWN, (), "not machine-checkable")
    if not hits:
        return KeyVerdict(NO_CORRECT, (), "no option matches the worked answer")
    if len(hits) > 1:
        return KeyVerdict(MULTIPLE, hits, "more than one option matches the worked answer")
    if hits[0] != key:
        return KeyVerdict(WRONG_KEY, hits, "key differs from the worked answer")
    return KeyVerdict(OK, hits, "worked answer matches the key")
