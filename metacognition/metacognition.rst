Metacognition
=============

This app defines and manages the "thinking processes" (Cognitive Blueprints) that the LLM uses to reason about complex tasks before delivering a final answer.

App achievements
=================

* **Blueprint Architecture:** Established the data models to store predefined cognitive strategies and workflows.
* **Unified Schema Registry:** Successfully consolidated Pydantic ``BaseModel`` definitions and Django-Ninja ``Schema`` classes into a dynamically loaded, migration-safe ``OUTPUT_TYPES`` registry.
* **Moderation Integration:** Created structures for reusable moderation lists and banned concept lemmas to enforce safety across all reasoning blueprints.

App enhancement
===============

* **Dynamic Workflow Branching:** Enable blueprints to contain conditional logic, allowing the AI to change its reasoning strategy dynamically based on intermediate generated factors.
* **Visual Strategy Builder:** Create a drag-and-drop or node-based interface in the admin to assemble cognitive pipelines (e.g., "Extract -> Reflect -> Critique -> Summarize").
* **Self-Correction Loops:** Implement automated reflection steps where the LLM is forced to review its own draft output against the prompt requirements and rewrite it if necessary.
* **Context-Aware Triggering:** Build a classification router that looks at a user's query and automatically selects the most appropriate Cognitive Blueprint without manual user selection.
* **Agentic Tool Access:** Allow blueprints to grant the LLM access to external tools (calculators, web search, database querying) during its hidden reasoning phase.