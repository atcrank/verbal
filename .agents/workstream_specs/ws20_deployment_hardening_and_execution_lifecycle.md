# Workstream 20: Deployment Hardening & Cognitive Execution Lifecycle

## Objectives & Scope

This workstream resolves 11 concrete architectural, lineage, resilience, and UI defects discovered during live multi-instance deployment testing. 

The goal is to ensure robust, self-healing execution across distributed instances (web, worker, inference, sandbox) with reliable data lineage in `PromptResponseLog`, predictable error pathways, and real-time user visibility.

---

## Issue Catalog & Mapping

| ID | Issue Observed | Category | Target Phase | Status |
|---|---|---|---|---|
| **#1** | No visual cue of loaded/responsive model, no toggle for external API credentials | UI / Experience | Phase 3 | ✅ Completed |
| **#2** | Multi-step blueprint progress and node layout not visible to user | UI / Experience | Phase 3 | ✅ Completed |
| **#3** | Sandbox offline causes endless 35s retry loop without user error pathway | Resilience | Phase 1 | ✅ Completed |
| **#4** | NF4 inference speed at 24 tps with 0.5s worker yield delay | Performance | Phase 4 (Analysis) | 📋 Documented |
| **#5** | Raw HTML badge `"Dispatched..."` saved in `PromptResponseLog.generated_response` | Data Lineage | Phase 1 | ✅ Completed |
| **#6** | Redundant `TASK_COMPLETE` tool invocation before final synthesis node | Graph Architecture | Phase 2 | ✅ Completed |
| **#7a** | Live Datastar SSE stream drops updates during blueprint execution | Streaming | Phase 2 | ✅ Completed |
| **#7b** | LaTeX math markdown (`$...$`, `$$...$$`) does not render to HTML | UI / Markdown | Phase 3 | ✅ Completed |
| **#8** | Conversation title takes raw prompt snippet instead of state tree `macro_objective` | UX / Data Lineage | Phase 1 | ✅ Completed |
| **#9** | `parent_log` is `None` across sequential nodes in blueprint executions | Data Lineage | Phase 1 | ✅ Completed |
| **#10** | `blueprint` and `reasoning_step` ForeignKeys on `PromptResponseLog` unpopulated | Data Lineage | Phase 1 | ✅ Completed |
| **#11** | `sandbox/Dockerfile` hardcodes base image, breaking environment variable pattern | Configuration | Phase 1 | ✅ Completed |

---

## Detailed Implementation Breakdown

### Phase 1: Quick Structural Fixes (Immediate Execution)

#### Task 1.1: Sandbox Base Image Normalization (Item #11)
- **Problem**: `sandbox/Dockerfile` hardcodes `FROM python:3.13-slim`, while all other containers in `docker-compose.yml` use `${DOCKER_*_IMAGE:-...}`.
- **Implementation**:
  - Update `sandbox/Dockerfile`:
    ```dockerfile
    ARG DOCKER_PYTHON_IMAGE=python:3.13-slim
    FROM ${DOCKER_PYTHON_IMAGE}
    ```
  - Update `docker-compose.yml`:
    ```yaml
    sandbox:
      build:
        context: ./sandbox
        args:
          - DOCKER_PYTHON_IMAGE=${DOCKER_PYTHON_IMAGE:-python:3.13-slim}
          - PIP_INDEX_URL=${PIP_INDEX_URL:-}
          - PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST:-}
    ```

#### Task 1.2: Sandbox Availability Pre-Flight & Fail-Fast Error Pathway (Item #3)
- **Problem**: In `metacognition/actions.py:789`, when `requests.post(sandbox_url)` fails with `RequestException`, it returns `"route_to": "SELF"`. The LLM retries indefinitely (up to 100 steps * 35s timeout = over an hour), locking up with zero feedback.
- **Implementation**:
  - In `metacognition/actions.py` (`_tool_execute_script` and `_execute_sandbox_code`):
    - Catch `requests.exceptions.RequestException` and check container reachability.
    - Route immediately to `"route_to": "FAILURE"` with a clear user-facing explanation:
      `"[EXECUTE_SCRIPT Error] Code execution sandbox is offline (unreachable at {sandbox_url}). Please start the sandbox container via 'docker compose up -d sandbox'."`
    - Do not loop on `"SELF"` when the infrastructure is physically unreachable.

