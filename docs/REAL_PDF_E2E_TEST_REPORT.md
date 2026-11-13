# Real-PDF End-to-End Test Report: Domain/Subtopic Learning System

- **Branch:** `feature/subtopic-learning-system` (uncommitted, nothing deployed)
- **Date:** 2026-11-04
- **Environment:** Windows 11, CPU only
- **Infrastructure:** isolated Docker **PostgreSQL 17** (`127.0.0.1:55432`) and **Redis 7** (`:56379`); Flask server and Celery worker started from the production entrypoint `app.celery_worker_entry`; Chroma in a scratch directory
- **LLM:** real Groq calls (vision: `qwen/qwen3.8-27b`; text: `openai/gpt-oss-120b`), as configured for dev
- **Data isolation:** no production data, and the local dev DB was not touched (file size and timestamp verified before and after every test run)

Evidence files are in `docs/e2e_evidence/`. Driver scripts are in `docs/e2e_evidence/scripts/`, with credentials redacted.

---

## Verdict: **NOT READY FOR PRODUCTION**

The core system now works end to end on the real DIL documents:

- real PDFs → extraction → Domain/Subtopic mapping → Baseline → Learning Path → Learning Chat → Guided Practice → Monday Follow-up → refreshed path → teacher analytics
- every database value was checked against independent calculations, with zero mismatches

Getting there required fixing **14 confirmed bugs**, several of them critical (see section I). Production readiness is still blocked by:

1. **Content and admin work that must be done first.**
   - 78 Target-bank questions still need human review (stacked fractions, radical indices, letter subscripts). Students only ever see the 121 approved ones.
   - 8 of the 17 subtopics the papers test have no bank match, because the documents name them differently. An admin has to confirm aliases.
2. **No load or concurrency test on the production stack.** Gunicorn does not run on Windows, and the dev server is single-threaded. In addition, two prewarm jobs for the same student running at the same moment are not locked against each other.
3. **Question-bank versioning is not implemented.** That spec requirement is still outstanding.
4. **Security:** the admin password is hard-coded in `app/utils/admin_init.py:43`. This was found during testing and has not been changed.
5. **Learning Chat** can produce fewer questions than the allocation rule asks for when the independent grounding checker rejects them (it never serves unverified questions).

**Recommendation:** deploy to staging for user acceptance testing once items 1 and 4 are handled. Run a load test there before production.

---

## A. Real PDF inventory

All files were opened and processed (`inventory.json`, `extract_all.json`).

