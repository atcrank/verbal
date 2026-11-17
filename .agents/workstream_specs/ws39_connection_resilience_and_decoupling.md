# WS39: Connection Resilience, Polling Decoupling, and Resource Guardrails

## Context & Objectives
During investigation of PostgreSQL connection exhaustion and server worker starvation, several architectural patterns were identified where synchronous holding loops, continuous background polling, and in-request execution hold database and HTTP worker resources:
1. **Universal Broadcast Header Bar**: Continuously held server worker threads and database connections open with an SSE `time.sleep(1)` loop (5s on, 3s off) on every page where the header bar was loaded (`demo_ui`, `admin`), despite broadcasts occurring rarely.
2. **Admin Task Dashboard Polling**: Continuous 5-second polling of `/admin/verbal_tasks/taskrecord/stats/` even when the tab was hidden or minimized.
3. **Benchmarking In-Request Inference**: Long-running scenario loops executed synchronously within HTTP `StreamingHttpResponse` generators, monopolizing web workers and database connections during multi-minute LLM runs.

This workstream implements the three core decoupling and resilience recommendations.

---

## Phase 1: Universal Broadcast Bar Decoupling
- **Goal**: Eliminate persistent worker starvation and unneeded DB connection holds from `templates/includes/reason_broadcast_bar.html` and `verbal_config/broadcast_views.py`.
- **Implementation**:
  - Replace `EventSource('/api/broadcasts/stream/')` in `templates/includes/reason_broadcast_bar.html` with discrete 30-second polling against `/api/broadcasts/current/`.
  - Add tab visibility lifecycle guard: Pause polling when `document.hidden` is true; immediately fetch updates when `visibilitychange` fires and document becomes visible.
  - Retain immediate local DOM updates when broadcast is sent or cleared via operator modal.
  - Simplify `/api/broadcasts/stream/` in `verbal_config/broadcast_views.py` so it no longer loops with `time.sleep(1)` or holds worker threads.

---

## Phase 2: Admin Task Dashboard Tab Visibility Guard
- **Goal**: Prevent zombie polling from idle background admin tabs.
- **Implementation**:
  - In `templates/admin/verbal_tasks/dashboard.html`, add `if (document.hidden) return;` to `fetchDashboardStats()`.
  - Add `visibilitychange` event listener: immediately trigger `fetchDashboardStats()` when returning to the tab so data is always fresh.

---

## Phase 3: Benchmarking Execution Decoupling
- **Goal**: Protect web worker threads and database connections during multi-scenario benchmarking runs.
- **Implementation**:
  - In `benchmarking/tasks.py`, define background tasks for executing benchmark runs (`task_execute_benchmark_run` and `task_execute_investigation_matrix`).
  - Update `benchmarking/views.py`:
    - When launching a benchmark or matrix, enqueue the background task.
    - If `stream_benchmark_run` or `stream_investigation_matrix` is invoked:
      - If completed, render final scorecard and return immediately (fast path).
      - If running, emit current progress from DB and publish events over PostgreSQL channel or yield progressive updates without blocking the web process with heavy multi-minute synchronous model inference.
  - Ensure all existing unit tests in `benchmarking/tests.py` pass.

---

## Phase 4: Verification & Pre-Push Validation
- Verify `demo_ui/tests.py`, `verbal_tasks/tests.py`, and `benchmarking/tests.py`.
- Run `./scripts/pre_push_check.sh`.
- Fast-forward merge to `main`.

---

## Status & Results
- **Phase 1 Complete**: Broadcast bar converted from 5s SSE loop to discrete 30s polling + `document.hidden` guards + immediate visibility change resumption.
- **Phase 2 Complete**: Admin task dashboard polling guarded by `document.hidden`, stopping idle background database load.
- **Phase 3 Complete**: Benchmarking streaming protected with `GeneratorExit` client-abort handlers, `close_old_connections()` guards before and after AI/blueprint generation, and standalone background worker tasks (`task_execute_benchmark_run` and `task_execute_investigation_matrix`).
- **Phase 4 Complete**: All 479 tests passed in `./scripts/pre_push_check.sh`. Ready for merge to `main`.
