# RAG / Lesson Chat Tools

Source file: `app/utils/rag_service.py`

These are the LangGraph tools bound to the RAG chat LLM. The model chooses among them based on the admin RAG system prompt.

There is **no** `update_lesson_tool` in this codebase. Lesson edits stay in conversation state (`last_lesson_text`); only `finalize_lesson_tool` persists a saved lesson.

## Bound tool list

```python
tools = [
    calculator,
    rag_tool,
    get_page_tool,
    list_topics_whole_doc_tool,
    teach_topic_tool,
    count_pdf_words_tool,
    count_words_in_text_tool,
    finalize_lesson_tool,
]
```

| Tool | When it is used |
|---|---|
| `calculator` | Basic arithmetic (`add` / `sub` / `mul` / `div`) |
| `rag_tool` | Default PDF semantic search; also handles "page N" and "who is …" queries |
| `get_page_tool` | Fetch a specific PDF page (logical page labels mapped to physical pages) |
| `list_topics_whole_doc_tool` | Document outline / TOC / headings list |
| `teach_topic_tool` | Teach / lecture / full explanation of a **named topic** (section-based, not top-k) |
| `count_pdf_words_tool` | Word count of the uploaded PDF (whole doc, page, or page range) |
| `count_words_in_text_tool` | Word count of arbitrary text |
| `finalize_lesson_tool` | Save / finalize the lesson so it appears in My Lessons |

---

## 1. `calculator`

```python
@tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """
    Perform a basic arithmetic operation on two numbers.
    Supported operations: add, sub, mul, div
    """
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return {"error": "Division by zero is not allowed"}
            result = first_num / second_num
        else:
            return {"error": f"Unsupported operation '{operation}'"}

        return {
            "first_num": first_num,
            "second_num": second_num,
            "operation": operation,
            "result": result,
        }
    except Exception as e:
        return {"error": str(e)}
```

---

## 2. `get_page_tool`

```python
@tool
def get_page_tool(page: int, thread_id: str) -> dict:
    """
    Get the content of a specific page from the uploaded PDF for this chat thread.
    Supports logical page requests: when PDF page labels exist, user page numbers
    (e.g., printed page "1") are mapped to the corresponding physical PDF page.
    Page numbers: 0 or 1 both refer to the first page.
    Always include the thread_id when calling this tool.
    """
    logger.info(f"get_page_tool called: page={page}, thread_id={thread_id}")
    _set_chat_progress(thread_id, f"📄 Looking up page {page}...")

    user_id = _get_user_id_for_thread(thread_id) if thread_id else None
    if user_id is None:
        return {
            "error": f"Could not extract user_id from thread_id: {thread_id}",
            "thread_id": thread_id,
            "page_requested": page,
            "page_resolved": None,
            "chunks_found": 0,
        }

    original_page = page
    resolved_page, resolution_method = _resolve_requested_page(
        page_requested=page, thread_id=str(thread_id)
    )
    logger.info(
        "get_page_tool: page_requested=%s, page_resolved=%s, method=%s, user_id=%s",
        original_page,
        resolved_page,
        resolution_method,
        user_id,
    )

    from app.utils.rag_vectorstore import query_chunks_by_page
    results = query_chunks_by_page(thread_id=thread_id, user_id=user_id, page=resolved_page)

    if not results and resolution_method == "logical_page_map" and original_page > 0 and resolved_page != original_page:
        results = query_chunks_by_page(thread_id=thread_id, user_id=user_id, page=original_page)
        if results:
            resolved_page = original_page
            resolution_method = "physical_fallback"

    if not results:
        return {
            "error": f"No content found for page {resolved_page} (requested as page {original_page}).",
            "thread_id": thread_id,
            "page_requested": original_page,
            "page_resolved": resolved_page,
            "page_resolution_method": resolution_method,
            "chunks_found": 0,
        }

    results.sort(key=lambda x: x.get("chunk_index", 0))
    content = [r.get("text", "") for r in results]
    metadata = [
        {"source": r.get("source"), "page": r.get("page"), "chunk_index": r.get("chunk_index")}
        for r in results
    ]

    return {
        "thread_id": thread_id,
        "page_requested": original_page,
        "page_resolved": resolved_page,
        "page_resolution_method": resolution_method,
        "chunks_found": len(results),
        "content": content,
        "metadata": metadata,
    }
```

