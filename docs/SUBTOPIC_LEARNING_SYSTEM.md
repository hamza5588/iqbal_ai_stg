# Domain → Subtopic learning system

Baseline, Follow-up, Learning Path, Learning Chat, Guided Practice, Target PDFs and
teacher analytics all read the same canonical Domain/Subtopic ids and the
student's **latest** mastery.

## Data model (all additive)

| Table | Purpose |
|---|---|
| `topics` (existing) | **Domains**. Unchanged ids, so every existing Domain score and analytic keeps working. |
| `lms_subtopics` | **Subtopics**: `(domain_topic_id, grade_level, name_key)` is unique. `aliases_json` holds admin-confirmed alternative names. |
| `lms_question_taxonomy` | Per question: Domain id, Subtopic id, printed Domain/Topic, `mapping_status` (`resolved` / `domain_only` / `unresolved`). |
| `lms_subtopic_evidence` | One row per graded answer (diagnostic / quiz / learning_chat / guided_practice). Unique per student+source+question, so it is idempotent. This is the audit trail. |
| `lms_student_subtopic_scores` | Current subtopic mastery. It is derived from evidence and can always be rebuilt. |
| `lms_target_bank_documents` / `lms_target_bank_items` | Target PDF question bank with verification status and report. |
| `lms_guided_practice_attempts` | Guided Practice history (option order shown, answer). |
| `deficiency_chat_sessions.close_reason` | New column: `superseded`, `refreshed_after_assessment`, … |
| `learning_paths.weakness_signature` | New column: the weak set the path was built for, so a refresh does not create duplicate paths. |

**Subtopic resolution** uses only names an admin or the paper confirmed. A match is an
exact normalised name (case, spacing, punctuation, `&`/`and`, simple plurals) or a
confirmed alias. Nothing is fuzzy-merged automatically. "Common Logarithms" and
"Exponential and Logarithmic Forms" stay separate until an admin merges them with
`POST /api/lms/taxonomy/subtopics/<id>/merge {"into": <id>}`.

The printed Topic comes from one of:

- the hybrid extractor's `Topic:` field in the question explanation, or
- legacy `question_concepts` values in the form `"Topic / Domain"`.

Questions without a printed Topic are `domain_only`. They count for Domain mastery only,
and no Subtopic score is ever fabricated for them.

## Mastery policy `subtopic_v1`

This is the same rule set Domain mastery already uses.

- **Baseline or Follow-up:** the subtopic score is replaced by that attempt's correct/total. The newest assessment is the source of truth.
- **Learning Chat:** each correct answer cancels one outstanding miss and each wrong answer adds one. Sample size is capped at 20.
- **Guided Practice:** recorded as evidence only. It does not move mastery, and it never moved Domain mastery either.
- **Weak:** a score below `PRACTICE_TARGET_THRESHOLD` (100%). This threshold is unchanged.
- **Domain rows** keep their existing calculation. Analytics add a question-weighted roll-up of subtopics next to it (total correct / total answered).

## Assessment flow (`attempt_service._run_post_submit_steps`)

On every Baseline and Follow-up, these steps run in order:

1. Domain scores
2. Subtopic evidence and scores
3. Learning Path refresh. A new path is created only when the weak signature changed; the old path is archived, not deleted.
4. Snapshot
5. Learning Chat handling:
   - **Diagnostic:** close old sessions.
   - **Follow-up:** run `reconcile_sessions_after_assessment`. Untouched stale sessions are closed as `superseded` with no mastery change. Sessions with progress keep their answered questions and drop pending questions on subtopics that are no longer weak. The UI then shows "updated after your latest quiz".
6. Prewarm. On a Follow-up this runs on Celery only, never inline in the submit request.

Learning Chat weak areas now come from live mastery. That includes quiz-only weaknesses, and Domains whose score is fine but which have a weak Subtopic. Each queue item carries `subtopic_id`.

## Target PDF question bank

Pipeline (`app/services/quiz/target_bank_extractor.py` + `app/services/lms/target_bank_service.py`):

