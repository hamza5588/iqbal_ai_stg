# Fixes for the LMS visual + backend QA findings (2026-09-29)

Original report: `20260929_075116/REPORT.md` (31 PASS / 4 FAIL / 2 BLOCKED).
Verification after fixes: `20260929_130156/REPORT.md`, **37 PASS / 0 FAIL / 0 BLOCKED**, run against the fixed code on port 5057 with the default `.env` (`SKIP_EXTRA_STARTUP=true`). Student UI E2E 25/25. pytest 591 passed; the 9 failures + 1 collection error are the same pre-existing ones.

| # | Finding | Root cause | Fix | Proof |
|---|---|---|---|---|
| F1 | Diagnostic topic breakdown differed per view; table vs tiles contradicted | `_recompute_area_scores` keeps only answered questions, but the cache check required every attempt question → cache rejected for any attempt with a skipped question → AI regrouped on every read | `weakness_analyzer`: cache must cover only *answered* questions (`_gradable_question_ids`); `_set_cache` re-reads the shared assessment row (row lock where supported) before writing so concurrent students' entries aren't lost | Probe: 3 result reads → 1 grouping, `topic_breakdown == all_topics`, 0.1 s each (was 16–22 s) |
| F2 | Viewing results rewrote StudentTopicScore (progress / weak topics drifted, Learning Chat gains lost) | `get_attempt_results()` called `update_topic_scores_from_attempt()` on every diagnostic read | Results are read-only. Mastery is written at submit; a one-time backfill runs only if the student has no topic scores at all | S3b PASS: no DB change after "View Results"; probe shows 0 changed rows |
| F3 | Teacher Topic Progress: UI ≠ by-topic API ≠ DB | Consequence of F1/F2 | (F1/F2) | Topic Progress PASS |
| F4 | Learning Chat created new weak topics | Session start re-read the uncached AI grouping → new topic ids | (F1) | Learning Chat PASS, no new topic ids |
| F4b | AI Learning Chat question with a wrong answer key | No key verification | New `app/services/quiz/mcq_answer_check.py`: one batched independent solve per generated queue; disputed questions are dropped. Fail-safe (errors / all-disputed → queue unchanged) | Unit tests |
| F5 | Lesson chat 500 on servers started with `SKIP_EXTRA_STARTUP=true` | Graph only built at startup | `lesson_qa_graph._ensure_lesson_qa_graph()` builds lazily (thread-safe) on first use | Lesson chat PASS on the default `.env` |
| F6 | Student with no data listed as "struggling" | `progress < 60` with 0.0 meaning "no data" | `is_struggling` requires topic-mastery data (`has_mastery_data`) in `class_service` + `analytics_service` | Struggling PASS |
| Blocked | Guided practice: "No practice question available" | AI diagnostic topics have no bank questions | `practice_service._diagnostic_question_for_topic()`: falls back to the student's own diagnostic questions for that topic (wrong ones first) | Guided practice PASS (question + hint) |
| UI | Teacher success toasts shown twice | shared `lmsShowToast` + page toast | `.td-body .lms-toast { display:none }` | visual |
| UI (found by the re-runs) | Reopening the diagnostic right after "Continue" showed the hub with the quiz hidden and its state wiped | async `closeLmsDiagnostic()` finished its answer re-save *after* the reopen | `lms-student.js`: open-generation guard; `student-shell.js`: reset the pending-close flag on open | Retake/reopen PASS |

## Data repair (existing students) — NOT applied globally
Stored rows damaged by F2 stay wrong until rebuilt. `scripts/rebuild_drifted_mastery.py` (dry run by default) replays each student's attempts and Learning Chat sessions:

```bash
python scripts/rebuild_drifted_mastery.py                  # dry run: lists students + current rows/samples/overall
python scripts/rebuild_drifted_mastery.py --student-id 39 --apply
python scripts/rebuild_drifted_mastery.py --apply          # everyone with a submitted diagnostic
```
Applied only to QA student 39: 14 rows / 61 samples → 8 rows / 36 samples (= 31 answered diagnostic questions + 5 Learning Chat answers). The local dry run lists 21 students, several clearly drifted (e.g. student 35: 66 samples from 31 answers). Run on staging/production only after review; rebuilding deletes and replays rows.

## Files changed
`app/services/lms/{weakness_analyzer,attempt_service,analytics_service,class_service,practice_service,deficiency_chat_service}.py`, `app/services/lesson/lesson_qa_graph.py`, new `app/services/quiz/mcq_answer_check.py`, `static/lms/lms-student.js`, `static/student/js/student-shell.js`, `static/teacher/css/teacher-dashboard.css`, new `scripts/rebuild_drifted_mastery.py`, new `tests/test_mastery_stability_fixes.py`.
