# HANDOFF

**Task:** Domain→Subtopic learning system (subtopic mastery, Target-PDF question bank, bank-only Guided Practice, Learning Chat on live weaknesses, teacher subtopic analytics), tested end to end on real DIL PDFs. Branch `feature/subtopic-learning-system`; **nothing committed or deployed**.

**Done:**
- New services under `app/services/lms/`: `taxonomy_service.py`, `subtopic_mastery_service.py`, `target_bank_service.py`, `guided_practice_service.py`.
- New extractor `app/services/quiz/target_bank_extractor.py` and migration `app/migrations/subtopic_taxonomy.py`; new models at the end of `app/models/lms_models.py`.
- UI: admin review page `templates/admin/target_bank_review.html` (served at `/admin/target-bank`); teacher Subtopics tab `templates/teacher/analytics/subtopics.html`.
- Real-PDF fixes (14 bugs) in `deficiency_chat_service.py`, `math_text.py`, `mcq_grounding_check.py`, `hybrid_vision_pipeline.py`, `pipeline.py`, `guided_practice_service.py` and `taxonomy_service.py` (grade fallback for grade-less teacher quizzes).
- Reports: `docs/REAL_PDF_E2E_TEST_REPORT.md` (verdict NOT READY) and `docs/SUBTOPIC_LEARNING_SYSTEM.md`. Evidence and re-run scripts are in `docs/e2e_evidence/`.

**State:**
- Full suite: 1259 passed, 20 failed. The 20 failures predate this work (list in the report); 0 are new.
- Verified against the real PDFs: E2E on Postgres + Celery + Playwright, 11/11 browser checks pass.
- Not done: load test, question-bank versioning, a lock on concurrent prewarm for the same student.
- `tests/test_diagnostic_variant_select.py` fails at import time (it did before this work).

**Next:**
1. User decides on commit. `tests/*` is gitignored, so new tests need `git add -f tests/test_subtopic_*.py tests/test_target_bank_extractor.py tests/test_real_dil_answer_keys.py tests/test_learning_chat_*.py tests/test_grounding_evidence_notation.py tests/test_student_render_unicode_scripts.py tests/test_vision_provider_outage.py`.
2. Admin content work:
   - review the 78 needs_review bank items;
   - confirm aliases for the 8 assessment subtopics with no bank match (e.g. Common Logarithms vs Exponential and Logarithmic Forms).
3. Move the hard-coded admin password (`app/utils/admin_init.py:43`) to an environment variable or secret. Not changed yet.
4. Implement question-bank versioning, plus a dedupe lock in `prewarm_session` (`deficiency_chat_service.py`).
5. Load test on Linux/Gunicorn staging. Optionally add a favicon to the admin review page (404 is cosmetic).

**Key facts:**
- Run tests with `DATABASE_URL=sqlite:///<scratch>/t.db venv/Scripts/python.exe -m pytest tests -p no:cacheprovider --ignore=tests/test_diagnostic_variant_select.py`. Otherwise tests write to `instance/iqbalai_local.db`.
- The Celery worker must use the production entrypoint `-A app.celery_worker_entry.celery`. `app.celery_app` has no Flask app context. On Windows add `--pool=solo`.
- A fresh database needs LLM provider set to `GROQ` via `PUT /admin/settings/llm`; the default `openai` has no key.
- Teacher quiz upload needs `time_limit_minutes`, and teacher quizzes are created with `grade_level=None`.
- E2E containers `iqbal-e2e-pg` (127.0.0.1:55432, user/db `e2e`/`iqbal_e2e`) and `iqbal-e2e-redis` (:56379) are still running. Remove with `docker rm -f`.
- E2E scripts and server launcher (`serve.sh`, `env.sh`) live in `C:\Users\user\AppData\Local\Temp\claude\c--Users-user-Desktop-iqbalai-v1-1\9dda0d04-9d75-4356-a50a-44fef6ad6728\scratchpad\e2e\`. Redacted copies are in `docs/e2e_evidence/scripts/`.
- Playwright: the venv expects chromium-1243 but 1248 is installed. Launch with `executable_path=~\AppData\Local\ms-playwright\chromium-1248\*\chrome.exe`.
- Groq vision (`qwen/qwen3.8-27b`) frequently returns 503. Retries are 8s / 20s / 45s (`VISION_RETRY_DELAYS` in `hybrid_vision_pipeline.py`).
- The real PDFs are in the parent folder `iqbalai-v1.1\` (Baseline, `source_pdf\` Quiz 1, `quiz_format\` Quiz 2, Practice Question Bank 1). Quiz 2 Q9: its inline Topic doesn't match its own answer key.
