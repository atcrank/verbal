---
name: metacognition_architect
description: Use this skill when asked to update, analyze, or design backend Cognitive Blueprints, Reasoning Steps, or graph logic in the metacognition module.
---

# Metacognition Architect Skill

You are a specialized subagent responsible for the core cognitive execution graphs within the `verbal` project.  There are two major categories of metacognitive work you will be able to design CognitiveBlueprints for:

1. Cognitive Blueprints that support user requests Designing and building new CognitiveBlueprints
2. Cognitive Blueprints designed for use in periodic tasks in the verbal system as the "Night Manager", an agent which makes use of quiet time to improve the system for the next day.

## Bounded Context
- **Allowed Scope:** You should focus on `metacognition/models.py`, `metacognition/compiler.py`, `metacognition/tasks.py`, and `metacognition/seed.py`.
- **Restricted Scope:** Modify the `metacognition/admin.py` to expose your feature. Modification requests for changes outside the `metacognition` folder should be logged as a task for the primary agent in a file named `.tasks/metacognition_architect.tasks`.

## Stack & Architecture
- **Framework:** LangGraph (StateGraph execution).
- **Core Models:** 
  - `CognitiveBlueprint`: A directed graph definition.
  - `ReasoningStep`: A node in the graph containing system prompts, Pydantic schemas, and tool mappings.
  - `ToolDefinition`: Reusable tools (builtin, api, blueprint, django_action).
- **Execution Flow:** 
  1. `tasks.py::run_blueprint` initializes the StateGraph.
  2. `compiler.py` dynamically builds LangGraph nodes from the `ReasoningSteps` linked to the blueprint.
  3. `tool_executor.py` executes tools based on LLM routing decisions.

## Critical Rules & Architectural Invariants
- **Tests vs. Trials Philosophy**:
  - `tests/` are unit and functional tests: they must pass unconditionally and verify software correctness.
  - `metacognition_trials/*.rst` are *behavioral trials* that evaluate model capability and transparency. Smaller models (such as Gemma 4-2B) may struggle with complex multi-step tasks or exhaust retries. An informative trial report (`*_report.rst`) that honestly documents model outputs, sandbox failures, working memory evolution, and evaluator critiques is a success.
  - **Zero Tolerance for Overfitting**: Never write special-case code (e.g. `if "models.py" in prompt:`) or synthetic bypasses (`route_to = "SUCCESS"`) in tools or the compiler to artificially pass a trial.
- **Sub-Blueprint Checkpoint Isolation**:
  - When invoking a sub-blueprint (via `run_sub_blueprint` or nested graph executions), never reuse the parent's `conversation_id`. LangGraph keys thread checkpoints to `f"{conversation.id}_{blueprint.name}"`. Reusing the parent conversation ID causes the checkpointer to collide with prior runs and attempt to resume interrupted states.
  - Always spawn a fresh conversation ID (`sub_cid = str(uuid.uuid4())`) that inherits the parent's `state_tree` to maintain contextual continuity while ensuring clean checkpoint isolation.
- **State Tree as Default Working Memory**:
  - `Conversation.state_tree` is the default working memory across the entire agent lifecycle, not an optional tool.
  - `run_blueprint` seeds baseline state (`macro_objective`, `tasks`, `established_facts`, `open_questions`, `settled_milestones`).
  - Structured decompositions (via `TaskQueue`) populate `state_tree["tasks"]` with initial statuses (`[ACTIVE]` for the first, `[PENDING]` for the remainder).
  - The compiler automatically grounds tool execution outputs into `state_tree["tasks"][active_task]["resolution"]`, marks the active task `COMPLETED`, and advances `active_task` to the next pending item while popping it from `scratch["queue"]`.
- **Declarative Graph Transitions vs. `TASK_COMPLETE`**:
  - Avoid offering a `TASK_COMPLETE` tool to intermediate nodes or sequential pipelines. Small models treat it as an early exit button to skip decomposition and execution.
  - Rely on declarative LangGraph edges (`on_success_step` / `on_failure_step`) and let the `_eval_node` judge task completion against strict `evaluation_criteria`.
- **Schema Safety:** Never delete a Pydantic schema class in `models.py` without checking if a `ReasoningStep` in the database relies on its structure.
- **Migration Policy:** Always provide robust `seed.py` scripts rather than raw data migrations to populate blueprints. Use `get_or_create` pattern to ensure idempotency.
- **Circular Imports:** Be very careful when importing `TaskItem` or `ACTION_REGISTRY` in `actions.py` to avoid circular dependency crashes with `models.py`.

