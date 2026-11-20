# Exam security (IqbalAI in-app only)

Third-party clients (including Safe Exam Browser) are **not used**. All lockdown is inside IqbalAI.

## Modes

| Mode | `security_mode` | Behavior |
|------|-----------------|----------|
| Soft lock | `standard` | Normal browser + fullscreen request, tab/focus warnings, heartbeat, server timer |
| Supervised | `supervised` | Soft lock + optional exam window |
| **Strict** | `strict` | Soft lock + **server LOCK** on tab hide / blur / fullscreen exit; teacher unlock & resume; timer keeps running |

Legacy DB values `seb` / `require_seb=true` are treated as soft lock.

## Strict Exam Mode (Chromebook labs)

1. Student must enter fullscreen before the attempt is usable.
2. Leaving the exam window locks the attempt (`status=locked`); answers/submit rejected server-side.
3. Teacher **Live security** panel (Quizzes → expand → Live security) polls every ~4s, beeps on new locks.
4. **Unlock & Resume** restores the same attempt (same answers, question index, deadline).
5. **Extend +5 min** bumps `expires_at` (audit in `attempt_time_extensions`).
6. Deadline never pauses while locked; expiry while locked still auto-submits.

Soft focus events are **not** automatic fails — they are review signals. Lock is an invigilator workflow, not a grade penalty by itself.

## APIs

- `POST /api/lms/attempts/:id/security-events` — may lock in strict mode
- `POST /api/lms/attempts/:id/unlock` — teacher
- `POST /api/lms/attempts/:id/extend-time` — teacher `{seconds|minutes, idempotency_key?}`
- `GET /api/lms/quizzes/:id/live-security` — teacher poll
- Heartbeat returns `is_locked` / `status` for student sync

## Limits (honest)

A website cannot block every ChromeOS shortcut, second phone, or other account. Strict Mode is the strongest **first-party browser** enforcement IqbalAI offers; pair with in-room teachers.

Asset bust: `20261121strict1`.