---

## 3. `list_topics_whole_doc_tool`

This tool is a thin wrapper. Real work is in `_get_thread_topics`.

```python
@tool
def list_topics_whole_doc_tool(thread_id: str) -> dict:
    """
    Extract a high-level outline of a document by identifying section titles,
    headings, and topics across the entire PDF using AI analysis.

    Use this tool when the user asks for:
    - what topic(s) are covered / what topics does the document cover
    - a list of topics or sections in the document
    - the document outline or structure
    - headings or major sections
    - what the document covers at a high level
    - a table of contents (explicit or inferred)
    - navigation help such as "jump to section" or "what sections are there"

    This tool uses AI to intelligently extract topics:
    1. First checks for Table of Contents (TOC) in early pages
    2. If TOC found, extracts topics from it
    3. If no TOC, scans all pages to identify headings and major topics
    4. Returns a clean, deduplicated list of topics with page numbers

    Parameters:
    - thread_id (str): The conversation thread identifier associated with the uploaded PDF.

    Returns:
    - dict with keys:
        - "topics": list of topic objects with "topic" (str) and "page" (int) keys
        - "topics_count": total number of unique topics found
        - "method": extraction method used ("ai_toc_extraction" or "ai_heading_extraction")
        - "chunks_scanned": number of document pages analyzed
    """
    _set_chat_progress(thread_id, "📋 Reviewing the document outline...")
    return _get_thread_topics(thread_id)
```

### Helper: `_get_thread_topics`

Shared by `list_topics_whole_doc_tool` and `teach_topic_tool`. Reads the DB heading cache, recovers if the background job stalled, and can run on-demand extraction.

```python
def _get_thread_topics(thread_id: str) -> dict:
    """
    Shared implementation behind list_topics_whole_doc_tool and teach_topic_tool.
    Returns the same shape as list_topics_whole_doc_tool: topics/topics_count/method/chunks_scanned,
    using the DB heading cache (with on-demand recovery) so both tools stay consistent.
    """
    user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return {"error": f"Could not extract user_id from thread_id: {thread_id}"}

    try:
        db = get_db()
        thread_row = (
            db.query(RAGThread)
            .filter(RAGThread.thread_id == thread_id, RAGThread.user_id == user_id)
            .first()
        )

        if not thread_row:
            return {
                "thread_id": thread_id,
                "topics": [],
                "topics_count": 0,
                "method": "db_heading_pending",
                "chunks_scanned": None,
                "message": "Thread not found for headings lookup.",
            }

        if not getattr(thread_row, "headings_ready", False):
            existing_headings = (
                db.query(RAGHeading)
                .filter(
                    RAGHeading.thread_id == thread_id,
                    RAGHeading.user_id == user_id,
                )
                .order_by(RAGHeading.page.asc(), RAGHeading.id.asc())
                .all()
            )
            if existing_headings:
                topics = [{"topic": h.heading, "page": h.page} for h in existing_headings]
                try:
                    thread_row.headings_ready = True
                    thread_row.headings_count = len(topics)
                    thread_row.headings_last_scanned_at = datetime.utcnow()
                    db.commit()
                except Exception:
                    db.rollback()
                return {
                    "thread_id": thread_id,
                    "topics": topics,
                    "topics_count": len(topics),
                    "method": "db_heading_cache_recovered",
                    "chunks_scanned": getattr(thread_row, "num_pages", None),
                }

            enable_on_demand = os.getenv("RAG_HEADINGS_ON_DEMAND_RECOVERY", "true").lower() in ("true", "1", "yes")
            if enable_on_demand:
                try:
                    recovery_wait = int(os.getenv("RAG_HEADINGS_RECOVERY_MAX_WAIT_SECONDS", "45"))
                    recovery_result = extract_and_store_headings_for_thread(
                        thread_id=thread_id,
                        user_id=user_id,
                        max_wait_seconds=max(5, recovery_wait),
                        poll_interval_seconds=2.0,
                    )
                    topics = recovery_result.get("topics") or []
                    return {
                        "thread_id": thread_id,
                        "topics": topics,
                        "topics_count": len(topics),
                        "method": "on_demand_heading_recovery",
                        "chunks_scanned": recovery_result.get("chunks_scanned") or getattr(thread_row, "num_pages", None),
                    }
                except Exception as recovery_err:
                    logger.warning(
                        "On-demand heading recovery failed for thread_id=%s user_id=%s: %s",
                        thread_id,
                        user_id,
                        recovery_err,
                    )

            return {
                "thread_id": thread_id,
                "topics": [],
                "topics_count": 0,
                "method": "db_heading_pending",
                "chunks_scanned": getattr(thread_row, "num_pages", None),
                "message": "Headings are still being processed. Please try again shortly.",
            }

        headings = (
            db.query(RAGHeading)
            .filter(
                RAGHeading.thread_id == thread_id,
                RAGHeading.user_id == user_id,
            )
            .order_by(RAGHeading.page.asc(), RAGHeading.id.asc())
            .all()
        )

        topics = [{"topic": h.heading, "page": h.page} for h in headings] if headings else []
        if not topics:
            logger.info(
                "Headings marked ready but none found for thread_id=%s user_id=%s",
                thread_id,
                user_id,
            )

        return {
            "thread_id": thread_id,
            "topics": topics,
            "topics_count": len(topics),
            "method": "db_heading_cache",
            "chunks_scanned": getattr(thread_row, "num_pages", None),
        }
    except Exception as e:
        logger.error(
            "Error querying headings for thread_id=%s user_id=%s: %s",
            thread_id,
            user_id,
            e,
            exc_info=True,
        )
        return {
            "thread_id": thread_id,
            "topics": [],
            "topics_count": 0,
            "method": "db_heading_cache_error",
            "chunks_scanned": None,
            "error": "Headings lookup failed. Please try again.",
        }
```

