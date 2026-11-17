# Scenario-Based Assessments

Teachers upload three separate PDF types per assessment (learning content, sample questions,
general reference answers). The AI drafts grounded scenario questions for teacher approval and
grades students' written answers conceptually against a configurable rubric. Teachers can
review, override and re-run any grade.

- Teacher UI: Teacher dashboard → **Scenario Assessments** (`#assessments`)
- Student UI: Student dashboard → **Scenario Assessments** (`#scenario`)
- API: `/api/lms/scenario/*` (`app/routes/scenario_routes.py`)
- Code: `app/services/scenario/` (documents, retrieval, generation, evaluation, service);
  prompts in `app/services/scenario/prompts/*.md` (version tag `PROMPT_VERSION` in `common.py`)
- Tables: `sba_*` in `app/models/scenario_models.py`, created by `create_all` on startup
  (additive only; no existing table is changed)

## Access rules
- Teachers manage only their own assessments (admins: all); an assessment belongs to one of
  the teacher's classes.
- Students see an assessment only when it is published **and** they are actively enrolled in
  its class, and only approved questions and their own answers.
- Reference answers, grading points, retrieved excerpts and review reasons are teacher-only.

## Grading
- The model returns a level 0-4 per rubric criterion; the score is computed server-side from
  the teacher's weights (default 40/30/20/10, editable; must total 100). Rubric and document
  versions are stored with every evaluation.
- Results go to teacher review instead of the student when: possible prompt injection, low
  model confidence, off-scenario answer scoring >50%, zero conceptual accuracy with >40%, a
  very short answer scoring ≥70%, or (with `SBA_EVAL_SAMPLES>1`) inconsistent repeated scores.
- Release modes: immediately, or after the teacher clicks *Release results*.

## Configuration (env, all optional)
| Variable | Default | Purpose |
|---|---|---|
| `SBA_MAX_PDF_MB` / `SBA_MAX_PAGES` | 50 / 400 | upload limits |
| `SBA_STORAGE_DIR` | `<instance>/scenario_docs` | original PDFs (content-addressed) |
| `SBA_EVAL_SAMPLES` | 1 | grading passes per answer (median level per criterion) |
| `SBA_CONSISTENCY_TOLERANCE` | 0.15 | flag if repeated passes differ by more (fraction of max) |
| `SBA_DUP_COSINE` / `SBA_DUP_JACCARD` | 0.90 / 0.60 | duplicate-question rejection |
| `SBA_SAMPLE_COPY_COSINE` / `SBA_SAMPLE_COPY_JACCARD` | 0.92 / 0.55 | "copied a sample" rejection |
| `SBA_DISABLE_EMBEDDINGS` | off | keyword-only retrieval |
| `SBA_SYNC_PROCESSING` | off | run jobs inline (tests) |

Uses the configured LLM provider (`get_chat_model`, Groq rate limiter) and the existing
`all-MiniLM-L6-v2` embeddings. Background work runs in daemon threads; anything interrupted
by a worker restart becomes *failed* (retryable) after 15 minutes.

## Tests
- `pytest tests/test_scenario_assessment.py` (deterministic fake LLM)
- Browser E2E with the real model (throwaway DB only):
  ```
  python tests/fixtures/scenario/make_fixtures.py
  DATABASE_URL=sqlite:///.../sba_e2e.db python tests/e2e/seed_scenario_e2e.py
  DATABASE_URL=... LLM_PROVIDER=groq python run.py 5077
  python tests/e2e/scenario_e2e.py            # Playwright
  DATABASE_URL=... PYTHONPATH=tests/e2e python tests/e2e/scenario_eval_live.py
  ```

## Known limitations
- No OCR for these documents: image-only PDFs fail with an explicit message; pages without a
  text layer are skipped with a warning.
- Fixture PDFs are synthetic. Grading bands in `scenario_eval_live.py` are developer
  judgement, **not instructor-approved scores**; build a teacher-scored dataset and measure
  agreement before relying on AI grades without review.
- Question generation sees ~16 retrieved sections per run and sample extraction reads the
  first ~24k characters; very long documents are covered by topic/objective retrieval, not
  exhaustively.
- Retrieval is a per-assessment table scan (fine for course-sized documents, not for
  thousands of pages per assessment).
- Unpublishing hides the assessment (and its results) from students until republished.
