# Student Performance Reports

The Admin Console exposes **Student Groups** and **Reports → Student Performance**. Access is restricted to authenticated administrators. The primary report form shows only Group, Test date, Assessment, and School. Optional date ranges, individual students, and assessment setup are under collapsed Advanced sections; the pilot cohort is fixed to `default` in the UI.

## Group membership

Group membership is stored per `cohort_key` in `student_study_group_assignments`. A row is active from `effective_from` until (but not including) `effective_to`. Reassignment closes the current row and creates a new row; unassignment only closes the current row. Every actual change is appended to `student_study_group_audit`.

Valid values are `Controlled` and `Uncontrolled`. `Unassigned` means no active assignment and is never treated as Uncontrolled. During a historical export, membership is evaluated at the assessment's `submitted_at`, so later changes do not alter old reports.

CSV/XLSX imports accept `Student ID` or `Email`, plus `Group`. Emails are normalized to lower case. A preview rejects unmatched identifiers, ID/email conflicts, and duplicate rows. Applying a preview never creates a user. An optional Pakistan calendar date may be supplied for the **initial** membership import. Backdating is rejected once any group history exists, so later reassignments cannot rewrite historical reports. Audit `changed_at` remains the real operation time.

## Assessment and attempt selection

`assessment_report_classifications` explicitly maps an assessment to `Baseline` or `FollowUp` within a cohort. It does not infer study type from the platform-wide first quiz. A cohort can have one Baseline; FollowUps carry a positive admin-controlled order.

If preview has no rows, it distinguishes no submitted attempts from unclassified assessments, missing historical group membership, and attempts belonging to another selected group. These diagnostics contain counts only and do not expose additional student data.

Date inputs are Pakistan dates (`Asia/Karachi`). The start is inclusive at local midnight and the end is inclusive through the next local midnight. For each student and assessment, the latest submitted attempt in that window is used. Results, topic rows, and answer rows all come from that same attempt.

Each workbook contains one group, assessment, and submitted date. Multi-test/date/group exports are returned as a ZIP of individual workbooks. Same-day tests receive stable suffixes ordered by assessment ID.

## Excel mapping and metric rules

The packaged Controlled or Uncontrolled reference workbook is loaded for every export. Example data rows are removed; the `Instructions` worksheet is preserved unchanged.

| Excel field | Database source or calculation |
|---|---|
| Student Name, Email, School | `users.full_name`/`username`, `useremail`, `school_name` |
| Group | effective `student_study_group_assignments.group_name` at submission |
| Test Type | `assessment_report_classifications.test_type` |
| Test Date | `assessment_attempts.submitted_at` converted to Pakistan date |
| Test Name | `assessments.title` |
| Total Questions | count of attempt answers with a stored `is_correct` outcome |
| Correct Answers / Incorrect | stored `attempt_answers.is_correct`; existing grading remains authoritative |
| Percentage | Correct / Total as a numeric Excel percentage; blank if no graded answers |
| Minutes Taken | `(submitted_at - started_at) / 60`; blank if either timestamp is unreliable |
| System Usage Time (Minutes) | sum of `tutor_daily_usage.active_ms` in the selected window; this tracks AI Tutor heartbeat time, not all platform time; blank when no tracking row exists |
| Days Active | distinct Pakistan dates with positive AI Tutor usage or a submitted assessment in the selected window |
| Last Active Date | latest qualifying activity date in the selected window |
| Activities Completed | count of submitted assessment attempts in the selected window |
| Topic Scores | stored graded answers grouped by `questions.topic_id`; missing topic metadata is not invented |
| Answers | attempt question order, question text/options, selected option and stored grading outcome |

The preview surfaces missing duration, grading, topic, question, and usage information. Missing usage is blank rather than zero. Excel strings are forced to string cells to prevent formula injection while preserving Unicode and mathematical content.

## Canonical DIL pilot bootstrap

Run `scripts/backfill_student_reporting.py` as a dry-run first. The two supplied diagnostic Results workbooks are authoritative for their `58` Controlled and `98` Uncontrolled unique/disjoint emails. The newer dashboard workbook is supplemental only for previously unseen emails: it adds three explicit Controlled students and cannot override the canonical files (its 44 conflicts are counted and ignored). The tool verifies stored DB user IDs and refuses school-based inference. It also validates the known Baseline/FollowUp assessment IDs before any write. `--apply` performs assignments, audit rows, and classifications in one transaction; conflicts cause a full rollback, and an exact rerun is idempotent. A student absent from every supplied roster remains Unassigned and is reported as such.
