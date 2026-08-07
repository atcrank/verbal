# Project Tickets

- [ ] [Improvement] Investigate workspace janitor and reuse empty workspaces (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260731_janitor_cleanup.md)
  *Summary*: The workspace janitor function reports deleting folders but leaves many behind. The doctrials should be updated to re-use empty workspaces instead of accumulating folders.

- [ ] [Feature] LoRA Affordances (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260804_unfinished_tasks.md)
  *Summary*: Implement workflows for creating training data, executing dimensioned LoRA training, and dynamically loading the trained adapters into the target LLM.

- [ ] [Feature] Demo UI Doctests (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260804_unfinished_tasks.md)
  *Summary*: Set up and execute Playwright-driven doctests to automate testing of the Demo UI and capture animated screenshots of the features.

- [ ] [Bug/Improvement] LLM API Message Roles (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260804_unfinished_tasks.md)
  *Summary*: Resolve the TODO in llm_api/api.py regarding the system prompt appearing only once and subsequent messages not being typed as system prompts.

- [ ] [Feature] Background Resources Multi-reading (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260804_unfinished_tasks.md)
  *Summary*: Complete the implementation and parameters for multi-reading and sub-chunking strategies noted in background_resources/models.py.

- [ ] [Feature] Grips Automated Curation (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260804_unfinished_tasks.md)
  *Summary*: Follow up on filling GRIPS ConceptNode stubs (Automated Curation via background Celery tasks, referenced in grips_app.rst and seed_grips_stub_filler).

- [ ] [Feature] Conversation logs as additional RAG knowledge-base (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260805_conversation_logs_rag.md)
  *Summary*: Investigate and design a system to index and retrieve past conversation logs as an additional RAG context source.

- [ ] [Bug/Feature] Enforce dynamic tool authorization and add "stop" button (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260806_dynamic_tool_security_and_stop_button.md)
  *Summary*: The LangGraph compiler currently ignores the `requires_approval` flag, allowing unverified dynamic tools to run. We need to halt graph execution when approval is needed, and also add a general "stop" button to interrupt long-running blueprints.

- [ ] [Feature] NightManager archival and cleanup routines (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260806_nightmanager_pruning.md)
  *Summary*: Give the NightManager responsibility to delete old `PromptResponseLogs`, prune `ReasoningStep` variant trees to 3-4 generations max, and delete redundant or noisy `Chunk` records.

- [ ] [Feature] GRIPS testing and structured claims symbolic computation (Status: pending) -> [Link to note](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20260807_grips_testing_and_claims_computation.md)
  *Summary*: Ensure grips test coverage includes realistic e2e tests for generated objects. Design a computational engine to process `structured_claims` objects for truth values, implications, and necessity/sufficiency.
