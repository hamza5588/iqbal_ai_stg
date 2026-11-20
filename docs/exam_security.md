# Exam security (IqbalAI in-app only)

Third-party clients (including Safe Exam Browser) are **not used**. All lockdown is inside IqbalAI.

## Modes

| Mode | `security_mode` | Behavior |
|------|-----------------|----------|
| Soft lock | `standard` | Normal browser + fullscreen request, tab/focus warnings, heartbeat, server timer / auto-submit |
| Supervised | `supervised` | Soft lock + optional exam window (`exam_window_start` / `exam_window_end`) |

Legacy DB values `seb` / `require_seb=true` are treated as soft lock (`require_seb` always forced off).

## Soft signals

Tab hide, blur, fullscreen exit, etc. are logged as **unverified** review signals. They do **not** auto-fail a student.

## Teacher UI

Quizzes → expand quiz → **Exam security (IqbalAI only)**. Asset cache-bust: `asset_v=20261121noseb1`.

## API

- `PATCH /api/lms/quizzes/:id` — security settings (`security_mode`, resume/focus/expiry knobs, exam window)
- `GET /api/lms/quizzes/:id/seb-config` — retired (`410 seb_removed`)
- Heartbeat + security-events on attempts

## Limits

Soft lock is **not** OS-level lockdown (Alt+Tab, screenshots, other apps). For high-stakes exams use invigilators / school lab supervision.

See also: [`exam_security_qa_report.md`](exam_security_qa_report.md), [`exam_security_deploy_readiness.md`](exam_security_deploy_readiness.md).
