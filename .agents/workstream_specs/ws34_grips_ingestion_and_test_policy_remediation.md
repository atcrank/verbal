# WS34: Grips Ingestion Pipeline Remediation & Test-Design Policy Augmentation

## 1. Executive Summary & Problem Context
An audit of recent production `TaskRecord`s and worker logs revealed a critical silent failure in the Grips corpus digestion pipeline:
1. **Fatal Import in `metacognition.actions.handle_create_concept_nodes_tool`**:
   Line 897 attempted `from document_storage.models import Document`. The `document_storage` app does not exist (the model belongs to `background_resources.models`), causing the tool to fail unconditionally with `ModuleNotFoundError: No module named 'document_storage'`.
2. **Control Flow / Indentation Bug in `grips.tasks.task_digest_corpus_level_1`**:
   The entire chunk-batching and `task_run_blueprint_async.enqueue` loop was nested inside `if not chunks and not existing_ids:`. Consequently, whenever Grobid TEI chunking succeeded, `task_digest_corpus_level_1` silently exited returning `None`, dropping all document chunks.
3. **LLM Evaluator "Silent Pass" Masking Failure**:
   When `create_concept_nodes_tool` crashed, the LLM generated Markdown prose in turn 2. Step 2's evaluation node (`Validate Concept Quality`) evaluated the prose rather than tool side-effects, rationalized the tool failure away, and marked `passed=True`. The task completed as `TaskRecordStatus.SUCCESSFUL` while zero `ConceptNode`s and zero `KnowledgeEdge`s were written to the database.
4. **Test Suite Blindspot**:
   No unit or integration tests exercised the registered tool handlers or the end-to-end task digestion pipeline. Tests tested module importability and vector arithmetic rather than real task execution and database state mutations.

---

## 2. Workstream Scope & Deliverables

### Phase 1: Augment Testing Policies & Skills Documentation
- Augment `.agents/skills/django_test_evaluator/SKILL.md`:
  - Directives for **Mandatory Tool Handler Contract Tests**.
  - Directives for **State-Change Invariant Testing (Side-Effect Verification)**.
  - Directives for **Full Task Execution over Import Smoke Tests**.
- Augment `.agents/skills/grips_maintainer/SKILL.md`:
  - Directives for testing corpus digestion (Grobid and standard fallbacks) and validating knowledge graph mutations.
- Augment `.agents/skills/metacognition_architect/SKILL.md`:
  - Directives for tool execution gatekeeping and preventing LLM evaluator "Silent Pass" hallucinations.
- Create enduring architectural note: `.agents/workstream_specs/notes/20261116_test_policy_and_side_effect_verification.md` and index in `INDEX.md`.

### Phase 2: Spec-Driven Test Implementation (TDD)
- In `metacognition/tests.py`:
  - `GripsActionToolsContractTests`:
    - `test_handle_create_concept_nodes_tool_success`: verify root node, child nodes, and `INCLUDES` edges are created.
    - `test_handle_create_concept_nodes_tool_missing_params`: assert clean error handling.
    - `test_handle_create_concept_nodes_tool_invalid_domain`: assert domain error handling.
    - `test_handle_evaluate_concept_neighbors_tool_merge`: assert `[MERGE SUGGESTED]` edge.
    - `test_handle_evaluate_concept_neighbors_tool_edge`: assert `RELATED_TO` edge.
    - `test_handle_evaluate_concept_neighbors_tool_distinct`: assert no edge created.
    - `test_handle_evaluate_cross_domain_tool`: assert cross-domain edge creation.
- In `grips/tests.py`:
  - `GripsCorpusDigestionIntegrationTests`:
    - `test_task_digest_corpus_standard_chunking`: verifies chunks are created and enqueued.
    - `test_task_digest_corpus_grobid_chunking`: verifies Grobid chunks are NOT dropped and are properly enqueued.
    - `test_task_digest_corpus_empty_chunks`: verifies clean handling when no chunks exist.

### Phase 3: Code Remediation
- In `metacognition/actions.py`:
  - Remove defunct `from document_storage.models import Document` and import `Document` from `background_resources.models`.
  - Ensure all concept nodes and edges are persisted cleanly with proper sanitization.
- In `grips/tasks.py`:
  - Fix indentation and control flow in `task_digest_corpus_level_1` so that both Grobid chunks and standard fallback chunks proceed through chunk hydration, batching, and `task_run_blueprint_async.enqueue`.
- In `metacognition/seed.py`:
  - Augment `DigestDocumentChunk` evaluation criteria to enforce that `create_concept_nodes_tool` must execute successfully without error.

### Phase 4: Verification & Gatekeeping
- Run all new unit tests.
- Run full pre-push check `./scripts/pre_push_check.sh`.
- Land clean commit.