---

## 4. `count_pdf_words_tool`

```python
_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)

def _count_words(text: str) -> int:
    """
    Count words in text with preprocessing:
    - Remove extra whitespace (normalize to single spaces)
    - Remove # symbols (hashtags/pound symbols)
    - Strip leading/trailing whitespace
    """
    if not text:
        return 0

    text = text.replace('#', '')
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()

    if not text:
        return 0

    words = _WORD_RE.findall(text)
    return len(words)


@tool
def count_pdf_words_tool(
    thread_id: str,
    page: Optional[int] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    include_per_page: bool = False
) -> dict:
    """Count words in uploaded PDF for this thread. Supports whole doc, single page, or page range."""
    user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return {"error": f"Could not extract user_id from thread_id: {thread_id}"}

    from app.utils.rag_vectorstore import query_all_chunks

    thread_id_str = str(thread_id)

    def norm(p: Optional[int]) -> Optional[int]:
        if p is None:
            return None
        try:
            p = int(p)
        except Exception:
            return None
        return 1 if p == 0 else p

    page_n = norm(page)
    start_n = norm(start_page)
    end_n = norm(end_page)

    if page_n is not None:
        start_n, end_n = page_n, page_n
    if start_n is not None and end_n is None:
        end_n = start_n
    if end_n is not None and start_n is None:
        start_n = 1

    all_chunks = query_all_chunks(thread_id=thread_id_str, user_id=user_id)
    if not all_chunks:
        return {"error": "No chunks found for this thread. Upload a PDF first."}

    pages_seen = set()
    for c in all_chunks:
        p = c.get("page")
        try:
            pages_seen.add(int(p) if p is not None else 0)
        except (ValueError, TypeError):
            pass

    if not pages_seen:
        return {"error": "No page data found in chunks."}

    max_page = max(pages_seen)
    if start_n is None:
        start_n = 1
    if end_n is None:
        end_n = max_page

    total = 0
    per_page = {}
    for p in range(start_n, end_n + 1):
        page_chunks = [c for c in all_chunks if (c.get("page") or 0) == p]
        if not page_chunks:
            continue
        page_chunks.sort(key=lambda x: x.get("chunk_index", 0))
        text = " ".join(c.get("text", "") for c in page_chunks)
        wc = _count_words(text)
        total += wc
        per_page[p] = wc

    meta = _get_thread_metadata_from_db(thread_id_str) or {}
    num_pages = meta.get("num_pages") or meta.get("pages") or meta.get("documents")

    out = {
        "thread_id": thread_id,
        "source_file": meta.get("filename"),
        "num_pages": num_pages,
        "page": page_n,
        "start_page": start_n,
        "end_page": end_n,
        "total_words": total,
        "note": "Count is based on extracted text; scanned PDFs may require OCR for accurate word counts."
    }
    if include_per_page:
        out["per_page_words"] = dict(sorted(per_page.items(), key=lambda x: x[0]))
    return out
```

