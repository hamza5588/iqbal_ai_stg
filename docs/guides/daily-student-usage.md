# Daily student usage

Admins open **Monitoring → Daily Usage** to filter by date range (up to 31 days), school, or student name and download an Excel workbook. Dates use **Asia/Karachi**. The report includes students with zero recorded usage; dates before tracking began have blank time and **Not tracked** coverage. The first tracking date is marked **Partial day**.

Only student accounts accrue time. Tracking covers the tutor panel in Learning Path, the lesson chat panel, and the general tutor panel if enabled. Interaction with a visible, focused tutor panel starts counting; reading time continues for up to two minutes after the last activity. Clicking elsewhere, leaving the panel's screen, hiding the tab, or losing focus pauses counting. Browser sleep and offline gaps are excluded. Students with already-open dashboards need to refresh to load the tracker.

The client sends cumulative millisecond counters every 15 seconds and flushes on pause/page exit. The server uses the authenticated user and server elapsed time, rejects stale sequences, bounds credit to 30 seconds per update, and does not backfill gaps over 45 seconds. Overlapping device intervals are credited once. An unexpected close or network failure may lose the last unsent interval; this is an engagement estimate rather than a stopwatch.

No message text, prompts, answers, or chat history are sent to these endpoints or included in reports. Clearing chat history does not affect usage totals. The workbook contains a Daily Usage sheet and a Report Info sheet explaining dates and measurement. Student and school names are explicitly written as text to prevent spreadsheet formula execution.

## Storage and deployment

School Name is required for every new account created through admin User Management or public registration. Both forms suggest the six schools from the controlled and non-controlled workbooks and accept a typed name for another school. Admins can edit school names; existing accounts without a school can still be edited, but a recorded school cannot be cleared. Bootstrap admin creation remains compatible.

Use `scripts/import_student_schools.py` for existing students: validate unique email and matching student name first, and cross-check workbook DB User IDs when available. Dry-run before applying; provide a private backup path. The import changes only matched students and verifies all other accounts remain unchanged. Student workbooks and import backups must remain outside Git.

- `tutor_usage_sessions`: per-page cumulative heartbeat cursor, tied to a student.
- `tutor_daily_usage`: per-user/date active milliseconds and an overlap watermark.
- `system_settings.tutor_usage_started_at`: original UTC rollout timestamp; preserved across restarts.

Normal `init_db` creates the tables and initializes the start timestamp. If startup initialization is disabled, explicitly create the two tables from `Base.metadata` and call `initialize_tracking(engine)` before reloading web workers. Install the `openpyxl` version in `requirements.txt`. No existing chat data needs to be migrated. Back up source files and the database schema/settings before deployment.

The heartbeat endpoint is `/api/tutor-usage/heartbeat`. Admin-only endpoints are `/admin/daily-usage` and `/admin/daily-usage/export.xlsx`, with `start`, `end`, `school`, and `search` filters. The JSON list is paginated; the workbook exports all matching rows. Responses are not cached.

## Verification

```text
python -m pytest scripts/test_tutor_usage.py scripts/test_student_school_import.py scripts/test_school_registration.py -q
node --test scripts/test_tutor_usage_client.cjs
```

These use disposable data. For live verification, use a temporary QA student and remove that student and its usage rows after checking the heartbeat, report, download, and permissions. Do not add test time to real students.
