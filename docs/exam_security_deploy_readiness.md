# Exam security — deploy readiness (in-app only)

## Pre-deploy

1. Run migrations if security columns are not yet present (`exam_security` migration).
2. Deploy app build with SEB removed (`asset_v=20261121noseb1` or later).
3. Clear legacy flags (optional but recommended):

```sql
UPDATE assessments
SET require_seb = 0,
    security_mode = CASE WHEN security_mode = 'seb' THEN 'standard' ELSE security_mode END,
    seb_browser_exam_keys_json = NULL,
    seb_config_key_hash = NULL
WHERE require_seb = 1 OR security_mode = 'seb'
   OR seb_browser_exam_keys_json IS NOT NULL
   OR seb_config_key_hash IS NOT NULL;
```

4. Confirm `EXAM_SECURITY_SEB_BYPASS` is unset in production (legacy; unused for gates).

## Post-deploy smoke

1. Teacher: Quizzes → Exam security → Soft lock / Supervised save.
2. Student: start quiz in normal Chrome — no SEB prompt; soft lock + timer work.
3. `GET /api/lms/quizzes/<id>/seb-config` returns **410**.

## Rollback

Revert app release. Columns are additive. Set quizzes to `security_mode=standard` / `require_seb=false` if needed.
