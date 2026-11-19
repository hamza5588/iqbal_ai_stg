# IqbalAI Exam Lockdown — QA Report

**Date:** 2026-11-20 (UTC+5)  
**Baseline commit:** `83e1e60` (+ local teacher UI / test follow-ups)  
**Deployment:** **Not performed** — awaiting explicit approval.

---

## Verdict

Exam security Modes A/B/C are implemented end-to-end in backend + teacher UI. Automated unit, regression, and isolated Playwright E2E (with demo video) **pass**. **Real Safe Exam Browser OS lockdown on Windows is UNVERIFIED** — SEB is not installed on the test host.

---

## What was delivered

| Area | Status |
|------|--------|
| Teacher security panel (Mode A Soft lock / B SEB / C Supervised) | Done — Quizzes expand → Exam security |
| API: list/detail/preview/PATCH security + SEB config download + rotate key | Done |
| Soft lock client (`lms-exam-security.js`) | Done — events, heartbeat, offline badge |
| Server SEB header gate + expiry Celery finalization | Done (from `83e1e60`) |
| Playwright E2E + video | Done — see Evidence |
| Real Windows SEB OS tests | **Unverified** |
| Production deploy | **Blocked pending approval** |

---

## Test results

### Unit / service (venv pytest)

```
tests/test_exam_security.py
tests/test_exam_security_extended.py
tests/test_exam_security_regression.py
tests/test_time_tracking_service.py
→ 26 passed
```

Coverage highlights: SEB hash formula, missing/spoofed headers, soft events `verified=false`, resume block, Mode A→B→C settings + key rotate, exam window gate, session-token conflict event, policy secret hiding, SEB config build, auto-finalize batch.

### Regression subset

```
tests/test_lms_smoke.py
tests/test_student_reporting.py
tests/test_ai_guided_practice.py
→ 82 passed
```

Also static/import checks for time tracking (LC/GP/AIGP timing helpers), quizzes teacher panel markers, learning-chat route parse.

### Isolated E2E runner

```
python scripts/exam_security_e2e_runner.py
→ 14/14 passed (API + Playwright + regression imports)
```

Synthetic accounts (isolated temp SQLite only):

- Teacher: `e2e.teacher.examsec@iqbalai.test`
- Student: `e2e.student.examsec@iqbalai.test`

| Check | Result |
|-------|--------|
| Teacher PATCH Mode A/B/C | Pass |
| SEB config download | Pass |
| API attack: SEB missing headers | Pass |
| Server auto-finalization | Pass |
| API attack: unauthenticated security-events | Pass |
| Playwright teacher security panel | Pass |
| Playwright soft-lock visibility/blur events | Pass |
| Playwright network-failure offline badge | Pass |

---

## Real SEB testing (Windows)

| Probe | Result |
|-------|--------|
| `SafeExamBrowser.exe` under Program Files / LocalAppData | **Not found** |
| Alt+Tab / exit / unauthorized access / recovery / bypass on real SEB | **Not executed** |
| Claim of OS lockdown from mocks | **Not made** |

**Finding:** Soft lock and server header checks are verified in automation. They do **not** prove OS-level lockdown. Install SEB on a lab Windows machine and re-run a manual checklist before high-stakes use.

---

## Security findings

1. **Residual — forgeable SEB headers:** A custom client that knows the Config Key can forge `X-SafeExamBrowser-ConfigKeyHash`. Mitigate with Mode C + invigilators / managed devices; do not treat SEB alone as inviolable.
2. **Soft signals are advisory:** `tab_hidden` / `fullscreen_exit` / blur are stored with `verified=false` and never auto-fail.
3. **`EXAM_SECURITY_SEB_BYPASS`:** Allowed only in development/pytest — must stay unset in production.
4. **Secrets:** `serialize_policy` does not expose raw BEK / config key material to clients.
5. **Session conflict:** Mismatched `session_token` records a verified `session_conflict` event and blocks the write.

---

## Remaining limitations

- No webcam / room proctoring.
- Soft lock ≠ OS lockdown (fullscreen/tab APIs are bypassable).
- Real SEB OS suite not run on this agent host.
- Celery beat must be running in production for reliable server-side expiry finalization (`finalize_expired_exam_attempts_task`).
- Controlled/Uncontrolled remain reporting concepts; lockdown modes are orthogonal security settings on quizzes.

---

## Deployment readiness (do not deploy yet)

Checklist before production:

1. [ ] Apply / confirm `exam_security` migration on production DB (startup migration or explicit run).
2. [ ] Deploy app code including teacher static assets (`asset_v=20261120examsec2`).
3. [ ] Confirm Celery worker **and** Celery beat are running.
4. [ ] Confirm `EXAM_SECURITY_SEB_BYPASS` is unset.
5. [ ] Smoke: teacher opens Quizzes → Exam security → save Mode A; download SEB config for a Mode B quiz.
6. [ ] Optional lab: install SEB, open `.seb`, verify Alt+Tab blocked.
7. [ ] **Explicit human approval** required before any production deploy command.

---

## Evidence

| Artifact | Path |
|----------|------|
| E2E JSON results | `docs/qa_evidence/exam_security_e2e_results.json` |
| Playwright demo video | `docs/qa_evidence/playwright_videos/page@dc9a01799e96b90f212d0762dc5197c0.webm` |
| Architecture notes | `docs/exam_security.md` |
| E2E runner | `scripts/exam_security_e2e_runner.py` |

Video covers: teacher login → Quizzes → Exam security panel (mode cycle) → soft-lock event fixture → offline sync badge.

---

## Bugs fixed this pass

1. Teacher `saveSecurity` checkbox expression (was inverted/always-true).
2. Mode A selection now clears “Require SEB”; server clears `require_seb` when mode→`standard` without an explicit require flag.
3. Preview merge of `security` into quiz list row after load.
4. Playwright locator for Exam security panel (invalid mixed CSS/`text=` selector).
