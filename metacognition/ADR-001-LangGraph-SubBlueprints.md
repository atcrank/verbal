# ADR: LangGraph Orchestration & Blueprint Design Principles

**Context:** The system relies on small, locally-hosted LLMs (e.g., Gemma4-2B) to execute complex, multi-step tasks like the proactive NightManager. Initial attempts to build "high-agency" nodes that dynamically planned and executed tasks using the `run_sub_blueprint` tool failed because small models struggle with strict XML tool-calling in long ReAct loops. 

**Decision:** We abandoned dynamic, LLM-driven tool-call orchestration in favor of native, statically-compiled LangGraph orchestration, leveraging the `sub_blueprint` field directly in the compiler.

---

## 1. Abstraction Mapping (Django to LangGraph)

The database models serve as a declarative definition of a LangGraph `StateGraph`. When `compile_graph_from_blueprint` is called, the Django objects are mapped to LangGraph concepts:

- **`CognitiveBlueprint` -> `StateGraph`**: 
  The blueprint represents the entire graph boundary. 
- **`ReasoningStep` -> Graph Node**: 
  Each step is compiled into a discrete python function (`_make_step_node`) that runs as a node in the graph. The node manages its own retry logic (`max_retries`) and calls the LLM.
- **`ToolDefinition` -> LangChain Tools**: 
  Mapped as tools that the LLM can invoke. If the LLM generates a tool call, the `compiler.py` executes it and routes the edge back to `"SELF"` (creating a ReAct loop) until the LLM resolves the step.
- **`sub_blueprint` -> Native Sub-Graph Execution**:
  *(The critical architectural pivot)*. If a `ReasoningStep` has a `sub_blueprint_id`, the compiler **bypasses the LLM entirely**. It synchronously executes the sub-blueprint as a nested graph using `run_blueprint()`. 
- **Graph Edges (`on_success_step` / `on_failure_step`) -> Conditional & Unconditional Routing**:
  The graph uses a custom router (`_make_router`) that reads the `route_to` variable in the `AgentState`. 
  - `route_to = "SUCCESS"` follows the `on_success_step`.
  - `route_to = "FAILURE"` follows the `on_failure_step`.
  - **Cyclic Directed Topology**: Note that the graph is **NOT strictly acyclic**. Edges can route backwards to earlier steps, form loops, or self-loop (`route_to = "SELF"`).
  - **Unconditional Continuation**: Setting `on_success_step == on_failure_step` for a step creates unconditional routing, ensuring downstream steps run regardless of step evaluation pass/fail outcomes.

---

## 2. Guiding Principles for Blueprint Designers

When designing Blueprints (as seen in `seed.py`), always assume the underlying LLM is highly fallible. Do not rely on the LLM to orchestrate its own execution path.

### A. Static Orchestration over Dynamic Agency
Small models cannot reliably maintain the context required to call a tool, read the result, realize they made a mistake, and try a different tool (a long ReAct loop). 
**Principle**: Use the Django relational graph to define the execution path. For example, instead of asking the NightManager to loop through tasks using tools, `seed_nightmanager` defines a strict, sequential pipeline where each step natively invokes a `sub_blueprint`.

### B. Context Isolation & State Accumulation
As pipelines chain together, the `working_memory` (conversation history) grows massively. Small models easily drown in this context.
**Principle**: When a parent blueprint invokes a `sub_blueprint`, pass a focused prompt, but accumulate key findings and open questions in `Conversation.state_tree` (or dedicated Grips domains) so partial understandings remain visible across long step chains.

### C. Micro-Stepping, Single Responsibilities & Housekeeping Exception
Do not ask an LLM to perform multiple discrete actions in one prompt.
**Principle**: Break tasks down to the smallest possible unit. 
* **Step Granularity**: A `ReasoningStep` must be strictly scoped as either:
  1. **Free-Text Contemplation**: Ideation, analytical synthesis, or formulating open questions without tool side-effects.
  2. **Single Tool-Call Action**: Invoking 1 specific tool with a correctly specified `output_schema` (ResponseSchema) or input schema.
* **Multi-Item Looping Subsections**: Tasks touching multiple objects (e.g. revising multiple reasoning steps, updating multiple Grips nodes, processing benchmark scenarios) must NOT attempt full execution in one step. Instead, use a **looping queue pattern** (`Read Next Item from Queue` -> `Execute Action on Item with Output Schema` -> `Update Status & Loop Back`).
* **Housekeeping Exception**: `NM_Housekeeping` is a documented exception where single `ReasoningStep` nodes executing deterministic tools directly (Document Ingestions, Grips Digestion, Database Backup, System Janitor) are clean, appropriate, and preferred.

### D. Fault-Tolerant Edge Routing & Unconditional Progress
Because models are fallible, steps *will* fail (e.g., timing out on `max_retries` because they couldn't format a tool call). 
**Principle**: Always define an `on_failure_step` to prevent the entire blueprint from crashing. For nightly maintenance sweeps, routing `on_failure_step` to the next step (or setting `on_failure_step == on_success_step`) ensures the pipeline degrades gracefully, continues execution, and allows downstream nodes (like `SelfReflection`) to observe and report on failures.

### E. Variant Evolution Lineage & Neighbor Context
When devising new `ReasoningStep` variants:
**Principle**: Inspect both the step's `parent` foreign key (ancestor prompt history) and its neighbor steps (preceding and succeeding steps in the parent blueprint) to preserve prompt harmony and avoid regressing to previously rejected wordings.

### F. Multi-Model Rotation & Heterogeneous Perspectives
Different model architectures (e.g., Gemma4, Qwen3.6, Llama) bring distinct reasoning strengths and failure modes.
**Principle**: Support rotating underlying LLM models across periodic NightManager runs to bring heterogeneous perspectives to system self-reflection and blueprint evolution.

### G. Safe Tool Provisioning & Generic Interfaces
Do not give autonomous agents arbitrary execution environments (like unchecked shell access or unprotected `exec()`) due to risks of catastrophic side-effects (e.g., rogue database migrations).
**Principle**: Prefer generic read/write/discover tools tailored for the ORM (e.g., `discover_django_models`, `write_django_model`), combined with strict AST-level security checks if arbitrary code execution is absolutely necessary.

### H. Idempotent State Tracking
When an agent tracks its own progress using global state (like a `state_tree`), the updates must be idempotent.
**Principle**: Returning errors like "Task already exists" when the agent re-queues a task can stall retry loops and derail execution. Updates to active state maps should silently succeed or overwrite if the intent aligns.

### I. Automatic Sequential Transitions vs. `TASK_COMPLETE`
In strict multi-step sequences (e.g., Fetch -> Contemplate -> Act -> Next Domain), do not provide explicit early-exit tools like `TASK_COMPLETE` to intermediate nodes.
**Principle**: Intermediate nodes with `TASK_COMPLETE` will confuse the LLM, leading to premature graph termination (if called) or retry stalls (if ignored). Rely on LangGraph's automatic `on_success_step` routing for transition, and reserve termination signals strictly for the final leaf nodes of a blueprint loop.
