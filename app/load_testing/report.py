import logging
import json
import statistics
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.load_testing.models import LoadTestResult, LoadTestLog, LoadTestStatus
from app.utils.db import get_session_factory
# from app.services.chat_service import ChatService # If we want to use existing service

logger = logging.getLogger(__name__)

class ReportGenerator:
    """Generates technical and executive reports for load tests"""
    
    def __init__(self, result_id: int):
        self.result_id = result_id
        self._session_factory = get_session_factory()

    def generate_technical_report(self) -> Dict[str, Any]:
        """Aggegregate metrics and logs into a structured report"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if not result:
                return {"error": "Result not found"}
                
            logs = session.query(LoadTestLog).filter_by(result_id=self.result_id).order_by(LoadTestLog.timestamp.asc(), LoadTestLog.id.asc()).all()
            
            # Basic Metrics
            metrics = result.metrics or {}
            total = metrics.get('total_requests', 0)
            success = metrics.get('successful_requests', 0)
            failed = metrics.get('failed_requests', 0)
            success_rate = (success / total * 100) if total > 0 else 0
            
            # Duration
            duration = 0
            if result.completed_at and result.started_at:
                duration = (result.completed_at - result.started_at).total_seconds()
            
            rps = total / duration if duration > 0 else 0
            
            # Error Analysis
            errors = metrics.get('errors', [])
            error_counts = {}
            for e in errors:
                msg = e.get('error', 'Unknown')
                error_counts[msg] = error_counts.get(msg, 0) + 1
            
            # Log Analysis (e.g. processing times from logs)
            processing_times = []
            for log in logs:
                if log.details and 'avg_processing_time' in log.details:
                    # Test 6 stats
                    pass
                if log.details and 'benchmark_data' in log.details:
                    # Test 7 stats
                    pass
            
            report = {
                "test_id": result.id,
                "test_type": result.test_type,
                "status": result.status,
                "timestamp": result.started_at.isoformat() if result.started_at else None,
                "duration_seconds": duration,
                "summary": {
                    "total_requests": total,
                    "success_rate": round(success_rate, 2),
                    "requests_per_second": round(rps, 2),
                    "concurrent_users": result.config.get('concurrent_users', 1) if result.config else 1,
                    "llm_analysis": result.llm_analysis,
                    "llm_analysis_created_at": result.llm_analysis_created_at.isoformat() if result.llm_analysis_created_at else None,
                    # Merge all other metrics from the DB
                    **{k: v for k, v in metrics.items() if k not in ['errors']}
                },
                "errors": error_counts,
                "raw_errors": errors[:100], 
                "detailed_logs": [{
                    "timestamp": l.timestamp.isoformat(),
                    "level": l.level,
                    "message": l.message,
                    "details": l.details
                } for l in logs]
            }
            
            return report
            
        finally:
            session.close()

    # ---------------------------------------------------------------------------
    # Per-test-type context: describes the test's purpose and key success metrics
    # ---------------------------------------------------------------------------
    _TEST_CONTEXT = {
        "multi_user_sign_in": {
            "name": "Test 1 – Multi-User Sign-In (Concurrent Authentication)",
            "purpose": (
                "Validates that the authentication stack (login endpoint, session creation, "
                "and dashboard redirect) remains stable under concurrent load. "
                "Each virtual user logs in, verifies the dashboard is reachable, then logs out."
            ),
            "key_metrics": [
                "Success Rate (target ≥ 95%)",
                "Avg. & P95 login-to-dashboard latency",
                "Successful logouts / total attempted logouts",
                "Rate-limit hits (429 responses)",
                "Error classification (redirect loops, 5xx, content mismatches)",
            ],
            "pass_threshold": "≥ 95% success rate across all three steps (login, dashboard check, logout).",
            "failure_signals": [
                "Redirect back to login page after authentication",
                "Dashboard content mismatch (login page served instead of dashboard)",
                "High rate of 429 responses indicating rate limiting",
                "Session cookie not persisting across requests",
            ],
        },
        "teacher_flow_concurrent": {
            "name": "Test 2 – Multi-Teacher Document Upload Flow (Concurrent)",
            "purpose": (
                "Simulates concurrent teachers performing the full lesson-creation workflow: "
                "create conversation → upload PDF → poll async ingestion → iterative RAG chat → "
                "fetch finalized lesson → save lesson to DB. "
                "Measures end-to-end throughput of the ingestion and RAG pipeline under parallel pressure."
            ),
            "key_metrics": [
                "Per-user ingestion time (seconds from upload to 'success' status)",
                "RAG chat turn latency per message",
                "Ingestion success rate (target ≥ 95%)",
                "Lesson save success rate",
                "Rate-limit hits on /api/rag/ingest and /api/rag/chat",
                "Total file size processed (MB)",
                "Latency trend across messages (degradation pattern)",
            ],
            "pass_threshold": "≥ 95% success across all 6 steps per user; no ingestion timeouts.",
            "failure_signals": [
                # Threshold: if Celery ingest worker is busy or slow, polling may exceed 60s. Tune threshold or add more ingest workers if needed.
                "Ingestion timeout (> 60 s polling without 'success' status)",
                "Chat failures or empty responses after successful ingest",
                "Lesson save failures",
                "Increasing latency trend across sequential chat turns (degradation)",
            ],
        },
        "student_chat_concurrent": {
            "name": "Test 3 – Multi-Student Lesson Chat (Concurrent)",
            "purpose": (
                "Validates that multiple students can simultaneously send questions to the "
                "/api/lessons/ask_question endpoint. Each student iterates through ALL CSV "
                "messages sequentially; students run concurrently. "
                "Measures chat latency consistency and throughput under student-side load."
            ),
            "key_metrics": [
                "Per-message response latency (avg, min, max, trend)",
                "Messages sent vs attempted (success rate)",
                "Rate-limit hits (429)",
                "Latency degradation across sequential turns per student",
                "Empty-response rate (answer field missing or blank)",
            ],
            "pass_threshold": "≥ 95% non-empty successful responses across all students and messages.",
            "failure_signals": [
                "Empty responses (LLM returned no content)",
                "Increasing latency trend suggesting backend saturation",
                "429 rate-limiting across concurrent student sessions",
                "HTTP 5xx errors on the lessons endpoint",
            ],
        },
        "teacher_rag_sequential": {
            "name": "Test 4 – Single Teacher Sequential RAG Stress Test",
            "purpose": (
                "Stress-tests the RAG pipeline for a single teacher by issuing N sequential "
                "chat messages against the same uploaded document. "
                "Measures how latency evolves over repeated turns and whether the RAG context "
                "window degrades or remains stable."
            ),
            "key_metrics": [
                "Per-turn latency trend (is latency increasing, stable, or decreasing?)",
                "Total test duration vs N messages",
                "Success rate per chat turn",
                "Ingestion baseline time (setup phase)",
                "Messages per second throughput",
            ],
            "pass_threshold": "≥ 95% chat success rate; latency should not increase monotonically beyond 2× the first-message baseline.",
            "failure_signals": [
                "Monotonically increasing per-turn latency (RAG context bloat)",
                "Failures mid-sequence after initially passing (context memory exhaustion)",
                "Ingestion failure preventing chat phase from starting",
            ],
        },
        "student_lesson_sequential": {
            "name": "Test 5 – Single Student Sequential Lesson Chat Stress Test",
            "purpose": (
                "Stress-tests the /api/lessons/ask_question endpoint by rapidly firing N "
                "sequential questions from a single student. "
                "Measures latency stability, backend LLM saturation, and any context-depletion effects."
            ),
            "key_metrics": [
                "Per-question latency trend across the full sequence",
                "Success rate (non-empty LLM responses)",
                "Messages per second",
                "Rate-limit hits",
                "Latency stdev (consistency of response time)",
            ],
            "pass_threshold": "≥ 95% non-empty successful responses; latency stdev < 50% of mean.",
            "failure_signals": [
                "Latency climbing beyond 10 s per turn",
                "Empty or truncated LLM responses mid-sequence",
                "429 rate-limiting from the lesson endpoint",
            ],
        },
        "doc_upload_repeat": {
            "name": "Test 6 – Repeated Document Ingestion Consistency (Stress)",
            "purpose": (
                "Uploads the exact same document N times and measures ingestion consistency. "
                "Detects non-deterministic chunking, variable processing times, and vector-store "
                "instability under repeated ingest pressure. "
                "Each iteration also sends a standardised chat prompt to validate response consistency."
            ),
            "key_metrics": [
                "Per-iteration ingestion time (trend across N iterations)",
                "Ingestion time standard deviation (stdev) — lower is better",
                "Chunk count per iteration (should be identical each time)",
                "Chat response similarity across iterations (semantic consistency)",
                "Total file data ingested (MB)",
            ],
            "pass_threshold": "Chunk count must be identical across all iterations; ingestion time stdev < 20% of mean; ≥ 95% chat success.",
            "failure_signals": [
                "Inconsistent chunk counts across iterations (non-deterministic chunking)",
                "Large ingestion time variance (stdev > 30% of mean)",
                "Chat responses diverging in structure or content across iterations",
                "Gradual ingestion time increase (vector store bloat)",
            ],
        },
        "rag_quality_benchmark": {
            "name": "Test 8 – Multi-File RAG Pipeline Quality Benchmark",
            "purpose": (
                "Benchmarks RAG quality across an entire document set. "
                "For each document: upload → ingest → ask a standardised question → score the "
                "response against target keywords. "
                "Produces a per-document PASS/FAIL quality verdict and an overall consistency score."
            ),
            "key_metrics": [
                "Per-document keyword hit count (quality score)",
                "Per-document ingestion time",
                "Per-document chat response latency",
                "Overall keyword hit rate (total hits / total possible)",
                "Documents with PASS vs FAIL verdicts",
                "Ingestion time consistency (stdev across documents)",
                "Response length distribution (are answers substantive?)",
            ],
            "pass_threshold": "≥ 80% of documents must achieve at least 1 keyword hit; ≥ 95% ingestion success rate.",
            "failure_signals": [
                "Zero keyword hits on a document (no relevant content extracted)",
                "Ingestion timeouts (> 120 s per document)",
                "Large variance in per-document ingestion time (pipeline instability)",
                "Very short responses (< 50 chars) suggesting LLM truncation",
            ],
        },
    }

    def generate_llm_analysis(self, api_key: str) -> str:
        """
        Generate a context-aware executive summary using LLM.
        The system prompt is tailored to the test type so the LLM knows exactly
        what the test measures and which metrics matter most.
        Returns the analysis text.
        """
        tech_report = self.generate_technical_report()

        if "error" in tech_report:
            return f"Cannot generate analysis: {tech_report['error']}"

        report_json = json.dumps(tech_report, indent=2)
        test_type = tech_report.get("test_type", "")
        ctx = self._TEST_CONTEXT.get(test_type, {})

        # --- Build context-aware blocks ---
        test_name    = ctx.get("name", test_type.replace("_", " ").title())
        purpose      = ctx.get("purpose", "A load test scenario.")
        key_metrics  = ctx.get("key_metrics", ["Success Rate", "Response Latency", "Error Rate"])
        pass_thresh  = ctx.get("pass_threshold", "≥ 95% success rate.")
        fail_signals = ctx.get("failure_signals", [])

        key_metrics_str  = "\n".join(f"  - {m}" for m in key_metrics)
        fail_signals_str = "\n".join(f"  - {s}" for s in fail_signals) if fail_signals else "  - Generic HTTP errors"

        system_prompt = f"""You are a Senior Performance Engineer at Iqbal AI reviewing load test results.

