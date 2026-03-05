# Implementation Plan - Fix Load Test 'Stop' Functionality

The 'Stop' button currently suffers from two issues:
1. **Busy Loop**: The background stop signal checker polls the database without a delay, potentially pinning the CPU.
2. **Graceful but Slow**: The runner waits for all workers to return naturally. If a worker is stuck in a long HTTP request (e.g., 30s LLM response), it won't see the stop signal until that request finishes.

## Proposed Changes

### [Component] Load Testing Runner
#### [MODIFY] [runner.py](file:///Users/abdurrehman/Documents/GitHub/iqbal_ai_stg/app/load_testing/runner.py)
- **Stop Signal Polling**: Add `await asyncio.sleep(2)` to `_check_stop_signal` to prevent high CPU usage.
- **Task Cancellation**:
    - Store worker tasks as a list.
    - Use `asyncio.wait` with a timeout or a periodic check for `self.stop_requested`.
    - If `self.stop_requested` becomes true, explicitly `cancel()` all worker tasks.
- **Graceful Cleanup**: Add `try...except asyncio.CancelledError` in `_worker` to ensure clean exit and logging when a task is cancelled.

### [Component] System-Wide Safety Optimizations
- **Global Request Timeouts (High-Tolerance)**:
    - Modify `runner.py` to initialize `aiohttp.ClientSession` with a `ClientTimeout`.
    - **Values**: `total=300` seconds (5 mins), `connect=10` seconds.
    - **Rationale**: Protects long RAG/Ingestion tasks while catching truly dead connections.
- **Polling Standardization**:
    - Audit all `while` loops in `app/load_testing/scenarios/*.py`.
    - Ensure `await asyncio.sleep(2)` is present to prevent CPU pinning.

### [Component] UI Scaling
- **Increased Limits**:
    - Update `load_testing.html` to allow up to **1000** users in "Create User Set".
    - Update `load_testing.html` to allow up to **1000** "Concurrent Users" in test execution.

### [Component] Data Residue & Cleanup
- **Selective Purging (No Leaks)**:
    - In `load_test_routes.py`, update `delete_result(test_id)`:
        1. Fetch result and extract `thread_id`s from `metrics['artifacts']`.
        2. **Safety Check**: Only purge `thread_id`s that follow the `user_[id]_conv_[id]` pattern and were specifically created for this test run.
        3. Delete matching files in `markdown_exports/`.
        4. Delete matching `RAGThread` entries in the database.
- **UI Interaction (Explicit Warnings)**:
    - Update `load_testing.html`:
        - Change individual delete confirmation: "Deleting this result will also purge its extracted text files and RAG history. Continue?"
        - Change "Delete All" confirmation: "This will permanently remove ALL test results, logs, and associated files/history. Continue?"

## Verification Plan

### Automated Verification
1. Start a long-running test (e.g., Test 7 with 60 iterations).
2. Click "Stop".
3. Verify that logs immediately show "Stop signal detected" and all workers terminate within ~2-3 seconds, rather than waiting for their current polling/chat step to finish.

### Manual Verification
- Check server CPU usage during a run to ensure `_check_stop_signal` is no longer a busy loop.
