# Phase 2 Design: Answer-Quality Gate (generalized lecture failsafe)

Status: **design only** — no changes to `app/utils/rag_service.py` or real test files yet.
Depends on Phase 1's `RouterOutput` / `router_intent` / `meta_conversation_active` fields on
`_ChatTurnSystemPrep`, which do not exist in this worktree yet. This doc treats those as a
known-future interface and does not invent routing of its own.

Worktree: `C:\Users\user\Desktop\iqbalai-v1.1\iqbal_ai_stg\.claude\worktrees\agent-a70d180170c889c7d`
Branch: `worktree-agent-a70d180170c889c7d`

## 0. Bug this fixes

Staging: user asked "what is zero discriminat just answer main one line" against an uploaded
PDF. Mandatory prefetch (`rag_tool.invoke`) returned strong evidence (score=1.0, page=41). The
model replied with pure filler: "If you have any further questions about quadratic equations or
any related topics, feel free to ask!" — never touching the evidence. The turn was correctly
routed; generation still failed. A quality gate is the only thing that catches this class of bug.

## 1. What already exists (read in full before designing)

`app/utils/rag_service.py` (line numbers are from this worktree's HEAD, `a39a37b`; see §6 for
a caveat about line-number drift vs. Phase 1's branch):

- `LectureFailsafeEvalResult` (BaseModel, lines 3053–3067): `passed`, `is_underspecified_clarification`,
  `reasoning`, `feedback_for_regeneration`.
- `LECTURE_FAILSAFE_EVAL_PROMPT` (3070–3096): asks an LLM to judge whether "LECTURE / lesson BODY
  text" is grounded in `{evidence}`, with an explicit carve-out: pure clarification questions get
  `is_underspecified_clarification=True, passed=True` (not treated as a failure).
- `_collect_document_evidence_for_failsafe` (3099–3127): merges `prefetch_evidence_for_eval` with
  ToolMessage bodies from the *current* user turn (found via the last `HumanMessage` index) — no
  new retrieval, purely reuses what the turn already fetched. Truncates at 16k chars.
- `_format_lecture_failsafe_prompt` (3130–3139): `str.replace`-based templating (not `.format`) so
  braces inside lecture text can't break it.
- `_lecture_failsafe_eval_and_maybe_regenerate` (3142–3272): the gate + loop itself.
- Call site: `_chat_invoke_llm_with_retry`, lines 4135–4157.

### Exact current gate order (top of the function, lines 3164–3179)

```python
if not is_lesson_creation_turn:
    return response_content, response
if short_mode_active or token_pressure_active:
    return response_content, response
if os.getenv("RAG_LECTURE_FAILSAFE_ENABLED", "false").lower() not in ("true", "1", "yes"):
    return response_content, response
if _LOAD_TEST_MODE and os.getenv("RAG_LECTURE_FAILSAFE_IN_LOAD_TEST", "false").lower() not in (
    "true", "1", "yes",
):
    return response_content, response
if not (response_content or "").strip():
    return response_content, response
if _is_underspecified_rag_query(last_user_msg_text):
    return response_content, response
```

Note **today's default is off** (`RAG_LECTURE_FAILSAFE_ENABLED` defaults to `"false"`), and even
when on, there is **no heuristic pre-filter** — every qualifying turn pays for a full
`user_llm.with_structured_output(LectureFailsafeEvalResult)` call. That's fine for lesson
generation (rare, already expensive, high-stakes) but would double latency+cost for ordinary
document Q&A (the bulk of chat volume) if the gate were simply widened to `router_intent`
without a cheap pre-filter — which is why it's never been turned on for Q&A.

### The loop (3181–3272)

`max_rounds = max(2, RAG_LECTURE_FAILSAFE_MAX_ROUNDS default 4)`. Each round: build the eval
prompt from `(last_user_msg_text, evidence_bundle, current_draft)`, call
`eval_llm.invoke(prompt, config=config)` (Groq rate-limiter wrapped), check
`verdict.passed or verdict.is_underspecified_clarification`. On fail (and rounds remain): append a
`HumanMessage` with `feedback_for_regeneration` (or `reasoning`, or a generic fallback string) to
`[system_message, *conversation_messages, AIMessage(current)]`, trim for token budget, and call
**`user_llm.invoke` (tool-free)** to regenerate. Sanitizes with `_sanitize_user_facing_response`.
On the last round or any exception, keeps the last draft as-is (never crashes the turn).

### Call site gate (today)

```python
if (
    is_lesson_creation_turn
    and isinstance(response, AIMessage)
    and not getattr(response, "tool_calls", None)
):
    response_content, response = _lecture_failsafe_eval_and_maybe_regenerate(...)
```

### Fallback wording that must never be flagged as filler

From `DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF` (lines 3306–3311), the two legitimate "I don't have
this" replies the model is instructed to give verbatim:

- `"The answer is not present in the document. Would you like me to answer from my own knowledge base?"`
- `"Irrelevant question. Do you want me to answer from my own knowledge base?"`

Both are short and contain no citation — i.e. they look exactly like the shape of response the
heuristic is hunting for. They must be excluded by content, not just by length.

**Note on citation convention**: the codebase's citation format (see `rag_tool`, lines
2908–2941) is `Page N` / `[Evidence i | Page N | Chunk c | Source s]`, not a `"(Source:"` marker.
The task brief mentioned `"(Source:"` as an example signal; I generalized the citation-marker
check to match the convention actually used in this codebase (`page\s*\d+`, `p\.\s*\d+`,
`section\s+\d+`) plus `(source:`/`[source:` as a superset, so it still catches the brief's literal
example without being blind to how this app actually cites.

