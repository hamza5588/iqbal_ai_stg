# Cursor Agent Prompt — IqbalAI Server Load Test (200 Students)

Copy everything below the line into a **new Cursor Agent** chat.

---

```
You are a performance engineer. Run a full load test on my IqbalAI server.
Do NOT guess — SSH into the server, verify services, create test data if needed, run the test, and return a report.

## Target server
- BASE_URL: {{209.23.10.34}}
- SSH_HOST: {{REPLACE — server IP or hostname}}
- SSH_USER: {{REPLACE — e.g. root or ubuntu}}
- APP_DIR: {{REPLACE — e.g. /opt/flask-app or path where docker-compose.yml lives}}

## Credentials (fill before running)
- ADMIN_EMAIL / admin@iqbalai.com
- TEACHER_EMAIL / teacher@iqbalai.com
- STUDENT_PASSWORD (create the student id by your self i dont have right now)

## Goal
Simulate **200 students at the same time** on the live server doing the real LMS flow:

1. Login
2. Platform diagnostic assessment (one-time, with timer)
3. Learning Chat after diagnostic (weak-area practice)
4. Teacher-assigned quiz (start → answer → submit)

Also verify admin has published diagnostic + teacher has published quiz assignment before load test starts.

---

## STEP 1 — Connect & health check

SSH to server and run:

```bash
cd $APP_DIR
docker-compose ps
curl -fsS $BASE_URL/health
curl -fsS $BASE_URL/api/lms/health
```

All containers must be Up: `flask_app1`, `celery_worker`, `postgres`, `redis`, `milvus`, `nginx`.

If celery_worker is down → start it before continuing (quiz/diagnostic PDF jobs need it).

---

## STEP 2 — Pre-requisites (create if missing)

### 2a. Published diagnostic (admin)
- Login as admin → Diagnostic Assessment section
- OR API: `GET /api/lms/admin/diagnostics` → must have `status: published`
- Must include target content PDF(s) for Learning Chat

If none exists, upload test diagnostic (Q&A PDF + 1–2 target PDFs) and publish.

### 2b. Published quiz + assignment (teacher)
- Teacher must have a **published quiz** assigned to a class
- Students in load test must be enrolled in that class (join code) OR assignment visible via `GET /api/lms/students/me/assignments`

### 2c. 200 student test accounts
Create if not exist:
- Emails: `loadtest_student_001@test.iqbalai.local` … `loadtest_student_200@test.iqbalai.local`
- Role: student, grade: 8
- **Important:** For diagnostic test, accounts must NOT have completed diagnostic yet (use fresh accounts or reset onboarding on staging only)

Option: use Admin → Load Testing → User Set if already configured.

---

## STEP 3 — Choose load test tool

Try in order:



 k6 script** (preferred for 200 users)
- Install k6 on server OR run from local machine targeting BASE_URL
- Create script at: `scripts/load/k6_lms_student_journey.js`
- Run: `k6 run --vus 200 --duration 30m --ramp-up 120s scripts/load/k6_lms_student_journey.js`

**C) Locust** (alternative)
- Create `scripts/load/locustfile_lms.py`
- Run: `locust -f scripts/load/locustfile_lms.py --host=$BASE_URL --users 200 --spawn-rate 15 --run-time 30m`

Implement B or C if built-in UI does not cover LMS diagnostic + learning chat + quiz.

---

## STEP 4 — Student journey (each virtual user)

Execute sequentially with 2–5 second think time between steps.

### 4.1 Login
```
POST /auth/login
Content-Type: application/x-www-form-urlencoded
Accept: application/json

useremail=loadtest_student_{N}@test.iqbalai.local&password={STUDENT_PASSWORD}
```
Save session cookie for all following requests.

### 4.2 Dashboard
```
GET /api/lms/students/me/dashboard
GET /api/lms/students/me/onboarding-status
```

### 4.3 Diagnostic — get & start
```
GET  /api/lms/diagnostics/default
POST /api/lms/quizzes/{diagnostic_id}/start
     Body: {}
```
Expect 201 + `attempt_id` + timer fields (`remaining_seconds`).

### 4.4 Diagnostic — answer all questions
```
GET  /api/lms/attempts/{attempt_id}/questions
GET  /api/lms/attempts/{attempt_id}/timer    # poll every 5s during test
POST /api/lms/attempts/{attempt_id}/answer
     Body: {"question_id": X, "selected_option_index": Y}