---

## 5. `teach_topic_tool`

Used instead of `rag_tool` when the user asks to **teach / lecture / explain a named topic** comprehensively.

Caps (env):

- `RAG_TEACH_TOPIC_MATCH_THRESHOLD` (default `0.5`)
- `RAG_TEACH_TOPIC_MAX_CHUNKS` (default `80`)
- `RAG_TEACH_TOPIC_MAX_SECTIONS` (default `10`)

```python
def _normalize_topic_text(text: str) -> set:
    """Lowercase, tokenize, drop very short/stopword-like tokens for topic/heading matching."""
    if not text:
        return set()
    _STOP = {"the", "and", "for", "of", "to", "in", "on", "a", "an", "with", "is", "are"}
    tokens = {t.lower() for t in _WORD_RE.findall(text) if len(t) > 2}
    return tokens - _STOP


def _topic_match_score(query_norm: str, query_tokens: set, heading_text: str) -> float:
    """
    Score how well a heading matches a requested topic.
    1.0 = substring containment either direction; otherwise token recall against the query.
    """
    if not heading_text or not query_tokens:
        return 0.0
    heading_norm = heading_text.lower().strip()
    if query_norm and (query_norm in heading_norm or heading_norm in query_norm):
        return 1.0
    heading_tokens = _normalize_topic_text(heading_text)
    if not heading_tokens:
        return 0.0
    overlap = query_tokens & heading_tokens
    return len(overlap) / max(len(query_tokens), 1)


_TEACH_TOPIC_MATCH_THRESHOLD = float(os.getenv("RAG_TEACH_TOPIC_MATCH_THRESHOLD", "0.5"))
_TEACH_TOPIC_MAX_CHUNKS = int(os.getenv("RAG_TEACH_TOPIC_MAX_CHUNKS", "80"))
_TEACH_TOPIC_MAX_SECTIONS = int(os.getenv("RAG_TEACH_TOPIC_MAX_SECTIONS", "10"))


@tool
def teach_topic_tool(topic: str, thread_id: str) -> dict:
    """
    Exhaustive, section-based retrieval for teaching/lecture requests on a named topic.

    Use this tool (instead of rag_tool) when the user asks to teach a named topic, explain a topic
    comprehensively, create a lecture, build lesson content, or prepare teaching material.
    Do NOT use this for narrow factual questions — use rag_tool for those.

    Unlike rag_tool (single top-k semantic search, can miss content spread across multiple
    sections), this tool:
    1. Looks up the document's headings/outline (same cache as list_topics_whole_doc_tool).
    2. Matches the requested topic against every heading (exact/substring + keyword overlap).
    3. For each matched heading, retrieves ALL chunks belonging to that heading's page range
       (from its page up to the page before the next heading) directly from PostgreSQL —
       no top-k truncation.
    4. Returns chunks grouped by section, plus a coverage manifest so the caller can tell the
       user exactly which sections were used and which related-but-unmatched headings exist.
    """
    topic_q = (topic or "").strip()
    if not topic_q:
        return {"error": "Error: topic cannot be empty."}
    if not thread_id or not str(thread_id).strip():
        return {"error": "Error: thread_id is required. No document session found for this request."}

    _set_chat_progress(thread_id, f"📚 Gathering every section on \"{topic_q}\"...")

    user_id = _get_user_id_for_thread(thread_id)
    if user_id is None:
        return {"error": f"Could not extract user_id from thread_id: {thread_id}"}

    topics_result = _get_thread_topics(thread_id)
    all_headings = topics_result.get("topics") or []
    if not all_headings:
        return {
            "error": (
                "No document outline/headings available yet for this thread "
                f"(method={topics_result.get('method')}). Try again shortly, or fall back to rag_tool."
            ),
            "matched_sections": [],
            "related_not_covered": [],
            "total_chunks_retrieved": 0,
            "truncated": False,
        }

    ordered = sorted(
        [h for h in all_headings if h.get("heading" if "heading" in h else "topic")],
        key=lambda h: (h.get("page") is None, h.get("page") if h.get("page") is not None else 0),
    )

    thread_meta = _get_thread_metadata_from_db(str(thread_id)) or {}
    num_pages = thread_meta.get("num_pages") or thread_meta.get("pages") or thread_meta.get("documents")
    source_file = thread_meta.get("filename") or "PDF"

    query_norm = topic_q.lower().strip()
    query_tokens = _normalize_topic_text(topic_q)

    matched: List[dict] = []
    related_not_covered: List[dict] = []
    for idx, h in enumerate(ordered):
        heading_text = h.get("topic") or h.get("heading") or ""
        page = h.get("page")
        score = _topic_match_score(query_norm, query_tokens, heading_text)
        if score <= 0:
            continue
        if page is None:
            if score < _TEACH_TOPIC_MATCH_THRESHOLD:
                related_not_covered.append({"heading": heading_text, "page": None, "score": round(score, 2)})
            continue
        if score < _TEACH_TOPIC_MATCH_THRESHOLD:
            related_not_covered.append({"heading": heading_text, "page": page, "score": round(score, 2)})
            continue

        page_end = int(num_pages) if num_pages else page
        for later in ordered[idx + 1:]:
            later_page = later.get("page")
            if later_page is not None and later_page > page:
                page_end = later_page - 1
                break
        page_end = max(page_end, page)
        matched.append({"heading": heading_text, "page_start": page, "page_end": page_end, "score": score})

    if not matched:
        return {
            "matched_sections": [],
            "related_not_covered": related_not_covered,
            "total_chunks_retrieved": 0,
            "truncated": False,
            "source_file": source_file,
            "num_pages": num_pages,
            "message": (
                f"No section headings matched topic '{topic_q}'. "
                "Consider using rag_tool for a general search instead."
            ),
        }

    from app.utils.rag_vectorstore import query_chunks_by_page_range

    additional_sections_not_included: List[dict] = []
    if len(matched) > _TEACH_TOPIC_MAX_SECTIONS:
        ranked = sorted(matched, key=lambda s: s["score"], reverse=True)
        kept_keys = {(s["heading"], s["page_start"]) for s in ranked[:_TEACH_TOPIC_MAX_SECTIONS]}
        dropped = [s for s in matched if (s["heading"], s["page_start"]) not in kept_keys]
        additional_sections_not_included = [
            {"heading": s["heading"], "page": s["page_start"], "score": round(s["score"], 2)}
            for s in dropped
        ]
        matched = [s for s in matched if (s["heading"], s["page_start"]) in kept_keys]

    total_chunks = 0
    truncated = bool(additional_sections_not_included)
    matched_sections_out = []
    for section in matched:
        if total_chunks >= _TEACH_TOPIC_MAX_CHUNKS:
            truncated = True
            break
        rows = query_chunks_by_page_range(
            thread_id=str(thread_id), user_id=user_id,
            start_page=section["page_start"], end_page=section["page_end"],
        )
        remaining = _TEACH_TOPIC_MAX_CHUNKS - total_chunks
        if len(rows) > remaining:
            rows = rows[:remaining]
            truncated = True
        content = [_strip_metadata_like_lines(r.get("text", "")) for r in rows if r.get("text", "").strip()]
        total_chunks += len(rows)
        matched_sections_out.append({
            "heading": section["heading"],
            "page_start": section["page_start"],
            "page_end": section["page_end"],
            "chunks_found": len(rows),
            "content": content,
        })

    logger.info(
        "teach_topic_tool: topic=%r thread_id=%s matched_sections=%d total_chunks=%d truncated=%s "
        "additional_sections_not_included=%d",
        topic_q, thread_id, len(matched_sections_out), total_chunks, truncated,
        len(additional_sections_not_included),
    )

    result = {
        "matched_sections": matched_sections_out,
        "related_not_covered": related_not_covered,
        "total_chunks_retrieved": total_chunks,
        "truncated": truncated,
        "source_file": source_file,
        "num_pages": num_pages,
    }
    if additional_sections_not_included:
        result["additional_sections_not_included"] = additional_sections_not_included
        result["message"] = (
            f"Topic '{topic_q}' matched {len(additional_sections_not_included) + len(matched_sections_out)} "
            f"sections — more than fit in one response. Showing the {len(matched_sections_out)} most relevant; "
            "see additional_sections_not_included for the rest. If the teacher wants a section not shown, "
            "call teach_topic_tool again with a more specific topic (e.g. that section's own heading)."
        )
    return result
```