## 2. Heuristic pre-filter

### Signature

```python
_FILLER_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bfeel free to ask\b", re.I),
    re.compile(r"\blet me know if\b", re.I),
    re.compile(r"\bany (?:other|further) questions\b", re.I),
    re.compile(r"\bif you have any (?:further |other )?questions\b", re.I),
    re.compile(r"\bhappy to help\b", re.I),
    re.compile(r"\banything else\b", re.I),
    re.compile(r"\bdon'?t hesitate\b", re.I),
    re.compile(r"\breach out\b", re.I),
    re.compile(r"\bi'?m here to help\b", re.I),
    re.compile(r"\bhope (?:this|that) helps\b", re.I),
]

# Legitimate fallback wording (see DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF) — must NOT be
# flagged as filler even though it is short and citation-free; it is a correct answer.
_LEGITIMATE_FALLBACK_PATTERNS: List[re.Pattern] = [
    re.compile(r"not present in the document", re.I),
    re.compile(r"irrelevant question", re.I),
    re.compile(r"answer from my own knowledge base", re.I),
]

_CITATION_MARKER_PATTERN = re.compile(
    r"\bpage\s*\d+\b|\bp\.\s*\d+\b|\bpp\.\s*\d+\b|\bsection\s+\d+\b|\(source[:\s]|\[source[:\s]",
    re.I,
)

_FILLER_MAX_CHARS = int(os.getenv("RAG_ANSWER_QUALITY_FILLER_MAX_CHARS", "350"))


def _looks_like_filler_non_answer(response_content: str) -> bool:
    """Cheap regex/length heuristic — no LLM call.

    True: `response_content` looks like a filler non-answer (evidence was ignored).
    False: looks like a substantive answer, OR is legitimate fallback wording, OR is empty
    (empty-response handling is a separate concern, not this gate's job).
    """
    text = (response_content or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _LEGITIMATE_FALLBACK_PATTERNS):
        return False
    if not any(p.search(text) for p in _FILLER_PATTERNS):
        return False
    # A filler phrase alone isn't damning — a good, substantive answer can still end with
    # "let me know if you want more detail." Only flag when the WHOLE response reads like
    # filler: short AND no citation marker anywhere in it.
    is_short = len(text) <= _FILLER_MAX_CHARS
    has_citation = bool(_CITATION_MARKER_PATTERN.search(text))
    return is_short and not has_citation


def _quality_gate_should_escalate(response_content: str, prefetch_evidence_for_eval: str) -> bool:
    """True => pay for the LLM eval this turn. False => skip it (the common case).

    Escalates only when there was real evidence to have used (prefetch_evidence_for_eval
    non-empty — exactly the zero-discriminant bug's signal: score=1.0/page=41 was present)
    AND the response looks like filler. No evidence => nothing to grade => never escalate,
    regardless of how the response reads.
    """
    if not (prefetch_evidence_for_eval or "").strip():
        return False
    return _looks_like_filler_non_answer(response_content)
```

### True/false positive matrix (see §4 for the full test list)

