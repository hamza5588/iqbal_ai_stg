# Performance Insights Playbook

This playbook is designed for stakeholders and non-technical team members to help interpret the results of Iqbal AI load tests and the AI-generated executive summaries.

## 1. The AI Performance Engineer
The "AI Executive Summary" uses a specialized **Performance Engineer** persona. This AI analyst doesn't just look at numbers; it looks for **intent** and **anomalies**.

### Key AI Terminology:
- **Bottleneck**: A point in the system that restricts the flow of data or requests (e.g., slow database queries or LLM rate limits).
- **Degradation**: Performance getting worse over time or as more users join.
- **Anomaly**: Unusual spikes or patterns that don't match the rest of the test data.

## 2. Reading the AI Summary
The summary is divided into actionable sections:
- **Performance KPIs**: Look here for the "Hero Numbers" (Avg. Latency, Max RPS).
- **Bottlenecks**: Pay attention to this section if the system is slowing down under load.
- **Recommendations**: Technical advice on how to improve the current score.
- **Final Verdict**: A strict **PASS/FAIL** based on a 95% success threshold.

## 3. Understanding the Charts

### Latency Trajectory (Line Graph)
This chart shows how processing time (in seconds) changes across iterations.
- **Flat Line (Ideal)**: Shows a stable system where performance remains consistent regardless of time.
- **Increasing Trend (Caution)**: Suggests a "Memory Leak" or that the system is getting tired/clogged as the test progresses.
- **High Variability (Spikes)**: Suggests "Noisy Neighbor" issues or intermittent infrastructure instability.

### Status Distribution (Doughnut Chart)
A visual breakdown of successful vs. failed requests.
- **Green (Success)**: Requests that reached the dashboard or received a valid AI response.
- **Red (Failure)**: Requests that timed out, returned 500 errors, or failed authentication.
- **Goal**: You should aim for a "Green Ring" (100% success).

## 4. Decision Making Matrix

| Indicator | Severity | Action |
| :--- | :--- | :--- |
| **Success Rate < 95%** | 🔴 Critical | **Hold Release**. Investigate 5xx errors in the technical report. |
| **Latency > 15s (Avg)** | 🟡 Warning | **Optimize**. The system is slow; user experience will be poor during peak hours. |
| **Increasing Trajectory** | 🟡 Warning | **Monitor**. The system might crash if the test ran for 2-3x longer. |
| **Pass Verdict** | 🟢 Good | **Proceed**. The system meets current performance benchmarks. |

## 5. Configuring AI Analysis
To generate these reports, ensure the **Groq API Key** is configured in the Admin Settings. Without this key, only the "Technical Report" will be available.

## 6. Staging / Load Test Tuning

When load tests show 504s on login or upload, or chat latency climbing with concurrency, apply these settings:

| Setting | Purpose | Recommended (staging) |
|--------|---------|------------------------|
| **USE_CELERY_FOR_INGESTION** | Run PDF ingestion in background workers; upload returns quickly so nginx doesn’t timeout. | `true` |
| **GROQ_MAX_CONCURRENT_REQUESTS** | Max concurrent Groq API calls (env, default 4). Higher = more parallel chat; lower = fewer 429s. | `4` (tune if you see 429s or want lower latency) |
| **Web workers** | More gunicorn (or app) workers so login and short requests get a worker quickly under load. | Increase workers for concurrent users (e.g. 2× concurrency) |
| **Nginx proxy_read_timeout** | Must be long enough for slow RAG chat and any sync ingestion fallback. | 600s (see `ngnix.stag.conf`) |

See `app/config.py` and `app/utils/rag_service.py` (GroqRateLimiter) for implementation details.