**Test:** {test_name}
**Purpose:** {purpose}

**Key metrics to focus on:**
{key_metrics_str}

**Pass threshold:** {pass_thresh}

**Failure signals to watch for:**
{fail_signals_str}

**Rules:**
- CRITICAL: Begin your response DIRECTLY with `# Executive Summary`. Output NO preamble, reasoning, or thinking text before that heading.
- Be concise. Every sentence must cite a specific number from the data.
- Use Markdown tables for KPIs and configuration. Use `##` for main sections, `###` for sub-sections.
- Avoid prose walls — prefer structured lists and tables over paragraphs.
- The data source is a JSON report containing summary metrics, detailed logs, and errors.
"""

        user_prompt = f"""Analyse this load test report and produce a structured executive summary.

```json
{report_json}
```

Output the following sections IN ORDER, starting immediately with `# Executive Summary`:

# Executive Summary
2–3 sentences: what was tested, scale (users/iterations), and overall verdict.

## Configuration
| Parameter | Value |
|---|---|
| Test Type | … |
| Concurrent Users | … |
| Duration | … |
| Environment | … |

## Key Performance Indicators
| Metric | Measured Value | Status |
|---|---|---|
(Include ONLY the {len(key_metrics)} metrics listed above. Status: ✅ Good / ⚠️ Warning / ❌ Critical)