| response | evidence present | escalate? | why |
|---|---|---|---|
| "If you have any further questions about quadratic equations or any related topics, feel free to ask!" | yes | **True** | filler phrase, short, no citation — the actual bug |
| "The answer is not present in the document. Would you like me to answer from my own knowledge base?" | yes | False | matches legitimate-fallback exclusion |
| "Irrelevant question. Do you want me to answer from my own knowledge base?" | yes | False | matches legitimate-fallback exclusion |
| Same filler text | **no** | False | evidence gate short-circuits — nothing to grade |
| Long grounded answer ending "...see Page 41 for the full derivation. Let me know if you'd like more detail." | yes | False | has citation marker → not flagged despite containing a filler phrase |
| "" (empty) | yes | False | out of scope for this gate (handled elsewhere by the existing empty-response check) |

## 3. Gate modification: before/after

### Function signature + top-of-function gate

**Before:**

```python
def _lecture_failsafe_eval_and_maybe_regenerate(
    *,
    user_llm: Any,
    system_message: SystemMessage,
    conversation_messages: List[BaseMessage],
    response: AIMessage,
    response_content: str,
    last_user_msg_text: str,
    prefetch_evidence_for_eval: str,
    has_document: bool,
    is_lesson_creation_turn: bool,
    user_id: Optional[int],
    provider: str,
    config: Any,
    max_input_tokens: int,
    short_mode_active: bool,
    token_pressure_active: bool,
    _mark_step: Any,
) -> Tuple[str, AIMessage]:
    """
    Lecture-only gate: verify grounding vs evidence; optionally regenerate without tools
    until pass or max attempts.
    """
    if not is_lesson_creation_turn:
        return response_content, response
    if short_mode_active or token_pressure_active:
        return response_content, response
    if os.getenv("RAG_LECTURE_FAILSAFE_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return response_content, response
    if _LOAD_TEST_MODE and os.getenv("RAG_LECTURE_FAILSAFE_IN_LOAD_TEST", "false").lower() not in (
        "true", "1", "yes",
    ):
        return response_content, response
    if not (response_content or "").strip():
        return response_content, response
    if _is_underspecified_rag_query(last_user_msg_text):
        return response_content, response

    max_rounds = max(2, int(os.getenv("RAG_LECTURE_FAILSAFE_MAX_ROUNDS", "4")))
    eval_llm = user_llm.with_structured_output(LectureFailsafeEvalResult)
    ...
```

**After** (renamed; see §5 for naming rationale):

