# Phase 3 Design: Lesson Lifecycle / Post-Finalize Editing

Status: design only, no implementation. Blocked on Phase 1's `RouterOutput` (intents
`lesson_modification` / `lesson_qa` / `lesson_save`, plus `meta_conversation` /
`document_qa`), which does not exist in the codebase yet (verified — see "What I
verified" below).

Branch/worktree: `worktree-agent-a1b84d13f82fd7d33` at
`.claude/worktrees/agent-a1b84d13f82fd7d33` (based on `main` @ `a39a37b`).

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
- **The three test files named in my task prompt
  (`tests/test_finalize_lesson_tool.py`, `tests/test_lesson_delete_visibility.py`,
  `tests/test_group_c_bug_fix_static.py`) do not exist in any branch reachable from
  this repo's `.git`** (`git log --all` on all three paths returns nothing). The only
  test file that exists anywhere is `tests/test_routes_helper.py` (9 trivial
  parametrized cases, no DB/fake-object pattern to mirror). I flag this as an open
  question for main — either they exist uncommitted in another phase's worktree, or
  the references were aspirational. My test plan below defines the pattern from
  scratch, following how `_persist_finalized_lesson_static` / `finalize_lesson_tool`
  actually talk to `get_db()` (a plain SQLAlchemy session via
  `app.utils.db.get_db`), since that's the only house pattern I could verify.
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

### After (Phase 3, once `RouterOutput.intent` is available on `state`/`prep`)

```python
elif thread_id_str and response_content and router_intent in ("lesson_modification",):
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
the change — since your reply is what gets saved."* This is a system-prompt change
that ships alongside the persistence-gate fix, in the same PR — without it the gate
fix is actively dangerous. Flagging it here so it isn't dropped when this design
becomes a ticket.

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

No usable house pattern exists yet for this (see "What I verified" — the three named
test files don't exist in the repo). Proposed new file:
`tests/test_lesson_state_persistence.py`, following the only observable convention
(`tests/test_routes_helper.py`: plain `pytest`, direct imports, no fixtures file) plus
a minimal fake-session pattern matched to how `_persist_finalized_lesson_static` /
`finalize_lesson_tool` actually talk to the DB (`get_db()` →
`db.query(RAGThread).filter_by(thread_id=...).first()` → mutate attrs → `db.commit()`).
A `FakeRAGThread` (plain object with the relevant attrs) + `FakeDBSession` (records
`.query().filter_by().first()` returning the fake row, and a `committed` counter) is
enough — no real SQLAlchemy engine needed, monkeypatching `get_db` in
`app.utils.rag_service`.

Planned cases:

**(a) Editing after finalize updates persisted content.**
Seed a `FakeRAGThread(lesson_finalized=True, last_lesson_text="<old lesson>")`. Call
`_chat_handle_lesson_state_and_persistence` with `response_content="<old lesson +
5 examples>"` and a turn-scope router intent of `lesson_modification` (mocked/passed
in per whatever Phase 1's actual state field turns out to be named). Assert
`thread_row.last_lesson_text == "<old lesson + 5 examples>"` and
`thread_row.lesson_finalized` is still `True` (unchanged — finalize state is a
display flag, not touched by an edit).

**(b) Re-finalizing after an edit saves the NEW content, not stale content.**
Two-step: (1) run the modification turn from (a) so `last_lesson_text` is updated in
the fake DB; (2) call `finalize_lesson_tool(thread_id=...)` against the same fake
thread row and assert the JSON result has `success=True`,
`already_finalized=True`, and — critically — that the persisted
`last_lesson_text` after the call equals the edited content from step 1, not the
original seed content. This directly tests the "lied about re-saving" symptom.

**(c) A `meta_conversation`/`document_qa` turn never touches lesson state.**
Seed a finalized thread with known `last_lesson_text`/`lesson_title`/
`lesson_finalized`. Call `_chat_handle_lesson_state_and_persistence` with a
substantial, lesson-shaped-looking `response_content` (long, has headings — i.e.
deliberately shaped to fool the *old* length/shape heuristic) but intent
`document_qa` (and separately, `meta_conversation`). Assert all three fields on
`thread_row` are byte-for-byte unchanged after the call, and that `db.commit()` was
never invoked for this turn (i.e., the fake session's write counter is `0`) — proving
this isn't just "wrote the same value back" but "never entered the write branch at
all."

**(d) Existing finalize behavior (backend-authoritative success/failure wording)
still works unchanged.** Regression-lock the existing tool-message-authoritative
override in `_chat_handle_lesson_state_and_persistence`: feed a turn tail containing
a `ToolMessage(name="finalize_lesson_tool", content='{"success": true, ...}')` and
assert `response.content == "Lesson finalized and saved. You can download it now."`
regardless of what the model itself said; then the `success: false` case and assert
`response.content` is forced to the tool's `reason` string. This is unchanged by
Phase 3, so this test is a guard against Phase 3's edits accidentally touching that
branch, not new coverage of new behavior.

I'm not writing these tests now per my instructions (design doc only, no real test
files yet) — this section is the spec for when Phase 3 actually implements.

## Open questions for main

1. **Field name/shape of the router's per-turn intent as it will actually appear in
   `rag_service.py`.** My design assumes something like a `router_intent` string
   available at the point `_chat_handle_lesson_state_and_persistence` runs (it
   already receives `messages`/`conversation_messages`/`config` — I'd expect the
   intent to be threaded through similarly, e.g. via `prep` like
   `is_lesson_creation_turn` already is). I need the actual field/param name once
   Phase 1 lands to write the real diff.
2. **The three test files named in my task prompt do not exist anywhere in this
   repo's git history.** Confirmed via `git log --all` on all three paths. Want to
   confirm whether they exist uncommitted in another phase's worktree (and I should
   go read them there) or whether my test plan above (written from scratch against
   the only real code path involved) is the right basis to build from.
3. **Confirm the "tighten pre-finalize writes too" scope call in section 3.** My fix
   naturally also stops non-`lesson_modification` turns from clobbering the draft
   *before* finalize (today's `not lesson_finalized` gate has no intent check at
   all). This is more correct, and I believe it's what the task's point 3 is asking
   for, but it's a slightly bigger behavior change than "just unfreeze after
   finalize" — want explicit sign-off before it's built.
4. **The `create_lesson_simple` duplicate-lesson-on-resave gap (section 4)** is real
   but downstream of Phase 3's boundary. Confirming it should stay a flagged
   follow-up and not get pulled into this phase.