1. Check the PDF signature, then decide whether the PDF is digital, mixed or scanned.
2. Run the **primary extraction** from PyMuPDF character spans. Superscripts and subscripts are classified per span from size and baseline offset, not from the font flag, and are emitted as Unicode (`x²`, `log₂`) or `^( )` / `_( )`.
3. Detect stacked fractions, radical indices, drawn bars and nested scripts, and flag them for review. They are never guessed.
4. Read the answer-key table with **pdfplumber**, a second engine. Its Domain/Topic columns must equal the inline labels.
5. **Independent verification**, which is deterministic. It checks:
   - option count and distinct options
   - answer key present
   - inline/key mapping agreement
   - layout flags
   - unmapped glyphs
   - superscript/subscript counts from pdfplumber character geometry matching PyMuPDF
   - **deterministic rendering** keeping every script (`bank_render`)
   - duplicates and conflicting duplicates
   - provenance
   - taxonomy resolved
6. Set the status:
   - `auto_verified` means every check passed. These items may be bulk-approved.
   - `needs_review` means an admin must approve the item individually after viewing the page crop, or correct it first. A correction keeps the original in history and returns the item to review.
   - Only `approved` items are `questions.is_active = TRUE` and visible to students.

Target PDFs uploaded through the diagnostic flow are imported automatically. This is best effort and never blocks the upload. Admins review at **`/admin/target-bank`**, or import there directly.

Why not the shared MathJax helper: `display_format_qa.student_render` re-guesses maths. It turns `log₂5` into `\log_{25}`, `10⁻⁵` into `10-5`, and `2⁵` into `25`. Bank questions use `bank_render`, which wraps only explicit scripts. The shared helper is unchanged for other content (see Known limitations).

### Extraction tools evaluated (CPU only, real DIL bank, 34 pages / 200 MCQs)

| Tool | Result |
|---|---|
| PyMuPDF plain text | Fast, but flattens `(a+b)²` to `(a+b)2`. Unusable on its own. |
| **PyMuPDF spans (chosen)** | 200/200 parsed. Superscripts/subscripts preserved. Full import 9.9 s, +152 MB. |
| pdfplumber | Same flattening in text. Its character geometry and table extraction are used as the **independent check** and for the answer key. |
| Docling 2.131 (no OCR, no formula model) | 121 s for 2 pages, +760 MB. Still flattened `(a+b)²`. Not used. The formula model is impractical on CPU. |
| ContextGem | An LLM-driven extraction framework, not math OCR, so it adds nothing over deterministic parsing here. Not used. |

Real-bank result:

- 102 items `auto_verified` and 98 `needs_review`.
- Review reasons: stacked fractions and radicals, letter subscripts such as `log_a`, nested scripts present in the source (e.g. Q41 `5x^(3y²)`), and 2 conflicting duplicates.
- On every non-flagged question, the two engines agree on all script characters.

Scanned PDFs are reported as such and are not auto-extracted.

## Guided Practice

`guided_practice_service` serves only questions that are:

- approved, and
- for the student's grade, and
- in a Subtopic that is weak **now**.

Unseen questions are served first, then the least recently seen, with `bank_exhausted` set. Options are shuffled unless an option refers to a position ("both A and B"). The answer key never leaves the server.

If a weak Subtopic has no approved questions, it is reported as a **coverage gap**: shown to the student, in teacher analytics, and at `GET /api/lms/target-bank/coverage-gaps`. Nothing is substituted for it. The old AI generation path in `practice_service` was removed.

## Teacher analytics

`GET /api/lms/classes/<id>/analytics/subtopics`, shown in the new **Subtopics** tab. It returns:

- Domain → Subtopic class average
- question-weighted %
- weak students
- baseline vs latest assessment, with the change
- low-evidence flag (fewer than 3 answers)
- mapping coverage
- coverage gaps

Teacher class ownership is enforced, and the existing Domain endpoints are unchanged.

## Deploy

