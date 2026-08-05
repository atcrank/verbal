# metacognition App Map

## 1. High-Level Architecture
The `metacognition` app manages autonomous agent loops, tool execution, and cognitive blueprints. It acts as the orchestration layer for LangGraph-based workflows, translating database-defined `CognitiveBlueprint`s and `ReasoningStep`s into stateful graphs that process user conversations and background tasks.

## 2. Time and State
* Map composed by NightManager at 2026-08-01 00:20:00
* on git branch feature/ws4-nightmanager-evolution
* with git hash 1f8b3044f05a71becfd56f4f2d305eff1a898325 (with local uncommitted modifications)

## 3. Component Directory
* **Models**: `CognitiveBlueprint`: Defines an overarching AI workflow. `ReasoningStep`: A single node in a blueprint, detailing system prompts, LLM parameters, and connected tools.  
* **Views / API endpoints**: No direct HTTP views. Triggered entirely via Celery background tasks or signals.
* **Admin**: `admin.py`: Provides UI for modifying `CognitiveBlueprint`, `ReasoningStep`, and `ToolDefinition`.
* **Tasks**: `tasks.py`: Contains `run_blueprint` (main entrypoint for execution) and `night_manager_task` (runs nightly maintenance).
* **Services**: 
  - `compiler.py`: Translates database models into executable LangGraph `StateGraph` instances. Handles context stripping and routing logic.
  - `actions.py`: The executable python functions that run inside the LangGraph nodes (e.g., calling the LLM, parsing tools, evaluating step success).
  - `seed.py`: Idempotent data seeding for all default blueprints and tools (e.g., NightManager, Demo UI Planner).
  - `meta_tools.py`: The python functions representing tools available to the LLMs (e.g., `get_conversation_metrics`, `fetch_log_details`, `update_conversation_state`).
* **Other special components**: `metacognition_trials/`: Directory of Sphinx-compatible RST doctests used to simulate and verify multi-turn agent interactions and background jobs.
