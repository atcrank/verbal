# ADR-002: Database-Driven LangGraph Architecture for Small Models

**Context: The Need for an Idiosyncratic Architecture**
Mainstream LangGraph examples often feature a monolithic "Agent Node" dynamically calling tools in a long `while` loop (the ReAct paradigm). Our system, however, relies on smaller, locally-hosted LLMs. We found these models struggle to maintain the strict formatting, tool-choice context, and iterative focus required for prolonged loops. 

The `NightManager`—our orchestrator for the large, difficult autonomous task of reviewing the system and improving it at slack times when user requests are rare—is a prime example of why big tasks must be broken down into discrete, highly-focused execution steps. Conversely, the `grill-me` blueprint, which loops until the user indicates a sufficient level of detail, exemplifies how common and valuable agent skills can be readily implemented.

To achieve reliability, we adopted a database-driven architecture where graph logic is declaratively defined in the Django ORM. The overall concept and motivation behind the seemingly profligate use of nodes is simple: **we achieve higher quality by providing more structured, step-by-step space for the agent to externalize and think.**

---

## 1. Database-Driven Architecture & The Admin Advantage

The system's core orchestration abstractions map directly to Django objects:
- **`CognitiveBlueprint`** maps to a LangGraph `StateGraph`.
- **`ReasoningStep`** maps to LangGraph nodes.
- **`ToolDefinition`**: Represents LangChain tools available to specific `ReasoningStep`s. This includes mapping to specific Python callables (`python_path`) or builtin tool types.
- **`ResponseSchema`**: Defines structured JSON schemas (via Pydantic) that the LLM is forced to output when resolving a node, ensuring predictable down-stream parsing.
- **`AgentState`**: A `TypedDict` defined in the compiler that moves between nodes. It holds the `working_memory` (conversation history), `token_budget_remaining`, `route_to` (for edge decisions), and dynamic `scratch` parameters injected back and forth between the database and the agent during execution.

By storing the graph topology in the database, we achieve a unique benefit: **"Getting a good picture by reading the admin."** While editing graphs via Django Admin inline forms might not be the pinnacle of UX, it provides a centralized, structural view of the entire agent workflow. Developers can visualize, tweak, and monitor tool assignments and prompts without wading through hardcoded Python routing logic or opaque JSON files. It is significantly more manageable than editing nested code dictionaries.

**A Note on the `dynamic_tools` Folder:**
The system includes a `metacognition/dynamic_tools/` directory and a `manage_dynamic_tools` meta-tool. Originally, we designed this to allow the agent to write and execute its own Python directly here to create brand new tools on the fly. While the architecture still supports this capability (by writing scripts and registering them as new `ToolDefinition`s), we currently steer away from autonomous raw Python execution to mitigate the risks of catastrophic side-effects (e.g., rogue database migrations). We prefer providing the agent with robust, parameterized tools, using `dynamic_tools` strictly under tight human supervision when extending agent capabilities.

---

## 2. The Two-Node Structure per ReasoningStep

Every `ReasoningStep` defined in the database compiles into **two distinct connected nodes** in the generated LangGraph:

1. **`_action_node`**: Responsible for context injection, executing the LLM generation, and firing any immediate tool executions requested by the model.
2. **`_eval_node`**: A separate LLM call specifically structured to evaluate the outcome of the `_action_node` against strict `evaluation_criteria`.

This separation is crucial: it prevents the model from conflating the generation of a task with the self-reflection needed to verify its success. Information passes back and forth between the agent, these two nodes, and the database as parameters within the LangGraph `AgentState`.

---

## 3. State Maintenance via `Conversation.state_tree`

Because this architecture spreads execution across many profligate nodes and nested sub-blueprints (like the `NightManager` phases), managing context and continuity is critical. 

The `Conversation.state_tree` acts as the persistent, global working memory. It is maintained as a nested tree that is explicitly injected at each invocation of a sub-blueprint. This ensures that the **active issue or current queued task is strongly emphasized** to the active `ReasoningStep`, allowing the agent to externalize its progress (like checking items off a list) without bloating the immediate context window. 

---

## 4. Parameterized Tool Calls and Graph Connections

Future agent developers should rely on parameterized tool calls and well-conceived graph connections rather than hardcoding special execution paths in Python. 