#### Task 1.3: Blueprint Lineage & ForeignKey Population (Items #9 & #10)
- **Problem**: 
  - `PromptResponseLog` has `blueprint` and `reasoning_step` FKs, but `metacognition/compiler.py` never passes `blueprint_id` into `log_kwargs`, and `ai_service.py` never stores `blueprint_id`.
  - Sequential nodes in a graph do not thread `parent_log_id`, creating orphaned, disconnected log records.
- **Implementation**:
  - In `metacognition/compiler.py`:
    - Pass `blueprint_id=blueprint.id` into `log_kwargs` on every step generation.
    - Pass `parent_log_id=state.get("last_log_id")` into `log_kwargs`.
    - Once the step's generation finishes, record the resulting `log.id` back into `state["last_log_id"]`.
  - In `llm_api/ai_service.py` (`_log_prompt_response`):
    - Extract `blueprint_id=log_kwargs.get("blueprint_id")` and pass to `PromptResponseLog.objects.create(..., blueprint_id=blueprint_id)`.
  - In `metacognition/state.py`:
    - Add `last_log_id: Optional[str] = None` to `AgentState`.

#### Task 1.4: Initial PromptResponseLog Lifecycle & Step-1 Claim (Item #5)
- **Problem**: In `demo_ui/views.py`, the initial placeholder log stores raw Datastar HTML markup in `generated_response`. When the async task completes, that initial log was never updated, leaving raw HTML in persistent database records, while subsequent reasoning steps created disconnected logs.
- **Adopted Design**:
  - The first `PromptResponseLog` created upon dispatch should **not** receive the final response at the end. Instead, it must be claimed and populated by the **first `ReasoningStep`** executed by the Blueprint (whether a tool call or `generate_response2` call).
  - Subsequent steps (Steps 2..N) each create their own `PromptResponseLog` chained via `parent_log_id`, with the final step's log naturally containing the primary `final_response`.
  - In `demo_ui`, intermediate step logs are presented in a low-impact, collapsible "thinking process" disclosure as they update and are succeeded, leaving the final answer prominent while preserving full DAG inspection.
- **Implementation**:
  - In `demo_ui/views.py`:
    - Create the initial log with clean placeholder state (`generated_response="[Executing Step 1...]"`, `blueprint_id=int(blueprint_id)`).
    - Pass `initial_log_id=str(log.id)` to `task_run_blueprint_async`.
  - In `metacognition/tasks.py` and `compiler.py`:
    - Initialize `state["last_log_id"] = initial_log_id`.
    - When the first reasoning node executes, it **updates/populates** the existing record (`initial_log_id`) with its `reasoning_step`, `system_prompt`, `user_prompt`, `generated_response` (the Step 1 output), and token metrics.
    - Subsequent steps create child logs with `parent_log_id=state["last_log_id"]`.
  - In `demo_ui/views.py` and `templates/demo_ui/chat_message.html`:
    - Group intermediate blueprint logs under a collapsible thinking disclosure (`<details class="blueprint-thinking-trace">`), displaying the final leaf log as the primary message.


#### Task 1.5: Conversation Title Sync from `macro_objective` (Item #8)
- **Problem**: `conversation.title` is hardcoded to the first 50 chars of the raw prompt.
- **Implementation**:
  - In `metacognition/tasks.py` (`run_blueprint`), when syncing the compacted state tree back to `conversation.state_tree`:
    ```python
    macro_obj = compacted.get("macro_objective")
    if macro_obj and str(macro_obj).strip():
        clean_title = str(macro_obj).strip().split("\n")[0][:80]
        conversation.title = clean_title
        conversation.save(update_fields=['state_tree', 'title'])
    ```

---

### Phase 2: Graph Execution Cleanliness & Stream Reliability

#### Task 2.1: Purge / Deprecate `TASK_COMPLETE` Tool (Item #6)
- **Problem**: Agents are instructed to call a dummy `TASK_COMPLETE` tool with a `final_answer` parameter, which creates redundancy with subsequent synthesis/report nodes.
- **Implementation**:
  - Remove `TASK_COMPLETE` from standard tool manifests.
  - In `metacognition/compiler.py`, configure termination conditions based on graph topology (LangGraph `END` node or reaching the designated synthesis step) without requiring a fake tool invocation.

#### Task 2.2: Reliable SSE Event Streaming (Item #7a)
- **Problem**: `metacognition/api.py:stream_blueprint` is an `async def` generator using async psycopg LISTEN/NOTIFY. Under standard WSGI `runserver`, async generators inside `StreamingHttpResponse` can stall or drop events.
- **Implementation**:
  - Refactor `stream_blueprint` to use synchronous event iteration via `subscribe_pg_events_sync` (matching the battle-tested pattern in `demo_ui.views.stream_generation`).
  - Ensure heartbeat signals (`status: 'keep-alive'`) are emitted periodically if node execution takes >5 seconds.

