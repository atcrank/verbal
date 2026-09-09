# Workstream 15: Demo UI Remediation & Real Capability Shakedown

## Objectives & Scope

Remediate the Reason Demo UI trial suite (`demo_ui/demo_ui_trials/`) from an inert, mocked façade into a verified, high-fidelity end-to-end demonstration and testing suite. 

The suite must genuinely exercise and showcase the system's core capabilities in the context of scientific experimentation and study design:
1. **Pytest `live_server` Testing Harness**:
   - Provide an authentic HTTP/WSGI test server running against `test_verbal_db` for Playwright browser interactions.
   - Eliminate static file and CSS asset latency, removing all progressive loading flicker in screenshots and GIFs.
   - Pre-compose and populate conversation histories and sidebars directly from the server.
2. **Real AI & Multi-Document RAG Integration (Trial 1)**:
   - Ingest the full 1.6MB research literature corpus (`firefighting_chunks.json`) from Talavera et al., Li et al., and Penders et al.
   - Formulate a real study-design inquiry, attach RAG context chips, calculate live token estimates, and dispatch through `send_message`.
   - Exercise live inference via `service_registry.ai_service` to generate grounded academic citations.
3. **Parametric Experimentation-Design & Python Sandbox Reasoning (Trial 2)**:
   - Deploy `Reasoning with Code Sandbox` (or `Computational Logic`) to assist a researcher with a back-of-the-envelope mission trade-off calculation (e.g. range vs. payload trade-off curve across battery capacities, sensor draws, and debris mobility).
   - Execute the calculation synchronously via `run_blueprint` / `task_run_blueprint_sync`, invoking the actual Python execution sandbox to compute the factor levels and return verified tables.
   - Render the complete thinking trace, code execution artifacts, and final synthesized guidance in the UI.
4. **Interactive Branching & Autonomous Grips Expansion (Trial 3)**:
   - Exercise conversation branching from an assistant response via `/demo/branch/<log_id>/`.
   - Navigate the Grips Explorer hierarchy over `live_server`.
   - Trigger real autonomous stub filling (`fill_grips_stub`) to elaborate an unelaborated concept node from literature and vector context.
5. **Visual Asset Polish & 8-Second Full-Dashboard Animated GIFs**:
   - Stabilize Playwright capture (`networkidle`, font rendering, selector visibility).
   - Generate full-dashboard (1280x800) animated GIFs with an 8-second display duration per semantic milestone frame.
   - Compile comprehensive Sphinx reports and embed the updated assets into `documentation/source/demo_ui_trials.rst`.

---

## Tasks & Implementation Breakdown

### Phase 1: Core View & Template Enhancements
- **Task 1.1**: Update `demo_ui/views.py::index` to accept optional `conversation_id` query parameter.
  - When present, load the conversation, format logs via `_prepare_log_for_display`, and fetch workspace files.
  - Pass `active_conversation`, `initial_logs`, and `initial_files` into template context.
- **Task 1.2**: Update `templates/demo_ui/index.html`:
  - Pre-render active conversation title in header, pre-render chat messages in `#chat-history`, and set hidden `#current-conversation-id`.
  - Mark active conversation item in the sidebar.
- **Task 1.3**: Wire a synchronous execution option in `send_message` or verify `run_blueprint` execution for doctest environments.
- **Task 1.4**: Ensure `Reasoning with Code Sandbox` blueprint is seeded in `metacognition/seed.py` equipped with `python_sandbox` tool.

### Phase 2: Live Server & Playwright Test Harness
- **Task 2.1**: Create `demo_ui/demo_ui_trials/conftest.py`:
  - Provide a scoped fixture exposing `live_server_url` and `live_server` to doctests.
  - Provide a helper to inject Django session cookies (`sessionid`) into Playwright contexts for instant authentication.
