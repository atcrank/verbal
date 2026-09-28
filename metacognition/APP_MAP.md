# metacognition App Map

## 1. High-Level Architecture
The `metacognition` app manages autonomous agent loops, tool execution, and cognitive blueprints. It acts as the orchestration layer for LangGraph-based workflows, translating database-defined `CognitiveBlueprint`s and `ReasoningStep`s into stateful graphs that process user conversations and background tasks.

## 2. Time and State
* Map updated at 2026-09-25 12:25:00
* on git branch feature/ticket-2.2-working-memory
* with state tree working memory and dynamic focal projections

## 3. Component Directory
* **Models**: `CognitiveBlueprint`: Defines an overarching AI workflow. `ReasoningStep`: A single node in a blueprint, detailing system prompts, LLM parameters, output schemas, sub-blueprint links, and connected tools.
* **Views / API endpoints**: `api.py`: Exposes Django Ninja endpoints for blueprint dispatching, Datastar Server-Sent Events (SSE) streaming, cancellation, and human-in-the-loop tool approvals.
* **Admin**: `admin.py`: Provides UI for modifying `CognitiveBlueprint`, `ReasoningStep`, and `ToolDefinition`.
* **Tasks**: `tasks.py`: Contains `run_blueprint`, `task_run_blueprint_async`, `task_resume_blueprint_async`, `task_compact_conversation_state_trees`, and `task_prune_reasoning_step_variants` orchestrated asynchronously via native `verbal_tasks`. Enforces governance policy checks before graph execution.
* **Services & Governance**: 
  - `governance.py`: Layered tool governance policy engine (WS17). Implements host lockdown level ceilings (`AIR_GAPPED`, `RESTRICTED`, `CONTROLLED`, `DEVELOPMENT`), user/session clearance resolution, strict domain model write allowlists, tool capability classification, and blueprint compatibility evaluation.
  - `middleware.py`: `ToolGovernanceMiddleware` injecting `X-Verbal-Lockdown-Level` headers into all HTTP, API, and streaming responses.
  - `context_processors.py`: `governance_context` exposing lockdown state, badge colors, and user clearance to all templates.
  - `compiler.py`: Translates database models into executable LangGraph `StateGraph` instances. Handles context stripping, dynamic focal state tree projection injection (`include_state_tree`), sub-blueprint isolation bypasses, conditional routing logic, governance prompt directive injection, and compiler-level tool filtering.
  - `tool_executor.py`: Execution gateway enforcing invariant clearance and lockdown checks before tool dispatch.
  - `pruning.py`: Service routines for variant depth capping, intermediate ancestor compression, dead-end leaf pruning, and graph edge rewiring.
  - `state.py`: Defines the central `AgentState` TypedDict contract and reducers (`update_compact_state_tree`).
  - `checkpointer.py`: `DjangoCheckpointer` providing relational checkpoint persistence and human-in-the-loop state resumption.
  - `actions.py`: The executable python functions that run inside the LangGraph nodes (e.g., calling the LLM, parsing tools, evaluating step success, schema action dispatchers).
  - `seed.py`: Idempotent data seeding for all default blueprints, reasoning steps, scheduled tasks, and capability-tagged tools.
  - `meta_tools.py`: The python functions representing tools available to the LLMs (e.g., `get_conversation_metrics`, `fetch_log_details`, `update_conversation_state`), hardened with domain model write allowlists.
* **Other special components**: `metacognition_trials/`: Directory of Sphinx-compatible RST doctests used to simulate and verify multi-turn agent interactions and background jobs.
