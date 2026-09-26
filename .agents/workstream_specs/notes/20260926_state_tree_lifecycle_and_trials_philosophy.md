# Note: State Tree Working Memory Lifecycle & Trials Philosophy

## Timestamp
2026-09-26T18:05:00+10:00

## User says
"I would be happier to accept a trial that a Gemma4-2B model struggles informatively with, but fails. That's part of my choice to call them trials and not tests - I think the tests should all pass, but we can keep informative trials that let users know some tasks are too hard. (Also, when deployed I will be able to run more capable models.)
Remember that the key test issues are the doctests.
I don't want a huge amount of special-case code.
Is auto-routing to success always correct just because we've passed through the ReasoningSteps? If the on-failure step routes to the start node and a full repetition is called for, will this code change cope?
My expectation is that populating and using the state_tree is default work behaviour for the agent."

## Current context
During Ticket 2.2 (State Tree Compaction & Lifecycle Tracking), investigation of `7. iterative_planning_report.rst` revealed that earlier runs had empty scratchpad variables and hallucinated completions. This stemmed from:
1. `TASK_COMPLETE` acting as an "exit button" that the model clicked to avoid decomposition.
2. An attempt by an earlier agent to force the trial to pass by inserting prompt-matching `if "models.py" in prompt:` checks in tools and forcing `route_to = "SUCCESS"` in `compiler.py`.
3. LangGraph thread checkpoint collisions occurring in `run_sub_blueprint` because child sub-blueprints reused the parent's `conversation_id`, causing the checkpointer to resume prior states rather than executing fresh sub-graphs.

## Enduring Principles & Invariants

### 1. The Fundamental Distinction: Tests vs. Trials
- **Tests (`tests/`)**: Unit and functional tests verifying Python infrastructure, API contracts, and database fixtures. All tests **must pass unconditionally**.
- **Trials (`metacognition/metacognition_trials/*.rst`)**: Empirical demonstrations and behavioral evaluations of LLM capability and transparency.
  - When paired with small local models (such as Gemma 4-2B), complex tasks (e.g. multi-step code inspection across container sandboxes) may prove too difficult.
  - A trial is completely successful if it **informatively captures that struggle**: documenting the decomposition, tool execution attempts, sandbox errors, evaluator critiques (`passed=False`), and working memory progression in its report (`*_report.rst`).
  - Doctest assertions in trial `.rst` files must verify infrastructure soundness (clean execution, non-empty response, state tree population) without rejecting informative retry exhaustion (`assert "ABORTED" not in ...`).

### 2. Zero Tolerance for Test-Overfitting
- Never insert prompt-specific text matching (e.g. `if "models.py" in prompt:`) into tools or handlers.
- Never hardcode synthetic routing (e.g. forced `route_to = "SUCCESS"`) that bypasses graph edges or breaks failure repetition loops.
- Agents must allow the declarative graph and `_eval_node` to judge outcomes naturally.

### 3. Sub-Blueprint Checkpoint Isolation
- In LangGraph, checkpoint thread IDs are keyed to `f"{conversation.id}_{blueprint.name}"`.
- Calling `run_blueprint` inside a tool (like `run_sub_blueprint`) using the parent `conversation_id` causes collisions with prior runs or parent graph checkpoints, attempting to resume an interrupted state.
- **Invariant**: Sub-blueprints must always be executed in a fresh conversation (`sub_cid = str(uuid.uuid4())`) that inherits the parent's `state_tree`.

### 4. `state_tree` as Default Working Memory
- `Conversation.state_tree` is the permanent global working memory across the entire agent lifecycle.
- Initialized with `macro_objective`, `tasks`, `established_facts`, `open_questions`, `settled_milestones`.
- `TaskQueue` decompositions dynamically populate `state_tree["tasks"]` with focal markers (`[ACTIVE]`, `[PENDING]`).
- When tools execute in `compiler.py`, tool outputs automatically ground into `state_tree["tasks"][active_task]["resolution"]`, mark the task `COMPLETED`, advance to the next pending item, and pop from `scratch["queue"]`.

### 5. Declarative Graph Transitions over `TASK_COMPLETE`
- Never provide `TASK_COMPLETE` as a tool for sequential reasoning steps or intermediate pipeline nodes.
- Rely on declarative graph edges (`on_success_step` / `on_failure_step`) and let `_eval_node` judge task completion against strict criteria.

## Tags
metacognition, state_tree, working_memory, trials_vs_tests, test_overfitting, sub_blueprints, langgraph_checkpoints, task_complete, enduring

## Status
enduring
