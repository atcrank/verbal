Metacognition - plans and self-evaluation
=========================================

This app defines and manages the "thinking processes" (Cognitive Blueprints) and agentic workflows that the LLM uses to reason about complex tasks, execute tools in a sandboxed environment, and iteratively evaluate its own results before delivering a final answer.

To see these agentic capabilities in action, including their execution traces and generated files, please review the :ref:`Metacognition Trials & Reports <metacognition_trials_page>`.

App achievements
----------------

* **Agentic Workflows:** Developed a state-machine-driven loop for LLM tool execution, including Git-tracked workspaces and secure Python sandbox integration.
* **Blueprint Architecture:** Established the data models to store predefined cognitive strategies and workflows.
* **Unified Schema Registry:** Successfully consolidated Pydantic ``BaseModel`` definitions and Django-Ninja ``Schema`` classes into a dynamically loaded, migration-safe ``OUTPUT_TYPES`` registry.
* **Moderation Integration:** Created structures for reusable moderation lists and banned concept lemmas to enforce safety across all reasoning blueprints.

App enhancement
---------------

* **Dynamic Workflow Branching:** Enable blueprints to contain conditional logic, allowing the AI to change its reasoning strategy dynamically based on intermediate generated factors.
* **Visual Strategy Builder:** Create a drag-and-drop or node-based interface in the admin to assemble cognitive pipelines (e.g., "Extract -> Reflect -> Critique -> Summarize").
* **Self-Correction Loops:** Implement automated reflection steps where the LLM is forced to review its own draft output against the prompt requirements and rewrite it if necessary.
* **Context-Aware Triggering:** Build a classification router that looks at a user's query and automatically selects the most appropriate Cognitive Blueprint without manual user selection.
* **Agentic Tool Access:** Allow blueprints to grant the LLM access to external tools (calculators, web search, database querying) during its hidden reasoning phase.

Database models
---------------

.. automodule:: metacognition.models
   :members:
   :undoc-members:
   :show-inheritance:

API Reference
-------------

.. automodule:: metacognition.api
   :members:
   :undoc-members:
   :show-inheritance:

Background Tasks
----------------

.. automodule:: metacognition.tasks
   :members:
   :undoc-members:
   :show-inheritance:
