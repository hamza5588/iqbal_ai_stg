# Iqbal AI Load Testing - User Guide

This guide explains the available test scenarios and how to execute performance tests using the Admin Load Testing Dashboard.

## System Architecture & Proxying

- **Hybrid Execution Mode**: The system is designed for maximum flexibility. It automatically uses **Celery** for background test execution if `USE_CELERY_FOR_INGESTION` is enabled in your config. If Celery is unavailable or disabled, it gracefully falls back to standard **Python Threads**, ensuring you can run tests in both production-grade and minimalist dev environments.
- **Unified Proxying**: The dashboard uses a "Fetch Proxy" system. All requests (starting tests, viewing results, etc.) are automatically routed to your **Target Environment** (configured in the Settings tab).
- **Scale Capability**: The system is optimized to support up to **1,000 concurrent users** and **1,000 users per set**.
- **Isolation**: Each simulated user has its own isolated session (cookie jar), mimicking real-world browser behavior.

## Available Test Scenarios

| Test | Name | What it Does | When to Use |
| :--- | :--- | :--- | :--- |
| **1** | **System Access** | Logs in multiple concurrent users (Students or Teachers) and verifies dashboard access. | Basic health check. Verify login system capacity. |
| **2** | **Teacher Flow** | Simulates a teacher creating a conversation, uploading a PDF, chatting, and creating a lesson. | **Critical Path Test**. Validate core product workflow under load. |
| **3** | **Student Chat** | Simulates multiple students chatting with an AI lesson concurrently using CSV messages. | Stress test the chat API and database concurrency. |
| **4** | **Teacher RAG Seq.** | A single teacher performs a long sequence of RAG chats. Waits for EACH response. | Test context window handling and long-running stability. |
| **5** | **Student Lesson Seq.** | A single student has a long, deep conversation about a specific lesson ID. | Test student-side chat persistence and memory usage. |
| **6** | **Repeated Ingest** | Uploads the *same* document repeatedly to stress the vector DB and ingestion pipeline. | Check for memory leaks or vector DB indexing failures. |
| **7** | **Ingest Stress** | Heavy parallel ingestion to stress infrastructure without chat interactions. | Peak load testing for the document processing engine. |
| **8** | **RAG Quality** | Runs "Golden Questions" against a document set to verify AI accuracy and speed. | Regression testing after model updates or prompt changes. |

## How to Run a Test

1.  **Navigate directly to**: `/admin/load-testing`
2.  **Verify Assets**:
    *   **User Sets**: Create sets in the "Assets" tab (up to 1,000 users).
    *   **Document Sets**: Required for Tests 2, 6, 7, 8.
    *   **Message CSVs**: Required for Tests 3, 4, 5, 8.
3.  **Configure Test**:
    *   Select the **Test Type** from the dropdown.
    *   **Base URL**: Ensure this matches your target environment (e.g., `http://localhost:5000`).
    *   **Concurrent Users**: High-scale support up to **1,000**.
4.  **Run**: Click **Start Load Test**. Monitor via the **Live Logs** console.

## Analyzing Reports

Once a test is `Completed`, click **View Report** to access:

### Technical Report
- **Metric Cards**: Real-time aggregation of success rates, RPS, and average file processing times.
- **Primary Checks**: Pass/Fail status for critical workflow milestones.
- **Artifacts**: Download chat transcripts or lesson MDs generated during the test.

### AI Executive Summary
- **Expert Analysis**: Click "AI Executive Summary" to have a virtual performance engineer analyze your JSON data (Requires `GROQ_API_KEY` in settings).
- **Trajectory Charts**: Visual line graphs showing how latency evolved across iterations.
- **Status Distribution**: Breakdown of success vs. failure codes.