---

### Phase 3: UI & Experience Enhancements (Completed & Verified)

#### Task 3.1: Active Model Indicator & External Provider Toggle (Item #1) - Completed
- **Delivered**:
  - `metacognition/context_processors.py`: added `active_model_context` providing `active_model_info` and `available_external_models`.
  - `templates/includes/active_model_pill.html`: interactive pill and dropdown embedded in the universal header bar.
  - `demo_ui/views.py`: added `@login_required @require_POST set_active_provider` endpoint for switching between local GPU and external models.
  - `demo_ui/urls.py`: registered `/demo/set_active_provider/`.
  - Verified with `test_set_active_provider_toggle`.

#### Task 3.2: KaTeX Math Markdown Rendering with Vendored Static Assets (Item #7b) - Completed
- **Delivered**:
  - Offline release v0.16.11 vendored to `static/vendor/katex/` (CSS, JS, auto-render, and all fonts) and collected via `collectstatic`.
  - `templates/demo_ui/index.html`: loaded local KaTeX assets and configured auto-render for `$...$` (inline) and `$$...$$` (display) with hooks for `DOMContentLoaded`, `htmx:afterSwap`, and `MutationObserver` on `#chat-history`.
  - Verified with `test_katex_static_assets_available`.

#### Task 3.3: Lightweight Blueprint Progress Node Visualizer & Thinking Trace (Item #2) - Completed
- **Delivered**:
  - `metacognition/visualizer.py`: implemented `build_visualizer_data` and `render_blueprint_visualizer_html` using CSS grid/flexbox with unicode arrows (`➔`), distinct active state (`●`, pulsing blue/amber), success (`✓`, green), failure (`✗`, red), and pending (`○`, gray). Sub-blueprints branch down to their own row (`↳ [Sub-Blueprint Name]`).
  - `static/demo_ui/css/styles.css`: added classes and keyframes for visualizer pills and `<details class="blueprint-thinking-trace">`.
  - `demo_ui/views.py`: added `group_conversation_logs_for_display` to group intermediate blueprint steps inside `<details class="blueprint-thinking-trace">`, displaying the final leaf log as the primary message.
  - `templates/demo_ui/chat_message.html` and `chat_history.html`: rendered the visualizer and collapsible thinking trace.
  - `metacognition/api.py`: updated `stream_blueprint` SSE to patch `#blueprint-visualizer` dynamically across `step_started`, `step_completed`, and `completed`.
  - Verified with `test_visualizer_rendering_and_grouping`.


---

### Phase 4: Inference Performance Analysis (Item #4)

- **Root Cause Analysis**:
  - Gemma-2-4B running at ~24 tokens/sec under PyTorch BitsAndBytes NF4:
    1. **NF4 Dequantization Overhead**: 4-bit weights are dequantized to FP16/BF16 layer-by-layer during PyTorch forward passes without fused memory kernels.
    2. **Worker Rate-Limiting**: `llm_api/ai_service.py:569` injects `time.sleep(0.5)` for worker calls.
  - **Path Forward**: vLLM with PagedAttention or AWQ/GPTQ provides 60–100+ tokens/sec on the same hardware. vLLM container is already defined in `docker-compose.yml` (`services.vllm`). Once GPU setup allows, switching `SystemConfiguration.hosting_backend = 'vllm'` unlocks high-speed throughput.

---

## Verification & Test Plan

1. **Phase 1 Verification**:
   - `docker compose config` validates `DOCKER_PYTHON_IMAGE` variable parsing.
   - Run unit test asserting sandbox failure returns `FAILURE` route rather than `SELF`.
   - Execute a multi-step blueprint and verify via Django shell:
     - `PromptResponseLog.objects.filter(conversation=conv).values('id', 'parent_log_id', 'blueprint_id', 'reasoning_step_id')` are fully populated.
     - `conv.title` matches `state_tree['macro_objective']`.
     - Initial log does not contain raw Datastar HTML strings.
2. **Phase 2 Verification**:
   - Run blueprint over live UI with browser dev tools open to verify continuous SSE stream receipt.
3. **Phase 3 Verification**:
   - Verify model badge displays in header.
   - Post sample message with LaTeX math (`$$\frac{a}{b}$$`) and verify KaTeX renders symbols.
