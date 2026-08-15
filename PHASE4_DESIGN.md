# Phase 4 Design: Routing Tracing, Consent State Machine, Eval Fixtures

Status: **design only** — no production code changes in this doc's commit. Written against
`app/utils/rag_service.py` / `app/utils/llm_gateway.py` / `app/models/database_models.py` as they
exist on this worktree's branch (`worktree-agent-af536ae0a784102d5`, based on
`fix/math-formatting-no-pdf-and-bare-brackets`). `RouterOutput` (phase1's structured-output
router) does not exist anywhere in the codebase yet — confirmed via full-history grep across all
local branches, including `phase1/llm-driven-routing`. This design is written against phase1's
stated interface (`intent`, `requested_brevity`, `meta_conversation_scope`, `meta_conversation_n`,
`reasoning`) and will need a follow-up pass once that lands for real.

Two runtime flags referenced in the phase4 brief were checked against current branch state:
- `own_answer_followup_active` **already exists** on `main` (merged via PR #135,
  `fix/follow-up-explain-own-answer-instead-of-retrieval`, not yet in this worktree's branch) —
  a hard tool-suppression flag set in `chat_node`'s prep phase when a deterministic prefetch
  finds the model's own prior answer, routing the turn through `user_llm` (tools unbound) instead
  of `user_llm_with_tools`.
- `meta_conversation_active` does **not** exist anywhere yet. It's treated here as the analogous
  flag phase1 is expected to introduce for meta-conversation turns (e.g. "what did I ask last
  question"). Naming/shape should be confirmed with phase1 (see Open Questions).

---

## 1. Routing decision tracing

### Decision: new table `RouterDecisionEvent`, not new columns on `LLMUsageEvent`

`LLMUsageEvent` (`app/models/database_models.py:546`) is **per-LLM-call** — one row per
`_generate`/`_agenerate`/`bind_tools`-runnable invocation, written by
`TelemetryChatModel`/`persist_llm_usage_event` in `app/utils/llm_gateway.py`. A single turn of
`chat_node` can produce several of these rows (main completion, retries, lecture failsafe eval,
lecture failsafe regen, and — once phase1 lands — the router's own structured-output call). A
routing decision is a **per-turn** concept, not a per-LLM-call one, so:

- There's no single "correct" `LLMUsageEvent` row to attach routing fields to — the router's own
  call is a *different* row from the main completion's row, and on a regex-fallback turn there
  may be no successful router-call row at all.
- Most of `LLMUsageEvent`'s columns (tokens, cost, provider/model) would be NULL noise on a
  routing-fields-only row, and vice versa — bolting routing fields onto the hot, heavily-indexed
  usage table sparsifies it for two unrelated concerns.
- Conflating grain ("one LLM call" vs "one turn's routing decision") makes both harder to query
  correctly (e.g. "average tokens per LLM call" vs "fallback rate per turn" need different
  denominators).

So: a new table, one row per `chat_node` turn attempt, with a nullable FK to the specific
`LLMUsageEvent` row produced by the router's own call (for cost/latency drill-down when needed),
not a required one-to-one link.

### Schema (`app/models/database_models.py`, house style matched to `LLMUsageEvent`)

```python
class RouterDecisionEvent(Base):
    """Structured trace of one routing decision (one chat_node turn)."""
    __tablename__ = 'router_decision_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True, server_default=func.now())

    # Same actor/context columns as LLMUsageEvent, for standalone queries without a join.
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    user_role = Column(String(50), nullable=True)
    traffic_source = Column(String(32), nullable=False, default='production', server_default='production', index=True)
    workflow = Column(String(64), nullable=False, default='unknown', server_default='unknown', index=True)
    conversation_id = Column(Integer, nullable=True, index=True)   # unconstrained, matches LLMUsageEvent.conversation_id
    thread_id = Column(String(255), nullable=True, index=True)

    # Link to the router's own structured-output LLM call, if one happened (nullable: regex
    # fallback turns, or a router call that errored before producing a recordable result, may
    # have no row to point to).
    router_llm_usage_event_id = Column(Integer, ForeignKey('llm_usage_events.id', ondelete='SET NULL'), nullable=True, index=True)

    # --- RouterOutput fields (mirror phase1's structured-output schema) ---
    intent = Column(String(64), nullable=True, index=True)
    requested_brevity = Column(Boolean, nullable=True)
    meta_conversation_scope = Column(String(64), nullable=True)
    meta_conversation_n = Column(Integer, nullable=True)
    reasoning = Column(Text, nullable=True)   # truncate at persist time, same pattern as error_message

    # --- Failure / fallback tracking ---
    router_used_fallback = Column(Boolean, nullable=False, default=False, server_default='0', index=True)
    fallback_reason = Column(String(255), nullable=True)   # e.g. "structured_output_error", "timeout", "validation_error"

    # --- Downstream branch/suppression flags actually taken this turn ---
    prefetch_branch = Column(String(64), nullable=True)   # e.g. 'lecture_evidence', 'own_answer_followup', 'none'
    meta_conversation_active = Column(Boolean, nullable=False, default=False, server_default='0')
    own_answer_followup_active = Column(Boolean, nullable=False, default=False, server_default='0')
    tool_rounds_used = Column(Integer, nullable=True)
    tool_round_limit_reached = Column(Boolean, nullable=False, default=False, server_default='0')

    # --- Turn outcome ---
    outcome = Column(String(16), nullable=False, default='success', server_default='success', index=True)  # success|error
    error_class = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)   # whole-turn wall time, distinct from any single LLMUsageEvent.duration_ms

    user = relationship("User")

    __table_args__ = (
        Index('idx_router_decision_created_at', 'created_at'),
        Index('idx_router_decision_intent_created', 'intent', 'created_at'),
        Index('idx_router_decision_fallback_created', 'router_used_fallback', 'created_at'),
        Index('idx_router_decision_user_created', 'user_id', 'created_at'),
    )
```

Notes on choices:
- `conversation_id` deliberately has **no** FK constraint, matching `LLMUsageEvent.conversation_id`
  exactly (checked: that column is a plain unconstrained `Integer`, presumably because RAG threads
  don't always cleanly map to `conversations.id`). Kept consistent rather than "fixed."
- `router_llm_usage_event_id` FK uses `ondelete='SET NULL'` so pruning old `LLMUsageEvent` rows
  (if that's ever done) doesn't cascade-delete routing history.
- No `back_populates` added to `LLMUsageEvent`/`User` beyond a plain `User` relationship — keeps
  the migration surface minimal; can be added later if a dashboard needs the reverse join.

### Migration mechanics

This codebase has **no Alembic** (confirmed: no `migrations/`/`alembic/` directory anywhere).
Schema changes happen in `app/utils/db.py::init_db()`:
- `Base.metadata.create_all(bind=engine)` creates any table that doesn't exist yet, for **every**
  `Base` subclass that has been imported by the time it runs. Confirmed that `LLMUsageEvent` is
  *not* in `init_db`'s explicit import tuple (`db.py:244-249`) yet it still gets created — because
  importing `app.models.database_models` for the other names in that tuple executes the whole
  module, which registers every class defined in it on the shared `Base.metadata` as a side
  effect of class definition. So: **a brand-new table needs nothing beyond adding the class to
  `database_models.py`** — no edit to `init_db`'s import list, no manual `CREATE TABLE`.
- New *columns* on an *existing* table are different: `create_all` does not alter existing
  tables, so those need the guarded pattern already used for `load_test_user_sets.set_prompt` and
  `users.subscription_tier` (`db.py:298-341`): `inspector.get_columns(table)`, check the column is
  absent, then `db.execute(text("ALTER TABLE ... ADD COLUMN ..."))`, guarded in a try/except with
  rollback on failure.

`RouterDecisionEvent` is a new table → **no `init_db` edit needed**, `create_all` covers it.
(This distinction matters for section 2, which does need a manual `ALTER TABLE` block.)

### Write path

Recommend a new module `app/utils/router_telemetry.py` (sibling to `llm_gateway.py`, not folded
into it — keeps `llm_gateway.py` focused on the existing per-call cost/latency concern) exposing:

```python
def persist_router_decision_event(*, router_output, router_used_fallback, fallback_reason,
                                    router_llm_usage_event_id, prefetch_branch,
                                    meta_conversation_active, own_answer_followup_active,
                                    tool_rounds_used, tool_round_limit_reached,
                                    outcome, error_class=None, error_message=None,
                                    duration_ms) -> None: ...
```

reading actor context from the existing `get_llm_telemetry_context()` (reused as-is — no new
ContextVar needed, since router decisions happen inside the same Flask request/Celery task as the
LLM calls already tracked). Gate behind a **separate** env flag,
`ROUTER_DECISION_TRACING_ENABLED` (default `true`), rather than overloading
`LLM_TELEMETRY_ENABLED` — decision tracing and cost telemetry are different concerns someone might
want to toggle independently (e.g. disable cost telemetry on a noisy load-test run while keeping
routing-drift visibility, or vice versa).

**Single-write-at-end-of-turn**, not create-then-update: collect all fields as `chat_node`
executes (mirroring the existing `perf_steps`/`_mark_step` accumulate-then-flush-via
`_write_speed_log` pattern already in this file), and call
`persist_router_decision_event(...)` once at the same points `_write_speed_log` is already called
(there are ~10 such call sites in `chat_node`'s error/early-return paths — the router event write
should happen alongside each, with `outcome` reflecting which path was taken). Avoids leaving
`pending`-state rows behind on a crash mid-turn, and avoids a second DB round-trip per turn.

---

## 2. Permission state machine for `general_knowledge_qa` consent

### Why per-thread sticky boolean is wrong

The existing prompt text (`DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF`,
`app/utils/rag_service.py:3288-3313`) frames consent as "the answer isn't in the document, would
you like me to answer from general knowledge?" — this is inherently tied to **the specific
question that had no grounded answer**, not the thread as a whole. A user saying "yes" to get a
general-knowledge answer for question A should not silently authorize the model to skip retrieval
and free-associate on question C three turns later, even though question C might also lack
document support. So the state machine tracks **one outstanding offer at a time**, single-use,
not a standing thread-level grant. This deliberately does not touch or resemble the RBAC system —
it is a two-state consent flag for one open question, not a permissions model.

### Schema: columns on `RAGThread`, not a new table

`RAGThread` (`app/models/database_models.py:380`) already carries exactly this shape of
single-flight per-thread state (`lesson_finalized`, `last_lesson_text`, `lesson_title` — all flat
columns representing "the current state of this thread," not an append-only history). Consent
fits the same pattern and the same access path (`_get_thread_metadata_from_db`,
`rag_service.py:1302`, already loads `RAGThread` by `thread_id` once per turn) — no new join, no
new table needed:

```python
gk_consent_state = Column(String(16), nullable=False, default='none', server_default='none')
# 'none' | 'offered' | 'granted' | 'denied'
gk_consent_question = Column(Text, nullable=True)   # the exact question the offer/grant refers to
gk_consent_updated_at = Column(DateTime, nullable=True)
```

Migration mechanics: `RAGThread` already exists, so — unlike section 1 — this **requires** the
guarded `ALTER TABLE` block in `init_db` (inspector column-check + conditional
`ALTER TABLE rag_threads ADD COLUMN ...`), following the exact `users.subscription_tier` pattern
at `db.py:314-341`. `create_all` alone will not add these columns to an existing `rag_threads`
table.

### Transitions

1. **`none` → `offered`**: after `chat_node` produces a reply matching the "not present in the
   document, want general knowledge?" / "irrelevant question" fallback pattern (this detection
   already effectively exists as the literal strings in the system prompt; phase4's job is just to
   also flip the DB flag when that branch fires, not to re-detect it from scratch). Set
   `gk_consent_question` to the user's message that triggered the offer.
2. **`offered` → `granted` | `denied`**: on the *next* user turn, if `gk_consent_state == 'offered'`.
   Detection mechanism — see Open Questions; recommended default (option b below) is a narrow,
   cheap classification scoped only to this check, not a general intent classifier.
3. **`offered` → `none` (lapsed)**: if the next user turn doesn't read as yes/no at all (topic
   changed) — the offer silently expires rather than staying "offered" indefinitely and being
   misread as consent for an unrelated later message.
4. **`granted` → `none` (consumed)**: immediately after the general-knowledge answer is produced
   for that one question — single-use, not a standing grant. Same for `denied` → `none` after the
   model acknowledges and declines.
5. A new `offered` event always overwrites whatever was previously in these columns — only one
   outstanding offer can exist per thread at a time (single-threaded conversation).

### Where yes/no detection lives (two options, recommendation below)

- **(a)** Extend `RouterOutput` with a `consent_response: Optional[Literal["yes","no"]]` field
  that phase1's router always fills in when `gk_consent_state == 'offered'` is passed as part of
  its input context. Cleanest long-term (one classifier, not two), but requires phase1 to accept a
  schema addition — flagged as an open question below rather than assumed.
- **(b)** Keep it fully outside the router: a small, narrowly-scoped yes/no classifier
  (deterministic keyword check first — "yes"/"sure"/"go ahead" vs "no"/"nvm"/"don't" — with a
  cheap LLM fallback only for ambiguous phrasing), invoked by `rag_service.py` **only** when
  `gk_consent_state == 'offered'`, so it's cheap and never runs on the common case. This is
  phase4-ownable without any dependency on phase1's schema landing first.

**Recommendation: build (b) now** (it's fully within phase4's scope and doesn't block on phase1),
and treat (a) as a future consolidation once `RouterOutput` has shipped and stabilized — note it
to phase1/main as a possible follow-up, not a blocking requirement.

The consuming check itself (replacing the current "the model just re-reads the prompt and
proceeds" behavior) is a single guard at the top of whatever code path answers
`general_knowledge_qa`-style questions once phase1's router exists: don't answer from general
knowledge unless `gk_consent_state == 'granted'` for the current question; then immediately
consume (reset to `none`) after answering.

---

## 3. Eval / regression fixture dataset

### Format

`tests/eval_fixtures/routing_regressions.jsonl` — JSONL (one case per line), not a single JSON
array: append-friendly as the set grows over time (per the brief), avoids merge conflicts when
multiple people add cases, trivially diffable in review.

```jsonl
{"id": "bug-001-discriminant-brevity", "source": "prod bug report", "setup": {"document_fixture": "quadratic_formula_notes.pdf", "prior_turns": []}, "input": "what is zero discriminat just answer main one line", "expected": {"intent": "document_qa", "requested_brevity": true}, "behavior_assertions": [{"type": "max_sentences", "value": 2}, {"type": "not_contains_any", "value": ["I'm sorry", "misunderstanding", "could you clarify"]}, {"type": "contains_any", "value": ["discriminant", "b^2", "b²"]}]}
{"id": "bug-002a-what-i-ask-last", "source": "prod bug report", "setup": {"document_fixture": null, "prior_turns": "@fixture:meta_conv_prior_turns_01"}, "input": "what i ask last question?", "expected": {"intent": "meta_conversation", "meta_conversation_scope": "last_question", "meta_conversation_n": 1}, "behavior_assertions": [{"type": "contains_exact_prior_user_text", "value": true}, {"type": "not_contains_any", "value": ["misunderstanding", "I think there might be some confusion"]}]}
{"id": "bug-002b-what-i-ask", "source": "prod bug report", "setup": {"document_fixture": null, "prior_turns": "@fixture:meta_conv_prior_turns_01"}, "input": "what i ask?", "expected": {"intent": "meta_conversation", "meta_conversation_scope": "last_question", "meta_conversation_n": 1}, "behavior_assertions": [{"type": "contains_exact_prior_user_text", "value": true}]}
{"id": "bug-002c-paste-exactly", "source": "prod bug report", "setup": {"document_fixture": null, "prior_turns": "@fixture:meta_conv_prior_turns_01"}, "input": "paste exactly to me what ia sk", "expected": {"intent": "meta_conversation", "meta_conversation_scope": "last_question", "meta_conversation_n": 1}, "behavior_assertions": [{"type": "contains_exact_prior_user_text", "value": true}]}
```

- `prior_turns` can inline a short seeded conversation or reference a shared fixture
  (`@fixture:name` → `tests/eval_fixtures/prior_turns/meta_conv_prior_turns_01.json`) so the three
  near-duplicate meta-conversation phrasings share one seed instead of triplicating it.
- `document_fixture` points at a small fixture doc under `tests/eval_fixtures/docs/` (kept tiny —
  a few paragraphs, enough to exercise real retrieval, not a full production PDF).
- `expected` fields are checked directly against `RouterOutput`'s actual fields (whatever subset
  is relevant per case — cases don't need to assert every field).
- `behavior_assertions` are pluggable, simple checks against the final reply text
  (`not_contains_any`, `contains_any`, `max_sentences`, `contains_exact_prior_user_text` — the
  last one specifically encodes bug #2's requirement of literal text retrieval, not paraphrase).

### Runner

`scripts/run_routing_eval.py` — deliberately **not** named `test_*.py` and **not** pytest-collected,
since it makes real, billed LLM calls against whatever provider `LLM_PROVIDER`/model env vars
currently point at (no separate eval-only provider config — it should evaluate the actual deployed
configuration). For each fixture case: seed the conversation state per `setup`, invoke the real
router (and/or full `chat_node`, depending on whether the assertion needs the final reply text or
just the classification), collect actual `RouterOutput` + reply, evaluate `expected` +
`behavior_assertions`, accumulate pass/fail.

### Scheduling & results surfacing

Run periodically (nightly/weekly scheduled CI job or manual invocation), explicitly **not**
per-PR/per-commit, since it's slow and costs real LLM spend. Output:
- A timestamped report file, `eval_reports/routing_eval_<UTC timestamp>.json` (or `.md` for
  human skimming) with per-case pass/fail + a rolled-up pass-rate-by-intent summary, plus a
  one-line append to `eval_reports/history.csv` (timestamp, total, passed, failed, pass_rate) for
  a trend view without building a dashboard.
- Non-zero process exit if any case not on a small `known_failing` allowlist (xfail-style, for
  cases filed but not yet fixed) regresses — so a scheduled CI job can alert on failure through
  whatever the team already uses for CI failures, rather than a bespoke notification system.
- Explicitly **not** building a dashboard/UI for this now — a flat report + CSV trend + CI exit
  code is proportionate to the current fixture-set size; revisit only if the fixture set and
  audience both grow substantially.

---

## 4. Test plan (unit-testable without live LLM calls)

Note: the brief points to `tests/test_finalize_lesson_tool.py` as the house fake-DB-session
pattern to follow — that file does not exist anywhere in this repo's git history (checked across
all local branches). The only existing test file is `tests/test_routes_helper.py`, which is a
pure-function test with no DB/mocking involved. The plan below infers the fake-session shape from
how `llm_gateway.py` itself is written (`persist_llm_usage_event` does a **local**
`from app.utils.db import get_db` import inside the function body — meaning tests should patch
`app.utils.db.get_db`, not a name imported at module load time), rather than copying a pattern
that doesn't exist yet. Flagged to main as worth confirming before implementation.

### a) `RouterDecisionEvent` write-path (`persist_router_decision_event`)

- A `FakeDbSession` double: `.add(obj)` appends to a list, `.commit()`/`.rollback()` set flags,
  no real DB. Monkeypatch `app.utils.db.get_db` to return it.
- Call `persist_router_decision_event(...)` with a known `LlmTelemetryContext` (via
  `set_llm_telemetry_context`) and a representative `RouterOutput`-shaped input; assert exactly
  one `RouterDecisionEvent` was `.add()`-ed with fields matching input (`intent`,
  `requested_brevity`, `meta_conversation_scope`, `meta_conversation_n`,
  `router_used_fallback`, `prefetch_branch`, `meta_conversation_active`,
  `own_answer_followup_active`, `tool_rounds_used`, `outcome`); assert `.commit()` called once.
- Failure-swallowing: make `get_db()` raise; assert the function does not propagate the
  exception (mirrors `persist_llm_usage_event`'s try/except-log-and-continue), and that no
  partially-built object leaks past the boundary.
- Flag gating: with `ROUTER_DECISION_TRACING_ENABLED=false`, assert `get_db` is never called at
  all (short-circuit before any DB access) — same shape as `LLM_TELEMETRY_ENABLED` gating already
  tested implicitly in `llm_gateway.py`'s own early-return.
- Truncation: `reasoning`/`error_message` longer than the column's practical limit get truncated
  the same way `_truncate_err` already handles `error_message` in `llm_gateway.py`.

### b) Consent state machine (pure logic, no DB, no LLM)

Extract transitions into a pure function independent of the ORM, e.g.:

```python
def resolve_gk_consent_transition(current_state, current_question, event, event_text) -> NewState
```

Unit test as a transition table (no mocking needed at all — plain function calls):
- `none` + "offer produced" → `offered`, question set.
- `offered` + affirmative reply → `granted`.
- `offered` + negative reply → `denied`.
- `offered` + unrelated reply (topic change) → `none` (lapsed), question cleared.
- `offered` + a brand-new offer for a different question → `offered` with the new question
  (overwrite, not stacked).
- `granted`/`denied` + "consumed" event → `none`, question cleared (single-use semantics).

For the "consume and clear" step specifically, test against a plain in-memory stand-in object
(not a real `RAGThread`/DB row) with the four relevant attributes, asserting
`consume_gk_consent(thread_like_obj)` returns whether it was granted **and** mutates the object's
state back to `none` — this only exercises attribute mutation, no session/DB needed.

Both files would be added as `tests/test_router_decision_tracing.py` and
`tests/test_gk_consent_state_machine.py` once the real modules exist — not created in this pass,
per scope (design doc only, no real test files yet).

---

## Open questions for phase1 / main

1. **Consent yes/no signal**: should `RouterOutput` eventually carry a `consent_response` field
   (option a in section 2), or should phase4 own a fully separate narrow classifier (option b,
   recommended as the immediate build since it has no dependency on phase1's schema)?
2. **Structured-output call mechanism**: does phase1's router use a `bind_tools`-based structured
   output path, or LangChain's `with_structured_output(method="json_mode")` (or similar) that
   might call the underlying provider directly rather than through `bind_tools`? This matters
   because `TelemetryChatModel` (`llm_gateway.py:405-441`) currently only overrides `bind_tools`
   (and `_generate`/`_agenerate`) — if the router's structured-output call doesn't route through
   one of those, it silently produces no `LLMUsageEvent` row, and
   `RouterDecisionEvent.router_llm_usage_event_id` would have nothing to link to. If needed,
   `TelemetryChatModel` would need a `with_structured_output` override too — that's a
   `llm_gateway.py` change outside phase4's current scope but worth flagging now.
3. **Exact `meta_conversation_scope` values**: this design uses `'last_question'` as a placeholder
   value; `RouterDecisionEvent.meta_conversation_scope` should mirror whatever enum/string values
   phase1 actually ships rather than this guess.
4. **Regex fallback**: `router_used_fallback` assumes phase1 is building a deterministic backup
   classifier for when structured-output parsing/validation fails — confirming this is in scope
   for phase1 (not something phase4 needs to build itself) would help finalize the fallback
   detection wiring.
5. **Detection of the "offer was made" event** (section 2, transition 1): currently the fallback
   text is a literal string match against the model's own reply
   (`"Would you like me to answer from my own knowledge base?"` /
   `"Irrelevant question. Do you want me to answer..."`). Once phase1's router exists, is intent
   classification itself expected to also flag "this turn IS the offer" more robustly than string
   matching, or should phase4 keep the string-match approach as the trigger for flipping
   `gk_consent_state`?

---

## Summary of concrete recommendations

| Item | Decision |
|---|---|
| Routing trace storage | New table `RouterDecisionEvent`, not columns on `LLMUsageEvent` |
| Migration for #1 | None needed beyond adding the class — `create_all` covers new tables |
| Consent state storage | 3 new columns on `RAGThread` (`gk_consent_state`, `gk_consent_question`, `gk_consent_updated_at`) |
| Migration for #2 | Requires a guarded `ALTER TABLE` block in `init_db`, following the `users.subscription_tier` pattern |
| Consent granularity | Per-question, single-use, not a sticky thread-level grant |
| Consent yes/no detection | Narrow phase4-owned classifier for now (option b); flag option (a) to phase1 as a possible future consolidation |
| Eval fixtures | `tests/eval_fixtures/routing_regressions.jsonl`, run via `scripts/run_routing_eval.py`, scheduled (not per-PR), flat JSON/CSV report — no dashboard |
| Unit tests | Fake-session DB write-path test for `RouterDecisionEvent`; pure-function transition-table tests for the consent state machine — neither needs a live LLM |
