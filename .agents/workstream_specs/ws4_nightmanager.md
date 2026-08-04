# WS4: NightManager — Autonomous System Maintenance & Self-Evolution

## Goal

Refactor the **NightManager** Blueprint to be fully autonomous using a multi-phase sub-blueprint pattern integrated with **WS7: Conversation State Observer**, **WS1: Blueprint Evolution**, and **Grips Knowledge Management**.

The NightManager operates in four distinct phases:
- **Phase 0: Housekeeping & Resource Management**: Complete deterministic tasks (document ingestions, Grips digestion/linting, database backups, workspace janitor).
- **Phase 1: Deep System Evaluation**: Evaluate conversation logs, benchmark results, RAG/ReadingStrategy efficiency, and Grips entries to discover system weaknesses and form structured insights.
- **Phase 2: System Modifications & Action Generation**: Revise `ReasoningStep` variants (tracing prompt lineage and neighbor step context), adjust `ReadingStrategy` entries, expand/cleanup Grips domains and concepts, and queue benchmarking experiments.
- **Phase 3: Meta-Self Reflection & Blueprint Self-Evolution**: Review overall NightManager execution patterns and propose an updated `NightManager` CognitiveBlueprint with improved `ReasoningSteps` and routing logic. Support rotating heterogeneous LLM models (e.g. Gemma4, Qwen3.6) across runs.

---

## Key Design Principles

1. **Deterministic Housekeeping Exception**: `NM_Housekeeping` is a documented exception where direct, single-step tool execution nodes (`sweep_unprocessed_documents`, `sweep_unlinted_concepts`, `database_backup`, `system_janitor`) are used without separate preliminary planning nodes.
2. **Multi-Step Cognitive Headroom**: For non-housekeeping steps, each evaluation/action phase consists of multiple `ReasoningSteps` to allow the LLM space to reason, formulate questions, and organize ideas before tool calls.
3. **Graph Topology & Edge Routing**:
   - The `ReasoningStep` graph is **directed and NOT strictly acyclic**. Edges (`on_success_step` / `on_failure_step`) can route backwards, form loops, or self-loop (`route_to="SELF"`).
   - **Unconditional Continuation**: Routing both `on_success_step` and `on_failure_step` to the same target node ensures that failing steps do not crash the pipeline; execution proceeds to downstream diagnostic steps.
4. **Parentage & Neighbor Context**: When proposing updated `ReasoningStep` variants, the agent inspects the `parent` FK (prompt lineage) and neighbor steps (preceding/succeeding nodes in the parent blueprint) to prevent regressing to old, inferior wordings.
5. **Context Accumulation**: Intermediate findings and questions are accumulated in `Conversation.state_tree` (and optionally a dedicated Grips domain for NightManager observations) so partial understandings remain visible across long step chains.

---

## Key Files

| File | Role |
|------|------|
| `metacognition/models.py` | `ReasoningStep` and `CognitiveBlueprint` models with parentage lineage (`parent` FK) and graph routing fields (`on_success_step`, `on_failure_step`). |
| `metacognition/seed.py` | Registration of tools and definition of `NightManager` and its phase sub-blueprints (`NM_Housekeeping`, `NM_PerformanceDiagnostics`, `NM_BenchmarkReview`, `NM_ConceptDiscovery`, `NM_SelfImprovement`). |
| `metacognition/compiler.py` | `compile_graph_from_blueprint()` logic building LangGraph nodes and handling nested `sub_blueprint` execution. |
| `metacognition/tasks.py` | `run_blueprint()` — core execution function and Celery periodic task entrypoints. |

---

## Pipeline Execution Details

### Phase 0: Deterministic Housekeeping & Resource Management
- **Document Ingestions**: Trigger `sweep_unprocessed_documents()`.
- **Grips Digestion & Linting**: Trigger `sweep_unlinted_concepts()` and `sweep_dirty_edges()`.
- **Database Backup**: Trigger `database_backup`.
- **System Janitor**: Trigger `system_janitor`.

### Phase 1: Deep System Evaluation
1. **Conversations & Blueprints Review**: Inspect `PromptResponseLog` entries for low scoring reasoning steps.
2. **Benchmark Review**: Analyze `get_benchmark_stats()` output for performance regressions.
3. **RAG & Input Efficiency Review**: Evaluate chunking and vector retrieval distances.
4. **Grips Knowledge Bank Review**: Evaluate Grips nodes and edges against findings.

### Phase 2: System Modifications & Action Generation
1. **ReasoningStep Variant Optimization**: Create improved variants, populating `parent` FK and setting `is_pending_review=True`.
2. **ReadingStrategy Refinement**: Propose additions/deletions for RAG reading strategies.
3. **Grips Expansion & Cleanup**: Add new concepts, links, or domains to Grips.
4. **Benchmarking Experiments**: Create new benchmark scenario definitions.

### Phase 3: Meta-Self Reflection & Blueprint Evolution
1. **Performance Review**: Evaluate the NightManager's own prompt choices and tool usage.
2. **Blueprint Self-Update**: Propose updated `NightManager` blueprint definitions for human review or autonomous adoption.

---

## Verification & Testing

- Run trial doctest: `pytest metacognition/metacognition_trials/8.\ proactive_nightmanager.rst -v`
- Run unit tests: `pytest metacognition/tests.py -v`
