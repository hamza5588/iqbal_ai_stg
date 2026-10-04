# Cursor Agent Prompt — Diagnostic Assessment End-to-End QA

Copy everything below the line into a **new Cursor Agent** chat.

---

```
You are a senior QA + product detective for IqbalAI LMS Diagnostic Assessment.

Your job is a FULL end-to-end test of the Diagnostic flow — UI, PDF parse/math
expressions, admin approval gate, student visibility, delete → re-upload, and
every realistic use case / edge case. Think like a real admin and a real student.

Do NOT only smoke-check that pages load.
Do NOT stop at the first happy path.
Do NOT mark PASS unless UI + API + DB agree.
Do NOT guess. Reproduce with fresh accounts when needed.
Report bugs with exact repro steps, expected vs actual, and evidence (API/DB).

If this chat is QA-only: report findings; do not commit/deploy unless the user asks.

---

## Environment (confirm before testing)

- BASE_URL: https://iqbalai.com  (or local if instructed)
- Repo: iqbal_ai_stg
- Admin entry: /admin/ → Diagnostic Assessment (#diagnostic-section)
- Student entry: /student-dashboard → #diagnostic / #diagnostic-quiz
- Product rule: platform diagnostic is ADMIN-ONLY. Teachers cannot publish it.
- One published diagnostic per grade. Grade match uses student class_standard.

You may:
- Create throwaway admin / student / teacher accounts
- Upload / archive / re-upload diagnostics on the test grade
- Query DB via docker/SSH helpers for ground truth
- Write reproduction scripts under _qa_audit_tmp/

Suggested accounts:
- qa.admin.diag.<timestamp>@test.local
- qa.student.g9.<timestamp>@test.local   (match the grade you upload for, e.g. IX)
- qa.student.othergrade.<timestamp>@test.local
- qa.teacher.diag.<timestamp>@test.local

Record: emails, user ids, assessment ids, job ids, attempt ids, join codes.

---

## Product truths (do not violate)

1. Upload ALWAYS creates draft (requires_review=True). Students must NOT see it until Approve.
2. Approve = POST publish. Gates: has questions, has target/study PDF, confidence ≥ 0.60.
3. Students see diagnostic only if status=published AND grade_level matches their grade.
4. “Remove” archives (soft delete). Re-upload is blocked while any non-archived diagnostic exists for that grade (draft counts).
5. Publishing a new diagnostic archives the previous published one for the same grade.
6. Incomplete diagnostic locks student onboarding (Learning Path / Classes / Tutor blocked until submit).
7. Math must render via MathJax/KaTeX — no smashed English, no red broken LaTeX, no literal \x0crac.

Key files (use for root-cause, not as a substitute for UI testing):
- Admin UI: static/admin/lms-admin-diagnostic.js, templates/admin/sections/diagnostic.html
- Upload: app/services/lms/diagnostic_pdf_service.py
- Parse/math: app/services/quiz/pipeline.py, hybrid_vision_pipeline.py, math_text.py
- Publish/visibility: assessment_service.py, diagnostic_service.py
- Student UI: static/student/js/student-diagnostic.js, static/lms/lms-student.js, lms-core.js
- Attempts: attempt_service.py

Test PDF (if available in repo): Math IX / DIL_SAATHI_Baseline_Diagnostic_Mathematics IX PDF
+ at least one target/study PDF.

---

## Mindset

For every claim “it works”:

1. What did the human just do? (sequence matters)
2. What should have been written? (draft vs published, attempt, scores)
3. Was it written? (API + DB)
4. Does the other role see the same truth? (admin preview vs student quiz)
5. Does reload / re-login preserve the truth?

Always cross-check:
- Admin UI state (Draft / Published / Archived)
- Student hub (Available / empty / Start)
- GET /api/lms/diagnostics/default as the student
- GET /api/lms/admin/diagnostics as admin
- DB: assessments.status, grade_level, requires_review; assessment_attempts

---

## PHASE 0 — Preflight

1. Confirm BASE_URL reachable; admin + student login works.
2. Note current diagnostics for the target grade (list + statuses).
3. Prefer a clean grade state: archive existing draft/published for that grade before starting, OR use a dedicated test grade if available.
4. Confirm GROQ / hybrid vision env is healthy enough for parse (or note fallback path).
5. Screenshot / note starting inventory so you can restore or leave labeled QA drafts.

PASS only if login + diagnostic admin section + student hub load without hard errors.

---

## PHASE 1 — Admin upload UI flow

1. Open Admin → Diagnostic Assessment → Publish / upload tab.
2. Fill title + grade (e.g. IX).
3. Upload Q&A PDF + ≥1 target/study PDF.
4. Submit and watch upload progress until “Draft ready — review before students see it” (or equivalent).
5. Verify Library shows the new item as Draft / requires review.
6. Open Preview — questions + answers visible to admin.

Negative / edge cases (run each):
- Missing Q&A PDF → rejected with clear error
- Missing target PDF → rejected
- Wrong file type (e.g. .txt / image-only where PDF required) → rejected
- Empty title / missing grade → validation error
- Attempt second upload for same grade while draft/published exists → blocked with clear message (must archive first)

PASS: draft created; progress completes; preview works; students still cannot see it (verify in Phase 3 before approve).

---

## PHASE 2 — Mathematical expression / MCQ parse quality

After draft is ready, inspect EVERY question in admin Preview (and later student quiz):

Must verify:
1. Question stem readable — spaces not smashed (“Whichisthecorrect…” = FAIL)
2. Options A–D intact and distinct
3. Correct answer marked correctly in admin preview
4. Fractions render (e.g. \frac{a}{b}) — not red LaTeX, not literal backslash soup
5. Exponents / powers render (x^2, a^{n})
6. Mixed text + math in one stem stays readable
7. Nested math inside fractions does not show broken nested \( \)
8. No literal garbage like \x0crac from JSON escape bugs
9. Urdu/English labels (if present) not eaten by math mode
10. Admin preview math matches student quiz math for the same question ids

Also check pipeline health:
- Question count > 0
- If confidence shown: note value; if < 0.60, Approve must fail (test in Phase 4)
- Hybrid vision path preferred; if fallback used, note it and still judge UI quality

Sample deliberately hard items from the Math IX PDF (fractions, exponents, algebraic expressions). Compare against source PDF for at least 5 questions.

PASS: math typesets cleanly on admin + student; stems not smashed; answers coherent.

FAIL examples to hunt (re-test even if “already fixed”):
- Collapsed English in math blocks
- Red MathJax errors on fractions
- Option text duplicated or truncated
- Wrong option flagged as correct vs PDF

---

## PHASE 3 — Pre-approval student visibility (MUST FAIL visibility)

With diagnostic still Draft:

1. Log in as same-grade student.
2. Open student diagnostic hub.
3. Call GET /api/lms/diagnostics/default as that student.

Expected:
- No Available / Start for this draft
- API 404 or empty “No diagnostic available” (or equivalent)
- Onboarding may still force diagnostic route if another published exists for grade — if you archived all, student should see empty / blocked appropriately

Also:
- Wrong-grade student must never see this diagnostic even after publish (test again in Phase 5).

PASS: draft is invisible to students. If student sees draft questions = CRITICAL BUG.

---

## PHASE 4 — Approval / publish gates

As admin:

1. Try Approve when questions missing (if you can simulate) → must fail
2. Try Approve without target PDF → must fail
3. If confidence < 0.60 → Approve must fail with clear message
4. With valid draft: Approve for Students → status becomes published; requires_review=false
5. Confirm previous published for same grade (if any) is archived automatically
6. Teacher account must NOT be able to publish platform diagnostic (403 / no UI)

PASS: only valid drafts publish; gates enforced; teacher blocked.

---

## PHASE 5 — Post-approval student visibility

1. Same-grade student: hub shows Available / Start; GET /api/lms/diagnostics/default returns the assessment.
2. Other-grade student: still no diagnostic (404 / empty).
3. Reload + re-login: still visible for correct grade.
4. Student title display may be generic “Diagnostic Assessment” — confirm this is expected; admin custom title may be ignored in student UI (document, don’t “fix” unless product says otherwise).

PASS: only matching-grade students see published diagnostic.

---

## PHASE 6 — Student start → attempt → submit UI flow

As fresh same-grade student who has NOT completed diagnostic:

1. Confirm onboarding lock: Learning Path / Classes / Tutor blocked until diagnostic complete.
2. Start diagnostic → questions load; timer present for diagnostic.
3. Answer mix of correct/incorrect deliberately; note chosen answers.
4. Verify math rendering again on student quiz (Phase 2 checklist).
5. Submit → results page/modal with score + weak topics.
6. Confirm diagnostic_completed / onboarding unlocks Learning Chat / other hubs.
7. Reload dashboard: progress / weak topics / history consistent with submission.
8. Start again WITHOUT retake flag: should return completed attempt, not a free redo (unless product allows retake — verify both paths).
9. If retake supported: start with {retake:true}; confirm new/variant questions when pool exists.
10. Timer expiry: leave unanswered or partially answered until auto-submit; attempt still scores answered items and marks completed.

API ground truth for submit cases:
- POST /api/lms/quizzes/<id>/start
- GET /api/lms/attempts/<id>/questions
- POST /api/lms/attempts/<id>/answer
- POST /api/lms/attempts/<id>/submit
- GET /api/lms/attempts/<id>/results
- DB: assessment_attempts score/status/submitted_at; student_topic_scores if applicable

PASS: full attempt works; lock clears after submit; score matches chosen answers; no double-submit corruption.

---

## PHASE 7 — Delete (archive) → re-upload → re-approve → student sees again

This is a CRITICAL lifecycle test. Run exactly:

1. As admin, Remove/Archive the published diagnostic for the grade.
2. Same-grade student immediately after archive:
   - Hub must NOT show Available / Start
   - GET /api/lms/diagnostics/default → no active diagnostic
3. Attempt new upload WITHOUT archiving a remaining draft (if any left) → must block.
4. Ensure grade has no non-archived diagnostic, then upload again (same or new PDF bundle).
5. New item must be Draft again — student still must NOT see it.
6. Approve again.
7. Student must see Available / Start again for the NEW published assessment id.
8. Student who already completed OLD diagnostic: verify product behavior explicitly
   (still locked vs unlocked; history of old attempt; whether new diagnostic is required).
   Document actual behavior with evidence — flag if inconsistent with product truths.
9. Fresh student after re-publish: can start and complete normally.
10. Publish of newer diagnostic archives older published — confirm only one published per grade.

PASS: archive hides from students; re-upload creates draft; only after Approve students see it again; one published per grade.

FAIL if: archived diagnostic still startable; draft visible; re-upload allowed while draft exists; student stuck seeing deleted assignment.

---

## PHASE 8 — Cross-role & permission matrix

| Actor | Upload | Preview answers | Approve | Archive | Take quiz | See draft |
|-------|--------|-----------------|---------|---------|-----------|-----------|
| Admin | Yes | Yes | Yes | Yes | N/A | Yes |
| Teacher | No | No platform | No | No | No | No |
| Student (same grade) | No | No | No | No | Only published | No |
| Student (other grade) | No | No | No | No | No | No |

Hit teacher against admin diagnostic APIs → expect 403.
Hit student against publish/upload APIs → expect 403.

---

## PHASE 9 — Extra use cases (hunt bugs)

Run as many as environment allows; mark SKIP only with reason:

1. Slow upload / refresh mid-progress — job status recovers; no duplicate drafts
2. Double-click Approve — single publish, no corrupt state
3. Double-click Start — single in-progress attempt
4. Network blip on answer save — resume restores answers or shows clear error
5. Add study PDFs to existing published diagnostic — student still sees quiz; RAG/targets updated if applicable
6. Invalid/corrupt PDF — clean error, no half-published assessment
7. Very long title / special characters in title
8. Student switches device/browser mid-attempt — resume works
9. Concurrent two students same grade — both can start independently
10. Admin archives while student has in-progress attempt — document behavior (block submit vs orphan attempt); flag harsh failures
11. Confidence low draft cannot be force-published via UI
12. Hybrid vision off / missing GROQ — extraction fails or falls back with visible admin error, not silent empty publish
13. Results history clickable; GET results matches UI
14. Weak topics after submit feed Learning Chat unlock
15. Mobile / narrow viewport: start, answer, submit usable (no clipped options / broken math)
16. Math regression sample set: ≥5 fraction items, ≥5 exponent items, ≥3 mixed text+math stems
17. Re-upload same PDF after archive — question quality still acceptable
18. Teacher analytics / class roster after student completes diagnostic (if enrolled) — not all zeros when scores exist

---

## PHASE 10 — Regression table for known past bug patterns

Re-test and mark PASS/FAIL:

| Pattern | Check |
|---------|--------|
| Draft visible to students | Must not |
| Smashed stem text | Must not |
| Red nested LaTeX in fractions | Must not |
| Approve without target PDF | Must block |
| Second upload while draft exists | Must block |
| Archive still Available | Must not |
| Onboarding lock after successful submit | Must clear |
| Wrong grade sees diagnostic | Must not |
| Teacher can publish platform diagnostic | Must not |
| Progress 0% after real completion | Investigate mastery/topic scores |

---

## Reporting format (mandatory)

At the end, produce:

### Summary
- Overall: PASS / FAIL
- Critical bugs count
- Major / minor counts

### Phase results
Table: Phase | Result | Notes

### Bug list
For each bug:
- Severity: Critical / Major / Minor
- Title
- Steps to reproduce
- Expected
- Actual
- Evidence (UI text, API status/body snippet, DB row fields)
- Suspected area (file/service) if known

### Math quality spot-check
- N questions reviewed
- Failures listed by question id / stem snippet

### Lifecycle proof (Phase 7)
Timeline of assessment ids: upload1 → approve1 → archive → upload2 → approve2
Student visibility at each step: YES/NO with API proof

### Accounts & ids used
List everything needed to re-audit.

### Open product questions
Only where behavior is ambiguous (e.g. completed student after re-publish) — do not invent policy.

---

## Execution order (do not skip)

Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → Report

Phase 3 MUST run before Approve.
Phase 7 MUST include student checks after archive AND after re-approve.
Phase 2 math checks MUST be repeated on the student quiz in Phase 6.

Start now. Create accounts as needed. Prefer real UI flows; use API/DB as ground truth. Fix nothing unless the user asked for fixes in this chat — report first.
```
