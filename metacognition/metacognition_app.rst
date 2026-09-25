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

1. **State Contract (``AgentState``)**: A typed dictionary (`AgentState`) flows across all graph nodes, containing:
   * ``working_memory``: Ephemeral sequence of LangChain message objects (`HumanMessage`, `AIMessage`, `SystemMessage`) representing immediate dialogue and step interactions.
   * ``state_tree``: Persistent structured dictionary tracking milestones, tasks, active focal points, hypotheses, and established facts.
   * ``scratch``: Step-level transient variables (e.g., active task queues, chunk indices, parsed schema objects).
   * ``rag_context``: Text excerpts retrieved from vector or keyword search.
   * ``approved_tools``: Authorization signatures for tools approved during human-in-the-loop pauses.
   * ``route_to`` & ``resume_to``: Dynamic transition targets determining whether the graph loops (`SELF`), progresses to another step, halts for human input (`USER_INPUT_REQUIRED`), or terminates.
2. **Conditional Transitions**: Evaluates step completion results (e.g. `ResultCritique` decisions, Pydantic schema action handlers) to dynamically route flow: proceeding to the next phase, looping back for refinement, or halting for user clarification.
3. **Sub-Blueprint Bypasses**: When a `ReasoningStep` specifies `sub_blueprint_id`, the node bypasses LLM inference entirely and recursively executes the sub-blueprint via `run_blueprint()`, maintaining clean context boundaries.


Working Memory & Dynamic Focal Projections
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rather than treating an agent conversation as a monolithic, unbounded list of chat messages, Metacognition employs a dual-layer working memory model:

1. **Ephemeral Dialogue Working Memory (``working_memory``)**:
   * Managed as a list of LangChain messages.
   * Initialized by replaying the conversation's active branch from `conversation.as_messages(leaf_log_id=parent_log_id)`.
   * Automatically pruned when the dialogue turn count exceeds the active model's context budget.
2. **Persistent Structured Working Memory (``Conversation.state_tree``)**:
   * A JSON tree storing structured milestones, task breakdowns, hypotheses, and settled constraints.
   * Attached to `Conversation` and snapshotted at every turn in `PromptResponseLog.state_tree_snapshot` to maintain exact DAG branch isolation.
3. **Dynamic Focal Projection (``format_focal_state_tree``)**:
   * Small local models (2B to 9B parameters) suffer severe degradation when presented with raw, deeply nested dictionaries in their prompt context.
   * The focal projector scans the tree for the active task (`current_focus` or tasks with `status: "in_progress"`).
   * It renders the active branch in complete, actionable detail (prefixed with ``>> [ACTIVE]``), while automatically collapsing sibling, pending, and completed branches into compact single-line stubs.
   * Keeps injected working memory small (typically 100–250 tokens), preventing prompt bloat while giving the model crystal-clear context about *what to do next*.
4. **Dual-Phase Compaction Lifecycle**:
   * **Fast Inline Compaction (``fast_compact_state_tree``)**: Deterministic, sub-millisecond Python rules running inside `AgentState` reducers. Rolls settled tasks into compact milestone lists, clamps bulky string values exceeding 300 characters, and strictly protects invariants stored under `established_facts`.
   * **Overnight Intelligent Compaction (``task_compact_conversation_state_trees``)**: Periodic background task scheduled via the NightManager infrastructure. Analyzes mature, inactive conversations and synthesizes distant milestones into high-level breadcrumb summaries (*"I can see a long time ago we explored..."*).


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


5. Practical Guidance for Prompt Authors & Blueprint Designers
--------------------------------------------------------------

Designing agent workflows that run reliably on local open-weight models requires deliberate architectural choices. The following best practices apply when drafting reasoning step prompts and assembling cognitive blueprints:

Guidance 1: Working Memory vs. Conversation History
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Do Not Replicate State in Prompts**: Do not attempt to manually format or summarize conversation history inside your system prompts. When `ReasoningStep.include_state_tree` is enabled (the default), the compiler automatically injects the focal state tree projection into the prompt context.
* **Rely on Continuous Dialogue**: `run_blueprint` automatically restores previous turns from `conversation.as_messages()`. System prompts should be written assuming recent conversational dialogue is already visible in the message stream, while older context is condensed into the state tree.

Guidance 2: Structuring the State Tree for Model Clarity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When prompts or schemas update `state_tree`, adhere to the standard key hierarchy to ensure the focal projection and compaction engines function optimally:

