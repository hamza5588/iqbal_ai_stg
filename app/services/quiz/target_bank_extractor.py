"""Faithful extraction of the printed MCQs in a Target PDF question bank (CPU only).

No LLM rewrites anything here. Digital PDFs are read from their character spans:

* PyMuPDF spans carry a superscript flag, so ``(a + b)²`` stays ``(a + b)²``
  instead of the flattened ``(a + b)2`` that plain text extraction produces.
* Layout the text stream cannot represent - stacked fractions, radical indices,
  drawn fraction bars / vinculums - is detected from geometry and drawings and the
  question is marked for admin review instead of guessing.
* The answer-key table (with its own Domain/Topic columns) is read with pdfplumber,
  an independent engine, so inline labels and the key cross-check each other.

Benchmark on the real "DIL SAATHI - Practice Question Bank" (34 pages, 200 MCQs,
CPU): PyMuPDF spans ~0.4s/2 pages; Docling (no OCR, no formula model) 121s/2 pages
and +760 MB RSS and still flattened the superscripts - so Docling is not used.
Scanned PDFs (no text layer) are reported as such; their questions cannot be
auto-extracted faithfully and need the vision pipeline + human review.
"""
from __future__ import annotations

import io
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

EXTRACTOR_VERSION = "native_spans_v1"

_Q_START = re.compile(r"^\s*Q\s*(\d{1,4})\s*[.)]\s*(.*)$")
_OPT_START = re.compile(r"^\s*([A-Da-d])\s*[.)]\s*(.*)$")
_META = re.compile(
    r"Domain\s*:\s*(?P<domain>.+?)\s*\|\s*Topic\s*:\s*(?P<topic>.+?)"
    r"(?:\s*\|\s*Cognitive\s*Level\s*:\s*(?P<cog>.+?))?"
    # The Baseline paper prints "Intended difficulty:", the banks "Difficulty:".
    r"(?:\s*\|\s*(?:Intended\s+)?Difficulty\s*:\s*(?P<diff>.+?))?\s*$",
    re.IGNORECASE,
)
_ANSWER_KEY_HEADING = re.compile(r"answer\s*key", re.IGNORECASE)
_PAGE_HEADER = re.compile(r"\|\s*Page\s+\d+\s*$|^\s*Page\s+\d+\s*$", re.IGNORECASE)

_SUPERSCRIPT_MAP = str.maketrans(
    "0123456789+-−=()ni",
    "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ",
)
_SUPERSCRIPT_CHARS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT_MAP = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")


@dataclass
class Span:
    text: str
    kind: str  # normal | sup | sub
    bbox: Tuple[float, float, float, float]


