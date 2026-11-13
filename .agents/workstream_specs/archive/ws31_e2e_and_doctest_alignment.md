# Workstream 31: E2E Test & Doctest Alignment with Current Harness Architecture

## 1. Objective
Bring the repository's Django E2E test suite (`@tag('e2e')`), unit tests affected by WS27/WS28/WS30 changes, `metacognition/metacognition_trials/`, `demo_ui/demo_ui_trials/`, module doctests, and Sphinx documentation into alignment with the current codebase while preserving strict "golden-path" fidelity (live inference server validation for doctests, zero test-overfitting).

Explicit exclusion: **Do NOT execute `8. proactive_nightmanager.rst`** during test verification due to runtime length, **DO** inspect and apply all relevant fixes (clearance, backup isolation, harness contracts) to it alongside trials `1`–`7` and `9`–`13`.

---

## 2. Architecture & Design

### A. Test Isolation & Inference Model Attribution (`llm_api/models.py`, `llm_api/apps.py`, `llm_api/api.py`, `conftest.py`)
1. **Prevent Host Container Side-Effects in Tests / Fixture Loads**:
   * `manage_hosting_backend` (`post_save` signal on `SystemConfiguration` in `llm_api/models.py`): return early when `kwargs.get("raw")` is `True` (e.g., `loaddata test_data.json`), when `'test' in sys.argv` or `'pytest' in sys.modules`, or when `VERBAL_ROLE` is a client role (`web`, `worker`, `scheduler`).
   * `LazyServiceRegistry._sync_hosting_backend_state` (`llm_api/apps.py`): check `'pytest' in sys.modules` in addition to `'test' in sys.argv` so pytest sessions do not stop/start host Ollama/vLLM containers.
