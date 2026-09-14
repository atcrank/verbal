Metacognition - Cognitive Blueprints, Agentic Workflows & Self-Evaluation
==========================================================================

The **Metacognition** application orchestrates multi-step agentic workflows and structured "thinking processes" for Verbal. It compiles database-defined cognitive strategies into stateful LangGraph state machines, coordinating tool execution in secure sandboxes, iterative self-evaluation, and human-in-the-loop tool approvals.

To see these agentic capabilities in action, including their execution traces and generated files, please review the :ref:`Metacognition Trials & Reports <metacognition_trials_page>`.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Multi-Step Agentic Decomposition is Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Designing a rigorous scientific study is inherently multi-faceted: it requires clarifying the core hypothesis, identifying confounding variables, retrieving empirical baselines, writing simulation code, and critiquing the methodology.

When presented with a complex study design prompt in a single zero-shot interaction, Large Language Models suffer characteristic cognitive breakdowns:

* **Shallow Synthesis**: The model attempts to resolve all dimensions simultaneously, producing generic advice rather than deep, actionable protocols.
* **Overlooked Confounders**: Without a dedicated phase to question assumptions, models accept flawed experimental setups without hesitation.
* **Premature Convergence**: The model commits to the first plausible design that comes to mind, lacking an internal mechanism to inspect its own work for mathematical or logical flaws.

**Metacognition** overcomes these limitations by decomposing monolithic queries into explicit, observable, and verifiable **Cognitive Blueprints**—structured sequences of planning, retrieval, code execution, critique, and reflection.


2. Architecture & Mechanism
---------------------------

Database Blueprint Architecture
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Workflows are defined in PostgreSQL rather than hardcoded in Python files, allowing researchers to refine thinking strategies without restarting the application:

* **``CognitiveBlueprint``**: An overarching agent workflow (e.g., *Study Design Planner*, *Deep Reader Ingestion*, *Empirical Code Simulating Agent*). Defines whether the workflow runs autonomously or requires human tool approval, its initial system prompt, and entry steps.
* **``ReasoningStep``**: An individual node within a blueprint. Configures:
  * **System Prompt & Role**: Instructions guiding this specific phase of reasoning.
  * **Inference Parameters**: Step-specific temperature, max tokens, and active model overrides.
  * **Output Schema**: Maps to Pydantic/Ninja schemas in the `OUTPUT_TYPES` registry to enforce structured JSON output.
  * **Connected Tools**: Declares which `ToolDefinition` instances (e.g., sandbox execution, RAG search, graph querying) are accessible at this step.
* **``ToolDefinition``**: Maps python callables in `meta_tools.py` into JSON Schema definitions provided to the LLM.

LangGraph Compilation Engine (``compiler.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
At runtime, `compiler.py` translates the relational `CognitiveBlueprint` and its `ReasoningStep` edges into an executable LangGraph `StateGraph`:

1. **State Dictionary Contract**: A shared state dictionary flows across all graph nodes, containing:
   * ``messages``: Conversation turn history.
   * ``state_tree``: Structured hierarchical state tracking active tasks, research hypotheses, and extracted factors.
   * ``context``: Accumulated RAG excerpts and tool outputs.
   * ``task_queue``: Pending sub-tasks dispatched by planning steps.
2. **Conditional Transitions**: Evaluates step completion results (e.g. `ResultCritique` decisions) to dynamically route flow: proceeding to the next phase, looping back for refinement, or halting for user clarification.

API Endpoints (``metacognition/api.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Exposes Django Ninja REST and Server-Sent Events (SSE) streaming endpoints:

* ``POST /api/metacognition/dispatch/``: Enqueues a blueprint run asynchronously via `verbal_tasks`.
* ``GET /api/metacognition/events/{run_id}``: Real-time Datastar SSE stream delivering live step transitions, reasoning tokens, and tool outputs.
* ``POST /api/metacognition/cancel/``: Safely halts an active blueprint run.
* ``POST /api/metacognition/approve-tool/``: Human-in-the-loop authorization endpoint that unblocks paused workflows when sensitive tools are requested.


3. Observability & Health Signals
---------------------------------

How to Know Blueprints are Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Streaming Reasoning Traces in the Demo UI**:
   In the Demo UI chat interface, expanding the **Reasoning Trace** accordion displays real-time step progress (e.g. *Step 1: Extract Factors* $\rightarrow$ *Step 2: Simulate in Sandbox* $\rightarrow$ *Step 3: Self-Critique*).
2. **State Tree Evolution in Prompt Logs**:
   In `PromptResponseLog`, the `state_tree_snapshot` field records the exact dictionary state at each step. Healthy workflows show progressive refinement of experimental factors and clear status transitions.
3. **Structured Tool Inputs/Outputs**:
   Tool executions log their exact JSON inputs and outputs, verifying that arguments (such as Python scripts or graph queries) are well-formed.


4. Diagnostic Tips & Failure Modes
----------------------------------

When Blueprints Miss the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Infinite Refinement Loops (Step Bounces Back Continuously)**:
  * *Hazard*: A critique step (e.g., `ResultCritique`) continuously rejects candidate outputs because validation criteria are overly stringent or ambiguous.
  * *Remedy*: In the `ReasoningStep` definition, adjust the routing condition to cap retry iterations, or soften the critique prompt to accept partial successes.
* **State Tree Bloat**:
  * *Hazard*: Accumulating raw tool outputs (e.g., massive CSV strings or full PDF dumps) directly in `state_tree` bloats the prompt context over successive steps.
  * *Remedy*: In `actions.py`, ensure tool outputs are summarized before updating the state tree, storing heavy raw files in the sandbox workspace rather than in memory.
* **Human-in-the-Loop Workflow Stalls**:
  * *Hazard*: A blueprint configured with `is_autonomous=False` halts execution waiting for tool authorization, appearing frozen to the user.
  * *Remedy*: In the Demo UI or via API (`/api/metacognition/approve-tool/`), review the requested tool call and click **Approve** or **Reject**, or toggle `is_autonomous=True` on the blueprint if human oversight is not required.


Module Reference
----------------

.. automodule:: metacognition.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: metacognition.compiler
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: metacognition.actions
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: metacognition.api
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: metacognition.tasks
   :members:
   :undoc-members:
   :show-inheritance:
