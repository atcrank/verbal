# Workstream 35: Test Hardening, Task Execution Contracts, Sandbox Resilience & Async Whiteboard

**Status**: In Progress  
**Branch**: `feat/ws35-test-hardening-and-sandbox-resilience`  
**Context**: Following the Grips corpus ingestion audit (WS34) and cross-app test architecture review, this workstream addresses systemic test gaps, executes uncalled `@task` wrappers, expands AST security boundaries, covers untested Demo UI views, and introduces async task capability to Work Organisation clustering.

---

## 1. Problem Statement & Audit Findings

1. **Task Wrapper Execution Blindspots**:
   - `metacognition.tasks.task_run_blueprint_async` is mocked in all tests. The wrapper logic (including conversation title generation via `maybe_refine_conversation_title`) is never executed in automated tests.
   - `background_resources.tasks.task_process_documents` and `task_process_reading_strategies` are mocked or imported without invocation. Downstream RAG chunk and vector creation is untested at the task layer.
2. **Tool Handler Contract Deficits**:
   - `handle_edge_lint_tool` and `handle_node_lint_tool` in `metacognition/actions.py` have zero unit or contract tests.
3. **Sandbox Security & Visitor Completeness**:
   - `SecurityASTVisitor` blocks standard network libraries (`requests`, `socket`, `urllib`), but modern Python libraries (`httpx`, `aiohttp`, `urllib3`) should also be explicitly blocked in static analysis.
   - Sandbox tests skip silently when port 8002 is not reachable.
4. **Untested Demo UI Views**:
   - Four routes in `demo_ui/urls.py` lack test coverage: `search_knowledge_base`, `grips_explorer_tab`, `grips_concept_children`, and `fill_grips_stub`.
5. **Work Organisation Synchronous LLM Bottlenecks**:
   - `cluster_whiteboard_cards` executes synchronously inside the HTTP POST endpoint `/api/whiteboard/cluster_ideas/`. On large brainstorming sessions, this risks worker thread starvation and gateway timeouts.
   - Clustering lacks tests for handling hallucinated/missing card IDs from LLM outputs.

---

## 2. Implementation Phases

### Phase 1: Task Execution & Wrapper Smoke Coverage
- [ ] Add `test_task_run_blueprint_async_execution` in `metacognition/tests.py` testing `.func(...)` with valid state, asserting that title refinement and blueprint execution complete without unhandled exceptions.
- [ ] Add contract tests for `handle_edge_lint_tool` and `handle_node_lint_tool` in `metacognition/tests.py` (`GripsActionToolsContractTests`).
- [ ] Add `test_task_process_documents_func` and `test_task_process_reading_strategies_func` in `background_resources/tests.py` testing direct task invocation and verifying that `RAGChunk` and `StrategyChunkUsage` database records are created.

### Phase 2: Sandbox Security Hardening & Validation
- [ ] Update `SecurityASTVisitor.FORBIDDEN_MODULES` in `metacognition/meta_tools.py` to include `httpx`, `aiohttp`, `urllib3`, and `ftplib`.
- [ ] Add unit tests in `sandbox_manager/tests.py` verifying that AST visitor rejects imports of `httpx`, `aiohttp`, `urllib3`, and `ftplib`.
- [ ] Ensure `django_shell_script` handles sandbox timeouts and errors cleanly with comprehensive test coverage.

### Phase 3: Demo UI & Explorer Test Coverage
- [ ] Create `DemoUIExplorerAndSearchTests` in `demo_ui/tests.py`:
  - `test_search_knowledge_base_empty_query`
  - `test_search_knowledge_base_with_results`
  - `test_grips_explorer_tab_hierarchy_view`
  - `test_grips_explorer_tab_search_view`
  - `test_grips_concept_children_view`
  - `test_fill_grips_stub_sync`
  - `test_fill_grips_stub_unauthenticated_redirect`
- [ ] Add error boundary test in `demo_ui/tests.py` testing graceful SSE error handling if LLM generation raises mid-stream.

### Phase 4: Work Organisation Async Architecture & Robustness
- [ ] Introduce `@task` `task_cluster_whiteboard_cards` in `work_organisation/tasks.py` to allow background worker processing of whiteboard idea clustering.
- [ ] Ensure backward compatibility: `/api/whiteboard/cluster_ideas/` supports both immediate execution (preserving existing frontend contracts) and background dispatching via Django Tasks.
- [ ] Add unit tests in `work_organisation/tests.py` for:
  - Error recovery when LLM returns hallucinated/invalid card IDs.
  - Background task invocation (`task_cluster_whiteboard_cards.func(...)`).
  - Datastar SSE event broadcasts upon cluster completion.

---

## 3. Verification Criteria
- `./scripts/pre_push_check.sh` passes 100% (all unit tests, migrations check, deployment check).
- Zero database migrations needed (or properly created with `makemigrations` if new fields added).
- PEP 8 compliance across all modified files.
