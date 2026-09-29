# Load Test Report — 2026-09-03 — https://209.23.10.34

Date: 2026-09-03
Environment: STAGING (`diliqbalai`)
Base URL: https://209.23.10.34
Peak concurrent users: 200
Test window: 13:45:28–14:13:51 UTC (28 min 12 s)
Load generator: k6 v2.2.0 on local Windows
TLS: insecureSkipTLSVerify (staging IP cert)
Explain: all 200 VUs (`EXPLAIN_ALL=1`)

## Summary

- Peak users: 200 unique `loadtest_student_*` sessions
- HTTP requests: 21,026 | failed: 62 (0.295%) | 5xx: **0** | client HTTP 429: **0**
- Server crash / OOM: **0** (health stayed `healthy` every 60s sample)
- Groq calls during test: **1,282** (789 ok / 493 fail)
- Groq `RateLimitError` (TPM 250,000): **473**
- Verdict: **FAIL**

**Why FAIL:** The box did not crash, but 200 concurrent students hitting Learning Chat + diagnostic submit exhausted Groq **tokens per minute** (250k TPM on `openai/gpt-oss-120b`). That caused empty chat queues, 15 chat-start failures, and 15 students never reaching the assigned quiz (quiz complete 92.5% vs 95% SLO).

## Results by scenario

| Scenario | Attempts | Success % | p50 | p95 | Top errors |
|---|---:|---:|---:|---:|---|
| Login | 200 | 100% | 935 ms | 1.41 s | none |
| Dashboard | 200 | 100% | 318 ms | 583 ms | none |
| Diagnostic start + submit | 200 / 200 | 100% | 316 ms | 3.34 s | none |
| Learning Chat start | 200 | **92.5%** (185/200) | 315 ms | 16.2 s | 15 starts failed after Groq MCQ gen 429 |
| Learning Chat 3 answers | 200 | **69.0%** (138/200) | 315 ms | 16.2 s | short queues after TPM 429 |
| LLM Explain | 185 | 100% of those who started chat | 2.15 s | 3.83 s | none (after TPM burst) |
| Assigned quiz submit | 200 intended / 185 reached | **92.5%** of 200 | 313 ms | 4.14 s | 15 skipped — chat start returned early |

k6 checks did not abort (HTTP fail rate 0.295% < 5%). Overall `http_req_duration` p95 = 2.78 s, max = 32.7 s (Learning Chat start).

## Groq — per student and per minute

Expected Groq calls **per student request** (from code, not guessed):

| Student HTTP request | Groq calls |
|---|---|
| Login / dashboard / onboarding | 0 |
| Diagnostic start, questions, answers, timer | 0 |
| Diagnostic **submit** | **1** (`weakness_analyzer`) |
| Learning Chat **start** | **1 per weak topic** (`generate_mcqs_from_content`) |
| Learning Chat answer / advance | 0 |
| Learning Chat **explain** | **1** (`tutor_chat`) |
| Assigned quiz start / answer / submit | 0 |

**Measured** (`llm_usage_events.id > 2499`, all 200 load-test students):

| Metric | Value |
|---|---|
| Students that triggered Groq | 200 / 200 |
| Groq calls per student | avg **6.41**, min 2, p50 **7**, p95 **8**, max 8 |
| Total Groq events | 1,282 |
| Success | 789 (61.5%) |
| RateLimitError 429 | **473** (36.9%) |
| BadRequestError (tool_use_failed) | 20 (1.6%) |
| Model | `openai/gpt-oss-120b` on_demand |
| TPM cap that fired | **250,000 tokens / minute** |

### Groq calls per minute (the 200-at-a-time burst)

| UTC minute | Groq calls | OK | Fail | Distinct students |
|---|---:|---:|---:|---:|
| 13:49 | 14 | 14 | 0 | 5 |
| 13:50 | 118 | 114 | 4 | 20 |
| 13:51 | 44 | 43 | 1 | 19 |
| **13:52** | **377** | 238 | **139** | 76 |
| **13:53** | **357** | 159 | **198** | 88 |
| **13:54** | **331** | 180 | **151** | 80 |
| 13:55 | 41 | 41 | 0 | 19 |

