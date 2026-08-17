"""
Dependency-free regression tests for the 4 lecture-generation/chat issues fixed on the
fix/lecture-generation-quality branch:
  1. Exhaustive topic/lecture retrieval (teach_topic_tool)
  2. Repair-turn handling for "try again" / "I still don't understand" follow-ups
  3. Mandated \\( \\) / \\[ \\] math delimiters + bare-bracket-math frontend fallback
  4. Chat message overlap caused by scrolling before async MathJax typesetting finishes

Run: python tests/test_lecture_generation_fixes_static.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")
RAG_VECTORSTORE_SRC = (ROOT / "app" / "utils" / "rag_vectorstore.py").read_text(encoding="utf-8")
FORMATTER_SRC = (ROOT / "static" / "teacher" / "js" / "chat-response-formatter.js").read_text(encoding="utf-8")
DASHBOARD_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")


# --- Issue 1: exhaustive retrieval ------------------------------------------------

def test_teach_topic_tool_defined_and_registered():
    assert "def teach_topic_tool(topic: str, thread_id: str)" in RAG_SERVICE_SRC
    tools_block = re.search(r"tools\s*=\s*\[(.*?)\]", RAG_SERVICE_SRC, re.S)
    assert tools_block, "tools list not found"
    assert "teach_topic_tool" in tools_block.group(1)


def test_teach_topic_tool_uses_page_range_query_not_topk_search():
    m = re.search(r"def teach_topic_tool\(.*?\n(?=@tool|\Z)", RAG_SERVICE_SRC, re.S)
    assert m, "teach_topic_tool body not found"
    body = m.group(0)
    assert "query_chunks_by_page_range" in body
    # Must not fall back to the top-k similarity search path for its exhaustive retrieval.
    assert "hybrid_search(" not in body
    assert "similarity_search(" not in body


def test_query_chunks_by_page_range_has_no_row_limit():
    m = re.search(r"def query_chunks_by_page_range\(.*?\n\n\n", RAG_VECTORSTORE_SRC, re.S)
    assert m, "query_chunks_by_page_range not found"
    body = m.group(0)
    assert ".limit(" not in body


def test_prompt_routes_lecture_requests_to_teach_topic_tool():
    for var in ("DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF", "DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF_LOAD_TEST"):
        m = re.search(rf"{var} = \((.*?)\n\)\n", RAG_SERVICE_SRC, re.S)
        assert m, f"{var} not found"
        assert "teach_topic_tool" in m.group(1), f"{var} missing teach_topic_tool routing rule"


def test_tool_round_limit_untouched_at_15():
    assert '_chat_safe_int_env("RAG_MAX_TOOL_ROUNDS_PER_TURN", 15)' in RAG_SERVICE_SRC
    assert '_safe_int_env("RAG_MAX_TOOL_ROUNDS_PER_TURN", 15)' in RAG_SERVICE_SRC


# --- Issue 2: repair-turn instruction ---------------------------------------------

def test_repair_turn_instruction_present_in_both_prompt_variants():
    for var in ("DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF", "DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF_LOAD_TEST"):
        m = re.search(rf"{var} = \((.*?)\n\)\n", RAG_SERVICE_SRC, re.S)
        assert m, f"{var} not found"
        body = m.group(1).lower()
        assert "try again" in body or "i still don't understand" in body
        assert "re-answer" in body


def test_follow_up_about_own_answer_instruction_present_in_both_prompt_variants():
    """
    Regression test for a live bug: after a lesson was finalized, asking a genuine follow-up
    about the AI's OWN previous answer ("explain why 2x, how did you get 2x and not x" - the
    lesson plan itself already derived 2x from the pool problem) got rag_tool called with just
    the bare fragment "2x" as the search query, which matched weakly, and the model fell back
    to a generic "the lesson has been saved" remark instead of either (a) answering from its
    own already-generated reasoning already visible in the conversation, or (b) the proper
    "not in document" fallback. Confirmed live via server logs: finalize_lesson_tool was NOT
    re-called (ruling out the earlier reordering bug), so this is a distinct prompt gap.
    """
    for var in ("DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF", "DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF_LOAD_TEST"):
        m = re.search(rf"{var} = \((.*?)\n\)\n", RAG_SERVICE_SRC, re.S)
        assert m, f"{var} not found"
        body = m.group(1).lower()
        assert "explain why 2x" in body
        assert "own" in body and "previous" in body
        assert "bare fragment" in body


# --- Issue 3: math delimiter convention -------------------------------------------

def test_formatting_instructions_mandate_bracket_delimiters_and_forbid_bare_brackets():
    m = re.search(r"RAG_REPLY_FORMATTING_INSTRUCTIONS = \((.*?)\n\)\n", RAG_SERVICE_SRC, re.S)
    assert m, "RAG_REPLY_FORMATTING_INSTRUCTIONS not found"
    body = m.group(1)
    assert r"\\( ... \\)" in body or r"\( ... \)" in body
    assert r"\\[ ... \\]" in body or r"\[ ... \]" in body
    assert "bare square brackets" in body.lower()


def test_frontend_bracket_math_fallback_exists_and_is_wired_in():
    assert "function convertBareBracketMathToLatex" in FORMATTER_SRC
    assert "text = convertBareBracketMathToLatex(text);" in FORMATTER_SRC
    # Must run before protectMathSegments (whose stash placeholders would otherwise be
    # mangled by a bracket-scanning regex if the ordering were flipped).
    convert_idx = FORMATTER_SRC.index("text = convertBareBracketMathToLatex(text);")
    protect_idx = FORMATTER_SRC.index("var mathProtected = protectMathSegments(text);")
    assert convert_idx < protect_idx


def test_bracket_math_fallback_skips_markdown_links():
    m = re.search(r"function convertBareBracketMathToLatex\(str\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "convertBareBracketMathToLatex body not found"
    body = m.group(1)
    # Guards against treating "[text](url)" or "[id]: url" as math.
    assert "after === '('" in body
    assert "after === ':'" in body


def test_bracket_math_fallback_handles_multiline_and_bare_parens():
    """
    Regression test for a live bug: the model wrote display math as bare brackets split
    across lines ("[\\nax^2 + bx + c = 0\\n]") and inline math as bare parens
    ("(a \\neq 0)" instead of "\\(a \\neq 0\\)"). The original fallback only matched
    single-line square brackets, so neither case got converted and rendered as raw text
    in the browser. Verified behaviorally with Node (see chat with the user) - this test
    locks in that the source still contains the fix.
    """
    m = re.search(r"function convertBareBracketMathToLatex\(str\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "convertBareBracketMathToLatex body not found"
    body = m.group(1)
    # [\s\S] (not [^\[\]\n]) so bracket content can span multiple lines, AND
    # (?<!\\[a-zA-Z]*) so an already-correct multi-line "\[ ... \]" block (very common -
    # delimiters on their own lines) is never matched and double-wrapped into broken
    # "\\[ ... \]" text. The first version of this fix had [\s\S] without the lookbehind
    # guard, which was itself a live regression - caught and fixed by testing the exact
    # reported text with Node before redeploying. The lookbehind was later widened from
    # "(?<!\\)" to "(?<!\\[a-zA-Z]*)" - see test_bracket_math_fallback_protects_left_right_delimiters.
    assert r"(?<!\\[a-zA-Z]*)\[([\s\S]{1,400}?)\]" in body
    # Separate pass for bare parens, guarded against double-wrapping already-correct \( \).
    assert r"(?<!\\[a-zA-Z]*)\(([^()]{1,200})\)" in body


def test_bracket_math_fallback_protects_left_right_delimiters():
    """
    Regression test for a live bug: "\\left(x + \\frac{b}{2a}\\right)^2" (a correctly-delimited
    equation using LaTeX sizing commands) was getting corrupted into
    "\\left\\(x + \\frac{b}{2a}\\right\\)^2" by the bare-paren fallback, because its negative
    lookbehind "(?<!\\)" only checks the single character immediately before "(" - which in
    "\\left(" is "t", not a backslash - so it didn't recognize the "(" as already belonging to
    a LaTeX command. MathJax then failed to render it with "Missing or unrecognized delimiter
    for \\left". Verified with Node against the exact reported text before fixing: the widened
    lookbehind "(?<!\\[a-zA-Z]*)" (backslash followed by zero-or-more letters) covers both the
    bare "\\(" case and any command-word case ("\\left(", "\\big(", "\\Bigg[", etc.).
    """
    m = re.search(r"function convertBareBracketMathToLatex\(str\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "convertBareBracketMathToLatex body not found"
    body = m.group(1)
    assert r"(?<!\\[a-zA-Z]*)\[" in body
    assert r"(?<!\\[a-zA-Z]*)\(" in body
    # Both occurrences of the old, too-narrow lookbehind must be gone, not just supplemented.
    assert r"(?<!\\)\[" not in body
    assert r"(?<!\\)\(" not in body


def test_math_restore_runs_after_marked_parse_not_before():
    """
    Regression test for the ACTUAL root cause of the recurring "raw [...]/(...)" reports:
    restoreMathSegments used to run on the markdown SOURCE text before marked.parse(), not on
    the parsed HTML after it. CommonMark backslash-escapes any ASCII punctuation, so restoring
    "\\[ ... \\]" / "\\( ... \\)" into the source before parsing meant marked.parse() itself
    stripped every leading backslash on render - confirmed empirically against the real marked
    library: marked.parse("\\[x\\]") produces "<p>[x]</p>", not "<p>\\[x\\]</p>". This bug
    existed regardless of whether the model's own output was correctly delimited, and pre-dates
    this branch entirely - my earlier bracket-conversion fixes could never have caught it since
    they only fixed what text got fed INTO protectMathSegments, not this ordering bug in how it
    was restored back out. Fixed by moving the restore to run on the HTML output, after
    marked.parse() and before DOMPurify.sanitize(), using a plain-alphanumeric placeholder
    (no <, >, or Unicode PUA chars) that survives marked.parse() unmangled.
    """
    m = re.search(r"function formatChatResponse\(rawText\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "formatChatResponse body not found"
    body = m.group(1)
    assert "html = marked.parse(text);" in body or "marked.parse(text)" in body
    restore_idx = body.index("restoreMathSegments(html")
    parse_idx = body.index("marked.parse(text)")
    dompurify_idx = body.index("DOMPurify.sanitize(html")
    assert parse_idx < restore_idx < dompurify_idx, (
        "restoreMathSegments(html, ...) must run after marked.parse() and before DOMPurify.sanitize()"
    )
    # The old text-level restore call (pre-parse) must be gone.
    assert "text = restoreMathSegments(text, mathProtected.stash);" not in FORMATTER_SRC


def test_math_placeholder_has_no_markdown_or_html_significant_chars():
    """
    marked.js HTML-escapes '<'/'>' (so the old "<<__MATH_0__>>" placeholder came back out of
    marked.parse() as literal "&lt;&lt;__MATH_0__&gt;&gt;", which would have broken the
    post-parse restore too) and silently drops Unicode private-use-area characters entirely -
    both verified empirically with the real marked library. The placeholder must avoid both.
    """
    assert "'<<__MATH_" not in FORMATTER_SRC
    m = re.search(r"function _mathPlaceholder\(i\) \{\s*return '([^']*)' \+ i \+ '([^']*)';", FORMATTER_SRC)
    assert m, "_mathPlaceholder not found or format changed"
    prefix, suffix = m.group(1), m.group(2)
    for forbidden in "<>_*`[]$\\":
        assert forbidden not in prefix and forbidden not in suffix, (
            f"placeholder contains markdown/HTML-significant char {forbidden!r}"
        )


def test_lesson_saved_card_uses_shared_formatter_not_hand_rolled_line_builder():
    """
    Regression test for a live bug: the "Lesson Created & Saved!" card built its own HTML by
    splitting lessonContent on '\\n' and wrapping each line in its own <div> with hand-rolled
    heading/list detection - completely bypassing TeacherChatFormatter.formatChatResponse (so
    none of the math-protection fixes applied), and worse, splitting a multi-line "\\[ ... \\]"
    block across separate sibling <div> elements meant MathJax could never recognize the
    opening/closing delimiters as belonging to the same expression even if typesetting ran.
    Fixed by reusing the same formatter already used (and fixed) for regular chat messages.
    """
    assert "TeacherChatFormatter.formatChatResponse(lessonContent" in DASHBOARD_SRC
    # The old per-line split-and-wrap builder must be gone.
    assert "(lessonContent || '').split('\\n').map(line =>" not in DASHBOARD_SRC


def test_looks_like_math_catches_bare_exponents_without_latex_commands():
    """
    "ax^2 + bx + c = 0" (from the live bug report) has no backslash command and no
    braced exponent - only the original heuristic (backslash commands, ^{ or _{) would
    have missed it entirely.
    """
    assert "function _looksLikeMath" in FORMATTER_SRC
    m = re.search(r"function _looksLikeMath\(inner\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "_looksLikeMath body not found"
    assert r"[a-zA-Z0-9]\^-?\d" in m.group(1)


# --- Live bug: math instructions missing entirely for no-PDF conversations --------

def test_no_pdf_system_message_includes_math_formatting_instructions():
    """
    Regression test for a live bug: a chat turn with no PDF uploaded routed to
    DEFAULT_RAG_CHAT_SYSTEM_BODY_NO_PDF, which never had RAG_REPLY_FORMATTING_INSTRUCTIONS
    (where the \\( \\)/\\[ \\] mandate lives) appended - every other branch (with-document,
    and no-document-with-custom-prompt) did. The model then free-styled with bare
    [...]/(...) and the browser rendered raw, unformatted LaTeX.
    """
    m = re.search(
        r"else:\n\s*# No document uploaded\n(.*?)\n    # Progressive message reduction",
        RAG_SERVICE_SRC, re.S,
    )
    assert m, "No-document branch of _chat_build_system_message not found"
    branch = m.group(1)
    # Every path through this branch (custom_prompt or not) must include the formatting instructions.
    assert branch.count("RAG_REPLY_FORMATTING_INSTRUCTIONS") >= 2


# --- Issue 4: chat overlap / async MathJax scroll timing --------------------------

def test_process_rendered_content_returns_a_promise():
    m = re.search(r"function processRenderedContent\(containerElement\) \{(.*?)\n  \}", FORMATTER_SRC, re.S)
    assert m, "processRenderedContent not found"
    body = m.group(1)
    assert "return typesetPromise.then(_settleLayout);" in body
    assert "return Promise.resolve();" in body


def test_add_assistant_message_scrolls_only_after_typesetting_settles():
    m = re.search(r"function addAssistantMessage\(message\) \{(.*?)\n      \}", DASHBOARD_SRC, re.S)
    assert m, "addAssistantMessage not found"
    body = m.group(1)
    assert "processRenderedContent(contentEl).then(function () {" in body
    assert "if (wasNearBottom) scrollToBottom();" in body
    # The old bug pattern: processRenderedContent fired off, then scrollToBottom() called
    # unconditionally right after, before its (unawaited) typesetting promise resolved.
    assert "TeacherChatFormatter.processRenderedContent(contentEl);\n        }\n        scrollToBottom();" not in body


def test_bulk_conversation_load_waits_for_all_renders_before_scrolling():
    assert "Promise.all(renderPromises).then(function () {" in DASHBOARD_SRC


# --- Live bug: frontend chat request timeout shorter than backend's own budget -----

def test_frontend_chat_timeout_is_not_shorter_than_gunicorn_timeout():
    """
    Regression test for a live bug: the frontend aborted a chat request at 180s while
    gunicorn's own --timeout (Dockerfile) is 300s, so a legitimately slow-but-successful
    backend run (confirmed live: a real lesson-plan request can take 90s+ under normal
    load, more under contention) could get aborted client-side while the backend was
    still validly working - discarding completed work and showing a confusing "Request
    timed out" error instead of the real answer.
    """
    DOCKERFILE_SRC = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"--timeout[\"'\s,]+(\d+)", DOCKERFILE_SRC)
    assert m, "gunicorn --timeout not found in Dockerfile"
    gunicorn_timeout_s = int(m.group(1))

    m2 = re.search(r"setTimeout\(\(\) => controller\.abort\(\), (\d+)\)", DASHBOARD_SRC)
    assert m2, "frontend chat AbortController timeout not found"
    frontend_timeout_ms = int(m2.group(1))

    assert frontend_timeout_ms > gunicorn_timeout_s * 1000, (
        f"frontend timeout ({frontend_timeout_ms}ms) must exceed gunicorn's own "
        f"--timeout ({gunicorn_timeout_s}s) so the backend's real timeout response wins "
        f"the race instead of a premature client-side abort discarding completed work"
    )


# --- Live bug: View Lesson kept showing the original after a chat edit -------------

LESSON_ROUTES_SRC = (ROOT / "app" / "routes" / "lesson_routes.py").read_text(encoding="utf-8")
MODELS_SRC = (ROOT / "app" / "models" / "models.py").read_text(encoding="utf-8")


def test_update_lesson_tool_syncs_existing_my_lessons_row():
    """Chat edits must also update the already-saved Lesson row, not just RAGThread."""
    assert "def _sync_saved_lesson_row(thread_id: str, content: str)" in RAG_SERVICE_SRC
    m = re.search(r"def update_lesson_tool\(full_lesson_text: str, thread_id: str\).*?\n@tool\n", RAG_SERVICE_SRC, re.S)
    assert m, "update_lesson_tool body not found"
    body = m.group(0)
    assert "_sync_saved_lesson_row(str(thread_id), content)" in body
    assert "get_lesson_by_rag_thread_id" in RAG_SERVICE_SRC


def test_lesson_persist_fallback_retries_when_tool_called_but_failed():
    """
    Confirmed live: the model called update_lesson_tool, got success=false, then dumped
    the complete updated lesson in chat ("issue with updating the lesson plan"). The
    fallback used to skip that case because it gated on `not update_lesson_tool_called`.
    """
    assert "update_lesson_tool_succeeded" in RAG_SERVICE_SRC
    assert "and not update_lesson_tool_succeeded" in RAG_SERVICE_SRC
    assert "and not update_lesson_tool_called" not in RAG_SERVICE_SRC


def test_create_lesson_simple_updates_existing_row_for_same_thread():
    """Re-save from chat must update the existing Lesson, not create a disconnected duplicate."""
    assert "def get_lesson_by_rag_thread_id" in MODELS_SRC
    m = re.search(r"def create_lesson_simple\(\):.*?\n@bp\.route", LESSON_ROUTES_SRC, re.S)
    assert m, "create_lesson_simple body not found"
    body = m.group(0)
    assert "get_lesson_by_rag_thread_id" in body
    assert "updated_existing" in body
    # Re-save must not overwrite the real title with the frontend's auto-suffix.
    update_call = re.search(r"LessonModel\(existing_lesson\['id'\]\)\.update_lesson\((.*?)\)", body, re.S)
    assert update_call, "re-save update_lesson call not found"
    assert "title=" not in update_call.group(1)


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_")]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"{len(failed)}/{len(tests)} test(s) FAILED")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)