.. code-block:: json

   {
     "milestones": [
       "Settled hypothesis: price shocks impact local consumer sentiment"
     ],
     "current_focus": "task_simulate_elasticity",
     "tasks": {
       "task_extract_parameters": {
         "status": "completed",
         "summary": "Extracted parameters alpha=0.3, beta=0.7"
       },
       "task_simulate_elasticity": {
         "status": "in_progress",
         "details": "Simulate demand elasticity under varying shock scenarios",
         "subtasks": ["prepare_grid", "run_monte_carlo"]
       },
       "task_critique_methodology": {
         "status": "pending",
         "details": "Run ResultCritique schema on simulation outcomes"
       }
     },
     "established_facts": {
       "target_market": "Retail domestic energy",
       "time_horizon": "12 months"
     }
   }

* **Explicit Active Status**: Mark the active item with ``status: "in_progress"`` or specify ``current_focus``. This causes `format_focal_state_tree` to spotlight it with ``>> [ACTIVE]``, collapsing completed and pending siblings into one-line stubs. This prevents models from hallucinating or attempting future steps prematurely.
* **Invariants in ``established_facts``**: Place immutable domain constraints or confirmed user specifications under `established_facts`. Compaction routines will never prune or summarize these keys.

Guidance 3: Design Patterns — Plans vs. Uncertainty Trees (The Grill-Me Pattern)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Blueprint designers often instinctively reach for an `ExecutionPlan` or `TaskQueue` for every workflow. This is effective for deterministic workflows, but backfires in interactive clarification loops:

* **When to use Execution Plans / Task Queues**:
  Use linear task queues when the problem is already well-scoped and the agent needs to decompose work into sequential execution passes (e.g., *Task 1: Search literature* $\rightarrow$ *Task 2: Write code* $\rightarrow$ *Task 3: Compile summary*).
* **When to use Uncertainty / Inquiry Trees (The Grill-Me Pattern)**:
  In clarification and interview loops (such as the *Grill-Me* blueprint), the agent **cannot predict user answers**. Forcing the model to generate a sequential multi-step execution plan produces brittle or irrelevant steps. Instead, instruct the model to maintain an **inquiry tree** tracking unresolved facets and depth:

  .. code-block:: json

     {
       "inquiry_facets": {
         "macro_goal": "clarified",
         "dataset_specifications": "unresolved",
         "failure_risks_and_adversarial_factors": "unresolved"
       },
       "settled_constraints": [
         "Must run on consumer GPU with 6 GB VRAM",
         "Dataset is unlabelled PDF reports"
       ],
       "clarification_depth": 2,
       "current_focus": "dataset_specifications"
     }

  This allows the model to daisy-chain follow-up questions organically, drilling deeper where ambiguity remains without getting tangled in a rigid, premature plan.

Guidance 4: Context Isolation with Sub-Blueprints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When a reasoning step requires extensive exploration, heavy tool usage, or sandbox code execution:

* **Bypass the LLM with a Sub-Blueprint**: Assign `step.sub_blueprint_id`. The engine will invoke the sub-blueprint in its own dedicated conversation.
* **Why Isolation Matters**: A subagent trying to verify facts in the document store or execute a python simulation might invoke tools 5 to 10 times, encounter errors, retry, and inspect files. If all of those intermediate steps were recorded in the main conversation, the user's primary context window would quickly fill with irrelevant scratchpad noise.
* **Distilled Return**: The sub-blueprint completes in its private sandbox. Its state tree milestones are merged into the parent conversation via `merge_state_trees()`, and only a single high-level summary is returned into the parent's working memory.

Guidance 5: Workspace File Hygiene
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Write Data to Files, Not to Messages**: When tools generate large datasets, simulation tables, or multi-page reports, instruct the model to write the content directly to the conversation's workspace using `WRITE_FILE`.
* **Store Handles in State Tree**: In `state_tree` or scratchpad variables, store only the file path (e.g., `results/simulation_run_1.csv`) and brief statistical summaries—never the raw content. This keeps prompt tokens under 250 tokens per step.

Guidance 6: Choosing Structured Output vs. ReAct Tool Calling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **Use Pydantic Schemas (Outlines Guided Generation)**: Whenever a step requires an explicit, structured decision (e.g., `ResultCritique`, `Factor`, `StrategicPlan`, `DifficultPromptEvaluation`), configure `ReasoningStep.output_schema`. The Outlines FSM constraint guarantees 100% syntactically valid JSON on local models without retries.
* **Use ReAct Tool Calling**: Reserve `step.available_tools` for open-ended exploration phases where the agent must freely decide which tools to invoke and with what arguments.


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
