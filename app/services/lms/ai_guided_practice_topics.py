"""Main topic -> subtopic hierarchy of a Target PDF, for AI Guided Practice.

Every title in the result is taken from the PDF itself:

* heading *candidates* come from the PDF bookmarks (captured at upload), the RAG
  heading cache and heading-shaped lines in the ingested page text;
* an LLM only *organises* candidates into main topics / subtopics (it answers
  with candidate indexes), and any title it proposes outside the candidate list
  is kept only when it occurs verbatim in that page's text;
* without an LLM answer, numbering ("Unit 2", "2.3 ...") decides the levels.

A main topic under which the PDF printed no subtopics becomes one explicit
"whole section" subtopic (``is_whole_section``) instead of invented subtopics.
No main topics at all is an error (``HierarchyError``) - the caller keeps the
previously active PDF version.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_CANDIDATES = 160
_MAX_PAGE_SNIPPET = 220
_MAX_PROMPT_SNIPPETS_CHARS = 14000

# Front/back matter that is never a teachable topic.
_NOISE_PATTERNS = [
    r"^published by\b", r"^consultants?\b", r"^reviewers?", r"^authors?\b", r"^editors?\b",
    r"^year of edition", r"^preface\b", r"^foreword\b", r"^(table of )?contents\b", r"^index\b",
    r"^glossary\b", r"^answers?( key)?\b", r"^answer key", r"^acknowledg", r"^copyright",
    r"^all rights reserved", r"^printed (at|by)", r"^page \d+$", r"^\d+$", r"^isbn",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.I)
_MAIN_RE = re.compile(r"^\s*(unit|chapter|part|module|section)\s+([0-9ivxlc]+)\b", re.I)
_NUM_RE = re.compile(r"^\s*(\d{1,2})((?:\.\d{1,2}){0,3})\s*[).:-]?\s+[A-Za-z]")
# "Topics: A, B, C" / "Subtopics covered: ..." lines printed under a unit heading.
_TOPIC_LIST_RE = re.compile(r"(?im)^\s*(?:sub\s*-?\s*)?topics?(?:\s+covered)?\s*[:\-]\s*(.+)$")
_LINE_HEADING_RE = re.compile(
    r"(?m)^\s*(?:#{1,4}\s+(.{3,90})|((?:unit|chapter)\s+\d{1,2}\b.{0,80})|(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s+[A-Z][^\n]{2,80}))\s*$",
    re.I,
)


class HierarchyError(Exception):
    """The PDF has no usable topic structure (or no text at all)."""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def clean_title(text: str) -> str:
    t = re.sub(r"^#+\s*", "", (text or "").strip())
    t = re.sub(r"\s+", " ", t).strip(" .:-*_")
    return t[:200]


def is_noise(title: str) -> bool:
    t = clean_title(title)
    if len(t) < 3 or len(t) > 160:
        return True
    if not re.search(r"[A-Za-z]{3,}", t):
        return True
    return bool(_NOISE_RE.search(t))


def page_texts(chunks: List[dict]) -> Dict[int, str]:
    pages: Dict[int, List[str]] = {}
    for c in chunks or []:
        try:
            page = int(c.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        text = (c.get("text") or "").strip()
        if page > 0 and text:
            pages.setdefault(page, []).append(text)
    return {p: "\n".join(parts) for p, parts in pages.items()}


def outline_from_pdf(pdf_bytes: bytes) -> List[dict]:
    """PDF bookmarks [{level, title, page}] minus "Page N"-style noise."""
    if not pdf_bytes:
        return []
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            toc = doc.get_toc(simple=True) or []
    except Exception as exc:  # noqa: BLE001 - bookmarks are optional
        logger.info("No PDF outline: %s", exc)
        return []
    out = []
    for level, title, page in toc:
        title = clean_title(title)
        if is_noise(title):
            continue
        out.append({"level": int(level), "title": title, "page": int(page) if page and page > 0 else None})
    return out


def collect_candidates(
    pages: Dict[int, str], rag_headings: List[dict], outline: List[dict]
) -> List[dict]:
    """Heading candidates [{title, page, source, level?}] in page order, de-duplicated."""
    raw: List[dict] = []
    for o in outline or []:
        raw.append({"title": o["title"], "page": o.get("page"), "source": "outline", "level": o.get("level")})
    # Page text first: a heading found in the text carries its true position (the
    # RAG heading cache can be a page off, which attached a unit's "Topics:" list
    # to the previous unit). Cached headings only fill in what the text lacks.
    for page, text in sorted(pages.items()):
        found = []
        for m in _LINE_HEADING_RE.finditer(text):
            found.append((m.start(), {"title": next(g for g in m.groups() if g), "page": page, "source": "text"}))
        for m in _TOPIC_LIST_RE.finditer(text):
            listed = m.group(1).rstrip()
            # The printed list may wrap ("..., Quadratic" / "Formula"): join short
            # continuation lines that are not a new "Label: value" line or prose.
            for nxt in text[m.end():].lstrip("\n").splitlines()[:3]:
                s = nxt.strip()
                if not s or len(s) > 40 or ":" in s or re.search(r"[=.]|\d", s):
                    break
                listed += ("" if listed.endswith(",") else " ") + s
            for j, item in enumerate(re.split(r"\s*[,;•·|]\s*", listed)):
                found.append((m.start() + j / 100.0, {"title": item, "page": page, "source": "topic_list"}))
        raw.extend(c for _, c in sorted(found, key=lambda x: x[0]))
    for h in rag_headings or []:
        raw.append({"title": h.get("topic") or h.get("heading") or "", "page": h.get("page"), "source": "heading"})
    seen = set()
    out = []
    for c in raw:
        title = clean_title(c["title"])
        if is_noise(title):
            continue
        key = norm(title)
        # A printed topic-list item may repeat elsewhere (e.g. under another unit):
        # keep it per page so each unit keeps its own list.
        dedupe = (key, c.get("page")) if c["source"] == "topic_list" else key
        if not key or dedupe in seen:
            continue
        seen.add(dedupe)
        out.append({**c, "title": title})
    # Stable: keeps in-page reading order (heading before its "Topics:" list).
    out.sort(key=lambda c: (c.get("page") or 10**6))
    return out[:_MAX_CANDIDATES]


def title_in_page_text(title: str, pages: Dict[int, str], page: Optional[int]) -> Optional[int]:
    """Page where ``title`` occurs verbatim (normalised), searching ``page`` first."""
    key = norm(title)
    if len(key) < 4:
        return None
    order = ([page] if page in pages else []) + [p for p in sorted(pages) if p != page]
    for p in order:
        if key in norm(pages[p]):
            return p
    return None


# -- structure builders ---------------------------------------------------------


def _from_outline(outline: List[dict]) -> Optional[List[dict]]:
    """Two-level bookmarks -> hierarchy; None when the outline is flat/useless."""
    entries = [o for o in outline or [] if o.get("page")]
    if len(entries) < 2:
        return None
    levels = sorted({o["level"] for o in entries})
    if len(levels) < 2:
        return None
    top, sub = levels[0], levels[1]
    mains: List[dict] = []
    for o in entries:
        if o["level"] == top:
            mains.append({"title": o["title"], "page": o["page"], "subtopics": []})
        elif o["level"] == sub and mains:
            mains[-1]["subtopics"].append({"title": o["title"], "page": o["page"], "kind": "heading"})
    if not mains or not any(m["subtopics"] for m in mains):
        return None
    return mains


def _from_numbering(candidates: List[dict]) -> Optional[List[dict]]:
    """"Unit 2" / "2" -> main topic; "2.3 Title" or a printed "Topics:" list item
    -> subtopic of the current main."""
    mains: List[dict] = []
    for c in candidates:
        title = c["title"]
        if c.get("source") == "topic_list":
            if mains:
                mains[-1]["subtopics"].append({"title": title, "page": c.get("page"), "kind": "topic_list"})
            continue
        if _MAIN_RE.match(title):
            mains.append({"title": title, "page": c.get("page"), "subtopics": []})
            continue
        m = _NUM_RE.match(title)
        if not m:
            continue
        if m.group(2):  # 2.3 ...
            if mains:
                mains[-1]["subtopics"].append({"title": title, "page": c.get("page"), "kind": "heading"})
        else:
            mains.append({"title": title, "page": c.get("page"), "subtopics": []})
    return mains or None


class _LLMSub(BaseModel):
    candidate_index: Optional[int] = Field(None, description="Index of the candidate heading, or null")
    title: str = Field("", description="Exact heading text as printed in the PDF")
    page: Optional[int] = None


class _LLMMain(BaseModel):
    candidate_index: Optional[int] = None
    title: str = ""
    page: Optional[int] = None
    subtopics: List[_LLMSub] = Field(default_factory=list)


class _LLMHierarchy(BaseModel):
    main_topics: List[_LLMMain] = Field(default_factory=list)


def _llm_organise(candidates: List[dict], pages: Dict[int, str], filename: str) -> Optional[List[dict]]:
    if not candidates and not pages:
        return None
    try:
        from app.services.quiz.retry_utils import invoke_structured
        from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
        from app.utils.llm_factory import get_chat_model
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM unavailable for topic hierarchy: %s", exc)
        return None

    cand_lines = "\n".join(
        f"[{i}] p.{c.get('page') or '?'} ({c['source']}): {c['title']}" for i, c in enumerate(candidates)
    )
    snippets, used = [], 0
    for p in sorted(pages):
        snippet = re.sub(r"\s+", " ", pages[p])[:_MAX_PAGE_SNIPPET]
        if used + len(snippet) > _MAX_PROMPT_SNIPPETS_CHARS:
            break
        snippets.append(f"p.{p}: {snippet}")
        used += len(snippet)
    prompt = (
        "You organise the topics of ONE educational PDF (a textbook / study material) into a two-level "
        "hierarchy: main topics (units/chapters/major sections) and their subtopics (the teachable "
        "sections inside them).\n\n"
        f"PDF file: {filename}\n\n"
        "HEADING CANDIDATES detected in the PDF (index, page, source, text):\n"
        f"{cand_lines or '(none)'}\n\n"
        "PAGE OPENINGS (first characters of each page):\n" + "\n".join(snippets) + "\n\n"
        "Rules:\n"
        "- Use ONLY headings that appear in this PDF. Prefer candidate headings and give their candidate_index.\n"
        "- Candidates marked (topic_list) were printed in a 'Topics:' line under a unit heading: they are "
        "that unit's subtopics.\n"
        "- If a real section heading is visible in the page openings but missing from the candidates, give "
        "candidate_index null and copy its title EXACTLY as printed, with its page.\n"
        "- Never invent, paraphrase, merge or rename headings. Never add topics from general knowledge.\n"
        "- Skip front matter, publisher/author credits, exercise numbers, worked-example labels, answer keys "
        "and sentence fragments.\n"
        "- A main topic with no printed subtopics gets an empty subtopics list.\n"
        "- Keep document order. Return JSON matching the schema."
    )
    try:
        llm = get_chat_model(temperature=0.0, max_tokens=4096)
        result: _LLMHierarchy = invoke_with_groq_rate_limit(
            lambda: invoke_structured(llm, _LLMHierarchy, prompt),
            description=f"AI guided practice topic hierarchy ({filename})",
        )
    except Exception as exc:  # noqa: BLE001 - numbering fallback
        logger.warning("Topic hierarchy LLM call failed for %s: %s", filename, exc)
        return None

    def _resolve(entry) -> Optional[Tuple[str, Optional[int], str]]:
        idx = entry.candidate_index
        if idx is not None and 0 <= idx < len(candidates):
            c = candidates[idx]
            return c["title"], c.get("page"), ("topic_list" if c.get("source") == "topic_list" else "heading")
        title = clean_title(entry.title)
        if is_noise(title):
            return None
        page = title_in_page_text(title, pages, entry.page)
        if page is None:
            logger.info("Dropped hierarchy title not found in PDF text: %r", title)
            return None
        return title, page, "heading"

    mains: List[dict] = []
    for m in result.main_topics or []:
        resolved = _resolve(m)
        if not resolved:
            continue
        subs = []
        for s in m.subtopics or []:
            r = _resolve(s)
            if r and norm(r[0]) != norm(resolved[0]):
                subs.append({"title": r[0], "page": r[1], "kind": r[2]})
        mains.append({"title": resolved[0], "page": resolved[1], "subtopics": subs})
    return mains or None


# -- assembly -------------------------------------------------------------------


def _finalise(mains: List[dict], max_page: int) -> Tuple[List[dict], List[str]]:
    """Dedupe, order, assign page ranges, add whole-section subtopics."""
    warnings: List[str] = []
    seen_main = set()
    cleaned = []
    for m in mains:
        key = norm(m["title"])
        if not key or key in seen_main:
            continue
        seen_main.add(key)
        cleaned.append(m)
    have_pages = all(m.get("page") for m in cleaned)
    if have_pages:
        cleaned.sort(key=lambda m: m["page"])
    for i, m in enumerate(cleaned):
        start = m.get("page") or 1
        nxt = cleaned[i + 1].get("page") if i + 1 < len(cleaned) else None
        # Inclusive of the page where the next main topic starts (it may start
        # mid-page); the source-text reader cuts that page at the next heading.
        end = max(start, nxt) if nxt else max(start, max_page or start)
        m["page_start"], m["page_end"] = start, end
        main_key = re.sub(r"^(unit|chapter|part|module|section)\s+[0-9ivxlc]+\s*", "", norm(m["title"]))
        subs, seen_sub = [], set()
        for s in m.get("subtopics") or []:
            key = norm(s["title"])
            page = s.get("page") or start
            if not key or key in seen_sub or key == main_key:
                continue
            if page < start or page > end:
                warnings.append(f"Subtopic '{s['title']}' (p.{page}) is outside '{m['title']}' and was skipped")
                continue
            seen_sub.add(key)
            subs.append({"title": s["title"], "page": page, "kind": s.get("kind") or "heading"})
        # Headings in page order; printed topic-list items keep the PDF's list order.
        subs.sort(key=lambda s: s["page"])
        headings = [s for s in subs if s["kind"] == "heading"]
        for j, s in enumerate(subs):
            s["is_whole_section"] = False
            if s["kind"] == "topic_list":
                # A listed topic has no heading of its own: its text is the unit's section.
                s["page_start"], s["page_end"] = start, end
                continue
            k = headings.index(s)
            s_next = headings[k + 1]["page"] if k + 1 < len(headings) else None
            s["page_start"] = s["page"]
            # The next subtopic may start mid-page, so ranges share that page.
            s["page_end"] = max(s["page"], s_next if s_next else end)
        if not subs:
            warnings.append(f"'{m['title']}' lists no subtopics; it is practised as one whole section")
            subs = [{"title": m["title"], "page_start": start, "page_end": end, "is_whole_section": True,
                     "kind": "whole_section"}]
        m["subtopics"] = subs
    # Last main topic otherwise inherits every remaining page (question banks often
    # reprint earlier units without headings → Unit 4 spanning p.3–22). Cap using
    # earlier spans so practice source stays on-topic (QA Agent 2).
    if len(cleaned) >= 2:
        earlier = [max(1, m["page_end"] - m["page_start"]) for m in cleaned[:-1]]
        cap = max(max(earlier) * 3, 4)
        last = cleaned[-1]
        if last["page_end"] - last["page_start"] > cap:
            last["page_end"] = last["page_start"] + cap
            for s in last.get("subtopics") or []:
                if s.get("is_whole_section") or s.get("kind") == "topic_list":
                    s["page_end"] = min(s.get("page_end") or last["page_end"], last["page_end"])
                else:
                    s["page_end"] = min(s.get("page_end") or last["page_end"], last["page_end"])
            warnings.append(
                f"'{last['title']}' page range was capped at p.{last['page_start']}-{last['page_end']} "
                "so later unheaded pages from other units are not pulled into practice."
            )
    return cleaned, warnings


def extract_hierarchy(
    chunks: List[dict],
    rag_headings: List[dict],
    outline: List[dict],
    filename: str = "",
    *,
    use_llm: bool = True,
) -> dict:
    """{structure, main_topics:[{title,page_start,page_end,subtopics:[...]}], warnings}."""
    pages = page_texts(chunks)
    if not pages:
        raise HierarchyError("The Target PDF has no extractable text yet (ingestion may still be running).")
    max_page = max(pages)
    structure = "outline"
    mains = _from_outline(outline)
    if not mains:
        candidates = collect_candidates(pages, rag_headings, outline)
        structure = "headings"
        mains = _llm_organise(candidates, pages, filename) if use_llm else None
        if not mains:
            mains = _from_numbering(candidates)
    if not mains:
        raise HierarchyError(
            "No main topics could be identified from the Target PDF's headings. "
            "Upload a PDF with clear unit/chapter headings."
        )
    final, warnings = _finalise(mains, max_page)
    if final and all(s.get("is_whole_section") for m in final for s in m["subtopics"]):
        structure = "whole_sections"
    elif structure == "headings" and all(
        s.get("kind") in ("topic_list", "whole_section") for m in final for s in m["subtopics"]
    ):
        structure = "topic_lists"
    return {"structure": structure, "main_topics": final, "warnings": warnings}


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
