# LMS Visual + Backend QA Report

- BASE_URL: http://127.0.0.1:5001
- Run: 20260929_063652
- Git: ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+19 uncommitted paths)
- Summary: **1 PASS / 1 FAIL / 0 BLOCKED** (2 items)

## Accounts & IDs

```json
{
  "accounts": {},
  "ids": {},
  "password": "QaPass!2026x"
}
```

## Setup results

| Feature | Visual | Interaction | API | DB | Status | Evidence |
|---|---|---|---|---|---|---|
| Health: app responds at BASE_URL | n/a | n/a | ok: GET /auth/login 200 | n/a | **PASS** | ui_integration_for_teacher @ 6c3c3ea Snapshot teacher UI integration, QA/load tooling and new UI design sources (+19 uncommitted paths)  |
| Register TEACHER via UI → lands on /teacher-dashboard | n/a | n/a | n/a | n/a | **FAIL** | AssertionError: http://127.0.0.1:5001/auth/verify_email/JH-NgwEbbUFt3YrvWYvVFwJgaSBlW3KmmshPtD8aiEU screenshots/FAIL_01_register_teacher_via_ui_lands_on_teacher.png |

## Failures / blocked (raw)

### FAIL: [Setup] Register TEACHER via UI → lands on /teacher-dashboard

```
AssertionError: http://127.0.0.1:5001/auth/verify_email/JH-NgwEbbUFt3YrvWYvVFwJgaSBlW3KmmshPtD8aiEU
```