The `Deep_Reader` (Deep Research) blueprint serves as an example of parameterized tool calling. Rather than giving the agent raw Python shell access to figure out RAG, specific nodes invoke distinct parameterized tools (`search_rag_chunks`, `search_grips_nodes`) and explicitly synthesize the results. Tool usage is explicitly constrained and passed as parameters.

---

## 5. Embracing Graph Loops Over Hardcoding

When processing lists of items (like analyzing multiple benchmarks or unresolved reasoning optimization tasks), do not hardcode the behaviour in a Python `while` loop or try to process the entire batch in one massive prompt. 

Instead, utilize LangGraph's native cyclic routing capabilities. Define edges using the ReasoningStep database fields `on_success_step` / `on_failure_step`, or instruct the agent to route back to the current node (compiling to `route_to="SELF"`). This creates resilient, native queue-processing loops that recover gracefully from single-step failures, maintaining the paradigm of providing a structured space for the agent to think.

---

## 6. Sub-Blueprint Conversation & Checkpoint Isolation

When orchestrating nested or sub-blueprints (e.g., through the `run_sub_blueprint` tool or dynamic sub-graph execution), **never reuse the parent's `conversation_id`**.

LangGraph's checkpointer identifies execution threads via `thread_id = f"{conversation.id}_{blueprint.name}"`. If a sub-blueprint is executed using the parent conversation ID:
1. The checkpointer finds the existing checkpoint from previous invocations or the parent's graph state.
2. Rather than starting a fresh execution from the start node, LangGraph attempts to resume an interrupted state, resulting in premature termination, state cross-contamination, or unexpected loop exits.

**The Architectural Solution:**
When `run_sub_blueprint` is called:
- Create a distinct, isolated `Conversation` record with a fresh UUID (`sub_cid = str(uuid.uuid4())`).
- Copy the parent conversation's current `state_tree` into the child record (`state_tree=parent_tree`).
- Execute `run_blueprint` with `conversation_id=sub_cid`.

This guarantees clean checkpoint isolation in LangGraph while maintaining seamless contextual continuity across the working memory hierarchy.

---

## 7. Working Memory Lifecycle: Semantic `state_tree` Grounding

The `Conversation.state_tree` is not an optional scratchpad or an ad-hoc debugging field; it is the **default working memory across the entire agent lifecycle**.

The working memory lifecycle operates through three automated phases:
1. **Baseline Seeding**: Upon invocation in `tasks.py::run_blueprint`, an initial baseline `state_tree` is seeded if empty, capturing `macro_objective` from the user's prompt alongside placeholders for `tasks`, `established_facts`, `open_questions`, and `settled_milestones`.
2. **Dynamic Task Decomposition**: When a node outputs a structured queue (via `ResponseSchema` using `TaskQueue`), `actions.py::handle_task_queue` dynamically populates `state_tree["tasks"]`, marking the first item `[ACTIVE]` and subsequent items `[PENDING]`.
3. **Automated Tool Output Grounding**: When a tool completes execution in `compiler.py`:
   - The tool's output is automatically grounded into `state_tree["tasks"][active_task]["resolution"]`.
   - The active task is marked `COMPLETED`.
   - The active pointer advances to the next pending task.
   - The completed task is popped from `scratch["queue"]`.
   - The updated `state_tree` is returned in LangGraph's node update dictionary, persisting it into the conversation record and formatting it into subsequent prompt injections (`*Working Memory Map*`).

---

## 8. Empirical Rigor: The Philosophy of Tests vs. Trials

A critical distinction governs verification in this repository:

- **Tests (`tests/`)**: Unit and functional tests that verify code correctness, API contracts, and infrastructure stability. Tests **must pass unconditionally**.
- **Trials (`metacognition_trials/*.rst`)**: Empirical behavioral evaluations of model capability and transparency. 
  - When executed against smaller local language models (like Gemma 4-2B), complex multi-step reasoning tasks may prove too difficult. The model may struggle, fail to locate sandbox files, or exhaust retries.
  - **An informative trial that fails transparently is a successful trial.** The trial report (`*_report.rst`) must honestly document the model's exact decomposition, tool invocations, evaluator critiques, and working memory state progression.
  - **Zero Tolerance for Test-Overfitting**: Developers and agents must never insert special-case code (e.g. `if "models.py" in prompt:`) into tools or force artificial routing (`route_to = "SUCCESS"`) in the compiler to fabricate a pass. Trials evaluate the true state of agent cognition; obscuring failure modes harms system evolution.

