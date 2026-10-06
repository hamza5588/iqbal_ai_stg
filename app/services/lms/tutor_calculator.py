"""Exact arithmetic for the AI tutor.

Language models are unreliable at arithmetic: the DIL feedback of 5 Oct 2026
shows the tutor accepting "100 + 240 + 144 = 480" (it is 484). Nothing here
asks a model to compute. The tutor uses this module three ways:

* ``calculate`` is offered to the model as a tool, for anything it wants worked out;
* ``build_calculator_context`` works out every sum already on the table (the
  tutor's last question, the student's message) and hands the model the results;
* ``find_wrong_equalities`` checks the model's reply before the student sees it.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

MAX_EXPRESSION_CHARS = 200
_MAX_EXPONENT = 200
_MAX_DIGITS = 400

_NUM = r"\d+(?:\.\d+)?"
# What a normalised arithmetic expression may be made of.
_SPAN = r"(?:sqrt|[0-9.+\-*/^() ])+"
_SPAN_RE = re.compile(_SPAN)
_CHAIN_RE = re.compile(rf"{_SPAN}(?:(?:=|≈){_SPAN})+")
_OPERATOR_RE = re.compile(r"[+*/^]|sqrt|(?<=[\d)])\s*-")
_FUNCTION_BEFORE_RE = re.compile(r"\\?(?:log|ln|lg|sin|cos|tan|sec|csc|cot|exp|mod|lim|max|min|gcd|lcm)\s*(?:_\S+\s*)?$", re.I)
_REMAINDER_AFTER_RE = re.compile(r"^\s*(?:r\b|rem\b|remainder|R\s*\d)", re.I)
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_BARE_NUMBER_RE = re.compile(rf"^\s*(?:=|is|it'?s|answer\s*(?:is|=|:)?|i\s*(?:get|got)|we\s*get)?\s*\$?\s*(-?{_NUM})\s*\$?\s*[.!?]?\s*$", re.I)


class CalculatorError(ValueError):
    """The text is not arithmetic this calculator can evaluate."""


@dataclass(frozen=True)
class Calculation:
    expression: str  # normalised, readable: "100 + 240 + 144"
    value: Fraction
    approximate: bool = False  # an irrational step (a non-perfect square root) was rounded

    @property
    def text(self) -> str:
        return format_number(self.value, self.approximate)


# ---------------------------------------------------------------- normalising

def normalize_math(text: str) -> str:
    """LaTeX / typed maths -> plain calculator syntax (``\\frac{1}{2}`` -> ``((1)/(2))``)."""
    s = text or ""
    # Models write "1 148" with a thin / no-break space as the thousands separator, and no-break hyphens.
    s = re.sub(r"(?<=\d)[   ](?=\d{3}(?!\d))", "", s)
    s = re.sub(r"[     ]", " ", s).replace("‑", "-")
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+", lambda m: "^(" + m.group(0).translate(_SUPERSCRIPTS) + ")", s)
    s = re.sub(r"\\\(|\\\)|\\\[|\\\]|\$", " ", s)
    s = re.sub(r"\\(?:left|right|displaystyle|,|;|!|:| )", " ", s)
    s = re.sub(r"\\(?:times|cdot)(?![A-Za-z])|[×·⋅]", "*", s)
    s = re.sub(r"\\div(?![A-Za-z])|÷", "/", s)
    # "**" in a tutor reply is markdown bold ("= **484**"), not a power; calculate() maps the Python power first.
    s = s.replace("−", "-").replace("–", "-").replace("\\%", "%").replace("\\approx", "≈").replace("**", "")
    for _ in range(6):  # innermost first, so nested fractions and roots unwind
        new = re.sub(r"\\[dtc]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
        new = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", new)
        new = re.sub(r"\^\s*\{([^{}]*)\}", r"^(\1)", new)
        if new == s:
            break
        s = new
    s = re.sub(rf"√\s*({_NUM})", r"sqrt(\1)", s).replace("√", "sqrt")
    s = re.sub(r"(?<=\d)(?:,|\{,\})(?=\d{3}(?!\d))", "", s)  # 1,000 and LaTeX 1{,}000 -> 1000
    s = re.sub(rf"({_NUM})\s*%\s*of\s*({_NUM})", r"(\1/100*\2)", s, flags=re.I)
    s = re.sub(rf"({_NUM})\s*%", r"(\1/100)", s)
    s = re.sub(r"(?<![\d./])(\d+)\s+(\d+)\s*/\s*(\d+)(?![\d.])", r"(\1+\2/\3)", s)  # mixed number 1 1/2
    s = re.sub(r"(?<=[\d)])\((?=[-\d(s])", "*(", s)  # 2(3+4) -> 2*(3+4)
    return s


# ---------------------------------------------------------------- evaluating

def _sqrt(value: Fraction) -> Tuple[Fraction, bool]:
    if value < 0:
        raise CalculatorError("square root of a negative number")
    num, den = math.isqrt(value.numerator), math.isqrt(value.denominator)
    if num * num == value.numerator and den * den == value.denominator:
        return Fraction(num, den), False
    return Fraction(math.sqrt(value)).limit_denominator(10 ** 12), True


def _eval(node: ast.AST) -> Tuple[Fraction, bool]:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Fraction(str(node.value)), False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value, approx = _eval(node.operand)
        return (-value if isinstance(node.op, ast.USub) else value), approx
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("sqrt", "abs") and len(node.args) == 1 and not node.keywords:
        value, approx = _eval(node.args[0])
        if node.func.id == "abs":
            return abs(value), approx
        root, rounded = _sqrt(value)
        return root, approx or rounded
    if isinstance(node, ast.BinOp):
        left, la = _eval(node.left)
        right, ra = _eval(node.right)
        approx = la or ra
        if isinstance(node.op, ast.Add):
            return left + right, approx
        if isinstance(node.op, ast.Sub):
            return left - right, approx
        if isinstance(node.op, ast.Mult):
            return left * right, approx
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise CalculatorError("division by zero")
            return left / right, approx
        if isinstance(node.op, ast.Pow):
            if right.denominator == 1:
                if abs(right) > _MAX_EXPONENT or len(str(left.numerator)) + len(str(left.denominator)) > 40:
                    raise CalculatorError("power too large")
                if left == 0 and right < 0:
                    raise CalculatorError("division by zero")
                return left ** int(right), approx
            if left < 0:
                raise CalculatorError("fractional power of a negative number")
            if right == Fraction(1, 2):
                root, rounded = _sqrt(left)
                return root, approx or rounded
            return Fraction(float(left) ** float(right)).limit_denominator(10 ** 12), True
    raise CalculatorError("unsupported expression")


def calculate(expression: str) -> Calculation:
    """Evaluate one arithmetic expression exactly (+ - * / ^ sqrt, brackets, %, fractions)."""
    cleaned = " ".join(normalize_math(str(expression or "").replace("**", "^")).split())
    if not cleaned or len(cleaned) > MAX_EXPRESSION_CHARS:
        raise CalculatorError("empty or too long")
    if not _SPAN_RE.fullmatch(cleaned):
        raise CalculatorError("only numbers and + - * / ^ sqrt ( ) are supported")
    try:
        tree = ast.parse(cleaned.replace("^", "**"), mode="eval")
        value, approximate = _eval(tree)
    except CalculatorError:
        raise
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError, MemoryError) as exc:
        raise CalculatorError(str(exc) or "not a valid expression") from exc
    if len(str(value.numerator)) > _MAX_DIGITS:
        raise CalculatorError("result too large")
    return Calculation(expression=_pretty(cleaned), value=value, approximate=approximate)


def _pretty(expression: str) -> str:
    out = re.sub(r"\s*([+*/])\s*", r" \1 ", expression)
    out = re.sub(r"(?<=[\d)])\s*-\s*", " - ", out)
    return " ".join(out.replace("*", "×").replace("/", "÷").split())


def format_number(value: Fraction, approximate: bool = False) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    den = value.denominator
    for prime in (2, 5):
        while den % prime == 0:
            den //= prime
    if den == 1 and not approximate:  # terminating decimal: exact
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    decimal = f"{float(value):.6g}"
    if approximate:
        return f"≈ {decimal}"
    return f"{value.numerator}/{value.denominator} (≈ {decimal})"


def calculator_tool_output(expression: str) -> str:
    """What the model is told when it calls the calculator tool."""
    try:
        result = calculate(expression)
    except CalculatorError as exc:
        return f"Cannot calculate '{expression}': {exc}. Send numbers and + - * / ^ sqrt() only."
    return f"{result.expression} = {result.text}"


# ---------------------------------------------------------------- finding sums in prose

def _side_is_free_standing(text: str, start: int, end: int) -> bool:
    """False when the numbers belong to algebra or a function: ``3x``, ``x^2 + 3``, ``log 100``."""
    raw = text[start:end]
    before = text[:start]
    stripped = raw.strip()
    if not stripped:
        return False
    # "x" not in "" guards: the empty string is "in" every string.
    prev = before.rstrip()[-1:]
    if stripped[0] in "+*/^" or (stripped[0] == "-" and prev and (prev.isalnum() or prev in ")}_")):
        return False
    touching = before[-1:]
    if touching and (touching.isalpha() or touching in "_\\."):
        return False
    if _FUNCTION_BEFORE_RE.search(before):
        return False
    after = text[end:end + 1]
    return not (after and (after.isalpha() or after in "_\\{"))


def _split_juxtaposed(span: str) -> List[Tuple[int, int]]:
    """"1. 3 + 4" is a list number and a sum, not one expression: split where two numbers touch."""
    pieces, last = [], 0
    for m in re.finditer(r"(?<=[\d.)])\s+(?=[\d(])", span):
        pieces.append((last, m.start()))
        last = m.end()
    pieces.append((last, len(span)))
    return pieces


def _trim(text: str, start: int, end: int) -> Tuple[int, int]:
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] in " .":
        end -= 1
    # drop brackets left open or closed by the surrounding sentence: "(so 3 + 4)"
    while end > start and text[start:end].count(")") > text[start:end].count("(") and text[end - 1] == ")":
        end -= 1
    while end > start and text[start:end].count("(") > text[start:end].count(")") and text[start] == "(":
        start += 1
    return start, end


def find_calculations(text: str, limit: int = 8) -> List[Calculation]:
    """Every free-standing numeric expression in ``text`` that has an operator, evaluated."""
    norm = normalize_math(text)
    found: List[Calculation] = []
    seen = set()
    for m in _SPAN_RE.finditer(norm):
        for a, b in _split_juxtaposed(m.group(0)):
            start, end = _trim(norm, m.start() + a, m.start() + b)
            piece = norm[start:end]
            if not _OPERATOR_RE.search(piece) or not _side_is_free_standing(norm, start, end):
                continue
            try:
                calc = calculate(piece)
            except CalculatorError:
                continue
            if calc.expression not in seen:
                seen.add(calc.expression)
                found.append(calc)
            if len(found) >= limit:
                return found
    return found


# ---------------------------------------------------------------- checking "a + b = c"

@dataclass(frozen=True)
class WrongEquality:
    expression: str  # "100 + 240 + 144"
    claimed: str     # "480"
    correct: str     # "484"

    def describe(self) -> str:
        return f"{self.expression} = {self.correct}, not {self.claimed}"


def _decimals(number_text: str) -> int:
    return len(number_text.split(".")[1]) if "." in number_text else 0


def _matches(value: Fraction, other: Fraction, other_text: str, approximate: bool) -> bool:
    if value == other:
        return True
    places = _decimals(other_text)
    if places == 0 and not approximate:
        return False  # a whole-number claim must be exact: 7 / 2 is not 3
    # Otherwise the claim may be the value rounded, or cut off, at the digits shown: 2/3 = 0.67 or 0.66.
    scaled = value * 10 ** places
    cut_off = Fraction(math.trunc(scaled))
    rounded = Fraction(math.floor(scaled + Fraction(1, 2)))
    return other * 10 ** places in (cut_off, rounded)


def find_wrong_equalities(text: str) -> List[WrongEquality]:
    """Statements such as ``100 + 240 + 144 = 480`` whose two sides are not equal.

    Only purely numeric sides are compared, so algebra (``x + 3 = 7``, ``(A+B)^2 = 480``)
    is never judged.
    """
    norm = normalize_math(text)
    wrong: List[WrongEquality] = []
    for chain in _CHAIN_RE.finditer(norm):
        if _REMAINDER_AFTER_RE.match(norm[chain.end():chain.end() + 14]):
            continue  # "10 / 4 = 2 remainder 2"
        sides = []
        pos = chain.start()
        for part in re.split(r"(=|≈)", chain.group(0)):
            if part in ("=", "≈"):
                pos += len(part)
                sides.append(part)
                continue
            start, end = _trim(norm, pos, pos + len(part))
            pos += len(part)
            calc = None
            if _side_is_free_standing(norm, start, end):
                try:
                    calc = calculate(norm[start:end])
                except CalculatorError:
                    calc = None
            sides.append((calc, norm[start:end].strip()))
        for i in range(0, len(sides) - 2, 2):
            (left, left_text), sign, (right, right_text) = sides[i], sides[i + 1], sides[i + 2]
            if left is None or right is None:
                continue
            left_has_op = bool(_OPERATOR_RE.search(left_text))
            right_has_op = bool(_OPERATOR_RE.search(right_text))
            if not (left_has_op or right_has_op):
                continue  # "100 = 2" out of context (e.g. after "log") proves nothing
            approx = sign == "≈" or left.approximate or right.approximate
            # judge the plain number against the worked side
            worked, stated, stated_text = (left, right, right_text) if not right_has_op else (right, left, left_text)
            if right_has_op and left_has_op:
                if left.value != right.value and not (approx and abs(left.value - right.value) <= Fraction(1, 100)):
                    wrong.append(WrongEquality(left.expression, right.expression, format_number(left.value, left.approximate)))
                continue
            if not _matches(worked.value, stated.value, stated_text, approx):
                wrong.append(WrongEquality(worked.expression, stated_text, worked.text))
    return wrong


# ---------------------------------------------------------------- context for the model

def _last_question_sentence(text: str) -> str:
    """The sentence the tutor ended on, if it is a question."""
    body = (text or "").strip()
    if not body.endswith("?"):
        return ""
    # do not split on the dot of a decimal number
    sentences = re.split(r"(?<=[.!?])(?<!\d\.)\s+|\n+", body)
    return sentences[-1] if sentences else ""


def student_number(message: str) -> Optional[str]:
    """The number, when the student's whole message is just an answer ("480", "= 480", "I got 480")."""
    m = _BARE_NUMBER_RE.match(normalize_math(message or "").strip())
    return m.group(1) if m else None