1. **Back up the database**, e.g. `pg_dump -Fc iqbalai > pre_subtopic_$(date +%F).dump`.
2. Deploy the code. On startup, `init_db` runs `app/migrations/subtopic_taxonomy.py`, which creates the tables and adds two nullable columns. It is idempotent.
3. Set `TARGET_BANK_STORAGE_DIR` to a **persistent volume**. Source PDFs are kept there for page previews; the default is `<instance>/target_bank`.
4. Backfill history:
   1. Run `python scripts/backfill_subtopic_taxonomy.py --dry-run`.
   2. Then run it without `--dry-run`. It is idempotent and calls no LLM.
5. Re-upload or attach the Target PDF so the bank is extracted. Then review and approve it at `/admin/target-bank`.

## Rollback

- **Code:** redeploy the previous image. The new tables and columns are ignored by the old code, and nothing existing was rewritten.
- **Data (optional):** drop these tables: `lms_guided_practice_attempts`, `lms_target_bank_items`, `lms_target_bank_documents`, `lms_student_subtopic_scores`, `lms_subtopic_evidence`, `lms_question_taxonomy`, `lms_subtopics`. You may also drop the columns `deficiency_chat_sessions.close_reason` and `learning_paths.weakness_signature`.
- **Note:** bank questions are rows in `questions` (`source_type='pdf_qa_converted'`, referenced by `lms_target_bank_items`). Delete them before dropping the bank tables if a full cleanup is wanted.

## Changes from the real-PDF E2E run (2026-11-04)

See `docs/REAL_PDF_E2E_TEST_REPORT.md` for the evidence behind each change.

- **Learning Chat grounding on question-bank Target PDFs.** The DIL Target PDF is a bank of MCQs with no teaching text per topic. Its RAG section lookup returned the answer-key table, so every generated question failed grounding. Learning Chat now grounds each weak subtopic on that subtopic's **approved** bank MCQs. Generated questions still pass the grounding and answer-key checks. Answer-key tables are never used as source text or as tutor context.
- **Grounding evidence matching** ignores notation-only differences (`7.5 × 10⁻⁵` vs `7.5 \times 10^{-5}`). Spliced quotes ("… is equal to: ... B. 4ab") pass only when every fragment is verbatim in the source.
- **Grade for teacher quizzes.** `/api/lms/quizzes/from-pdf` creates quizzes without `grade_level`. Their questions now use the student's grade, so a Follow-up updates the same grade-scoped Subtopics as the Baseline and matches the bank.
- **Prewarm** also warms each weak Domain's own session (what the Learning Path opens), so newly weak subtopics after a Follow-up get prepared questions.
- **Guided Practice** serves every weak subtopic's unseen questions (weakest first) before repeating any question.
- **Shared renderer** (`math_text`): Unicode super/subscripts are converted to `^{}`/`_{}` *before* NFKC, so `2⁵`, `10⁻⁵`, `log₂5` survive; digits after a subscript group stay its operand (`\log_{2}5`, never `\log_{2}^{5}`).
- **Vision provider outages** (Groq 503 "over capacity", 429, timeouts) are retried with backoff (8 s, 20 s, 45 s). If the provider is still down, the admin is told the service is busy. It is no longer reported as "document does not match the required format".
- **Extractor:** reads "Intended difficulty:" metadata; filters running headers/footers by page position; warns (never silently skips) when printed questions are not extracted or the layout is unsupported; the verifier blocks page-margin text in a question.

## Known limitations

- About half of a math-heavy bank needs human review: stacked fractions, radical indices, letter subscripts. This is by design; nothing unverified is published.
- `student_render` (the shared helper) no longer flattens Unicode scripts (fixed 2026-11-04). It still re-guesses exponents in text that was *already* flattened (e.g. legacy `x2` → `x^{2}`); that legacy heuristic is unchanged.
- Subtopic names differ between documents, e.g. a quiz's "Common Logarithms" vs the bank's "Exponential and Logarithmic Forms". Until an admin merges them, such weak subtopics show as coverage gaps in Guided Practice.
- Data isolation is by grade and teacher-class ownership. The platform has no tenant column.
