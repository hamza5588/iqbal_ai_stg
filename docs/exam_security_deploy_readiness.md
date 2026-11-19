# Exam security — production deploy readiness

**Status:** Ready to deploy **after explicit approval**. No production deploy was run.

## Pre-deploy

1. Confirm migration `app/migrations/exam_security.py` will run (or already applied).
2. Ship backend + `static/teacher/js/teacher-quizzes.js`, `teacher-dashboard.css`, `lms/lms-exam-security.js`, templates with `asset_v=20261120examsec2`.
3. Celery worker + **beat** for `finalize_expired_exam_attempts_task`.
4. Env: `EXAM_SECURITY_SEB_BYPASS` unset / not `1`.
5. Post-deploy smoke: teacher security panel save; optional SEB config download.

## Explicit approval gate

Do **not** run production deploy, restart, or migration-on-prod until the owner replies with clear approval (e.g. “approve deploy exam security”).

## Rollback sketch

Revert app release to pre-exam-security build. Security columns are additive — leaving columns in place is safe; set quizzes back to `security_mode=standard` / `require_seb=false` if needed.