def build_calculator_context(history: Optional[List[Dict[str, str]]], message: str) -> Optional[str]:
    """Verified results for the sums in play, to append to the tutor's system prompt."""
    last_tutor = ""
    for turn in reversed(history or []):
        if turn.get("role") == "assistant":
            last_tutor = turn.get("content") or ""
            break
    calcs: List[Calculation] = []
    for source in (last_tutor, message):
        for calc in find_calculations(source):
            if all(calc.expression != c.expression for c in calcs):
                calcs.append(calc)
    if not calcs:
        return None

    lines = [
        "CALCULATOR RESULTS (computed exactly by the calculator, not by you - treat them as the truth):",
    ]
    lines += [f"- {c.expression} = {c.text}" for c in calcs[:8]]

    answer = student_number(message)
    asked = find_calculations(_last_question_sentence(last_tutor))
    if answer is not None and len(asked) == 1:
        target = asked[0]
        try:
            given = Fraction(answer)
        except (ValueError, ZeroDivisionError):
            given = None
        if given is not None:
            if _matches(target.value, given, answer, target.approximate):
                lines.append(f"- The student answered {answer}. That equals {target.expression}.")
            else:
                lines.append(
                    f"- The student answered {answer}, but {target.expression} = {target.text}. "
                    f"If your question asked for the value of {target.expression}, the student's answer is WRONG: "
                    "do not say it is right, do not repeat their number as the result. Kindly ask them to check the "
                    "calculation again (you may point to the step that went wrong, without doing it for them)."
                )
    lines.append(
        "Never state a calculation result that disagrees with these. They are for judging and checking - "
        "your other rules about when to reveal an answer still apply."
    )
    return "\n".join(lines)


CALCULATOR_TOOL_RULE = (
    "ARITHMETIC: you make mistakes when you calculate in your head. For every calculation "
    "(adding, subtracting, multiplying, dividing, powers, roots, percentages) call the `calculate` tool and use its "
    "result. Before telling a student that a number they gave is right or wrong, check it with `calculate`."
)

CALCULATOR_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Evaluate one arithmetic expression exactly and return the result. Use it for every calculation and to "
            "check a student's numeric answer. Supports + - * / ^ sqrt() and brackets, e.g. '100 + 240 + 144', "
            "'(10 + 12)^2', '3/4 + 1/2', 'sqrt(144)', '15/100 * 240'. Numbers only: replace letters with their values first."
        ),
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "The arithmetic expression, numbers and operators only."}},
            "required": ["expression"],
        },
    },
}


def looks_numeric(*texts: Optional[str]) -> bool:
    """Cheap test for 'this turn may involve a calculation' (a digit, or a number word with an operation word)."""
    blob = " ".join(t or "" for t in texts)
    if re.search(r"\d", blob):
        return True
    return bool(re.search(r"\b(plus|minus|times|divided|multipl|subtract|sum of|product of|percent|square root|squared|cubed)\b", blob, re.I))