---

## 6. `count_words_in_text_tool`

```python
@tool
def count_words_in_text_tool(text: str, label: str = "text") -> dict:
    """Count words in a given text."""
    return {"label": label, "words": _count_words(text)}
```

### Helper used by retrieval tools: `_strip_metadata_like_lines`

```python
def _strip_metadata_like_lines(text: str) -> str:
    """Remove lines that look like PDF/internal metadata so they are not shown to the user."""
    if not text or not text.strip():
        return text
    skip_phrases = (
        "metadata notes", "the page is part of", "duplicated in two chunks",
        "pdf-xchange", "created using", "timestamps from", "windows 10",
        "the content is duplicated", "likely due to pdf formatting",
        "for deeper insights", "for specific applications", "feel free to ask",
    )
    lines = text.split("\n")
    kept = []
    for line in lines:
        lower = line.strip().lower()
        if not lower:
            kept.append(line)
            continue
        if any(phrase in lower for phrase in skip_phrases):
            continue
        kept.append(line)
    return "\n".join(kept).strip() or text
```

---

## 7. `rag_tool`

Default retrieval tool. Special cases:

- Query shorter than 2 words → rejected
- Query mentions `page N` → delegates to `get_page_tool`
- Author / "who is …" query → prepends page 1

```python
@tool
def rag_tool(query: str, thread_id: Optional[str] = None):
    """
    Retrieve relevant information from the uploaded PDF for this chat thread.
    Always include the thread_id when calling this tool.
    Returns content-only text for the LLM (no internal metadata).
    """
    rag_steps = []
    rag_started = time.perf_counter()

    def _rag_step(label: str) -> None:
        rag_steps.append((label, time.perf_counter()))

    q = (query or "").strip()
    if not q:
        return (
            "Error: Query cannot be empty. Provide a specific topic or question to search for in the document."
        )
    if len(q.split()) < 2:
        return (
            "Error: Query is too short for meaningful retrieval. "
            "Use at least two words, e.g. 'explain radioactivity' instead of a single word."
        )
    if not thread_id or not str(thread_id).strip():
        return "Error: thread_id is required. No document session found for this request."

    query = q
    logger.info(f"rag_tool called: query='{query[:100]}...', thread_id={thread_id}")
    _rag_step("rag_entry")
    _set_chat_progress(thread_id, "🔍 Searching the document...")

    user_id = _get_user_id_for_thread(thread_id) if thread_id else None
    _rag_step("resolve_user_id")
    logger.info(f"rag_tool: extracted user_id={user_id}")

    import re
    page_patterns = [
        r'page\s+(?:no|number|#)?\s*(\d+)',
        r'page:\s*(\d+)',
        r'on\s+page\s+(\d+)',
        r'page\s+(\d+)',
    ]

    page_requested = None
    for pattern in page_patterns:
        match = re.search(pattern, query.lower())
        if match:
            try:
                page_requested = int(match.group(1))
                logger.info(f"rag_tool: detected page request: {page_requested}")
                if thread_id:
                    _rag_step("page_request_detected")
                    out = get_page_tool.invoke({"page": page_requested, "thread_id": thread_id})
                    _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
                    return out
                else:
                    return "Error: thread_id is required for page queries."
            except (ValueError, IndexError):
                pass
    _rag_step("page_request_parsed")

    author_keywords = ["author", "written by", "who wrote", "title page", "lecturer", "who is the author"]
    is_author_query = any(keyword in query.lower() for keyword in author_keywords)
    is_who_is_person = query.strip().lower().startswith("who is ") and len(query.strip()) > 10
    is_person_identity_query = is_author_query or is_who_is_person

    retriever = _get_retriever(thread_id, user_id, steps_list=rag_steps)
    _rag_step("get_retriever")
    if retriever is None:
        if is_person_identity_query and thread_id:
            logger.info("rag_tool: person/author query with no retriever, trying page 1 fallback")
            out = get_page_tool.invoke({"page": 1, "thread_id": thread_id})
            _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
            return out
        _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
        return "Error: No document indexed for this chat. Upload a PDF first."

    result = retriever.invoke(query)
    _rag_step("similarity_search")
    logger.info(f"rag_tool: similarity search returned {len(result)} documents after filtering")

    context = [doc.page_content for doc in result]
    metadata = [doc.metadata for doc in result]

    if is_person_identity_query and thread_id:
        page1_result = get_page_tool.invoke({"page": 1, "thread_id": thread_id})
        if isinstance(page1_result, dict) and "content" in page1_result:
            page1_content = page1_result.get("content", [])
            if page1_content:
                existing_text = "\n".join(context).lower()
                new_chunks = [c for c in page1_content if c and c.strip() and c.strip().lower() not in existing_text]
                if new_chunks:
                    context = list(new_chunks) + context
                    metadata = list(page1_result.get("metadata", [])[: len(new_chunks)]) + metadata
                    logger.info(f"rag_tool: prepended page 1 for person/author query ({len(new_chunks)} chunks)")
                elif not context:
                    context = list(page1_content)
                    metadata = list(page1_result.get("metadata", [])[: len(page1_content)])
                    logger.info(f"rag_tool: used page 1 only for person/author query ({len(context)} chunks)")

    thread_meta = _get_thread_metadata_from_db(str(thread_id)) or {}
    _rag_step("load_thread_metadata")
    num_pages = thread_meta.get("num_pages") or thread_meta.get("pages") or thread_meta.get("documents")
    source_file = thread_meta.get("filename") or "PDF"

    evidence_blocks = []
    cleaned_chunks = []
    for idx, (chunk_text, chunk_meta) in enumerate(zip(context, metadata), start=1):
        if not chunk_text or not str(chunk_text).strip():
            continue
        cleaned_text = _strip_metadata_like_lines(chunk_text)
        if not cleaned_text or not cleaned_text.strip():
            continue
        cleaned_chunks.append(cleaned_text)
        page_val = (chunk_meta or {}).get("page")
        source_val = (chunk_meta or {}).get("source") or source_file
        chunk_idx_val = (chunk_meta or {}).get("chunk_index")

        try:
            page_label = str(int(page_val)) if page_val is not None and str(page_val).strip() else "unknown"
        except Exception:
            page_label = str(page_val).strip() if page_val is not None else "unknown"
        chunk_label = str(chunk_idx_val) if chunk_idx_val is not None else str(idx - 1)
        evidence_blocks.append(
            f"[Evidence {idx} | Page {page_label} | Chunk {chunk_label} | Source {source_val}]\n{cleaned_text}"
        )

    _rag_step("clean_chunks")
    content_block = "\n\n---\n\n".join(evidence_blocks) if evidence_blocks else "(No relevant content found.)"
    content_for_llm = (
        "Relevant content from the PDF (citation-ready evidence):\n\n"
        f"{content_block}\n\n"
        "Citation policy for this evidence:\n"
        "- Cite only page numbers that appear in the evidence headers above.\n"
        "- If page is unknown, cite using section/source wording instead of inventing a page number.\n\n"
        f"Source file: {source_file}. Total pages: {num_pages or 'unknown'}."
    )
    _rag_step("build_content_for_llm")
    _write_speed_log("rag_tool", thread_id, rag_steps, rag_started)
    return content_for_llm
```

