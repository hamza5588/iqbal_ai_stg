# IqbalAI Exam Security (defense-in-depth)

## Architecture assessment (existing platform)

- **Frontend:** vanilla JS (`student-quiz.js`, `lms-student.js`, `lms-exam-security.js`)
- **Backend:** Flask `/api/lms` + `attempt_service`
- **Auth:** Flask session + RBAC (`login_required`, role checks)
- **Attempts:** `AssessmentAttempt` / `AttemptAnswer` with per-answer save and server `expires_at`
- **Gaps closed by this work:** SEB gate, security events, heartbeat route, Celery expiry finalization, teacher security settings, Mode A soft lockdown UX

## Modes

| Mode | Code | Behavior |
|------|------|----------|
| A Standard | `security_mode=standard` | Soft browser controls + logging. **Not OS lockdown.** |
| B SEB | `security_mode=seb` or `require_seb=true` | Server rejects clients without valid SEB Config Key / Browser Exam Key headers |
| C Supervised | `security_mode=supervised` | SEB required + exam window / institutional policy knobs |

## Server enforcement

- Start: `exam_security_service.authorize_start`
- Heartbeat: `POST /api/lms/attempts/:id/heartbeat` (15s client ping)
- Events: `POST/GET /api/lms/attempts/:id/security-events`
- SEB config download: `GET /api/lms/quizzes/:id/seb-config`
- Teacher settings: `PATCH /api/lms/quizzes/:id` with security fields
- Expiry: Celery beat `finalize_expired_exam_attempts_task` every 60s (does **not** depend on the browser)

## SEB verification

Follows [SEB Config Key](https://www.safeexambrowser.org/developer/seb-config-key.html) / Browser Exam Key:

`SHA256(absoluteURL + keyHex)` compared to  
`X-SafeExamBrowser-ConfigKeyHash` or `X-SafeExamBrowser-RequestHash`.

**Residual risks:** headers can be forged by a custom client that knows the key; phone cameras; second devices; compromised endpoints. Mode C still needs invigilators / managed devices.

`EXAM_SECURITY_SEB_BYPASS=1` is allowed only under pytest / development — never production.

## Soft signals

Fullscreen exit, tab hide, blur, reload are **suspected** events (`verified=false`). They never auto-fail an exam by themselves.

## What is / is not verified in automated tests

| Claim | Verified? |
|-------|-----------|
| SEB hash formula + reject spoofed hash | Yes (unit) |
| Missing SEB headers blocked when required | Yes (unit) |
| Soft events recorded, not treated as proof | Yes (unit) |
| Expired attempts finalized without client | Yes (unit batch) |
| Resume policy enforced | Yes (unit) |
| Real SEB OS lockdown (Alt+Tab / screenshots) | **No** — requires installed SEB on a real OS |
| Webcam proctoring | Not implemented (by design) |

## Teacher configuration fields

`security_mode`, `require_seb`, `allow_resume`, `max_interruptions`, `max_concurrent_sessions`, `exam_window_start/end`, `auto_submit_on_expiry`, `warn_on_focus_loss`, `seb_browser_exam_keys`, `rotate_seb_config_key`

## Teacher UI

On **Quizzes**, expand a quiz. The **Exam security** panel (Mode A / B / C) maps to `PATCH /api/lms/quizzes/:id`. Actions: Save security settings, Rotate SEB config key, Download SEB config. Soft-lock vs SEB/Supervised pills show on the quiz card. Asset cache-bust: `asset_v=20261120examsec2` in `templates/teacher/base.html`.

## QA

Full results, SEB unverified status, and deploy checklist: [`docs/exam_security_qa_report.md`](exam_security_qa_report.md).  
Isolated E2E + video: `python scripts/exam_security_e2e_runner.py`.
