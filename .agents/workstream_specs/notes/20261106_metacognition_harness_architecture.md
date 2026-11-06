# Note: Metacognition Harness Architecture & Output Contract

**Date:** 2026-11-06  
**Status:** In Progress  
**Workstream:** WS27 ([ws27_metacognition_harness_architecture.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/ws27_metacognition_harness_architecture.md))

## Summary & Key Findings
Investigation into raw JSON appearing in `demo_ui` when selecting `Deep_Reader` uncovered three core architectural issues:
1. `final_response` is derived as `monologue[-1]["output"]`, which in any blueprint with `evaluation_criteria` is the judge's `EvaluationResult` verdict, or a raw Pydantic/JSON dump on schema steps.
2. `PromptResponseLog` acts as both the internal execution trace and the public conversation transcript. Step 1 overwrites the placeholder turn, judge logs fork the DAG, and `Conversation.as_messages()` pulls intermediate tool calls and judge verdicts into future conversation history turns.
3. `Deep_Reader` is an intermediate knowledge retriever (`output_kind="CONTEXT"`), not a conversational answer generator (`output_kind="ANSWER"`). The UI dropdown must filter selectable blueprints accordingly.

## Invariants & Design Decisions
- **Evaluation Failure Handling**:
  If evaluation fails and the graph halts (e.g. retries exhausted, or no alternative failure edge), the candidate response is **not** discarded or replaced with a cold error. Instead, `final_response` pairs the candidate output with the evaluator's constructive critique:
  ```markdown
  {candidate_output}

  ---
  > ⚠️ **Evaluation Notice**: The proposed response did not satisfy all verification criteria.
  > **Evaluator Critique**: {evaluator_reasoning}
  ```
  This ensures the user always receives an explanatory, actionable response rather than silence, an error code, or "evaluation: failed".
- **Preservation of Trace Interrogability & RAG Context**:
  Separating `log_kind="TURN"` from `log_kind="TRACE"` is strictly to keep the *LLM context window* clean across conversational turns (`Conversation.as_messages()`).
  - `TRACE` records remain fully accessible in the database and API.
  - In `demo_ui`, `group_conversation_logs_for_display` groups all `TRACE` records into `log.thinking_steps`, preserving the interactive `💭 Thinking Trace` `<details>` accordion with every tool execution, sub-blueprint result, and judge evaluation.
  - Retrieved RAG sources are aggregated into `log.rag_selections`, preserving the user-facing `Attached Context` `<details>` quotation block in the UI.
- `tasks.py::run_blueprint` writes exactly one user-facing `TURN` log for the conversation transcript; intermediate node generations are recorded as `TRACE`.
- `Conversation.as_messages()` queries only `log_kind="TURN"` and resolves `UUID` leaf IDs robustly.
- Blueprints in the chat prompt picker are restricted to `category="REASONING"` and `output_kind="ANSWER"`.

