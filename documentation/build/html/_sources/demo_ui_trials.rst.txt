Demo UI Empirical Trials & Verification Reports
=================================================

This suite of empirical trials documents and verifies the core capabilities of **Reason** (`demo_ui`), the computational study design assistant. Grounded in the rigorous domain of **Autonomous Robotics in Structural Firefighting**, these trials showcase the entire lifecycle of an empirical research intervention: from literature ingestion and context retrieval to parametric factor construction, sandboxed engineering calculation, active error detection, counterfactual hypothesis testing in conversation branches, and domain ontology synthesis.

Methodology: Executable Doctests as Verifiable Reports
------------------------------------------------------

Rather than presenting static mockups or hand-written marketing summaries, Reason uses **executable Python doctests** driven by `pytest` and Playwright browser automation. 

Every trial runs against:
- **Live Local GPU Inference**: Token generation executed by `google/gemma-2-2b-it` on local hardware (port 8001).
- **Live Browser Automation**: End-to-end browser session driving the HTMX frontend, context chips, and conversation DAG.
- **Dockerized Execution Sandbox**: Sandboxed code execution (`verbal_sandbox`) for numerical modeling.
- **Persistent Knowledge Graph**: Grips ontology storage and autonomous conceptual elaboration in PostgreSQL / pgvector.

Upon test completion, the test harness compiles a comprehensive **Trial Report** recording verbatim model tokens, timing measurements, captured sandbox stdout, high-resolution screenshots, and animated walkthrough GIFs. These reports represent the primary reading material for evaluating Reason's capabilities.

.. toctree::
   :maxdepth: 1
   :caption: Empirical Trial Reports

   demo_ui_trials/trial_1_ingestion_and_context_report
   demo_ui_trials/trial_2_careful_literature_extraction_report
   demo_ui_trials/trial_3_multistep_blueprint_and_tools_report
   demo_ui_trials/trial_4_branching_and_grips_expansion_report

The Four Trial Reports
----------------------

1. :doc:`Report 1: Targeted Literature Retrieval & Grounding Audit <demo_ui_trials/trial_1_ingestion_and_context_report>`
   Audits semantic retrieval against peer-reviewed papers (*Talavera et al. 2023*, *Penders et al. 2011*). Evaluates context chip formulation, token estimation, and trade-offs between 3D LiDAR backscatter and LWIR thermal signatures in aerosolized smoke with strict token-only evaluation.

2. :doc:`Report 2: Careful Literature Extraction & Parametric Factor Construction <demo_ui_trials/trial_2_careful_literature_extraction_report>`
   Extracts physical hardware specifications directly from literature tables: GM25-370 locomotion motors, 12,800mAh 3.7V motor bank, 10,800mAh 5V compute bank, and Pozyx UWB transceiver. Establishes the empirical $3 \times 2$ factorial space (Payload: 25W, 75W, 110W; Locomotion: 280W, 460W; 960Wh usable battery) with zero fabricated numbers.

3. :doc:`Report 3: Multi-Step Reasoning, Sandboxed Simulation & Adversarial Critique <demo_ui_trials/trial_3_multistep_blueprint_and_tools_report>`
   Demonstrates agentic multi-step reasoning using the Docker Python sandbox. Actively flags the model's `.idxmin()` optimization inversion error in real time, prompts the model for correction, captures the valid recommendation (**Standard Multi-spectral on Concrete: 162 min / 42 min margin**), and uncovers the decisive finding that onboard power fails on rubble (<119 min).

4. :doc:`Report 4: Counterfactual Branching, Empirical Ontology Synthesis & Protocol Generation <demo_ui_trials/trial_4_branching_and_grips_expansion_report>`
   Forks an isolated DAG conversation branch via `Branch from here` and actively evaluates a live counterfactual query (deployable breadcrumb relays offloading 45W transmitter draw, reaching **130.9 minutes** on rubble). Elaborates the Grips ontology stub `Breadcrumb Relay Node` with genuine UWB physics (3.5–6.5 GHz, 15cm accuracy, 3 structured claims), culminating in a synthesized 2x2 factorial Empirical Intervention Protocol ($N=8$, two-way ANOVA).

.. raw:: html

   <details class="doctest-breakout">
   <summary>🔍 Click to inspect the 4 Underlying Executable Doctest Specifications</summary>
   <div class="breakout-content">
   <p>The four reports above are compiled automatically by executing the underlying doctest test files. You can inspect or run any test file directly:</p>

.. list-table::
   :widths: 25 35 40
   :header-rows: 1

   * - Trial Specification
     - Pytest Command
     - Test Automation Highlights
   * - :doc:`Trial 1 Doctest <demo_ui_trials/trial_1_ingestion_and_context>`
     - ``pytest demo_ui/demo_ui_trials/trial_1_ingestion_and_context.rst``
     - RAG indexing, context chip injection, micro-badge status tracking, and token counting.
   * - :doc:`Trial 2 Doctest <demo_ui_trials/trial_2_careful_literature_extraction>`
     - ``pytest demo_ui/demo_ui_trials/trial_2_careful_literature_extraction.rst``
     - Paper chunk attachment, hardware spec extraction verification, and factorial matrix assertion.
   * - :doc:`Trial 3 Doctest <demo_ui_trials/trial_3_multistep_blueprint_and_tools>`
     - ``pytest demo_ui/demo_ui_trials/trial_3_multistep_blueprint_and_tools.rst``
     - Cognitive blueprint invocation, Docker sandbox stdout extraction, and adversarial logic audit.
   * - :doc:`Trial 4 Doctest <demo_ui_trials/trial_4_branching_and_grips_expansion>`
     - ``pytest demo_ui/demo_ui_trials/trial_4_branching_and_grips_expansion.rst``
     - DAG branch button click, multi-turn branched submission, Grips stub elaboration, and protocol generation.

.. raw:: html

   </div>
   </details>

.. toctree::
   :hidden:

   demo_ui_trials/trial_1_ingestion_and_context
   demo_ui_trials/trial_2_careful_literature_extraction
   demo_ui_trials/trial_3_multistep_blueprint_and_tools
   demo_ui_trials/trial_4_branching_and_grips_expansion