```
Realistic delay: 3–15 seconds per question.

### 4.5 Diagnostic — submit
```
POST /api/lms/attempts/{attempt_id}/submit
```
Verify one-time rule: second start → must fail with "already completed".

### 4.6 Learning Chat (post-diagnostic)
```
POST /api/lms/deficiency/sessions
     Body: {"force_new": true}
GET  /api/lms/deficiency/sessions/{session_id}
POST /api/lms/deficiency/sessions/{session_id}/answer
     Body: {"selected_option_index": 0}
```
Answer at least 3 questions. Only 20% of users call explain (LLM-heavy):
```
POST /api/lms/deficiency/sessions/{session_id}/explain
     Body: {"message": "Explain this step by step"}
```

### 4.7 Assigned quiz
```
GET  /api/lms/students/me/assignments
POST /api/lms/quizzes/{quiz_id}/start
     Body: {"assignment_id": ID}
GET  /api/lms/attempts/{attempt_id}/questions
POST /api/lms/attempts/{attempt_id}/answer  (each question)
POST /api/lms/attempts/{attempt_id}/submit
```

### 4.8 Logout (optional, 10% of users)
```
GET /auth/logout
```

---

## STEP 5 — Load profile (200 concurrent)

| Phase      | Time   | Users        |
|------------|--------|--------------|
| Warm-up    | 2 min  | 20           |
| Ramp-up    | 2 min  | 20 → 200     |
| Steady     | 20 min | 200          |
| Cool-down  | 4 min  | 200 → 0      |

Never spike 200 logins in 1 second. Ramp gradually.

---

## STEP 6 — Monitor server during test

Every 60 seconds on server:
```bash
docker stats --no-stream
docker-compose logs --tail=30 flask_app1
docker-compose logs --tail=30 celery_worker
```

Watch for: 502/504, OOM, Redis errors, DB connection pool exhausted, Celery queue backlog, Groq/LLM rate limits.

---

## STEP 7 — Pass / Fail criteria

| Check                         | Target    |
|-------------------------------|-----------|
| Login success rate            | ≥ 99%     |
| Diagnostic start success      | ≥ 98%     |
| Diagnostic submit success     | ≥ 95%     |
| Learning chat session start   | ≥ 90%     |
| Quiz complete success         | ≥ 95%     |
| HTTP 5xx overall              | < 1%      |
| Login p95 latency             | < 3 sec   |
| Non-LLM API p95               | < 2 sec   |
| LLM explain p95               | < 15 sec  |
| Server crash / OOM            | 0         |

If error rate > 5% in first 5 minutes → STOP test, capture logs, report root cause.

---

## STEP 8 — Deliverable report

Return this filled in:

```markdown
# Load Test Report — [DATE] — [BASE_URL]

## Summary
- Peak users: 200
- Total requests: X | Success: Y% | Failed: Z%
- Verdict: PASS / FAIL

## Results by scenario
| Scenario          | Success % | p50 | p95 | p99 | Top errors |
|-------------------|-----------|-----|-----|-----|------------|
| Login             |           |     |     |     |            |
| Diagnostic full   |           |     |     |     |            |
| Learning chat     |           |     |     |     |            |
| Assigned quiz     |           |     |     |     |            |

## Server health
- Flask CPU/RAM peak:
- Celery queue depth:
- Errors in logs:

## Bottlenecks & recommendations
1. ...

## Files created
- Script path:
- Raw results:
```

---

## Rules
1. Default to **staging**. Only test production if I explicitly approve.
2. Use load-test accounts only — never real student data.
3. SSH and run commands yourself — do not tell me what to run without doing it.
4. If something is missing (diagnostic, accounts, assignment), create it on staging first.
5. Clean up test data named `LoadTest*` after run if possible.

START NOW: SSH → health check → prerequisites → run 200-user load test → report.
```

---

## Before you paste — fill these 4 values

| Placeholder | Example |
|-------------|---------|
| BASE_URL | `https://staging.iqbalai.com` |
| SSH_HOST | `your-server-ip` |
| SSH_USER | `root` |
| APP_DIR | `/opt/flask-app` |

Also set admin, teacher, and student passwords in the prompt.