2. **Accurate Proxy Model Attribution**:
   * `openai_chat_completions` (`llm_api/api.py`): return `"model"` in OpenAI chat completion JSON responses (resolved from the inference server's active backend model via helper `_resolve_active_model_identifier(payload_model)`, covering structured and unstructured completions) so proxy `PromptResponseLog` rows and trial reports show the real active model rather than `"Unknown"`.
3. **Golden-Path `pytest_sessionstart` Diagnostics (`conftest.py`)**:
   * Retain the strict live-inference-server gate on `INFERENCE_URL` (port `8001` / fallback `8000`) so doctest sessions run against a genuine inference server.
   * When `/api/llm/internal/ping/` returns HTTP `503` (model load failure on inference server), log the server's `error` payload before calling `sys.exit(1)`.

### B. Django Unit & E2E Tests (`metacognition/tests.py`, `llm_api/tests.py`)
1. **`metacognition/tests.py`**:
   * `DeploymentHardeningPhase1Tests.test_prompt_response_log_dag_lineage_and_step1_claim`: align assertions with WS27 (`TURN` vs `TRACE` isolation): `initial_log` becomes the single `TURN` record containing the final response (`"Step 2 Final Advice"`), while Step 1 and Step 2 create chained `TRACE` records (`parent_log` lineage, `reasoning_step_id` set).
   * `MetacognitionE2ELocalProxyTests`: consolidate the duplicate `tearDownClass` definitions into one so both `SystemConfiguration` reset and `_rag_service`/`_grips_service` `.disconnect()` run cleanly on teardown.
2. **`llm_api/tests.py`**:
   * `LlmApiIntegrationTests`: remove premature `cls.ai_service.load_models()` from `setUpClass` (runs before `setUp()` configures `SystemConfiguration`); update `test_generate_response_with_real_rag` prompt to express a genuine research query matching `NLPService.requires_research()` (`"Search the knowledge base for details in the France document regarding its capital."`) so `Deep_Reader` triggers and grounds `"France"`/`"Paris"` in `log.rag_selections`.
   * Add unit tests verifying `manage_hosting_backend` skips container orchestration during `raw=True` (`loaddata`) / pytest mode and `openai_chat_completions` populates `"model"`.

### C. Retire Stale UI Test (`demo_ui/tests_ui.py` & `demo_ui/demo_ui_app.rst`)
* Delete `demo_ui/tests_ui.py` (`DemoUIWalkthroughTestCase`), which silently swallowed chat timeouts, raced DB teardown with `/demo/send/` `ForeignKeyViolation`s, and wrote outside test boundaries into `documents/walkthroughs/`.
* Update `demo_ui/demo_ui_app.rst` Visual Interface Walkthrough to reference the actively maintained `documentation/source/_images/demo_ui/*.png` trial screenshots instead of stale `../../documents/walkthroughs/screenshots/*.png` paths.

### D. Metacognition & Demo UI Doctests
1. **`5. computational_logic.rst`, `6. large_output.rst`, `7. iterative_planning.rst`**:
   * Remove `from conftest import _report_doctest_run` (shadowed by `demo_ui/demo_ui_trials/conftest.py`) and call the injected `report_doctest_run(...)` fixture directly.
   * Grant `test_user` staff/superuser clearance (`is_staff=True, is_superuser=True`) so `python_sandbox` (`TRUSTED` clearance required under WS17 governance) is permitted.
   * Align trials `5` & `6` with `.agents/workstream_specs/notes/20260926_state_tree_lifecycle_and_trials_philosophy.md` (allow informative retry exhaustion rather than asserting `"ABORTED" not in final_response`, while asserting error-free execution, non-empty monologue, and response bounds).
2. **`8. proactive_nightmanager.rst` (static update only — DO NOT RUN)**:
   * Create/ensure a staff+superuser user and pass `user_id=user.id` into `run_blueprint(...)` so `NM_Housekeeping` (`django_shell_script`, `system_janitor`, `database_backup`) is permitted under WS17 governance.
   * Patch `metacognition.meta_tools.database_backup` instead of `django.core.management.call_command` so empty `backups/db_backup_*.json` files are never created during trials and unrelated `call_command` invocations stay unmocked.
3. **`Deep_Reader` & `9. deep_rag_retrieval.rst` (`metacognition/seed.py`, `metacognition/metacognition_trials/9. deep_rag_retrieval.rst`)**:
   * Update `seed_deep_reader` (`is_autonomous=True`, remove premature `Gather Context` `evaluation_criteria=""`) so `Gather Context` can absorb search results across `SELF` turns and call `TASK_COMPLETE` to proceed to `Distill Context` instead of halting early on `on_failure_step=None`.
   * Update blueprint name and prose from `"NM_Deep_Reader"` to `"Deep_Reader"` and run via `run_blueprint(..., user_id=user.id)` so `search_past_conversations` receives `user_id` and `final_response` returns WS27's distilled output.
4. **`1. counting_letters.rst`, `2. design_compute.rst`, `4. modelling_simulation.rst`**:
   * Update stale narrative text referencing `ExecutionPlan`/`StrategicPlan` where `ResearchEvaluation` is used, and ensure `test_user` holds staff/superuser clearance if needed consistently across trials.
5. **`demo_ui/demo_ui_trials/trial_3_multistep_blueprint_and_tools.rst`**:
   * Resolve the conversation workspace directory via `Conversation.objects.get(id=cid).get_workspace_dir()` (`workspaces/conversations/<cid>`) instead of `Path("workspaces") / str(cid)` so generated Python scripts are found on disk for the report.
   * Restore mutated `ReasoningStep` token limit cleanly or keep mutations scoped safely.

### E. Documentation, Test Discovery & Sphinx Rebuild
* **`verbal_tasks/__init__.py` & `deploy/__init__.py`**: add missing package `__init__.py` markers and register `e2e` marker in `pytest.ini` so bare `manage.py test` and `pytest` collect all test modules cleanly without relative-import or `tests` collision errors.
* **`documentation/Makefile`**: update `SPHINXBUILD` default interpreter path from `../../../py313/bin/python` to `../../../py312/bin/python`.
* **`metacognition/metacognition_trials.rst`**: add Trials `10`–`13` and their `*_report` entries to the toctree so all trial documents in `documentation/source/metacognition_trials/` are referenced without Sphinx orphan warnings.
* **`documentation/source/tests.rst`** & **`documentation/source/demo_ui_trials.rst`**: remove stale Redis container instruction (replaced by native Django Tasks worker/scheduler) and keep backend model descriptions accurate.

---

## 3. Verification Plan
1. Run `../../py312/bin/python manage.py test --exclude-tag=e2e` across all apps (`425+` tests, expecting 0 failures after `test_prompt_response_log_dag_lineage_and_step1_claim` and new WS31 unit tests pass).
2. With `VERBAL_ROLE=inference` live on `127.0.0.1:8001` (backed by `verbal_ollama`), run `../../py312/bin/python manage.py test --tag=e2e` (`background_resources`, `benchmarking`, `llm_api`, `metacognition`).
3. Run `../../py312/bin/pytest` across module doctests (`--doctest-modules`), metacognition trials `1`–`7` and `9`–`13` (**explicitly excluding `8. proactive_nightmanager.rst`**), and Demo UI trials `1`–`4`.
4. Rebuild Sphinx HTML (`make -C documentation clean html`) and confirm no trial/demo_ui broken image or unreferenced document warnings remain.
5. Inspect diffs with `/code-reviewer` PEP 8 compliance checks before final commit.

