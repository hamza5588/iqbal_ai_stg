# Load Testing Scenario Alignment Audit

This report evaluates the technical implementation against the finalized **1-8 scenario definitions**.

## Summary Table

| Test | Request Name | Status | Alignment & Logic |
| :--- | :----------- | :--- | :--- |
| **Test 1** | System Access | ✅ Matches | Includes full Login -> Dashboard -> Logout cycle. |
| **Test 2 & 3** | Teacher's Heart | ✅ Matches | Implements the Critical Path (Ingest -> Poll -> Chat -> Save). |
| **Test 4** | Virtual Classroom | ✅ Matches | Concurrent student questions against a specific `lesson_id`. |
| **Test 5** | Deep Research | ✅ Matches | Stress test for long-context sequential Teacher chat. |
| **Test 6** | Intense Study | ✅ Matches | Stress test for long-form sequential Student chat. |
| **Test 7** | Ingest Reliability | ✅ Matches | Repeated uploads of the same document for stability metrics. |
| **Test 8** | IQ Benchmark | ✅ Matches | RAG quality check with keyword hit scoring. |

---

## Detailed Alignment Verification

### 1. Test 1 (System Access) - Logout & Cookies
- **Status**: ✅ **Aligned**. `test_auth.py` now includes a `/logout` request. `runner.py` explicitly verifies session cookie generation during login.

### 2. Test 2 & 3 (Teacher's Heart) - Critical Path
- **Status**: ✅ **Aligned**. `test_teacher_flow.py` handles the full sequence and supports CSV-based messaging for the chat phase.

### 3. Test 8 (IQ Benchmark) - Quality Scoring
- **Status**: ✅ **Aligned**. `test_rag_pipeline_quality.py` now performs automated keyword hit analysis to quantify response relevance.

---

## Technical Consolidation
The "8 Scenarios" are powered by **7 specialized script files**. The distinction between "Single" and "Concurrent" (e.g., in Test 2 vs 3) is handled dynamically by the **Concurrency** parameter in the UI, ensuring a streamlined codebase while fulfilling all 8 stress-testing goals.