## Performance Trajectory
2–4 bullet points on trends (latency over time, degradation, stability). Cite specific log lines or metric values.

## Bottlenecks & Anomalies
For each issue found:
### [Issue Name]
- **Observed:** exact value / log excerpt
- **Affected:** which users, iterations, or documents
- **Probable cause:** one-line inference

If no issues, state: _No anomalies detected._

## Recommendations
1. (Specific, actionable — reference the exact metric that motivated it)
2. …

## Final Verdict
**PASS ✅** / **FAIL ❌** / **CONDITIONAL PASS ⚠️** — one sentence citing the threshold and the actual measured value.
"""


        try:
            from app.utils.llm_factory import get_chat_model
            from langchain_core.messages import HumanMessage, SystemMessage

            llm = get_chat_model()
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            response = llm.invoke(messages)
            analysis = response.content if hasattr(response, "content") else str(response)
            return analysis

        except Exception as e:
            logger.error(f"Failed to generate LLM analysis: {str(e)}")
            return f"Failed to generate analysis: {str(e)}"

    def save_analysis(self, analysis_text: str):
        """Save the analysis to the DB"""
        session = self._session_factory()
        try:
            result = session.query(LoadTestResult).get(self.result_id)
            if result:
                result.llm_analysis = analysis_text
                result.llm_analysis_created_at = datetime.utcnow()
                session.commit()
        finally:
            session.close()
