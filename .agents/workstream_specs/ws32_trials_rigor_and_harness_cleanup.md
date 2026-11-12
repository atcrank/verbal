# Workstream 32: Trials Scientific Rigor, Harness Cleanup, and `TASK_COMPLETE` Purge

## 1. Objective
Resolve the systemic metacognition harness defects, self-evaluation blind spots, mocked-vs-live mismatches, and fabricated Demo UI assertions uncovered in the WS31 critical audit of all 17 doctest reports (`metacognition/metacognition_trials/*_report.rst` and `demo_ui/demo_ui_trials/*_report.rst`), while preserving the core trials philosophy: trials document what small local models (`Gemma 4 E2B`, etc.) find easy versus hard without test-overfitting or prompt hacks that mask genuine failures.

Explicit exclusion: **Do NOT execute `8. proactive_nightmanager.rst`** during test verification due to runtime length; **DO** apply all relevant static/seeder updates so NightManager aligns with the `TASK_COMPLETE`-free runtime contract.

---

## 2. Architecture & Design

### A. Purge `TASK_COMPLETE` & Unify Tool-Loop Completion (`metacognition/compiler.py`, `actions.py`, `meta_tools.py`, `governance.py`, `seed.py`)
1. **Empty `tool_calls: []` Termination Contract (`compiler.py`)**:
   - Change `tool_calls_schema` in `_make_action_node` from `"minItems": 1` to `"minItems": 0` so LLMs on tool-enabled steps (native tool calling or Outlines fallback) can signal *"no further tool calls needed"* by outputting `{"tool_calls": []}` (or `[]` / clean text without tool calls).
   - Treat `result == []` (or an unstructured assistant final message when `route_to` is not an error) cleanly as `route_to = "SUCCESS"`, passing execution onward to `_make_eval_node` (if `evaluation_criteria` is set) or directly via `on_success_step` instead of raising `ToolCallMissing` (`route_to = "FAILURE"`).
   - Reserve `ToolCallMissing` strictly for unparseable/malformed output when JSON/schema extraction fails (`isinstance(raw_response, str)` with invalid JSON on Outlines tool steps), NOT valid `[]` termination.
   - Retain `pause_for_input` / `NO_TOOL` where appropriate without keeping `TASK_COMPLETE` special-cases or nagging prompts (`"...or call TASK_COMPLETE now"`).