```python
_ANSWER_QUALITY_GATE_DEFAULT_INTENTS = (
    "lesson_generation", "document_qa", "general_knowledge_qa", "lesson_qa",
)


def _answer_quality_gate_enabled() -> bool:
    """New RAG_ANSWER_QUALITY_GATE_ENABLED (default true) wins if set. Falls back to the old
    RAG_LECTURE_FAILSAFE_ENABLED name for one release so an ops config that pinned the old
    var to false doesn't silently flip on underneath them the moment this ships."""
    new_val = os.getenv("RAG_ANSWER_QUALITY_GATE_ENABLED")
    if new_val is not None:
        return new_val.lower() in ("true", "1", "yes")
    old_val = os.getenv("RAG_LECTURE_FAILSAFE_ENABLED")
    if old_val is not None:
        logger.warning(
            "RAG_LECTURE_FAILSAFE_ENABLED is deprecated; use RAG_ANSWER_QUALITY_GATE_ENABLED."
        )
        return old_val.lower() in ("true", "1", "yes")
    return True  # new default: on


def _answer_quality_gate_eval_and_maybe_regenerate(
    *,
    user_llm: Any,
    system_message: SystemMessage,
    conversation_messages: List[BaseMessage],
    response: AIMessage,
    response_content: str,
    last_user_msg_text: str,
    prefetch_evidence_for_eval: str,
    has_document: bool,
    is_lesson_creation_turn: bool,
    router_intent: Optional[str],
    meta_conversation_active: bool,
    user_id: Optional[int],
    provider: str,
    config: Any,
    max_input_tokens: int,
    short_mode_active: bool,
    token_pressure_active: bool,
    _mark_step: Any,
) -> Tuple[str, AIMessage]:
    """
    Answer-quality gate: verify grounding vs evidence for lesson-generation, document_qa,
    general_knowledge_qa, and lesson_qa turns; optionally regenerate without tools until
    pass or max attempts. Generalized from the lecture-only failsafe (see PHASE2_DESIGN.md).

    Non-lesson intents are additionally heuristic-gated (_quality_gate_should_escalate) so
    the expensive LLM eval only runs when evidence existed AND the response looks like
    filler — this is what makes default-ON safe. Lesson generation keeps the original
    unconditional-eval behavior: ungrounded lecture claims read as long, well-formed prose,
    not short filler, so the filler heuristic would routinely miss the failure mode it was
    built to catch there.
    """
    is_lesson_mode = is_lesson_creation_turn or router_intent == "lesson_generation"
    qualifying_intents = set(
        os.getenv(
            "RAG_ANSWER_QUALITY_GATE_INTENTS", ",".join(_ANSWER_QUALITY_GATE_DEFAULT_INTENTS)
        ).split(",")
    )

    if meta_conversation_active:
        return response_content, response
    if not is_lesson_mode and (router_intent not in qualifying_intents):
        return response_content, response
    if short_mode_active or token_pressure_active:
        return response_content, response
    if not _answer_quality_gate_enabled():
        return response_content, response
    load_test_override = os.getenv(
        "RAG_ANSWER_QUALITY_GATE_IN_LOAD_TEST",
        os.getenv("RAG_LECTURE_FAILSAFE_IN_LOAD_TEST", "false"),
    )
    if _LOAD_TEST_MODE and load_test_override.lower() not in ("true", "1", "yes"):
        return response_content, response
    if not (response_content or "").strip():
        return response_content, response
    if _is_underspecified_rag_query(last_user_msg_text):
        return response_content, response

    if not is_lesson_mode:
        if not _quality_gate_should_escalate(response_content, prefetch_evidence_for_eval):
            return response_content, response

    max_rounds = max(2, int(os.getenv(
        "RAG_ANSWER_QUALITY_GATE_MAX_ROUNDS",
        os.getenv("RAG_LECTURE_FAILSAFE_MAX_ROUNDS", "4"),
    )))
    eval_llm = user_llm.with_structured_output(AnswerQualityEvalResult)
    ...  # rest of the loop body is UNCHANGED from today (evidence collection, eval/regen
         # rounds, sanitize, max-rounds fallback) — see §5 for what else gets renamed only.
```

`is_lesson_creation_turn` is kept (not dropped) as a belt-and-suspenders OR with
`router_intent == "lesson_generation"` during the transition: if Phase 1's router
misclassifies a genuine lesson-creation turn as something else, the legacy heuristic still
routes it through the unconditional (non-heuristic-gated) lesson path instead of silently
falling through the new intent check. Revisit removing the OR once the router is proven
reliable in production.

### Call site (`_chat_invoke_llm_with_retry`, lines 4135–4157)

**Before:**

```python
if (
    is_lesson_creation_turn
    and isinstance(response, AIMessage)
    and not getattr(response, "tool_calls", None)
):
    response_content, response = _lecture_failsafe_eval_and_maybe_regenerate(
        user_llm=user_llm,
        system_message=system_message,
        conversation_messages=conversation_messages,
        response=response,
        response_content=response_content,
        last_user_msg_text=last_user_msg_text,
        prefetch_evidence_for_eval=prefetch_evidence_for_eval,
        has_document=has_document,
        is_lesson_creation_turn=is_lesson_creation_turn,
        user_id=user_id,
        provider=provider,
        config=config,
        max_input_tokens=max_input_tokens,
        short_mode_active=mode_flags[0],
        token_pressure_active=mode_flags[1],
        _mark_step=_mark_step,
    )
```

**After:** the outer `is_lesson_creation_turn and` condition is dropped — the intent
qualification now lives entirely inside the gate function (single source of truth, easier to
unit test in isolation) — and two new args are threaded from `prep`:

```python
if (
    isinstance(response, AIMessage)
    and not getattr(response, "tool_calls", None)
):
    response_content, response = _answer_quality_gate_eval_and_maybe_regenerate(
        user_llm=user_llm,
        system_message=system_message,
        conversation_messages=conversation_messages,
        response=response,
        response_content=response_content,
        last_user_msg_text=last_user_msg_text,
        prefetch_evidence_for_eval=prefetch_evidence_for_eval,
        has_document=has_document,
        is_lesson_creation_turn=is_lesson_creation_turn,
        router_intent=prep.router_intent,
        meta_conversation_active=prep.meta_conversation_active,
        user_id=user_id,
        provider=provider,
        config=config,
        max_input_tokens=max_input_tokens,
        short_mode_active=mode_flags[0],
        token_pressure_active=mode_flags[1],
        _mark_step=_mark_step,
    )
```