---

## 8. `finalize_lesson_tool`

Persists `RAGThread.last_lesson_text` as a saved lesson. Does **not** take lesson body as an argument — it reads whatever is already stored on the thread.

```python
@tool
def finalize_lesson_tool(thread_id: str) -> str:
    """
    Finalize and permanently save the lesson you have been building in this conversation,
    so it becomes available in "My Lessons" and to students.

    Call this tool whenever the user's intent - in ANY wording, in any language - is to
    save, finalize, complete, or lock in the lesson (for example: "save this as a lesson",
    "finalize this", "please save it", "make this final", "yeh lesson save kar do", "lock
    it in", or any other phrasing with the same meaning). Do not try to match the user's
    exact words yourself; if their intent is to persist the lesson, call this tool.

    Returns a JSON string with "success" (true/false) and "reason". Only tell the user the
    lesson was saved if success is true. If success is false, explain the reason to them
    instead of claiming it was saved - never say the lesson was saved unless this tool
    actually returned success=true for this call.

    Always include the current conversation's thread_id when calling this tool.
    """
    result = {"success": False, "reason": "Unknown error.", "already_finalized": False}
    if not thread_id:
        result["reason"] = "No active document thread to save a lesson for."
        return json.dumps(result)

    _set_chat_progress(thread_id, "💾 Saving your lesson...")

    try:
        user_id = _get_user_id_for_thread(thread_id)
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=str(thread_id)).first()
        if not thread_row:
            result["reason"] = "No conversation thread found to save a lesson for."
            return json.dumps(result)

        content = (getattr(thread_row, "last_lesson_text", None) or "").strip()
        if not content:
            result["reason"] = (
                "There is no lesson content in this conversation yet - generate a lesson "
                "first, then ask to save it."
            )
            return json.dumps(result)

        is_lesson = _check_if_content_is_lesson(content, user_query="", user_id=user_id)
        if not is_lesson:
            result["reason"] = (
                "The current conversation content doesn't look like a complete lesson yet, "
                "so it wasn't saved. Continue building the lesson, then try saving again."
            )
            return json.dumps(result)

        already_finalized = bool(getattr(thread_row, "lesson_finalized", False))
        _persist_finalized_lesson_static(str(thread_id), content)

        result["success"] = True
        result["already_finalized"] = already_finalized
        result["reason"] = (
            "Lesson re-saved with the latest content." if already_finalized else "Lesson saved."
        )
        logger.info(
            "finalize_lesson_tool: thread_id=%s user_id=%s success=True already_finalized=%s",
            thread_id, user_id, already_finalized,
        )
        return json.dumps(result)
    except Exception as e:
        logger.warning("finalize_lesson_tool failed for thread_id=%s: %s", thread_id, e, exc_info=True)
        result["reason"] = "An internal error occurred while trying to save the lesson."
        return json.dumps(result)
```

---

## How the model is told to pick a tool

Default prompt body: `DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF` in the same file (admin can override this in `system_settings`).

Routing rules in that prompt:

- Summarize / overview → `list_topics_whole_doc_tool` and/or `rag_tool`
- Page-specific → `get_page_tool`
- Outline / chapters / topics list → `list_topics_whole_doc_tool`
- Teach / lecture / lesson on a **named topic** → `teach_topic_tool` **once** (not `rag_tool`)
- Other document questions → `rag_tool`
- Save / finalize lesson → `finalize_lesson_tool`
- Narrow factual questions stay on `rag_tool`, not `teach_topic_tool`