2. **Full `TASK_COMPLETE` Purge**:
   - Remove `TASK_COMPLETE` from [`metacognition/meta_tools.py`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py), [`metacognition/governance.py`](file:///home/crank/coding/antigrav/verbal/metacognition/governance.py), [`metacognition/compiler.py`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py), [`metacognition/actions.py`](file:///home/crank/coding/antigrav/verbal/metacognition/actions.py) (`TaskCompleteAction`, `TaskCompleteArgs`, prompt suffixes), and [`metacognition/seed.py`](file:///home/crank/coding/antigrav/verbal/metacognition/seed.py) (`TOOL_METADATA`, `seed_tools`, all blueprint seeders including `seed_nm_*`, `seed_nightmanager`, `seed_reasoning_with_code_sandbox`, `seed_deep_reader`, `seed_research_evaluation`).
   - Inside `seed_tools` / `seed_all`, clean up any leftover `ToolDefinition(name="TASK_COMPLETE")` row in existing test/dev DBs safely via `.filter(name="TASK_COMPLETE").delete()`.
   - Update [`metacognition/ADR-001-LangGraph-SubBlueprints.md`](file:///home/crank/coding/antigrav/verbal/metacognition/ADR-001-LangGraph-SubBlueprints.md) and replace WS20 `TASK_COMPLETE` backward-compatibility unit tests in [`metacognition/tests.py`](file:///home/crank/coding/antigrav/verbal/metacognition/tests.py) with tests verifying `[]` termination and `eval_node` gating.

### B. Double-Node `ResearchEvaluation` & Missing Schema Handlers (`metacognition/seed.py`, `actions.py`)
1. **Migrate `seed_research_evaluation` to `available_tools` + Double-Node Pattern**:
   - Replace the bespoke `ExecutionPlan` JSON-queue step (`step3`) and passive `ResultCritique` JSON-dump step (`step4`) in [`seed_research_evaluation`](file:///home/crank/coding/antigrav/verbal/metacognition/seed.py) with standard double-node execution:
     - **Step 1 (`Research`)**: Retains `ResearchEvaluation` schema (or optional retrieval before planning) with `handle_research` actually registered in `SCHEMA_ACTION_HANDLERS`.
     - **Step 2 (`Plan`)**: `StrategicPlan` planning node.
     - **Step 3 (`Execute`)**: Tool-enabled `available_tools = [python_sandbox]` step with explicit `evaluation_criteria` requiring actual Python code execution in `python_sandbox` producing verifiable non-empty `stdout` (looping back to `Plan` on `FAILURE` via `on_failure_step`).
     - **Step 4 (`Critique & Final Answer`)**: Synthesizes and critiques real `working_memory` / `[Tool 'python_sandbox' Result]` output (including actual `stdout` and return codes) with `evaluation_criteria` rejecting fabricated numbers, unexecuted code, or missing answers and looping back to `Plan` (`step4.on_failure_step = step2`).
2. **Register Schema Action Handlers (`actions.py`)**:
   - Register `ResearchEvaluation` -> `handle_research`, `DifficultPromptEvaluation` -> `handle_difficult_prompt`, and `ResultCritique` -> `handle_result_critique` in `SCHEMA_ACTION_HANDLERS` (adapting or preserving legacy `ExecutionPlan` compatibility only if callers exist, without `TASK_COMPLETE`) so structured schema nodes execute their side effects and honour `FAILURE` loop-backs rather than acting as passive text formatters.

### C. Harness Safety & Working Memory Integrity (`metacognition/actions.py`, `compiler.py`, `llm_api/state_tree.py`, `conftest.py`)
1. **Reject Empty Sandbox / File Operations (`actions.py`)**:
   - In `python_sandbox`: if `not (code or "").strip()`, return explicit failure (`route_to = "SELF"` and `[SYSTEM: Refusing to execute empty Python script. Write non-empty Python code and re-run.]`) instead of returning `Sandbox Execution Succeeded`.
2. **Stop False Auto-Completion in `State Tree` (`compiler.py`)**:
   - Do **not** mark `state_tree["tasks"][active_tid]["status"] = "COMPLETED"` or advance `active_task` on every arbitrary tool string.
   - Skip auto-completion completely for state-mutation/diagnostic tools (`update_conversation_state`) and whenever tool output indicates failure (`"error"`, `"not found"`, `"aborted"`, `"failed"`, `RC != 0`, `"offline"`). Only auto-advance `active_task` on genuine task-completion / successful sub-blueprint execution when `active_tid` has not already been updated in that turn (max one task transition per turn unless `queue`/`update_conversation_state` explicitly updated tasks).
3. **Preserve Clean `established_facts` Across Sub-Blueprints (`compiler.py`, `llm_api/state_tree.py`)**:
   - In `compiler.py` sub-blueprint result bubbling: normalize structured `Evidence Extractor` findings (`- Finding: ... - Citation: ...`) cleanly into single-line cited facts, and do **not** append negative `"No empirical evidence found in corpus."` outputs into `established_facts`.
   - In [`llm_api/state_tree.py`](file:///home/crank/coding/antigrav/verbal/llm_api/state_tree.py): protect `established_facts` and `invariants` from `fast_compact_state_tree`'s 300-character `_sanitize_string` mid-sentence chopping (`... [truncated]`), using a higher bound (e.g. `max_len=1200`) for factual invariants while keeping tight clamping for noisy fields.
4. **Per-Trial Doctest Workspace Cleanliness & Trace Preservation (`conftest.py`, `demo_ui/demo_ui_trials/helpers.py`)**:
   - Keep `Conversation.get_workspace_dir()` backward-compatible for `llm_api/tests.py` (`workspaces/doctests/metacognition`), while adding a pytest doctest fixture in [`conftest.py`](file:///home/crank/coding/antigrav/verbal/conftest.py) that clears files inside `workspaces/doctests/metacognition` before each `.rst` doctest item runs (so Trial 6's `employees.csv` cannot leak into Trial 7).
   - Ensure `_report_doctest_run` captures per-turn responses and full step traces on multi-turn runs (Trials 3 & 12) instead of overwriting `Final Response` with only the last turn's dict.
   - Fix `capture_ai_response` in [`demo_ui/demo_ui_trials/helpers.py`](file:///home/crank/coding/antigrav/verbal/demo_ui/demo_ui_trials/helpers.py) to strip `.blueprint-thinking-trace` from `.bubble` clones before reading the final user-visible response (eliminating Trial 3's duplicate trace rendering).

### D. Honest Report Scoring & Separate-Conversation Teaching Review (`conftest.py`, `demo_ui/demo_ui_trials/helpers.py`)
1. **Programmatic Execution Facts & Honest Status Headers**:
   - Replace hardcoded `status="PASSED"` defaults and meaningless `"complete attempt: 1/1"` report cards with deterministic checks (`expected_outcome` specs + runtime verification of sandbox `returncode`, non-empty `stdout`, syntax errors, workspace artifacts, and evaluator verdicts).
   - Distinguish **Infrastructure Status** (test harness executed cleanly without internal crashes) from **Model Task Outcome (`PASSED` / `PARTIAL` / `FAILED`)** in report headers and verification output.
2. **Isolated Post-Trial "Expected vs. Observed" Comparison `Conversation`**:
   - Add a shared helper (used by both `_report_doctest_run` and `record_ui_doctest_run` on live non-mocked runs) that creates an isolated `Conversation` using the currently active `AIService` model (marked clearly so it does not pollute user chat/RAG history) and prompts it with:
     - Original trial prompt(s),
     - Authoritative **Expected Results**,
     - **Deterministic Observed Facts** (exit codes, actual `stdout`, generated files, extracted matches, evaluator verdicts), and
     - The trial model's **Observed Final Response**.
   - Render its concise teaching analysis under **`Expected vs. Observed Comparison (Independent Review)`** at the end of `*_report.rst`, highlighting where the model succeeded versus where plausible-looking claims fail against ground truth.

### E. Metacognition Trials `1`–`13` & Mock Segregation (`metacognition/metacognition_trials/*.rst`, `metacognition/tests.py`)
1. **Live Golden-Path Trials `1`–`7` & `9`**:
   - Supply explicit `expected_outcome` specs (`'strawberry'` has `3` `'r'`s; coin-flip D6/D20 analytical mean `7.0` and population std dev `~5.10`; Lotka-Volterra simulated peaks; Influence Diagram `'Launch'` with EU `$25`; 1,000-row numeric CSV + department means; all 3 codebase inspection tasks) and infra assertions without overfitting prompts for `Gemma 4 E2B`.
   - Tighten `9. deep_rag_retrieval.rst` expectations to track `battery`, `cobalt`/`manganese`, and `ecosystems` individually rather than passing silently on a single `or`.
   - Keep `8. proactive_nightmanager.rst` unexecuted while checking static consistency with updated seeders.
2. **Un-Mock Trials `10`–`12` & Segregate Fast Mocked Unit Tests**:
   - Move purely mocked `generate_response2` / `document_reader` schema & sub-blueprint tests into [`metacognition/tests.py`](file:///home/crank/coding/antigrav/verbal/metacognition/tests.py) so fast CI always exercises those contracts offline.
   - Refactor `10. evidence_extractor.rst`, `11. compound_evidence_investigation.rst`, and `12. wild_corpus_evidence_extraction.rst` to seed real `RAGChunk`/`Document` records and run **live** inference against the active model (using lexical DB retrieval in `document_reader` without patching `AIService.generate_response2`).
   - Refactor `13. multimodal_chart_reasoning.rst` to run Steps 1–3 live and mock `inspect_chart_image` / vision response **only** when `get_active_modality_status()["is_multimodal"]` is `False`, labeling `Execution Mode: Hybrid (Live Text / Mocked VLM)` in the report.

### F. Demo UI Trials `trial_1`–`trial_4` Re-Grounding (`demo_ui/demo_ui_trials/*.rst`)
1. **Honest Corpus Counts & Compact Salient Retrieval (`Trial 1` & `Trial 2`)**:
   - Replace stale `"17 domain papers"` wording with dynamic corpus counts (`{total_docs} research papers`) and retrieve focused salient chunks/excerpts for LiDAR/LWIR and Talavera (2023) `Level 0` (`GM25-370`, `5 V 10.800 mAh`, `3.7 V 12.800 mAh`, `Arduino UNO WiFi REV2`, `Raspberry Pi 4`) so small models do not lose key values mid-chunk inside a ~1,100-token wall of text.
   - Remove all fabricated `The Researcher (Evaluation & Decision)` numbers from Trials 1 and 2; clearly label the `3x2` simulation matrix (`25W/75W/110W`, `280W/460W`, `1200Wh` pack / `960Wh` usable) as explicit **Researcher scenario constraints** informed by Talavera's dual-battery design.
2. **Dynamic Verification & Arithmetic Consistency (`Trial 3` & `Trial 4`)**:
   - `Trial 3`: Compute `adversarial_audit` and pass/fail status dynamically from actual sandbox `RC == 0`, syntax-error detection, and parsed output numbers (`188.8`, `162.2`, `147.7`, `118.7`, `107.6`, `101.0` min / `T_op >= 120` filtering). Flag `SyntaxError` / error-help replies honestly (`LOGIC AUDIT FAILED`), optionally run a corrective follow-up turn, and replace static endurance claims in `researcher_evaluation` with actual verified findings + post-trial comparison `Conversation`.
   - `Trial 4`: Reconcile the Emergency Protocol reserve math (`240Wh` reserve floor $\rightarrow$ `Hard shutdown at 240Wh remaining`, removing contradictory `192Wh`), evaluate `cf_response` dynamically against `130.9 minutes` rather than claiming success over a model refusal, and replace hardcoded `"Gemma4"` strings with dynamic `model_name` across `demo_ui_trials`.

---

## 3. Verification Plan
1. `../../py312/bin/python manage.py test --exclude-tag=e2e` — all unit tests pass (including new WS32 runtime & mocked contract tests).
2. `../../py312/bin/python manage.py test --tag=e2e` — E2E suite passes against live inference.
3. `../../py312/bin/pytest` across module doctests, `metacognition/metacognition_trials/1..7,9..13` (explicitly excluding `8. proactive_nightmanager.rst`), and `demo_ui/demo_ui_trials/trial_1..4` — pytest exits cleanly, and close human-style review of every regenerated `*_report.rst` confirms clean isolated workspaces, honest `Model Outcome` verdicts, dynamic Demo UI audits, and sharp separate-`Conversation` Expected-vs-Observed critiques.
4. `make -C documentation clean html` + PEP 8 / code-review audit before commit.