@dataclass
class Line:
    page: int
    bbox: Tuple[float, float, float, float]
    spans: List[Span]

    @property
    def raw(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class ExtractedMCQ:
    number: int
    stem: str
    options: List[str]
    printed_domain: Optional[str] = None
    printed_topic: Optional[str] = None
    cognitive_level: Optional[str] = None
    difficulty: Optional[str] = None
    pages: List[int] = field(default_factory=list)
    regions: List[dict] = field(default_factory=list)  # [{page, bbox}]
    flags: List[str] = field(default_factory=list)  # layout issues seen while parsing
    sup_runs: int = 0
    sup_chars_native: int = 0
    sub_chars_native: int = 0


@dataclass
class AnswerKeyEntry:
    answer: str
    domain: Optional[str] = None
    topic: Optional[str] = None
    cognitive_level: Optional[str] = None


@dataclass
class BankExtraction:
    pdf_type: str
    page_count: int
    questions: List[ExtractedMCQ]
    answer_key: Dict[int, AnswerKeyEntry]
    warnings: List[str]
    extractor_version: str = EXTRACTOR_VERSION


# ---------------------------------------------------------------------------
# Character-level helpers
# ---------------------------------------------------------------------------


def normalize_glyphs(text: str) -> str:
    """Map Word's math alphanumerics (𝑎, 𝟔) to plain letters/digits; keep symbols."""
    out = []
    for ch in text:
        code = ord(ch)
        if 0x1D400 <= code <= 0x1D7FF:  # Mathematical Alphanumeric Symbols
            out.append(unicodedata.normalize("NFKC", ch))
        elif ch == "−":
            out.append("−")
        elif ch in (" ", " ", " ", " "):
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def to_superscript(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    converted = stripped.translate(_SUPERSCRIPT_MAP)
    if all(c in _SUPERSCRIPT_CHARS for c in converted):
        return converted
    return f"^({stripped})"


def to_subscript(text: str) -> str:
    stripped = text.strip()
    converted = stripped.translate(_SUBSCRIPT_MAP)
    if converted != stripped and all(not c.isascii() or c in " " for c in converted):
        return converted
    return f"_({stripped})"


def classify_spans(spans_raw: List[dict]) -> List[str]:
    """normal | sup | sup2 (script inside a script) | sub, from size + baseline offset.

    The baseline comes from full-size spans only, so a subscript span cannot
    drag the baseline down onto itself (``log₂5`` stays a subscript).
    """
    if not spans_raw:
        return []
    base = max(s["size"] for s in spans_raw)
    full = [s for s in spans_raw if s["size"] >= 0.9 * base and s["text"].strip()]
    baseline = statistics.median(s["bbox"][3] for s in full) if full else spans_raw[0]["bbox"][3]
    kinds = []
    for s in spans_raw:
        size, bottom = s["size"], s["bbox"][3]
        if size >= 0.9 * base or not s["text"].strip():
            kinds.append("normal")
        elif bottom < baseline - 0.15 * base:
            deep = size < 0.65 * base or bottom < baseline - 0.55 * base
            kinds.append("sup2" if deep else "sup")
        elif bottom > baseline + 0.08 * base:
            kinds.append("sub")
        else:
            kinds.append("normal")
    return kinds


def render_spans(spans: List[Span]) -> str:
    parts = []
    i = 0
    while i < len(spans):
        s = spans[i]
        if s.kind == "sup":
            group = s.text
            # A deeper script right after belongs inside this exponent: x^(3y²).
            while i + 1 < len(spans) and spans[i + 1].kind == "sup2":
                i += 1
                group += to_superscript(spans[i].text)
            parts.append(to_superscript(group))
        elif s.kind == "sup2":
            parts.append(to_superscript(s.text))
        elif s.kind == "sub":
            parts.append(to_subscript(s.text))
        else:
            parts.append(s.text)
        i += 1
    return "".join(parts)


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Page reading
# ---------------------------------------------------------------------------


def _read_lines(doc) -> Tuple[List[Line], Dict[int, list]]:
    lines: List[Line] = []
    hbars: Dict[int, list] = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        width = page.rect.width
        bars = []
        for d in page.get_drawings():
            for item in d.get("items", []):
                if item[0] == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.y - p2.y) < 1.0 and 2.0 < abs(p2.x - p1.x) < width * 0.45:
                        bars.append((min(p1.x, p2.x), p1.y, max(p1.x, p2.x)))
                elif item[0] == "re":
                    r = item[1]
                    if r.height < 1.5 and 2.0 < r.width < width * 0.45:
                        bars.append((r.x0, (r.y0 + r.y1) / 2, r.x1))
        hbars[pno + 1] = bars
        data = page.get_text("dict", sort=False)
        for block in data.get("blocks", []):
            for ln in block.get("lines", []):
                spans_raw = [s for s in ln.get("spans", []) if s.get("text")]
                if not spans_raw:
                    continue
                spans = [
                    Span(text=normalize_glyphs(s["text"]), kind=kind, bbox=tuple(s["bbox"]))
                    for s, kind in zip(spans_raw, classify_spans(spans_raw))
                ]
                lines.append(Line(page=pno + 1, bbox=tuple(ln["bbox"]), spans=spans))
    return lines, hbars


def detect_pdf_type(doc) -> str:
    text_pages = sum(1 for p in doc if len(p.get_text().strip()) > 80)
    if text_pages == 0:
        return "scanned"
    if text_pages < doc.page_count * 0.6:
        return "mixed"
    return "digital"


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------


def _answer_key_pages(lines: List[Line]) -> List[int]:
    start = None
    for ln in lines:
        if _ANSWER_KEY_HEADING.search(ln.raw) and len(ln.raw) < 80:
            start = ln.page
            break
    if start is None:
        return []
    last = max(ln.page for ln in lines)
    return list(range(start, last + 1))


def parse_answer_key(pdf_bytes: bytes, pages: List[int]) -> Dict[int, AnswerKeyEntry]:
    """Answer-key table via pdfplumber (independent of the PyMuPDF question parse)."""
    if not pages:
        return {}
    import pdfplumber

    key: Dict[int, AnswerKeyEntry] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pno in pages:
            if pno - 1 >= len(pdf.pages):
                continue
            for table in pdf.pages[pno - 1].extract_tables() or []:
                header = None
                for row in table:
                    # Merged cells come back as None/"" padding whose position differs
                    # between header and data rows - compact both before mapping columns.
                    cells = [re.sub(r"\s+", " ", normalize_glyphs(c or "")).strip() for c in row]
                    cells = [c for c in cells if c]
                    lowered = [c.lower() for c in cells]
                    if "answer" in lowered:
                        header = lowered
                        continue
                    if not cells or not cells[0].isdigit():
                        continue
                    qno = int(cells[0])
                    col = (lambda name, default: cells[header.index(name)]
                           if header and name in header and header.index(name) < len(cells) else default)
                    answer = (col("answer", cells[1] if len(cells) > 1 else "") or "").strip().upper()[:1]
                    if answer not in "ABCD" or not answer:
                        continue
                    key[qno] = AnswerKeyEntry(
                        answer=answer,
                        domain=col("domain", cells[2] if len(cells) > 2 else None) or None,
                        topic=col("topic", cells[3] if len(cells) > 3 else None) or None,
                        cognitive_level=col("cognitive level", cells[4] if len(cells) > 4 else None) or None,
                    )
    return key


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------


def _bar_inside(bars, page: int, region) -> bool:
    x0, y0, x1, y1 = region
    for bx0, by, bx1 in bars.get(page, []):
        if y0 - 1 <= by <= y1 + 1 and bx0 >= x0 - 2 and bx1 <= x1 + 40:
            return True
    return False


def parse_questions(lines: List[Line], hbars: Dict[int, list], stop_page: Optional[int]) -> List[ExtractedMCQ]:
    questions: List[ExtractedMCQ] = []
    cur: Optional[ExtractedMCQ] = None
    cur_part: List[Span] = []  # spans of the stem or of the option being read
    target = "stem"
    margin_x: Dict[int, float] = {}
    prev_line: Optional[Line] = None
    meta_buf = ""

    # Running headers/footers: the same text (page numbers ignored) in the top or
    # bottom margin of two or more pages. Footers can sit early in the content
    # stream, so position - not reading order - decides.
    page_bottom: Dict[int, float] = {}
    for ln in lines:
        page_bottom[ln.page] = max(page_bottom.get(ln.page, 0.0), ln.bbox[3])

    def in_margin(ln: Line) -> bool:
        raw = ln.raw.strip()
        # Question, option and Domain/Topic lines are content even at a page edge.
        if _Q_START.match(raw) or _OPT_START.match(raw) or raw.lower().startswith("domain"):
            return False
        return ln.bbox[1] < 75 or ln.bbox[1] > page_bottom.get(ln.page, 1e9) - 40

    def margin_key(ln: Line) -> str:
        return re.sub(r"\d+", "#", re.sub(r"\s+", " ", ln.raw.strip()))

    margin_pages: Dict[str, set] = {}
    for ln in lines:
        if in_margin(ln) and ln.raw.strip():
            margin_pages.setdefault(margin_key(ln), set()).add(ln.page)
    running_margins = {k for k, pages in margin_pages.items() if len(pages) >= 2}

    # Left margin per page = most common x0 of question/option starts.
    for ln in lines:
        if _Q_START.match(ln.raw) or _OPT_START.match(ln.raw):
            margin_x.setdefault(ln.page, ln.bbox[0])
            margin_x[ln.page] = min(margin_x[ln.page], ln.bbox[0])

    def flush_part():
        nonlocal cur_part
        if cur is None:
            cur_part = []
            return
        text = _clean(render_spans(cur_part))
        if target == "stem":
            cur.stem = _clean(f"{cur.stem} {text}") if cur.stem else text
        else:
            cur.options[-1] = _clean(f"{cur.options[-1]} {text}") if cur.options[-1] else text
        cur.sup_runs += sum(1 for s in cur_part if s.kind in ("sup", "sup2") and s.text.strip())
        cur.sup_chars_native += sum(len(s.text.replace(" ", "")) for s in cur_part if s.kind in ("sup", "sup2"))
        cur.sub_chars_native += sum(len(s.text.replace(" ", "")) for s in cur_part if s.kind == "sub")
        if any(s.kind == "sup2" for s in cur_part) and "nested_script" not in cur.flags:
            cur.flags.append("nested_script")
        cur_part = []

    def add_region(ln: Line):
        for reg in cur.regions:
            if reg["page"] == ln.page:
                b = reg["bbox"]
                reg["bbox"] = [min(b[0], ln.bbox[0]), min(b[1], ln.bbox[1]), max(b[2], ln.bbox[2]), max(b[3], ln.bbox[3])]
                return
        cur.regions.append({"page": ln.page, "bbox": list(ln.bbox)})
        cur.pages.append(ln.page)

    def finish():
        nonlocal cur, meta_buf
        if cur is None:
            return
        flush_part()
        if meta_buf and not cur.printed_topic:
            cur.flags.append("metadata_line_unparsed")
        for reg in cur.regions:
            if _bar_inside(hbars, reg["page"], reg["bbox"]):
                cur.flags.append("drawn_math_rule")  # fraction bar / radical vinculum
                break
        questions.append(cur)
        cur, meta_buf = None, ""

    for ln in lines:
        if stop_page and ln.page >= stop_page and _ANSWER_KEY_HEADING.search(ln.raw):
            break
        raw = ln.raw.strip()
        if not raw or (in_margin(ln) and (_PAGE_HEADER.search(raw) or margin_key(ln) in running_margins)):
            continue
        q = _Q_START.match(raw)
        if q:
            finish()
            cur = ExtractedMCQ(number=int(q.group(1)), stem="", options=[])
            target = "stem"
            first = ln.spans[0]
            # drop the "Qn." prefix from the first span(s)
            prefix_len = len(raw) - len(q.group(2))
            consumed, rest = 0, []
            for s in ln.spans:
                if consumed >= prefix_len:
                    rest.append(s)
                    continue
                take = prefix_len - consumed
                if len(s.text) > take:
                    rest.append(Span(text=s.text[take:], kind=s.kind, bbox=s.bbox))
                consumed += len(s.text)
            del first
            cur_part = rest
            add_region(ln)
            prev_line = ln
            continue
        if cur is None:
            continue
        if meta_buf and _OPT_START.match(raw):
            # A wrapped metadata line never runs into the options: keep what was
            # read (flagged for review) and parse this line as option A.
            m = _META.search(meta_buf)
            if m:
                cur.printed_domain = m.group("domain").strip()
                cur.printed_topic = m.group("topic").strip()
                cur.cognitive_level = (m.group("cog") or "").strip() or None
            if "metadata_line_unparsed" not in cur.flags:
                cur.flags.append("metadata_line_unparsed")
            meta_buf = ""
        meta_probe = f"{meta_buf} {raw}".strip() if meta_buf else raw
        if meta_buf or raw.lower().startswith("domain"):
            m = _META.search(meta_probe)
            if m and (m.group("diff") or not meta_buf or "difficulty" not in meta_probe.lower()):
                if m.group("diff") or "difficulty" not in meta_probe.lower():
                    flush_part()
                    cur.printed_domain = m.group("domain").strip()
                    cur.printed_topic = m.group("topic").strip()
                    cur.cognitive_level = (m.group("cog") or "").strip() or None
                    cur.difficulty = (m.group("diff") or "").strip() or None
                    meta_buf = ""
                    add_region(ln)
                    prev_line = ln
                    continue
            meta_buf = meta_probe  # metadata wrapped onto the next line
            add_region(ln)
            prev_line = ln
            continue
        o = _OPT_START.match(raw)
        if o and len(cur.options) < 4 and o.group(1).upper() == "ABCD"[len(cur.options)]:
            flush_part()
            target = "option"
            cur.options.append("")
            prefix_len = len(raw) - len(o.group(2))
            consumed, rest = 0, []
            for s in ln.spans:
                if consumed >= prefix_len:
                    rest.append(s)
                    continue
                take = prefix_len - consumed
                if len(s.text) > take:
                    rest.append(Span(text=s.text[take:], kind=s.kind, bbox=s.bbox))
                consumed += len(s.text)
            cur_part = rest
            add_region(ln)
            prev_line = ln
            continue
        # Continuation: a wrapped line starts at the margin; a stacked fragment
        # (fraction part, radical index) is indented and/or overlaps vertically.
        margin = margin_x.get(ln.page, ln.bbox[0])
        overlaps_prev = (
            prev_line is not None and prev_line.page == ln.page
            and ln.bbox[1] < prev_line.bbox[3] - 1.0
        )
        if abs(ln.bbox[0] - margin) > 12 or overlaps_prev:
            if "stacked_math_layout" not in cur.flags:
                cur.flags.append("stacked_math_layout")
        cur_part.extend([Span(text=" ", kind="normal", bbox=ln.bbox)] + ln.spans)
        add_region(ln)
        prev_line = ln
    finish()
    return questions


_ANY_Q_MARKER = re.compile(r"(?m)^\s*(?:Q\s*)?(\d{1,4})\s*[.)]\s+\S")
_DIL_Q_MARKER = re.compile(r"(?m)^\s*Q\s*(\d{1,4})\s*[.)]")


def coverage_warnings(doc, questions: List[ExtractedMCQ], stop_page: Optional[int]) -> List[str]:
    """Never skip silently: compare parsed questions with the numbers visibly printed."""
    pages = range(doc.page_count if not stop_page else stop_page - 1)
    text = "\n".join(doc[p].get_text() for p in pages)
    parsed = {q.number for q in questions}
    dil = {int(n) for n in _DIL_Q_MARKER.findall(text)}
    out = []
    if dil - parsed:
        out.append("Printed questions not extracted: " + ", ".join(f"Q{n}" for n in sorted(dil - parsed)))
    if not dil:
        bare = {int(n) for n in _ANY_Q_MARKER.findall(text)}
        if len(bare) >= 3 and len(parsed) < len(bare):
            out.append(
                f"{len(bare)} numbered questions are printed but {len(parsed)} were extracted: this layout "
                "(no 'Qn.' numbering / no 'Domain: | Topic:' line) is not the supported DIL Target PDF format. "
                "No question from this document can be published until it is converted or entered manually."
            )
    for q in questions:
        if len(q.options) != 4:
            out.append(f"Q{q.number}: {len(q.options)} options extracted (expected 4)")
        if dil and not q.printed_topic:
            out.append(f"Q{q.number}: printed Domain/Topic line not read")
    return out


def extract_bank(pdf_bytes: bytes) -> BankExtraction:
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pdf_type = detect_pdf_type(doc)
        warnings: List[str] = []
        if pdf_type == "scanned":
            return BankExtraction(
                pdf_type=pdf_type, page_count=doc.page_count, questions=[], answer_key={},
                warnings=["PDF has no text layer (scanned). Faithful automatic extraction is not "
                          "possible; run the vision pipeline and review every question."],
            )
        if pdf_type == "mixed":
            warnings.append("Some pages have no text layer; questions on them were not extracted.")
        lines, hbars = _read_lines(doc)
        key_pages = _answer_key_pages(lines)
        questions = parse_questions(lines, hbars, key_pages[0] if key_pages else None)
        answer_key = parse_answer_key(pdf_bytes, key_pages)
        if not key_pages:
            warnings.append("No answer-key section found.")
        seen = set()
        for q in questions:
            if q.number in seen:
                warnings.append(f"Question number Q{q.number} appears more than once.")
            seen.add(q.number)
        warnings.extend(coverage_warnings(doc, questions, key_pages[0] if key_pages else None))
        return BankExtraction(
            pdf_type=pdf_type, page_count=doc.page_count, questions=questions,
            answer_key=answer_key, warnings=warnings,
        )
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Independent superscript evidence (pdfplumber character geometry)
# ---------------------------------------------------------------------------


def script_char_counts(pdf_bytes: bytes, regions: List[dict], _cache: Optional[dict] = None) -> Tuple[int, int]:
    """(superscript, subscript) character counts pdfplumber sees inside ``regions``.

    Independent of PyMuPDF (different parser, per-character geometry, no font
    flags): a character is a script when it is at most 85% of the size of the
    character just before it on the same visual line and sits clearly above
    (superscript) or below (subscript) that character's baseline. Chained
    scripts (x^(3y²)) are compared with the script they follow.
    """
    import pdfplumber

    cache = _cache if _cache is not None else {}
    pdf = cache.get("pdf")
    if pdf is None:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
        cache["pdf"] = pdf
    sup = sub = 0
    for reg in regions:
        page = pdf.pages[reg["page"] - 1]
        x0, y0, x1, y1 = reg["bbox"]
        chars = [
            c for c in page.chars
            if c["x0"] >= x0 - 1 and c["x1"] <= x1 + 1 and c["top"] >= y0 - 1 and c["bottom"] <= y1 + 1
            and c["text"].strip()
        ]
        chars.sort(key=lambda c: c["x0"])

        def left_neighbour(i: int) -> Optional[int]:
            c, best, best_x1 = chars[i], None, None
            for j, n in enumerate(chars):
                if j == i or not (n["top"] < c["bottom"] and c["top"] < n["bottom"]):
                    continue
                if -3 <= c["x0"] - n["x1"] <= 6 and n["x0"] < c["x0"] and (best_x1 is None or n["x1"] > best_x1):
                    best, best_x1 = j, n["x1"]
            return best

        kinds: Dict[int, str] = {}
        for i, c in enumerate(chars):  # x-order: a script's base is classified first
            j = left_neighbour(i)
            if j is None:
                continue
            n = chars[j]
            if kinds.get(j) and abs(c["size"] - n["size"]) < 0.3 and abs(c["bottom"] - n["bottom"]) < 0.6:
                kinds[i] = kinds[j]  # next character of the same script run
            elif c["size"] <= 0.85 * n["size"] and n["bottom"] - c["bottom"] >= 0.2 * n["size"]:
                kinds[i] = "sup"
            elif c["size"] <= 0.85 * n["size"] and c["bottom"] - n["bottom"] >= 0.08 * n["size"]:
                kinds[i] = "sub"
        sup += sum(1 for k in kinds.values() if k == "sup")
        sub += sum(1 for k in kinds.values() if k == "sub")
    return sup, sub