## 4. Env vars

| New | Default | Replaces | Purpose |
|---|---|---|---|
| `RAG_ANSWER_QUALITY_GATE_ENABLED` | `"true"` | `RAG_LECTURE_FAILSAFE_ENABLED` (was `"false"`) | master on/off |
| `RAG_ANSWER_QUALITY_GATE_MAX_ROUNDS` | `"4"` | `RAG_LECTURE_FAILSAFE_MAX_ROUNDS` | eval/regen rounds |
| `RAG_ANSWER_QUALITY_GATE_IN_LOAD_TEST` | `"false"` | `RAG_LECTURE_FAILSAFE_IN_LOAD_TEST` | run under `LOAD_TEST_MODE` |
| `RAG_ANSWER_QUALITY_FILLER_MAX_CHARS` | `"350"` | (new) | heuristic length threshold |
| `RAG_ANSWER_QUALITY_GATE_INTENTS` | `"lesson_generation,document_qa,general_knowledge_qa,lesson_qa"` | (new) | ops kill-switch per intent without a redeploy |

Old names are read as a one-release fallback (see `_answer_quality_gate_enabled` /
`RAG_ANSWER_QUALITY_GATE_MAX_ROUNDS` above) with a deprecation log line, then can be deleted
in a follow-up cleanup once staging/prod configs are confirmed migrated.

### Naming decision: rename, don't keep `RAG_LECTURE_FAILSAFE_*`

Grepped the whole repo (`grep -r RAG_LECTURE_FAILSAFE`): the only hit is
`app/utils/rag_service.py` itself — no docker-compose, deploy.sh, README, or `.env.example`
reference it (none of the latter exists in this repo). Blast radius of a rename is therefore
just this file plus whatever staging/prod `.env` currently sets it (main should confirm with
whoever manages the staging env before this ships — flagged in the report). Given:

- the default value is changing regardless (`false` → `true`), which already requires an
  ops-facing deploy note — renaming doesn't add a *new* category of surprise, it rides the
  same one;
- `RAG_LECTURE_FAILSAFE_*` actively misleads once it also gates `document_qa` — an ops
  person grepping for "why is this document Q&A turn calling `with_structured_output` twice"
  would never find `RAG_LECTURE_FAILSAFE_ENABLED` as the answer;

I'm renaming to `RAG_ANSWER_QUALITY_GATE_*`, with the one-release fallback read above to
protect anyone who already has the old var pinned to `false` in an environment file.

## 5. Other renames (for consistency, mechanically same content)

| Old | New |
|---|---|
| `LectureFailsafeEvalResult` | `AnswerQualityEvalResult` (fields unchanged: `passed`, `is_underspecified_clarification`, `reasoning`, `feedback_for_regeneration`) |
| `LECTURE_FAILSAFE_EVAL_PROMPT` | `ANSWER_QUALITY_EVAL_PROMPT` — wording changed from "LECTURE / lesson BODY text" to "ANSWER TEXT (a full lecture body OR a direct document/general-knowledge Q&A reply)" so the prompt reads correctly for the 3 new intents, not just lessons |
| `_collect_document_evidence_for_failsafe` | `_collect_document_evidence_for_quality_gate` |
| `_format_lecture_failsafe_prompt` | `_format_answer_quality_eval_prompt` |
| `_lecture_failsafe_eval_and_maybe_regenerate` | `_answer_quality_gate_eval_and_maybe_regenerate` |

Confirmed via `grep -rn "lecture_failsafe\|LectureFailsafeEvalResult\|LECTURE_FAILSAFE" tests/`
in the main checkout (`iqbal_ai_stg/tests`, which has the real test suite — see §6): **zero
hits**. No existing test locks any of these names by string, so renaming is safe from a
test-breakage standpoint. `tests/test_lecture_generation_fixes_static.py` (read in full, see
§7) does regex-check the literal fallback strings my heuristic depends on
(`DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF` at lines 3306–3311) — noted as a shared-fixture risk
in §7.

## 6. Open questions / risks for main + Phase 1

