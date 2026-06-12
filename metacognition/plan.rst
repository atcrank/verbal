==============================
Metacognition Development Plan
==============================

Introduction
============
This document outlines the strategic roadmap for extending the ``metacognition`` application. The primary goal is to empower the LLM to transition from pure text-based reasoning to active tool use, specifically focusing on code execution, file system operations, and automated verification. By providing a safe sandbox for executing small scripts or queries, we can mitigate common LLM pitfalls (like spelling tasks or poor game-state tracking) and tackle complex, multi-step simulations.

1. Automated Database Seeding & Startup Checks
==============================================
To ensure consistency across environments and reduce manual setup, the application must automatically verify and seed essential database content during startup (e.g., via Django's ``AppConfig.ready()`` or a dedicated management command).

1.1 Auto-generate ResponseSchemas
---------------------------------
- Verify that a ``ResponseSchema`` object exists in the database for every Pydantic type registered in ``OUTPUT_TYPES``.
- Automatically populate missing schemas with sensible default descriptions and metadata.

1.2 Default ReasoningStep Templates
-----------------------------------
- Ensure that a template ``ReasoningStep`` object exists for each ``ResponseSchema``.
- These steps will serve as modular building blocks for constructing complex blueprints.

1.3 Baseline Validation Blueprints
----------------------------------
- Maintain a set of valid ``CognitiveBlueprint`` objects designed specifically to exercise each ``ReasoningStep`` in its intended manner.
- These blueprints will act as functional baselines for system health checks and regression testing.

2. Architectural Paradigm: Contemplation vs. Action
===================================================
To ensure stable AI trajectories, the architecture explicitly separates internal cognition from external side-effects.

- **Contemplation (Blueprints & ReasoningSteps):** Safe, internal operations like searching vector databases, traversing Knowledge Graphs, filtering context, and planning. These are explicitly wired as nodes in a DAG (CognitiveBlueprint). Blueprints can also embed sub-blueprints for compositional reasoning.
- **Action (ExecutionPlans & ActionItems):** High-risk, high-power interactions with the external world (I/O). Handled exclusively by the ``ExecutionPlan`` reasoning step, which drops the LLM into a ReAct loop to write code, read files, execute scripts in a sandbox, and review stdout/stderr.

3. Review and Extend Schemas and Actions
========================================
We will elevate the existing ``ExecutionPlan`` capabilities to support fully-fledged code generation, execution, and workspace management, strictly omitting cognitive tools (like RAG) from the ActionItem toolbelt.

3.1 Code Generation Actions
---------------------------
- Build upon the existing ``handle_execution_plan`` logic in ``actions.py``.
- Introduce explicit ``ActionItemType`` schemas for code manipulation (e.g., ``GenerateCodeAction``, ``ReviewCodeAction``).
- Ensure the LLM can define inputs, outputs, and dependencies for the code it intends to write.
- Implement a bridge ActionItem (``TaskCompleteAction``) to gracefully exit the ExecutionPlan loop and return the final data/results to the parent CognitiveBlueprint.

3.2 Safe Sandboxed Execution
----------------------------
- Implement a secure sandbox environment for code execution.
- **Architecture:** Spin up a dedicated, isolated Python Docker container with an environment matching the main project.
- The sandbox must have restricted network access and enforce strict timeouts and memory limits to prevent runaway processes or malicious code execution.
- Implement an action hook (e.g., ``ExecuteSandboxedCodeAction``) that passes code strings to the sandbox container via an API or socket, returning ``stdout``, ``stderr``, and exit codes back to the LLM's working memory.

3.3 File System Operations
--------------------------
- Define Pydantic output types and corresponding action hooks for file system manipulations within a restricted workspace volume shared with the sandbox.
- **Operations to support:**
  - Read File
  - Write / Create File
  - Edit / Patch File (rewriting specific lines/blocks)
  - Parse / Syntax Check (expanding on the current ``CHECK_PARSING`` tool)
  - Delete File
  - Execute Script / Terminate Process

4. Test-Driven Development (TDD) & Adversarial Test Cases
=========================================================
Before implementing the new sandbox and file actions, we will define an agreed-upon set of challenging use cases, Blueprints, and ReasoningSteps. Writing the tests first will guide the architecture.

4.1 Automated TDD Workflow
--------------------------
- Create test harnesses in ``metacognition/tests.py`` that mock the LLM outputs to verify that the action hooks, sandbox APIs, and graph traversal logic perfectly handle code generation and execution loops.
- Integrate these tests to ensure that new tools do not break existing agentic behaviors.

4.2 Adversarial Use Cases
-------------------------
- Design complex, multi-step challenges that force the LLM to use RAG, write code, test it, read the stack trace, and iterate on failures.
- **Flagship Example:** Translate a real-world, game-like problem into a Multi-Agent Causal Influence Diagram (MACID).
  - The LLM will query the RAG database for causal modeling principles.
  - It will write Python code (e.g., using ``macid`` or ``pgmpy`` libraries) to construct the graph mathematically.
  - It will execute the code in the sandbox, parse any structural errors, and refine the model until it accurately represents the game mechanics.

4.3 Reusability of Test Scenarios
---------------------------------
- The defined use cases will be structured so they can be reused across the project ecosystem:
  - As automated tests for CI/CD.
  - As tutorials and practical examples in the ``documentation/`` folder.
  - As scenario seeds for benchmarking in ``benchmarking/generators.py``.
  - As high-quality trajectories (monologues) for future model fine-tuning.