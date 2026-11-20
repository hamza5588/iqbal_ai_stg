# Exam security QA report (CEO: in-app only)

**Date:** 2026-11-21  
**Policy:** No third-party exam browser. Soft lock + supervised window only inside IqbalAI.

## Verdict

SEB / Mode B removed from product path. Students start quizzes in a normal browser. Teacher panel exposes Soft lock and Supervised only. Legacy `require_seb` / `security_mode=seb` in DB cannot block starts.

## Coverage

| Area | Status |
|------|--------|
| Soft lock UX (fullscreen, focus, heartbeat, timer) | Kept |
| Supervised + exam window | Kept |
| Teacher security panel (no SEB option) | Done |
| `GET .../seb-config` | 410 retired |
| Server SEB header gate on start | Disabled |
| Unit/regression tests updated for no-SEB policy | Done |
| OS-level lockdown (Alt+Tab etc.) | Not claimed — use invigilators |

## Deploy notes

See [`exam_security_deploy_readiness.md`](exam_security_deploy_readiness.md). Clear legacy `require_seb` rows after deploy if any remain.
