# Phase 3 Design: Lesson Lifecycle / Post-Finalize Editing

Status: design only, no implementation. Blocked on Phase 1's `RouterOutput` (intents
`lesson_modification` / `lesson_qa` / `lesson_save`, plus `meta_conversation` /
`document_qa`), which does not exist in the codebase yet (verified — see "What I
verified" below).

Branch/worktree: `worktree-agent-a1b84d13f82fd7d33` at
`.claude/worktrees/agent-a1b84d13f82fd7d33` (based on `main` @ `a39a37b`; main has
flagged this base is stale — same issue phase2/phase4 hit — and will sort out the
right base once Phase 1 merges).

**Update after checking in with main:** the four open questions originally at the
bottom of this doc are answered inline now (search "RESOLVED"). Two corrections to
what I originally wrote, both from main's answers:
1. The router intent field is confirmed as `router_intent`, a plain string threaded
   through `_ChatTurnSystemPrep` exactly like `is_lesson_creation_turn` /
   `own_answer_followup_active` already are — section 2's sketch below is updated to
   match the real call-site shape.
2. `tests/` in this repo is gitignored except `tests/test_routes_helper.py` (an
   established convention) — the three test files I originally reported as
   "don't exist anywhere in git history" DO exist for real, as uncommitted local
   files in the main checkout's `tests/` directory
   (`C:\Users\user\Desktop\iqbalai-v1.1\iqbal_ai_stg\tests\`). I've now read them
   directly and corrected section 5 below to match their actual house style instead
   of inventing one from scratch.

## The bug, precisely

`app/utils/rag_service.py`, function `_chat_handle_lesson_state_and_persistence`
(currently ~line 3911 on `main`; ~line 4630 on `phase1/llm-driven-routing`, which
already has the tool-call-based `finalize_lesson_tool` this doc assumes — see below).

The persistence branch that writes the in-chat draft is gated like this today
(phase1 branch, which already fixed the "always capture even short replies" half of
this — I'm quoting *that* version since it's the one Phase 3 will actually build on):

```python
elif thread_id_str and response_content:
    try:
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
        if thread_row and not getattr(thread_row, "lesson_finalized", False):
            thread_row.last_lesson_text = response_content
            db.commit()
    except Exception as e:
        logger.warning("Error saving lesson text to DB: %s", e)
    _mark_step("persist_in_progress_lesson")
```

The write only happens `not thread_row.lesson_finalized`. The moment
`finalize_lesson_tool` sets `lesson_finalized = True`, this branch permanently stops
writing for that thread. A later "add 5 examples" turn produces a perfectly good
`response_content`, but it is never written to `last_lesson_text` — the column is
frozen at whatever it held at finalize time. If the user then says "save it again",
`finalize_lesson_tool` re-reads `thread_row.last_lesson_text` (still the stale
pre-edit text) and reports `success=true, "Lesson re-saved with the latest content"`
— which is a lie; nothing new was saved.

## What I verified (codebase ground truth)

- **My worktree/`main` does not have `finalize_lesson_tool` at all.** `main`'s
  `rag_service.py` is 4843 lines and finalizes lessons via a regex/keyword match on
  the user's message (`user_wants_to_finalize`), not a tool call. The tool-call-based
  design this doc assumes (`finalize_lesson_tool`, `_check_if_content_is_lesson` at
  ~5395, `_persist_finalized_lesson_static` at ~5437, the 5572-line file) lives on
  `phase1/llm-driven-routing` (HEAD `8ef34d7`, same commit as
  `feature/llm-driven-agentic-routing`). I read that branch's file directly (via
  `git show`, not merged into my worktree) to ground this design. Per instructions I
  have not merged it or edited `rag_service.py`.
- **`RouterOutput` / `lesson_modification` / `lesson_qa` / `lesson_save` /
  `meta_conversation` / `document_qa` do not exist anywhere in git history yet** (I
  grepped `phase1/llm-driven-routing` for all of these — zero matches). This is
  consistent with "Phase 1 is currently implementing" — I'm designing against the
  *contract* described in my task prompt, not against code I could read.
- **RESOLVED — the three test files named in my task prompt are real**, just not
  visible from any worktree: `tests/` is gitignored in this repo except
  `tests/test_routes_helper.py`, so they only exist as uncommitted local files in the
  main checkout (`C:\Users\user\Desktop\iqbalai-v1.1\iqbal_ai_stg\tests\`), which
  explains why `git log --all` on those paths found nothing. I read all three
  directly from there — see section 5, which now reflects their real fake-object
  pattern instead of one I invented from scratch.
- **Traced the `RAGThread` ↔ `LessonModel`/`Lesson` relationship precisely** (this
  was the highest-risk unknown): they are **two independent persistence systems**,
  bridged only by a one-shot, user-initiated copy. See section 4.

## 1. State model

**Recommendation: no new table, no new column.** The existing three fields
(`RAGThread.lesson_finalized`, `last_lesson_text`, `lesson_title`) are sufficient. The
bug is not a missing state dimension — it's that the write-gate uses the wrong
variable. Today it asks "has this thread ever been finalized?" (a one-way ratchet).
It should ask "does *this turn* represent a lesson edit?" (a per-turn intent
question) — which is exactly what Phase 1's router now answers for us.

Concretely: replace the `not thread_row.lesson_finalized` gate with a gate on the
router's per-turn intent. Once that's true, `lesson_finalized` no longer needs to
suppress writes at all — it becomes purely a display/UX flag ("has this thread ever
produced a saved lesson," used by the finalized-lesson GET endpoint and the frontend
download/"Save to My Lessons" buttons), not a write-lock. No "resume editing" state
transition is needed because there is no longer a state that *needs* resuming — the
intent classification already tells us, turn by turn, whether we're in an editing
turn.

Why not a version-history table: the task that actually needs "does this table need
versions" is already solved by `Lesson.lesson_id` / `version_number` /
`parent_version_id` / `has_child_version` on the **published** side (see section 4).
`RAGThread.last_lesson_text` is a single-slot scratch buffer for "the current draft
in this chat," and a single slot is the right model for it — a teacher editing a
lesson in chat has exactly one current draft, not a browsable history, and nothing in
the bug report asks for undo/history in the chat draft itself. If undo-in-chat is
wanted later, it's a small additive table (see "Optional, not recommended now"
below), not a redesign.

## 2. The finalize → edit → resave flow: before/after

### Before (current, phase1 branch)

```python
elif thread_id_str and response_content:
    try:
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
        if thread_row and not getattr(thread_row, "lesson_finalized", False):
            thread_row.last_lesson_text = response_content
            db.commit()
    except Exception as e:
        logger.warning("Error saving lesson text to DB: %s", e)
    _mark_step("persist_in_progress_lesson")
```

### After (Phase 3 — `router_intent` confirmed by main: a plain string threaded
through `_ChatTurnSystemPrep` exactly like `is_lesson_creation_turn` /
`own_answer_followup_active` already are, per `_chat_invoke_llm_with_retry`,
~line 4754-4762 on phase1 branch)

Function signature gets one new keyword-only param, following the existing pattern
(`turn_scope_messages` was added the same way for the reordering fix):

```python
def _chat_handle_lesson_state_and_persistence(
    *,
    response: AIMessage,
    response_content: str,
    messages: List[BaseMessage],
    last_user_msg_text: str,
    thread_id_str: Optional[str],
    provider: str,
    user_llm_structured_output: Any,
    config: Any,
    _mark_step: Any,
    turn_scope_messages: Optional[List[BaseMessage]] = None,
    router_intent: Optional[str] = None,   # new
) -> AIMessage:
```

Call site in `_chat_invoke_llm_with_retry` (~line 4862) adds one line:

```python
response = _chat_handle_lesson_state_and_persistence(
    response=response,
    response_content=response_content,
    messages=messages,
    last_user_msg_text=last_user_msg_text,
    thread_id_str=thread_id_str,
    provider=provider,
    user_llm_structured_output=user_llm_structured_output,
    config=config,
    _mark_step=_mark_step,
    turn_scope_messages=conversation_messages,
    router_intent=prep.router_intent,   # new — same pattern as prep.is_lesson_creation_turn
)
```

And the write-gate itself:

```python
elif thread_id_str and response_content and router_intent == "lesson_modification":
    try:
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id_str).first()
        if thread_row:
            thread_row.last_lesson_text = response_content
            db.commit()
    except Exception as e:
        logger.warning("Error saving lesson text to DB: %s", e)
    _mark_step("persist_in_progress_lesson")
```

Key changes:
- The gate switches from `not thread_row.lesson_finalized` to
  `router_intent == "lesson_modification"`. This makes the write fire identically
  before *and* after finalize — finalized is no longer special-cased for writes at
  all.
- `lesson_qa`, `meta_conversation`, `document_qa`, and any other intent never reach
  this branch, so they never touch `last_lesson_text` — pre- or post-finalize. This
  is strictly safer than today, where *any* sufficiently-produced reply during an
  un-finalized thread currently overwrites the draft regardless of what the turn was
  actually about.
- **`finalize_lesson_tool` needs no code change.** It already does
  `content = thread_row.last_lesson_text` fresh from the DB on every call (including
  re-finalize). Once the write-gate fix above lands, by the time the user says "save
  it again," `last_lesson_text` already holds the edited content — so
  `finalize_lesson_tool` re-saving it is correct by construction. I traced this
  specifically because the task called out "re-finalize must save NEW content" as a
  thing to verify, not assume: it holds once the upstream write is fixed, and *only*
  once — today, with the write frozen, re-finalize genuinely re-persists stale
  content while claiming "re-saved with the latest content," which is the visible
  symptom of the bug.
- `lesson_save` intent: maps to the model calling `finalize_lesson_tool` (unchanged;
  already tool-driven per the docstring: "Call this tool whenever the user's intent…
  is to save, finalize, complete, or lock in the lesson"). Nothing here needs to
  change — `lesson_save` is Phase 1's classification of the *same* user utterances
  that already trigger the tool call today, just via router-scored intent instead of
  leaving it entirely to the model's own tool-calling judgment.

### A required companion change I want to flag explicitly (system prompt, not this function)

If the model's reply to "add 5 examples" is a short confirmation ("I've added 5
examples to the Photosynthesis section") rather than the *full* updated lesson body,
then the fix above would write that short confirmation into `last_lesson_text`,
**destroying** the finalized lesson content rather than updating it — a strictly
worse outcome than today's "frozen" bug. I checked
`DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF` (phase1 branch, ~line 3900-3990): there is no
instruction telling the model to re-emit the complete lesson text on an edit turn.
Phase 3 must add one, e.g.: *"When the user asks you to modify a lesson you have
already been building or that was previously finalized in this conversation, always
respond with the complete, updated lesson text in full — not just a description of
the change — since your reply is what gets saved."* This is a **hard dependency of
the persistence-gate fix, not an optional companion or a separate follow-up** — it
ships in the same PR, same commit if practical. Without it, the gate fix is actively
dangerous (confirmed with main: this is how it'll be treated at implementation time).

## 3. State-aware context switching

Confirmed and stated explicitly per the task's ask: with the fix in section 2, the
write to `RAGThread.last_lesson_text` is reachable **only** when
`router_intent == "lesson_modification"`. `meta_conversation` and `document_qa` turns
fall through to neither the finalize branch (they won't produce a
`finalize_lesson_tool` call) nor the modification-write branch (intent mismatch), so
they touch no lesson-state field at all — not `last_lesson_text`, not
`lesson_finalized`, not `lesson_title`. This holds regardless of whether the thread
has ever been finalized, which is the actual guarantee this task asked me to confirm
(today, pre-finalize, that guarantee does *not* hold — see "one more finding" below).

One more finding worth surfacing: today's *pre-finalize* write condition
(`not thread_row.lesson_finalized`, with no intent check at all) means an unrelated
`document_qa`-shaped turn happening before the user ever finalizes — e.g. "what does
photosynthesis mean" asked mid-lesson-build — currently *can* overwrite
`last_lesson_text` with an unrelated answer, if that answer happens to be produced
while the thread isn't finalized yet. The router-intent gate in section 2 fixes this
for free on both sides of finalize, not just post-finalize. I'm calling this out
because it means Phase 3's fix is not purely additive to the post-finalize case — it
also tightens pre-finalize behavior, which is in scope per the task's point 3 ("never
as a side effect of an unrelated turn") but is a slightly larger behavioral change
than "just unfreeze after finalize," so I want main's sign-off that tightening the
pre-finalize path too is wanted, not just the post-finalize unfreeze.

## 4. Relationship to `LessonModel` / the published lesson system

**Traced this fully; drawing a hard boundary: Phase 3 is scoped entirely to the
`RAGThread` in-chat draft. It does not touch `Lesson`/`LessonModel` at all.**

They are two independent systems, connected only by a one-shot manual copy:

- **`RAGThread`** (`app/models/database_models.py:380`) — one row per chat thread.
  `last_lesson_text` / `lesson_finalized` / `lesson_title` are a scratch buffer used
  only by (a) `finalize_lesson_tool`'s validation/save, and (b) two frontend
  actions: the chat "Download" button and the "Save Lesson" button, both of which
  `GET /api/rag/thread/<id>/finalized-lesson` to read the current draft
  (`app/routes/rag_routes.py:2149`). Nothing in `app/services/lesson/` or
  `lesson_qa_graph.py` ever reads `RAGThread.last_lesson_text`.

- **`Lesson`** (`app/models/database_models.py:50`, wrapped by `LessonModel` in
  `app/models/models.py`) — the actual system of record for "My Lessons" and student
  Q&A (`app/services/lesson/lesson_qa_graph.py`). It has real version history:
  `lesson_id` (logical id shared across versions), `version_number`,
  `parent_version_id`, `has_child_version`, `status`, `draft_content` /
  `original_content`. This is the system `tests/test_lesson_delete_visibility.py` and
  `tests/test_group_c_bug_fix_static.py` were supposed to exercise (see caveat above
  — I could not find these files to confirm).

- **The bridge is a one-shot, explicit, frontend-driven copy, not a live link.**
  `templates/teacher_dashboard.html` `saveLessonToMyLessons()` (~line 9573) fetches
  the thread's `last_lesson_text` via the endpoint above and `POST`s it to
  `/api/lessons/create` (`app/routes/lesson_routes.py:482`,
  `create_lesson_simple`), which calls `LessonModel.create_lesson(...,
  rag_thread_id=<the chat thread id>, status='finalized')`. This **always creates a
  brand-new `Lesson` row** (new `id`, and `check_title_exists` rejects a duplicate
  title for the same teacher) — it never looks for an existing `Lesson` with a
  matching `rag_thread_id` and never calls `LessonModel.create_new_version(...)`.
  `rag_thread_id` on the `Lesson` row is set purely so
  `lesson_qa_graph.py`'s student "Ask Question" flow can retrieve against the same
  uploaded PDF/vector store — it is not used to keep the `Lesson` row's `content` in
  sync with the chat thread going forward.

**Consequence, explicitly out of scope for Phase 3, flagged as a separate known gap:**
once a teacher has clicked "Save Lesson" (creating a `Lesson` row) and later edits
further in the same chat and clicks "Save Lesson" again, today's `create_lesson_simple`
creates a **second, disconnected `Lesson` row** rather than a new version of the
first (or fails outright on a duplicate title, since the button reuses
`getCurrentChatThreadTitleForLesson()`-derived titles with a uniqueness suffix — it
degrades to producing near-duplicate lessons, not a version chain). Fixing Phase 3's
bug (post-finalize edits landing in `last_lesson_text`) does **not** fix this,
because the corruption/duplication happens one layer downstream, in
`create_lesson_simple`, which Phase 3 does not touch. I'm flagging this as a
candidate follow-up (teach `create_lesson_simple` to look up an existing `Lesson` by
`(teacher_id, rag_thread_id)` and call `LessonModel.create_new_version_from_draft`
instead of `create_lesson`), but recommend treating it as separate work: it's a
`LessonModel`/`lesson_routes.py` change with its own test surface
(`has_child_version`/visibility semantics), not a `rag_service.py` chat-persistence
change, and conflating the two would be exactly the "significant rework" risk this
task warned me to avoid.

## 5. Test plan

**Updated after reading the real files** (found in the main checkout's gitignored
`tests/` dir — see "What I verified"). The house pattern is exactly the fake-object
style I'd guessed at, but now confirmed verbatim from
`tests/test_finalize_lesson_tool.py`:

```python
class _FakeThreadRow:
    def __init__(self, last_lesson_text="", lesson_finalized=False):
        self.last_lesson_text = last_lesson_text
        self.lesson_finalized = lesson_finalized
        self.lesson_title = ""

class _FakeQuery:
    def __init__(self, row):
        self._row = row
    def filter_by(self, **kwargs):
        return self
    def first(self):
        return self._row

class _FakeDB:
    def __init__(self, row):
        self._row = row
        self.committed = False
    def query(self, model):
        return _FakeQuery(self._row)
    def commit(self):
        self.committed = True
```

Tests monkeypatch `rag_service.get_db` to return a `_FakeDB`, and call
`rag_service.finalize_lesson_tool.invoke({...})` /
`rag_service._chat_handle_lesson_state_and_persistence(...)` directly — no real
SQLAlchemy engine, no `pytest.importorskip("sqlalchemy")` needed for this file
(that guard is only in `test_lesson_delete_visibility.py`, which uses a real
in-memory SQLite engine because it tests actual query filtering, not simple
attribute mutation).

**Home for new tests: extend `tests/test_finalize_lesson_tool.py` in place**, not a
new file. It already has the exact scaffolding above, already tests
`_chat_handle_lesson_state_and_persistence` directly with hand-built
`HumanMessage`/`AIMessage`/`ToolMessage` lists, and its existing
`test_no_tool_call_falls_back_to_in_progress_persistence` test is the one Phase 3's
fix directly changes the behavior of (see below) — keeping the before/after
assertions in the same file next to each other is more useful than splitting them
out. Per main, the file needs to land in the main checkout's `tests/` directory at
implementation time (gitignored, so my worktree's copy won't be the one that
persists) — main will handle that path detail during integration.

**One existing test needs updating, not just new tests added.**
`test_no_tool_call_falls_back_to_in_progress_persistence` (current file, line ~200)
today calls `_chat_handle_lesson_state_and_persistence` with no intent concept at all
and asserts a plain conversational turn (`"add a diagram section"` /
`lesson_finalized` unset → defaults to `False`) still writes
`last_lesson_text`. Once the fix adds the `router_intent` param, this call will need
`router_intent="lesson_modification"` passed explicitly to keep passing — otherwise,
under the new gate, it will (correctly) NOT write, which would look like a
regression in that test even though it's the new fix behaving as designed. Flagging
so whoever implements doesn't mistake "this old test now fails" for a bug.

Planned new cases (function names anticipate the real file's naming style, e.g.
`test_finalize_lesson_tool_success_persists_and_returns_success`):

**(a) `test_modification_intent_after_finalize_updates_persisted_content`.**
Seed `_FakeThreadRow(lesson_finalized=True, last_lesson_text="<old lesson>")`. Call
`_chat_handle_lesson_state_and_persistence` with
`response_content="<old lesson + 5 examples>"`, `messages=[HumanMessage("add 5
examples")]`, and `router_intent="lesson_modification"`. Assert
`fake_db._row.last_lesson_text == "<old lesson + 5 examples>"` and
`fake_db._row.lesson_finalized is True` (unchanged — finalize state is a display
flag, not touched by an edit) and `fake_db.committed is True`.

**(b) `test_refinalize_after_edit_persists_new_content_not_stale`.**
Two-step, reusing one `_FakeDB`/`_FakeThreadRow` across both calls (mirrors how a
real turn sequence shares one DB row): (1) run the modification turn from (a) so
`last_lesson_text` is updated in the fake row; (2) monkeypatch
`_check_if_content_is_lesson` to `True` (as the existing
`test_finalize_lesson_tool_success_persists_and_returns_success` does) and call
`rag_service.finalize_lesson_tool.invoke({"thread_id": "user_1_abc"})` against the
*same* fake row; assert the JSON result has `success=True`,
`already_finalized=True`, and — critically — assert
`fake_db._row.last_lesson_text` equals the edited content from step 1, not the
original seed content. This directly tests the "lied about re-saving" symptom from
the bug report.

**(c) `test_document_qa_intent_never_touches_lesson_state` /
`test_meta_conversation_intent_never_touches_lesson_state`.**
Seed a finalized thread with known `last_lesson_text`/`lesson_title`. Call
`_chat_handle_lesson_state_and_persistence` with a long, heading-shaped
`response_content` (deliberately shaped to fool the *old* length/shape heuristic
that predates phase1's simplification) but `router_intent="document_qa"` (and
separately `"meta_conversation"`). Assert `fake_db._row.last_lesson_text`,
`.lesson_title`, and `.lesson_finalized` are all byte-for-byte unchanged, and
`fake_db.committed is False` — proving this isn't "wrote the same value back" but
"never entered the write branch at all." Also run the same assertion with
`lesson_finalized=False` on the seed row, to lock in the pre-finalize tightening from
section 3 (main's approval, open question 3 resolved below).

**(d) Existing finalize behavior still works unchanged — already covered, no new
test needed.** `test_response_forced_to_success_message_when_tool_succeeded`,
`test_response_forced_to_failure_reason_when_tool_failed`, and
`test_old_finalize_call_reordered_into_windowed_messages_does_not_leak_into_new_turn`
already regression-lock the tool-message-authoritative override and the turn-scoping
fix. None of them pass a `router_intent`, so they'll pass `None` under the new
signature — I need to confirm at implementation time that `router_intent=None`
(intent not classified/passed) safely falls through to "don't write," same as any
non-`lesson_modification` value, so these three keep passing unmodified.

I'm not writing these tests now per my instructions (design doc only, no real test
file edits yet) — this section is the spec for when Phase 3 actually implements.

## Open questions for main — RESOLVED

All four resolved by main; keeping the original questions plus answers here for the
record, since this doc is what implementation will actually be built from.

1. **Field name/shape of the router's per-turn intent.** RESOLVED: `router_intent`,
   a plain string on `_ChatTurnSystemPrep`, threaded the same way
   `is_lesson_creation_turn` / `own_answer_followup_active` already are, available
   wherever `_chat_handle_lesson_state_and_persistence` is called from
   `_chat_invoke_llm_with_retry`. Gate on `router_intent == "lesson_modification"`.
   Section 2 above is now written against this exact shape (signature + call site).
2. **Whether the three named test files exist.** RESOLVED: they're real, just
   uncommitted — `tests/` is gitignored in this repo except
   `tests/test_routes_helper.py` (confirmed by main, matches phase2 hitting the same
   thing independently). Read all three directly from the main checkout; section 5
   now reflects their actual `_FakeThreadRow`/`_FakeQuery`/`_FakeDB` pattern instead
   of an invented one, and identifies which existing test needs updating (not just
   which new tests to add). Implementation-time reminder from main: the new/edited
   test file needs to land in the main checkout's `tests/` dir specifically, not a
   worktree's (gitignored) copy — main will handle that path detail during
   integration.
3. **Tightening pre-finalize writes too, not just unfreezing post-finalize.**
   APPROVED: gate both pre- and post-finalize writes on
   `router_intent == "lesson_modification"`, dropping `not lesson_finalized`
   entirely, as designed. Main flagged this will be called out explicitly at ship
   time (PR description) and both pre-finalize and post-finalize edit flows get
   live-tested, not just the post-finalize case the original bug report was about.
4. **`create_lesson_simple` duplicate-lesson-on-resave gap.** CONFIRMED out of
   scope: stays a flagged follow-up (`LessonModel`/`lesson_routes.py` territory,
   its own test surface), not pulled into Phase 3.

## Status

Design complete and confirmed with main. Waiting on Phase 1's `RouterOutput`/
`router_intent` to land and merge before real implementation (edits to
`app/utils/rag_service.py` and the test file) can start. Worktree base
(`main @ a39a37b`) is known-stale per main (same issue as phase2/phase4); main will
provide the correct base once Phase 1 merges.