| Role | File (absolute path) | Grade | Pages | Questions | Answer key | Type |
|---|---|---|---|---|---|---|
| Baseline Diagnostic | `C:\Users\user\Desktop\iqbalai-v1.1\DIL_SAATHI_Baseline_Diagnostic_Mathematics IX (1).pdf` | IX | 5 | 20 | 20 rows (table) | digital |
| Monday Follow-up (Weekly Quiz 1) | `C:\Users\user\Desktop\iqbalai-v1.1\source_pdf\DIL SAATHI Weekly Practice Quiz 1 - Mathematics IX Finalized.pdf` | IX | 3 | 10 | 10 rows | digital |
| Weekly Quiz 2 (extraction only) | `C:\Users\user\Desktop\iqbalai-v1.1\quiz_format\DIL SAATHI Weekly Practice Quiz 2.pdf` | IX | 3 | 10 | 10 rows | digital |
| Target PDF question bank | `C:\Users\user\Desktop\iqbalai-v1.1\DIL SAATHI - Practice Question Bank 1 (1).pdf` (identical copy in `target_pdff\`) | IX | 34 | 200 | 200 rows | digital |
| Sindh "Question Bank Unit 1 to 4" | `C:\Users\user\Desktop\iqbalai-v1.1\DIL SAATHI _ Question Bank _ Unit 1 to 4.pdf` | IX | 22 | 100 | "Variant B" grid | digital, **unsupported layout** |

Domains and printed Topics:

- **Baseline:** Numbers (12 questions), Algebra (8), across 18 printed Topics. "Algebraic Identities" and "Algebraic identities" both appear and are canonically merged, giving 16 subtopics.
- **Quiz 1:** 10 Topics, including the new "Common Logarithms".
- **Bank:** Numbers 100 and Algebra 100, across 21 Topics.
- **Sindh bank:** prints no Domain/Topic line and numbers questions as `16.` with options as `(A)`. It is not the DIL Target format. The extractor now **warns** ("99 numbered questions are printed but 0 were extracted…") instead of returning 0 silently. Its questions cannot be published until they are converted.

## B. Extraction accuracy

### Baseline: the app's production pipeline (LlamaParse + hybrid Groq vision), stored in Postgres

Evidence: `baseline_compare.json`.

| Check | Result |
|---|---|
| Printed vs stored questions | **20 / 20**, none missing, no duplicates |
| Options | 4 for all 20, every option matches the PDF |
| Answer keys | **20 / 20 match** the printed key (C B B C A B B D B C B B C C B C B C C C) |
| Domain | 20 / 20 |
| Topic | 20 / 20 after the BUG-6 fix (before it, Q20 was stored as "Factor Theorem DIL SAATHI Program – Baseline Diagnostic Assessment") |
| Math (checked visually against page crops) | Q9 `{(−4)²}⁵`, Q11 `log_a 8 = 3/2`, Q17 `(2+3i)/(4−2i)` with fraction options, Q19 `4y/(y²−1) − (y+1)/(y−1)`: all stored exactly as printed (`crops/baseline_q*.png`) |
| Time | 214 s, and 379 s on a run where Groq returned 503 four times and the new retry logic recovered |

### Weekly Quiz 1: teacher upload, processed by the **Celery worker** (`quiz1_compare.json`)

All 10 questions, options, answer keys, Domains and Topics match. No math was lost. Time: 86 s.

### Target bank: native span extractor (CPU) and an independent second engine

Evidence: `bank_items.md`, `extract_all.json`.

| Check | Result |
|---|---|
| Questions extracted / printed | **200 / 200**; 200/200 answer-key rows joined |
| Inline Domain/Topic vs answer-key table | 200/200 agree |
| Running header/footer text inside questions | 0. Before BUG-3 it leaked into 30 questions, and the verifier had no check for it: those items were held for review only when the footer happened to trip the layout check. |
| Superscript/subscript counts, PyMuPDF spans vs pdfplumber geometry | agree on **every** question not flagged for layout |
| Status after import | 120 `auto_verified`, 80 `needs_review` |
| Status after admin review in this run | **121 approved, 78 needs_review, 1 rejected** |

Reasons items need review (an item can have several):

- 71 stacked fraction or radical index;
- 50 cases where the two engines disagree, all of them items that are already layout-flagged;
- 41 scripts with no faithful plain-text form (e.g. `log_a`);
- 1 empty option (Q68: option D's stacked fraction was not captured);
- 1 conflicting duplicate (Q155 vs Q110);
- 1 pair of identical options (Q158).

Nothing is skipped silently: every held-back item lists its reasons in the review UI and in `bank_items.md`.

### Source-document defect found

**Weekly Quiz 2, Q9:** the inline label says `Topic: Classification of Rational Numbers`, but the answer-key table says `Decimal Representation of Rational Numbers`. This is an inconsistency in the PDF itself and should be corrected at the source.

### Math rendering against the real data (`render_audit.json`)

The audit covered 1,200 stem and option strings from the four PDFs; 339 of them contain super- or subscripts.

| Renderer | Strings that lose a script |
|---|---|
| Bank renderer `bank_render` | **0** |
| Shared `student_render`, before the fix | systematic: `2⁵`→`25`, `10⁻⁵`→`10-5`, `log₂5`→`\log_{25}` |
| Shared `student_render`, after the fix | 4 |

The remaining 4 are all strings the extractor itself could not express in Unicode (`^(`/`_(`). The verifier blocks exactly those from publication.

## C. Domain/Subtopic accuracy

- **Printed vs canonical:** 30 / 30 answered questions mapped `resolved`. All 29 subtopics are scoped to grade 9 (`lms_subtopics.grade_level`). There are no grade-less duplicates, which BUG-10 would have created.
- **Merged correctly:** case and spacing variants only ("Algebraic identities" with "Algebraic Identities"; "Factorization of quadratic/Quadratic expressions").
- **Kept separate, as the spec requires:**
  - quiz "Common Logarithms" vs bank "Exponential and Logarithmic Forms";
  - "Conjugate of a Complex Number" vs "Complex Numbers";
  - "Factorization of quadratic expressions" vs "Factorization";
  - "Classification of Real Numbers" and "Decimal Representation of Rational Numbers" vs "Real and Irrational Numbers".

  Result: **8 of the 17 tested subtopics have no bank questions** (query in section G). When weak, they show up as coverage gaps for the student, in teacher analytics and in admin reporting. Nothing is substituted for them.
- **Admin alias workflow, run on real data:**
  - a student calling the merge endpoint gets 403;
  - an admin merged "Conjugate of a Complex Number" into "Complex Numbers": the source was retired and its name stored as an alias;
  - 2 assessment questions and 2 evidence rows were re-pointed, with 0 left behind;
  - the student's score was rebuilt with history intact (baseline 0 → latest 100).

## D. Complete student journey (isolated student `e2e_student`, real API, Postgres)

### Baseline

Q5, Q9, Q10 and Q14 were answered wrong on purpose. The expected values were calculated by hand before the run (`db_after_baseline.json`).

| Item | Expected | Database |
|---|---|---|
| Score | 16/20 | **16/20** |
| Domain: Numbers | 9/12 = 75% | **75.00** |
| Domain: Algebra | 7/8 = 87.5% | **87.50** |
| Weak subtopics | Scientific Notation 0, Laws of Exponents 0, Conjugate 0, Algebraic Identities 50 | **identical**; the other 12 at 100 |
| Evidence rows | 20 (16 correct) | **20 (16 correct)** |
| Learning Path | created with a weakness signature | path 1, `{"d":[1,2],"s":[7,12,15,26]}` |

### Learning Chat

Real Groq generation, grounded on approved bank questions.

- **Scope:** every generated question was on a current weak subtopic, and no answer-key fields reached the client.
- **Allocation** (existing rule): Numbers at 83.3% gets 2 questions and Algebra at 77.8% gets 3. Expected 4, got 4: Scientific Notation 1, Algebraic Identities 3, Conjugate 0 (no verified source).
- **Every generated question was verified by hand**, for example:
  - (4³·2⁵)/8² = 32
  - (a+b)² − (a−b)² with a=4, b=3 = 48
  - a³+b³ with a+b=9, ab=14 = 351
  - 0.00032 = 3.2×10⁻⁴
  - (4²·5³)/20² = 5
  - (3³·9²)/27² = 3

  All keys were correct.
- **Interaction:**
  - a correct answer advanced the session;
  - a wrong answer kept the student on the question;
  - the tutor, asked "just tell me the option", guided without revealing it;
  - skip and pause both worked;
  - progress was saved.
- **Mastery on completion** (outstanding-miss policy):
  - Laws of Exponents: 0% → 100% (1 correct cancels 1 miss);
  - Algebraic Identities: 50% → 33.33% (1 wrong adds a miss).
- **Difficulty plan:** starts at "medium" for a 77.8% Domain, as the existing rule says.

### Guided Practice (`step_f_guided.py`)

40 questions were served and each was checked against Postgres:

- every one was `approved` and active, and in a currently weak subtopic;
- text and options were identical to the approved bank row;
- each carried a source reference (question number, page, file);
- no answer key was exposed, and every answer was graded correctly.

Other results:

| Check | Result |
|---|---|
| Rows in `questions` before and after | **240 / 240**: no generation |
| Repeats | started only after all 8 unique questions, flagged `bank_exhausted` |
| Weak subtopic with no coverage (Conjugate) | "There are no verified practice questions for this subtopic yet. Your teacher has been notified." |
| Mastered subtopic requested | refused (`not_weak`) |
| History | 40 entries saved |
| Evidence | recorded with `counts_toward_mastery = false`, so mastery is unchanged |
| BUG-9 (found here) | all 40 questions came from the weakest subtopic; fixed, after which unseen Algebraic Identities questions were served |

### Monday Follow-up (Weekly Quiz 1)

Assigned by the teacher. Q3, Q6 and Q10 answered wrong (`db_after_followup.json`).

| Item | Expected | Database |
|---|---|---|
| Score | 7/10 | **7/10** |
| Domains (latest assessment replaces) | Numbers 6/8, Algebra 1/2 | **75.00, 50.00** |
| Weak subtopic improves | Scientific Notation 0 → 100 | 100 (baseline 0 kept) |
| Strong subtopic becomes weak | Properties of Real Numbers 100 → 0 | 0 (baseline 100 kept) |
| Weak subtopic stays weak | Algebraic Identities → 0 | 0 (baseline 50 kept) |
| Cleared, then weak again | Laws of Exponents → 0 | 0 |
| New subtopic appears | Common Logarithms | 100 (no baseline) |
| Baseline history preserved | 20 diagnostic evidence rows | 20 still present |

### Learning Path and session refresh (Phase 8)

| Scenario | Expected | Result |
|---|---|---|
| **A**: untouched Numbers session before the Follow-up | superseded | session 5 `completed / superseded` |
| **B**: partly answered session (1 of 4) | answered work kept, cleared pending removed | session 4: 4 → 3 items (Scientific Notation dropped), `refreshed_after_assessment`, notice shown in the UI |
| **C**: cleared subtopic leaves weak practice | not offered | Scientific Notation and Conjugate absent from the Learning Path, Learning Chat and Guided Practice |
| **D**: new weakness enters practice | Learning Path, Learning Chat and Guided Practice | Learning Path breakdown and Guided Practice include Properties and Laws of Exponents; the Numbers Learning Chat targets exactly those two. The post-quiz prewarm was fixed to warm these per-Domain sessions. |
| **E**: refresh run repeatedly | no duplicate active paths or sessions | 3 dashboard loads + 3 prewarms: **1 active path**, 1 open session per scope |
| Path versioning | old path archived, new path active | path 1 archived, path 2 active with signature `{"s":[1,7,12]}` |

## E. Teacher analytics (`step_j_analytics.py`)

Every metric from `/analytics/topics` and `/analytics/subtopics` was compared with values computed independently from the raw tables (scores and evidence). Result: **0 mismatches**.

- All 17 subtopics appear under the correct Domain.
- Weak-student counts, class averages, Baseline vs latest and the changes are all correct (e.g. Properties of Real Numbers 100 → 0, −100; Scientific Notation 0 → 100, +100; Common Logarithms "— → 100").
- Existing Domain analytics are unchanged (Algebra 50, Numbers 75).
- A student calling the endpoint gets 403.

**Observation:** the Domain row shows the existing Domain mastery (latest assessment replaces, so Algebra 50%) next to the question-weighted roll-up of current subtopic scores (85.7%). Both are correct by definition, but the gap may confuse teachers. Labels or tooltips should be agreed with the product owner.

## F. Browser evidence: Playwright with Chromium 1248 (`browser_report.json`, `screenshots/`)

**11 / 11 checks passed.** 0 page errors and 0 failed or 5xx requests. The only console error is a 404 for `/favicon.ico` on the standalone admin review page, which is cosmetic.

| Screenshot | What it shows |
|---|---|
| `01_admin_bank_review_approved.png` | original PDF crop next to the rendered MCQ, answer key and Domain/Subtopic |
| `02_admin_bank_item_math.png` | MathJax rendering (11 `mjx-container` elements, no raw TeX left) |
| `03_admin_bank_needs_review.png` | blocking reasons listed for items under review |
| `04_student_learning_path.png` | weak Domains with weak subtopics, scores and Practice (n) / Learning Chat buttons; cleared subtopics absent |
| `05` / `06_student_guided_practice*.png` | verified bank question ("Question 172 from your teacher's practice question bank · 6 of 9 practised"), then feedback |
| `07_student_learning_chat.png` | "Numbers › Laws of Exponents", `Evaluate (4² × 5³) ÷ 20²` typeset by MathJax |
| `08_teacher_subtopic_analytics.png` | Subtopics tab: Domain drill-down, weak students, Baseline → latest with changes |

`02` was captured before the lazily loaded crops below the fold arrived (a script artifact); `01` shows the crops render.

## G. Database and background jobs

| Test | Result |
|---|---|
| Migration on fresh Postgres 17 | 74 tables; every `lms_*` table created with FKs, unique and check constraints; `close_reason` and `weakness_signature` columns added |
| Migration on an existing schema (copy of the dev SQLite) | app booted, columns added, no errors |
| Background PDF extraction (Celery) | Weekly Quiz 1 processed by the worker in 86 s |
| Learning Chat prewarm (Celery) | `warmed: True`, ~16–35 s per run (real Groq) |
| Restart recovery | task enqueued while the worker was stopped (Redis `default` length 1); a new worker received it and it succeeded; `task_acks_late=True`, `prefetch=1` |
| Idempotency | re-importing the bank is a 3 ms no-op; repeated prewarm or refresh creates no duplicates; the backfill script is idempotent |
| Transaction safety | post-submit steps are best-effort with rollback; a bank import that crashed midway is cleaned up on retry |
| Duplicate-job protection | **not implemented** for two prewarm jobs for the same student running concurrently (sequential repeats are safe) |
| Celery retries | LMS tasks use `max_retries=0` by design (best-effort); vision-provider calls now retry inside the task (8 s / 20 s / 45 s). Not tested further. |

Note: the first worker started from `app.celery_app` failed with "outside of application context". That was **my setup error**: production starts the worker from `app.celery_worker_entry`, and with that entrypoint the tasks ran correctly. It is not counted as an app bug.

## H. Performance (measured, CPU only)

| Operation | Measured |
|---|---|
| Bank import, 200 MCQs: extraction + second engine + verification + taxonomy + DB | **9.8 s wall, 9.6 s CPU, peak RSS +148 MB** |
| Bank re-import (idempotent) | 3 ms |
| Page-crop preview | 0.06–0.32 s |
| Baseline upload to draft diagnostic (vision + RAG + bank) | 214 s; 379 s with Groq 503 retries |
| Weekly quiz upload via Celery | 86 s |
| Learning Chat session generation | 18–55 s (live); prewarmed open is 0.2 s |
| Guided Practice next + answer | median 89 ms, max 138 ms |
| Student dashboard / subtopic mastery | 117 ms / 39 ms median |
| Teacher subtopic analytics | 36 ms median |
| Docling for comparison (not used) | 121 s and +760 MB for 2 pages |
| Load / concurrency | **NOT TESTED**: the Windows dev server is single-threaded and Gunicorn needs Linux |

Regression suite: **1259 passed, 20 failed, 3 skipped**. The 20 failures are exactly the ones that failed before any of this work, so there are **no new failures**. The earlier report of "1225 passed" predates the tests added in this round. `tests/test_diagnostic_variant_select.py` still fails at import time, as before.

## I. Bugs

| # | Severity | Bug (found on real data) | Fix | Regression test | Status |
|---|---|---|---|---|---|
| 1 | Critical | Baseline Q20 prints "Intended difficulty:"; the metadata regex missed it and swallowed all 4 options | regex accepts it; metadata never runs into options | `test_real_dil_answer_keys.py` | Fixed |
| 2 | High | Unsupported layout (Sindh bank) returned 0 questions with no warning | coverage warnings list missing Qn and unsupported layouts | same | Fixed |
| 3 | Critical | Page footer ("…Bank \| Page N") appended to the last option of 30 bank questions; the verifier had no check for it | margin filter by page position; verifier blocks margin text | `test_real_dil_answer_keys.py` | Fixed |
| 4 | High | Regression introduced by fix 3: Domain/Topic lines at the top of a page dropped (12 questions) | content lines are never treated as furniture | real-bank tests | Fixed |
| 5 | High | Groq 503 "over capacity" reported to the admin as "document does not match the required format"; no backoff | retry 8/20/45 s; outage message | `test_vision_provider_outage.py` | Fixed (seen working: 4 retries in the real run) |
| 6 | High | Production key parser stored Baseline Q20 Topic with the page header appended (bogus subtopic) | running header/footer lines removed before parsing | `test_real_dil_answer_keys.py` | Fixed, re-uploaded and verified |
| 7 | **Critical** | Learning Chat produced **0 questions** with the real DIL Target PDF: RAG returned the answer-key table, so grounding rejected everything | grounding on approved bank MCQs per subtopic; answer-key text refused as source and as tutor context | `test_learning_chat_bank_grounding.py` | Fixed |
| 8 | High | Grounding evidence failed on notation (`10⁻⁵` vs `10^{-5}`) and on spliced quotes | notation-only canonicalisation; every fragment must be verbatim | `test_grounding_evidence_notation.py` | Fixed |
| 9 | Medium | Pausing a completed Learning Chat session reopened it | pause only affects active sessions | `test_learning_chat_item_quality.py` | Fixed |
| 10 | Medium | Generated stems with no question wording; solving formula appended to complete stems | filter + no LaTeX append; an over-correction (dropped "Evaluate …") was caught and fixed | same | Fixed |
| 11 | High | Guided Practice stuck on the weakest subtopic; another weak subtopic never served | unseen questions across all weak subtopics before any repeat | `test_subtopic_learning_system.py`, `scripts/test_guided_practice_fresh_questions.py` | Fixed, verified on real data |
| 12 | **Critical** | Teacher quizzes have no grade, so Follow-up questions mapped to grade-less subtopics and **never updated the Baseline weaknesses** | the student's grade is used when the paper has none | `test_gradeless_teacher_quiz_updates_the_baseline_subtopics` | Fixed, verified on Postgres |
| 13 | High | Shared `student_render` flattened Unicode scripts (`2⁵`→`25`, `10⁻⁵`→`10-5`, `log₂5`→`log25`); first fix exposed `\log_{2}^{5}` | scripts protected before NFKC; subscript operands kept | `test_student_render_unicode_scripts.py`, full suite | Fixed |
| 14 | Medium | Prewarm after a Follow-up returned a stale combined session, so new weaknesses were not prepared | also warms each weak Domain's session | Celery run in the real E2E | Fixed |
| – | Low | Re-imported bank kept a link to the archived diagnostic | follows the newest diagnostic | – | Fixed |
| – | Low | `/favicon.ico` 404 on `/admin/target-bank` | – | – | Open (cosmetic) |
| – | Medium | Learning Chat can return fewer questions than allocated when the checker rejects them (Properties: 0 of 1) | by design nothing unverified is served; Guided Practice still covers it | – | Open |
| – | High | Hard-coded admin password `app/utils/admin_init.py:43` | needs an environment variable or secret | – | Open (not changed) |
| – | Medium | No lock against concurrent prewarm for the same student | – | – | Open |
| – | – | Question-bank versioning | not implemented | – | Outstanding requirement |

## J. Final acceptance table

| Feature | Status | Evidence |
|---|---|---|
| Real Baseline, Follow-up and Target PDFs processed | **PASS** | section A; `inventory.json`, `extract_all.json` |
| Original MCQs and answer keys verified | **PASS** | `baseline_compare.json`, `quiz1_compare.json` (all keys match); bank 200/200 keys |
| Math formatting validated against source pages | **PASS** for published items; 78 bank items held for human review | `crops/`, `render_audit.json`, `bank_items.md` |
| Unverified questions blocked from students | **PASS** | Guided Practice 40/40 approved; DB: needs_review/rejected `is_active=false` |
| Domain/Subtopic mapping consistent | **PASS** (canonical IDs, aliases, no silent merges) | section C; 8 unmapped names need admin aliases |
| Baseline mastery updates | **PASS** | section D, `db_after_baseline.json` |
| Follow-up mastery updates (improve / new weak / remain weak) | **PASS** | section D, `db_after_followup.json` |
| Learning Path uses latest weaknesses; no duplicates | **PASS** | scenarios A–E; screenshot `04` |
| Learning Chat targets correct weak subtopics, valid math, no leaks | **PASS** (with the open allocation-shortfall note) | section D; screenshot `07` |
| Guided Practice uses only approved original Target-PDF questions | **PASS** | `step_f_guided.py` output; screenshots `05`/`06` |
| Teacher analytics match DB values | **PASS** | section E, 0 mismatches; screenshot `08` |
| Admin bank workflow (import, review, correct, approve, reject, revalidate) | **PASS** | `step_c_admin.py`: bulk approval refused needs_review; Q16 corrected and approved; Q155 rejected; correcting approved Q6 unpublished it |
| Question-bank versioning | **FAIL** (not implemented) | – |
| PostgreSQL migrations, FKs, persistence | **PASS** | section G |
| Redis/Celery: background extraction, prewarm, restart recovery | **PASS** | section G |
| Celery retry behaviour / duplicate-job protection | **NOT TESTED** / **FAIL** (no lock) | section G |
| Browser E2E | **PASS** (11/11) | `browser_report.json`, `screenshots/` |
| Regression suite | **PASS** (no new failures; 20 failures predate this work) | section H |
| CPU performance measured | **PASS** | section H |
| Load / concurrency on the production stack | **BLOCKED** (Windows dev server single-threaded; Gunicorn needs Linux) | – |

### How to re-run

1. Start the isolated containers:
   - `docker run -d --name iqbal-e2e-pg -e POSTGRES_USER=e2e -e POSTGRES_PASSWORD=<pw> -e POSTGRES_DB=iqbal_e2e -p 127.0.0.1:55432:5432 postgres:17-alpine`
   - `docker run -d --name iqbal-e2e-redis -p 127.0.0.1:56379:6379 redis:7-alpine`
2. Start the server and worker with `scripts/serve.sh` (fill in `env.sh` first).
3. Run the steps in order:
   - `step_a_users.py`
   - `step_b_upload.py`
   - `step_c_admin.py`
   - `step_d_baseline.py`
   - `step_e2_sessions.py`
   - `step_f_guided.py`
   - `step_g_quiz_upload.py`
   - `step_h_followup.py`
   - `step_i_refresh.py`
   - `step_j_analytics.py`
   - `browser_e2e.py`
4. Remove the containers afterwards with `docker rm -f iqbal-e2e-pg iqbal-e2e-redis`.
