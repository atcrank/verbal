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