1. **Worktree base mismatch.** This worktree's branch (`worktree-agent-a70d180170c889c7d`)
   was cut from `a39a37b` (tip of the default line at the time), while Phase 1's worktree
   (`agent-a31abe7adf8f0d07e`, branch `phase1/llm-driven-routing`) is cut from `8ef34d7`
   (`feature/llm-driven-agentic-routing`). These two commits are **not** ancestors of each
   other — `git merge-base a39a37b 8ef34d7` = `c9d1106`, and each side has ~15-20 commits the
   other lacks (e.g. `8ef34d7`'s line has the own-answer-followup fix, the math-delimiter
   fixes, and `_is_own_answer_followup_request` / `_find_last_substantive_ai_answer`, none of
   which exist in this worktree; `a39a37b`'s line has PDF NUL-byte/UTF-8 ingestion fixes not
   on `8ef34d7`). The `_lecture_failsafe_eval_and_maybe_regenerate` function itself predates
   the split and is present verbatim on both lines, so this design doc's line numbers and
   before/after diffs are accurate for *that function*, but the surrounding file has drifted
   enough that a plain patch/diff apply will likely conflict. **Before I do the real
   integration, I should be pointed at whatever branch is the actual merge target** (probably
   `feature/llm-driven-agentic-routing` after Phase 1 lands) rather than rebasing this
   worktree's current base.
2. **Router intent taxonomy confirmation.** This design assumes `router_intent` is a plain
   string with values including exactly `lesson_generation`, `document_qa`,
   `general_knowledge_qa`, `lesson_qa`, `meta_conversation`, `greeting_casual`,
   `clarification`, and that `meta_conversation_active` is a separate bool (not derivable
   from `router_intent == "meta_conversation"` alone — I gate on it directly in case it's
   true for more than one intent value). Please confirm the literal string values before I
   wire the real `set(...)` comparison, since a mismatch would silently no-op the whole gate.
3. **`has_document=False` turns.** The existing function already handles no-PDF lessons
   (`evidence_bundle = "(no PDF for this thread)\n\n" + evidence_bundle` when `not
   has_document`). For `general_knowledge_qa` specifically, is evidence *expected* to be
   empty by design (it's answering from the model's own knowledge, no RAG involved)? If so,
   `_quality_gate_should_escalate` will always return `False` for that intent (no
   `prefetch_evidence_for_eval`), meaning general_knowledge_qa effectively never escalates
   under this design — which seems correct (there's no "evidence ignored" failure mode to
   catch when there was never evidence), but worth confirming that's the intended semantics
   for including it in the qualifying set at all, versus it being included mainly so the
   *lesson-mode* unconditional path can still catch a general-knowledge lecture.
4. **No existing direct unit tests for the eval/regen loop itself.** I searched
   `tests/` for anything exercising `_lecture_failsafe_eval_and_maybe_regenerate`'s
   internals (mocking `user_llm.with_structured_output`, asserting round-count behavior,
   etc.) — there are none. Today's only coverage is indirect, via the static prompt-content
   checks in `test_lecture_generation_fixes_static.py`. Phase 2's test plan (§8) adds the
   first-ever direct unit tests for this loop, which is good, but means "regression-proof
   lesson-generation still works exactly as before" is a *new* test asserting existing
   behavior, not a pre-existing test that already caught regressions — main should know the
   safety net here is being built now, not verified against a pre-existing baseline.
5. **Default-flip cost impact on lesson_generation is NOT covered by the heuristic.**
   Because lesson_generation intentionally bypasses `_quality_gate_should_escalate` (§3
   rationale), flipping the default to `"true"` means every lesson-generation turn now pays
   for at least one full LLM eval call by default in every environment that doesn't override
   the env var — this exactly matches what "explicitly turn it on" already did today, just
   with a new default. Confirm this is acceptable prod cost (lesson generation is already the
   most expensive turn type) before shipping the default flip; if not, an easy knob is to keep
   `RAG_ANSWER_QUALITY_GATE_ENABLED` defaulting to `true` for the three new intents but require
   an explicit opt-in specifically for `lesson_generation` via
   `RAG_ANSWER_QUALITY_GATE_INTENTS` (drop it from the default set) until cost is measured.

## 7. Test plan

Following house style: dependency-requiring unit tests use `pytest.importorskip("app.utils.rag_service")`
and `monkeypatch` (pattern from `tests/test_finalize_lesson_tool.py`, `tests/test_own_answer_followup.py`);
dependency-free tests do regex/source inspection only (pattern from
`tests/test_lecture_generation_fixes_static.py`), useful for CI legs without the full
requirements installed.

### 7a. `tests/test_answer_quality_gate_heuristic.py` (new, pytest + importorskip)

