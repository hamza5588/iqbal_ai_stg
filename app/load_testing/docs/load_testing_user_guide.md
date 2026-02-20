
# Iqbal AI Load Testing - User Guide

This guide explains the available test scenarios and how to execute performance tests using the Admin Load Testing Dashboard.

## System Architecture & Proxying

- **No Celery Required**: Unlike PDF ingestion, the load testing engine uses standard Python threads for concurrency. You do **not** need to start a Celery worker to run these tests.
- **Unified Proxying**: The dashboard uses a "Fetch Proxy" system. All requests (starting tests, viewing results, etc.) are automatically routed to your **Target Environment** (configured in the Settings tab).
- **Isolation**: Each simulated user has its own isolated session (cookie jar), mimicking real-world browser behavior.

## Available Test Scenarios

| Test | Name | What it Does | When to Use |
| :--- | :--- | :--- | :--- |
| **1** | **Auth & Dashboard** | Logs in multiple concurrent users and verifies they can access the dashboard. | Basic health check. Verify login system capacity. |
| **2** | **Teacher Full Flow** | Simulates a teacher creating a conversation, uploading a PDF, chatting, and creating a lesson. | **Critical Path Test**. Validate the core product workflow under load. |
| **3** | **Student Lesson Chat** | Simulates multiple students chatting with an AI lesson concurrently. | Stress test the chat API and database concurrency. |
| **4** | **Teacher Sequential RAG** | One teacher performs a long sequence of RAG chats in a single thread. | Test context window handling and long-running conversation stability. |
| **5** | **Student Sequential Chat** | One student has a long, deep conversation about a lesson. | Test student-side chat persistence and memory usage. |
| **6** | **Repeated Ingest** | Uploads the *same* document repeatedly (N times) to stress the vector DB and ingestion pipeline. | Check for memory leaks, race conditions, or vector DB failures. |
| **7** | **RAG Quality Benchmark** | Runs a set of documents through the pipeline and verifies the AI response quality/speed. | Regression testing after model updates or prompt changes. |

## How to Run a Test

1.  **Navigate directly to**: `/admin/load-testing`
2.  **Verify Assets**:
    *   **User Sets**: Ensure you have a test user set created in the "Assets" tab.
    *   **Document Sets**: (Required for Tests 2, 4, 6, 7) Ensure you have a set with at least one PDF.
3.  **Configure Test**:
    *   Select the **Test Type** from the dropdown.
    *   **Base URL**: `http://localhost:5000` (Dev) or `https://staging.iqbalai.com` (Staging).
    *   **Concurrent Users**: Number of simultaneous users (e.g., 5, 20, 50).
    *   **Assets**: Select the User Set and Document Set (if applicable).
4.  **Run**: Click **Start Load Test**.
5.  **Monitor**:
    *   Watch the **Live Logs** console for real-time progress.
    *   Wait for the Status Badge to change to `Completed`.
6.  **Results**: Click the **View Report** button to see detailed statistics, error breakdowns, and performance charts.
