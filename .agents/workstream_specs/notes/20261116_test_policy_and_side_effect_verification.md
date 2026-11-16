# Test-Design Policy, Tool Handler Contracts & State-Change Invariant Testing

## Timestamp
2026-11-16 10:35:00 AEST

## Status
Enduring Architectural Standard & Core Testing Contract

---

## 1. Executive Context & Incident Audit

During a production audit of background `TaskRecord`s and worker logs for Grips corpus digestion, a severe latent failure mode was uncovered:
1. **Fatal Import in Registered Action Tool**:
   `metacognition.actions.handle_create_concept_nodes_tool` attempted to import `Document` from `document_storage.models`, a non-existent Django app. This caused `create_concept_nodes_tool` to crash with `ModuleNotFoundError` unconditionally on every single call across 67 tasks.
2. **Evaluator "Silent Pass" Masking**:
   The subsequent evaluation node (`Validate Concept Quality`) evaluated the assistant's prose rather than verifying that the requested database mutation occurred. Because the model produced well-structured Markdown prose with headings in turn 2, the LLM judge reasoned that the user's intent was satisfied despite the tool failure, voting `passed=True`. The task finished as `TaskRecordStatus.SUCCESSFUL` without writing a single `ConceptNode` or `KnowledgeEdge` to PostgreSQL.
3. **Control Flow Dropping Chunks in Tasks**:
   `grips.tasks.task_digest_corpus_level_1` contained an indentation error nesting the entire batching and blueprint enqueueing loop inside `if not chunks and not existing_ids:`. Consequently, whenever Grobid TEI chunking succeeded, the task silently exited with `return_value: None`.
4. **Test Suite Blindspot**:
   The test suite had 449 passing unit tests, but not one test executed `create_concept_nodes_tool` or `task_digest_corpus_level_1`. Tests tested vector math, string helpers, and module-level import smoke checks rather than handler contracts and database mutations.

---

## 2. Core Testing Invariants

### Invariant 1: Mandatory Tool Handler Contract Tests
* **Definition**: Every `ToolDefinition` registered in `metacognition/seed.py` or defined across apps (with a `python_path`) must have dedicated unit tests.
* **Requirements**:
  - Test imports and execution in the active Django environment.
  - Verify parameter schema parsing and validation.
  - Test both successful paths and error paths.
  - Assert that expected database rows are created.

### Invariant 2: State-Change Invariant Testing (Side-Effect Verification)
* **Definition**: For action-oriented tasks, blueprints, and tools, tests must assert physical database state changes, never relying on return codes or status flags.
* **Requirements**:
  - Assert pre- and post-test counts on destination tables (`ConceptNode`, `KnowledgeEdge`, `Document`, `PromptResponseLog`).
  - Example:
    ```python
    initial_nodes = ConceptNode.objects.filter(domain=domain).count()
    handle_create_concept_nodes_tool(state, params)
    self.assertEqual(ConceptNode.objects.filter(domain=domain).count(), initial_nodes + expected_new)
    ```

### Invariant 3: Full Task Execution Over Module Import Smoke Tests
* **Definition**: Importing a task in `tests.py` merely checks syntax. Tasks must be executed directly with real or fixture models.
* **Requirements**:
  - Test all branching logic (e.g. Grobid extraction vs. text fallback).
  - Verify task queue dispatching (`task_run_blueprint_async.enqueue` call counts and arguments).

### Invariant 4: Guardrails Against LLM Evaluator Rationalization
* **Definition**: Small local models (2B–4B) acting as LLM judges frequently rationalize failures away if the assistant's conversational prose looks plausible.
* **Requirements**:
  - Evaluation criteria for steps requiring tool execution must explicitly require that the designated tool executed successfully without errors.
  - Graph routers and compiler logic should treat unhandled tool crashes as step failures rather than allowing assistant conversational prose to bypass missing mutations.