**Answer to the Groq RPM question:** Yes. When ~200 students overlapped on diagnostic submit + Learning Chat start, Groq returned `429 rate_limit_exceeded` on **TPM** (not a request-count RPM cap). Limit 250,000 TPM; used ~247k–249k with the next request asking 2k–4k tokens. Retry hints were 75–700 ms — the app does **not** retry those 429s on chat MCQ gen, so the topic is dropped.

Client-visible LMS APIs did **not** return HTTP 429 (k6 `http_429` = 0). Failures were swallowed as warnings and showed up as missing chat questions / failed chat start.

## Server health (sampled every ~60s over SSH)

No container crash, restart, or OOM. `/health` and `/api/lms/health` stayed OK on every sample. Celery queues stayed at 0. Postgres `pg_stat_activity` peaked at 58.

| Service | CPU peak | RAM peak | Notes |
|---|---|---|---|
| flask_app1 | **75.09%** @ 13:52:00Z | **3.879 GiB** / 15.63 GiB (24.8%) | Burst during Groq/chat start |
| postgres | 14.1% @ 13:52 | 236 MiB | Peak 58 connections |
| nginx | 3.2% | 22 MiB | 0 client 502/504 |
| celery_worker | ≤0.3% | 498 MiB | Idle (no PDF jobs) |
| redis | ≤3.2% | 4.7 MiB | Healthy |
| milvus | ~5% | 160 MiB | Healthy |
| Host loadavg | 0.93 peak @ 13:53:55Z | — | Recovered to ~0.01 by 14:01 |

Monitor `STOP_UNHEALTHY` / `crashed=True` was a **false positive**: `docker compose ps --format` braces were eaten by Python. Real compose/`docker stats` showed all six services Up.

## Pass / fail vs SLO

| Check | Target | Result |
|---|---|---|
| Login success | ≥ 99% | 100% PASS |
| Diagnostic start | ≥ 98% | 100% PASS |
| Diagnostic submit | ≥ 95% | 100% PASS |
| Learning chat start | ≥ 90% | 92.5% PASS |
| Learning chat 3 answers | (quality) | 69% WEAK |
| Quiz complete | ≥ 95% | **92.5% FAIL** |
| HTTP 5xx | < 1% | 0% PASS |
| Login p95 | < 3 s | 1.41 s PASS |
| LLM explain p95 | < 15 s | 3.83 s PASS |
| Server crash / OOM | 0 | 0 PASS |
| Groq TPM 429 at 200 concurrent | none | **473 FAIL** |

## Bottlenecks and recommendations

1. **Learning Chat start is live Groq/RAG MCQ generation** (one completion per weak topic). 200 students × ~5 topics ≈ 1,000 Groq calls in ~3 minutes — that is what blew the 250k TPM cap.
2. **No retry / backoff** on Groq 429 in `_build_question_queue` or `weakness_analyzer`. The API then returns a short or empty queue.
3. **k6 (and the product path) blocks quiz** if chat start fails. 15 students never took the assignment.
4. Precompute or cache Learning Chat queues at diagnostic submit (or a Celery job with rate-limited Groq) so `POST /deficiency/sessions` is a DB read.
5. Add server-side retry with `Retry-After` / Groq's "try again in X ms" for TPM 429.
6. Raise Groq TPM or shard models if 200 concurrent chat starts are a real production event.
7. Keep the load generator off the app host (this run did).

## Prerequisites used

- Diagnostic id **44** (published)
- Quiz id **34** `Load Test Quiz` / assignment id **3**
- Class join code `GYPXMDH0`
- 200 accounts `loadtest_student_001`–`200@test.iqbalai.local` (state reset immediately before the run)

## Files

- Script: `scripts/load/k6_lms_student_journey.js`
- Raw k6: `scripts/load/results/k6_summary.json`
- Monitor: `scripts/load/results/docker_stats.csv`, `monitor.log`, `groq_monitor.log`
- Groq SQL: `scripts/load/results/groq_after.txt`, `groq_baseline.json`
- This report: `scripts/load/results/LOAD_TEST_REPORT.md`

## Final verdict

**FAIL**

Server stayed up (no crash, no 5xx, login + diagnostic 100%). At 200 concurrent students, Groq **did** return per-minute token-limit errors. That degraded Learning Chat and pulled assigned-quiz completion below the 95% bar.