`TestLooksLikeFillerNonAnswer` (true/false positive matrix from §2):

- `test_flags_the_live_zero_discriminant_filler_reply` — exact staging text: `"If you have
  any further questions about quadratic equations or any related topics, feel free to
  ask!"` → `True`.
- Parametrized true positives: `"Let me know if you need anything else!"`,
  `"Don't hesitate to ask if you have more questions."`, `"Happy to help further — just
  ask!"`, `"Anything else I can help with?"`.
- `test_does_not_flag_not_present_in_document_fallback` — the exact string from
  `DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF`: `"The answer is not present in the document.
  Would you like me to answer from my own knowledge base?"` → `False`.
- `test_does_not_flag_irrelevant_question_fallback` — `"Irrelevant question. Do you want me
  to answer from my own knowledge base?"` → `False`.
- `test_does_not_flag_long_answer_with_citation_that_ends_politely` — a >350-char grounded
  answer containing `"Page 41"` and ending with `"Let me know if you'd like more detail."` →
  `False` (citation marker present).
- `test_does_not_flag_substantive_short_answer_without_filler_phrase` — `"The discriminant
  is b^2 - 4ac; zero means one repeated real root (Page 41)."` → `False` (no filler phrase
  matched at all).
- `test_empty_response_not_flagged` — `""` / `None` → `False`.

`TestQualityGateShouldEscalate`:

- `test_escalates_on_filler_with_evidence_present` — filler text + non-empty
  `prefetch_evidence_for_eval` → `True`.
- `test_does_not_escalate_when_no_evidence_even_if_filler` — same filler text +
  `prefetch_evidence_for_eval=""` → `False` (evidence gate short-circuits first).
- `test_does_not_escalate_on_substantive_response_even_with_evidence` — grounded answer +
  evidence present → `False`.

### 7b. `tests/test_answer_quality_gate_wiring_static.py` (new, dependency-free, mirrors
`test_lecture_generation_fixes_static.py`'s `inspect.getsource`/regex style)

- `test_gate_function_checks_router_intent_not_just_lesson_flag` — asserts the function
  source contains the qualifying-intents check (e.g. `"router_intent not in"` or the
  `_ANSWER_QUALITY_GATE_DEFAULT_INTENTS` tuple), not a bare `if not is_lesson_creation_turn:`
  early-return as the *sole* gate.
- `test_meta_conversation_active_short_circuits_before_eval` — asserts
  `if meta_conversation_active:` appears before the heuristic/LLM-eval code in the function
  body.
- `test_default_enabled_flips_to_true` — asserts the new env var's default literal is
  `"true"`, and that reading it happens via a helper (not a bare inline `os.getenv(...,
  "false")` regression of the old default).
- `test_lesson_mode_bypasses_heuristic_prefilter` — asserts the source has an
  `if not is_lesson_mode:` guard around the `_quality_gate_should_escalate` call, proving
  lesson-generation turns still get the unconditional full-eval path (the §3/§6.5
  regression this whole gate depends on).
- `test_old_env_var_names_still_read_as_fallback` — asserts
  `RAG_LECTURE_FAILSAFE_ENABLED` / `RAG_LECTURE_FAILSAFE_MAX_ROUNDS` /
  `RAG_LECTURE_FAILSAFE_IN_LOAD_TEST` still appear *somewhere* in the source (the
  deprecation-fallback reads), so a careless rename doesn't strand an existing pinned prod
  config.

### 7c. `tests/test_answer_quality_gate_eval_loop.py` (new, pytest + importorskip;
first-ever direct coverage of the eval/regen loop — see §6.4)

Mirrors the monkeypatch style of `tests/test_finalize_lesson_tool.py`
(`monkeypatch.setattr(rag_service, ...)`), but here the target is `user_llm`, a fake object
whose `.with_structured_output(...)` returns a stub whose `.invoke(...)` is scripted:

- `test_lesson_generation_turn_passes_on_first_eval_unchanged` — **regression test**: stub
  eval returns `passed=True` immediately; assert `eval_llm.invoke` called exactly once,
  `user_llm.invoke` (regen) never called, and the returned content is unchanged from the
  input `response_content`. Run with `router_intent="lesson_generation"` AND separately with
  `router_intent=None, is_lesson_creation_turn=True` (legacy path) to prove both still work
  identically — this is the "lesson-generation turns still work exactly as before" proof.