- **Task 2.2**: Update `demo_ui/demo_ui_trials/helpers.py`:
  - Update `take_ui_screenshot(page, name)` to enforce `page.wait_for_load_state('networkidle')`, wait for fonts, and verify DOM element visibility.
  - Update `create_animated_gif` to set default duration to 8000ms (8 seconds) per frame with clean palette quantization.
  - Refactor `record_ui_doctest_run` to include rich step descriptions and experimentation rationales.

### Phase 3: Trial 1 Overhaul (Literature Ingestion & RAG Context Dropping)
- **Task 3.1**: Rewrite `trial_1_ingestion_and_context.rst`:
  - Introduce rich narrative motivating literature review and hypothesis formulation for emergency robotics.
  - Load full `firefighting_chunks.json` fixture and index into vector store.
  - Authenticate and navigate to `f"{live_server_url}/demo/"`.
  - Formulate cross-paper sensor research inquiry and attach RAG context chip; capture **Frame 1** (Prompt with chips and token estimate).
  - Submit query via `send_message` with real RAG retrieval; capture **Frame 2** (Active generation / thinking indicator).
  - Receive context-grounded response with real literature citations; capture **Frame 3** (Grounded response).
  - Assert semantic criteria on generated response.

### Phase 4: Trial 2 Overhaul (Multi-Step Reasoning & Sandboxed Calculation)
- **Task 4.1**: Rewrite `trial_2_multistep_blueprint_and_tools.rst`:
  - Introduce narrative on study design: calculating range vs. payload trade-offs to determine factor levels for a UGV deployment trial.
  - Pose the parametric mission endurance calculation in the UI with `Reasoning with Code Sandbox` selected.
  - Execute the blueprint synchronously through the real LangGraph compiler and Python execution sandbox.
  - Capture intermediate thinking trace and tool execution output; capture **Frame 2** (Thinking trace with sandbox execution).
  - Render final synthesized response with trade-off table and design recommendation in the UI; capture **Frame 3** (Final synthesis).
  - Assert execution metrics, presence of sandbox output, and mathematical consistency.

### Phase 5: Trial 3 Overhaul (Conversation Branching & Grips Expansion)
- **Task 5.1**: Rewrite `trial_3_branching_and_grips_expansion.rst`:
  - Introduce narrative on branching alternative design forks (Heavy UGV with thermal SLAM vs. Swarm mesh relays).
  - Fork conversation branch via `/demo/branch/<log_id>/` and verify isolated conversation state in UI; capture **Frame 2** (Forked branch).
  - Navigate Grips Explorer tab over `live_server`, displaying domain hierarchy.
  - Execute `fill_grips_stub` on an unelaborated concept node, running the background blueprint to synthesize literature and generate narrative + structured claims.
  - Capture **Frame 3** (Elaborated concept node in Grips Explorer).

### Phase 6: Verification, Sphinx Build & Shakedown Catalog
- **Task 6.1**: Run pytest on the full doctest suite (`pytest demo_ui/demo_ui_trials/ -v`).
- **Task 6.2**: Rebuild Sphinx documentation (`cd documentation && make html`) and verify that `demo_ui_trials.rst` renders complete, high-resolution 8-second animated GIFs and clear study-design summaries.
- **Task 6.3**: Maintain honest shakedown notes in `notes/20260904_demo_ui_remediation.md` and update `INDEX.md`. Log any model performance bottlenecks or prompt sensitivities encountered with the local LLM.

---

## Acceptance Criteria

1. **Zero Mocked Facades**: All three trials must invoke real backend subsystems (`service_registry.rag_service`, `service_registry.ai_service`, `metacognition.compiler`, `python_sandbox`, and `grips`).
2. **Visual Fidelity**: Generated screenshots must show fully-styled pages (zero raw Times New Roman or default form controls), fully-populated sidebars (no "Loading..." spinners), and visible chat messages.
3. **Pacing & Polish**: Animated GIFs must loop between 2–3 clear semantic milestone frames with an 8-second viewing window per frame.
4. **Documentation Value**: The doctests and Sphinx reports must read as compelling, informative walk-throughs demonstrating how Reason helps researchers make better experimentation-design decisions.