- `test_lesson_generation_turn_regenerates_until_pass` — stub eval fails round 1
  (`passed=False, feedback_for_regeneration="add citations"`), passes round 2; assert
  exactly one regen call, the `"[Automated quality verification — lecture only]"` /
  `"Required fixes:\nadd citations"` HumanMessage was in the regen prompt, and the final
  returned content is the round-2 stub's regenerated text.
- `test_lesson_generation_hits_max_rounds_keeps_last_draft` — stub eval always fails;
  assert loop stops at `max_rounds`, no exception propagates, last regenerated draft is
  returned.
- `test_document_qa_turn_skips_eval_when_heuristic_says_no` — `router_intent="document_qa"`,
  substantive (non-filler) `response_content`, evidence present; assert `eval_llm.invoke`
  is **never called** (heuristic pre-filter blocked escalation) — this is the cost-safety
  property the whole design exists for.
- `test_document_qa_turn_runs_eval_when_heuristic_escalates` — `router_intent="document_qa"`,
  filler `response_content` + evidence present; assert `eval_llm.invoke` **is** called.
- `test_general_knowledge_qa_with_no_evidence_never_escalates` — `router_intent=
  "general_knowledge_qa"`, `prefetch_evidence_for_eval=""`; assert `eval_llm.invoke` never
  called regardless of response content (§6.3's assumption, locked in as a test).
- `test_greeting_casual_intent_is_a_full_noop` — `router_intent="greeting_casual"`; assert
  the function returns `(response_content, response)` unchanged and neither `with_structured_
  output` nor `invoke` are ever called on `user_llm`.
- `test_meta_conversation_active_is_a_full_noop_even_with_qualifying_intent` — sets
  `meta_conversation_active=True` together with `router_intent="document_qa"` and a filler
  response + evidence (would otherwise escalate); assert still a full no-op.

### 7d. What must keep passing unchanged: `tests/test_lecture_generation_fixes_static.py`

Read in full (main checkout, `iqbal_ai_stg/tests/test_lecture_generation_fixes_static.py`).
None of its ~19 tests reference `_lecture_failsafe_eval_and_maybe_regenerate`,
`LectureFailsafeEvalResult`, or any `RAG_LECTURE_FAILSAFE_*` var by name, so the §5 renames
don't touch it directly. What Phase 2 **must not break**:

- `test_no_pdf_system_message_includes_math_formatting_instructions` and
  `test_prompt_routes_lecture_requests_to_teach_topic_tool` regex over
  `DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF` / `_NO_PDF` / `_LOAD_TEST` bodies — Phase 2 does not
  touch these system-prompt strings, only reads two fixed substrings out of them for the
  heuristic exclusion list (§2). If a future change to that fallback wording (lines
  3306–3311) ever lands, **both** this static test's assumptions and my
  `_LEGITIMATE_FALLBACK_PATTERNS` list need updating together — flagging so it isn't missed
  as a silent drift.
- `test_tool_round_limit_untouched_at_15` — unrelated counter, untouched by this change.
- Everything else in that file is frontend/formatter (`chat-response-formatter.js`,
  `teacher_dashboard.html`) or Dockerfile timeout checks — no overlap with this gate at all.

## 8. Summary of the actual code diff shape (for Phase 2's real implementation later)

1. Add `_looks_like_filler_non_answer` + `_quality_gate_should_escalate` (new, pure
   functions, easy to unit test standalone).
2. Rename `LectureFailsafeEvalResult` → `AnswerQualityEvalResult`,
   `LECTURE_FAILSAFE_EVAL_PROMPT` → `ANSWER_QUALITY_EVAL_PROMPT` (reworded),
   `_collect_document_evidence_for_failsafe` → `_collect_document_evidence_for_quality_gate`,
   `_format_lecture_failsafe_prompt` → `_format_answer_quality_eval_prompt`,
   `_lecture_failsafe_eval_and_maybe_regenerate` → `_answer_quality_gate_eval_and_maybe_regenerate`.
3. Rewrite the gate preamble per §3; loop body (evidence collection call, eval/regen rounds,
   sanitize, max-rounds handling) stays mechanically the same, just renamed callees.
4. Update the call site in `_chat_invoke_llm_with_retry` per §3 (drop outer
   `is_lesson_creation_turn and`, thread `router_intent`/`meta_conversation_active` from
   `prep`).
5. Add the 3 new test files per §7; do not touch `test_lecture_generation_fixes_static.py`.
