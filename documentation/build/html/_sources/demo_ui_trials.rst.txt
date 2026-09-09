Demo UI Interactive Trials & Feature Walkthroughs
==================================================

This suite of executable doctests provides an interactive, visual walkthrough of the **Reason** (`demo_ui`) application. 
They exercise the full feature set—from real-world scientific literature ingestion to multi-step reasoning, conversation branching, and knowledge graph ontology expansion—grounded in the domain of **Autonomous Drones and Ground Robots in Firefighting**.

These trials serve a dual purpose:
1. **Verifiable Automated End-to-End Tests**: Executed directly with ``pytest demo_ui/demo_ui_trials/``.
2. **Offline Presentation Deck**: If presenting without live GPU inference or network access, these compiled walkthroughs and their high-resolution screenshots provide a complete, verified case study of the system's capabilities.

.. toctree::
   :maxdepth: 2
   :caption: Walkthrough Trials

   demo_ui_trials/trial_1_ingestion_and_context
   demo_ui_trials/trial_1_ingestion_and_context_report
   demo_ui_trials/trial_2_careful_literature_extraction
   demo_ui_trials/trial_2_careful_literature_extraction_report
   demo_ui_trials/trial_3_multistep_blueprint_and_tools
   demo_ui_trials/trial_3_multistep_blueprint_and_tools_report
   demo_ui_trials/trial_4_branching_and_grips_expansion
   demo_ui_trials/trial_4_branching_and_grips_expansion_report

Trial Descriptions
------------------

1. :doc:`Trial 1: Document Ingestion and Context Dropping <demo_ui_trials/trial_1_ingestion_and_context>`
   Demonstrates document upload, micro status badge transitions (``INDEXED (N)``), token estimation, and context chips in the prompt interface.

2. :doc:`Trial 2: Careful Literature Extraction & Parametric Factor Construction <demo_ui_trials/trial_2_careful_literature_extraction>`
   Extracts physical UGV hardware specifications, battery bank architectures, and sensor draw parameters directly from Talavera et al. (2023), establishing the empirical $3 \times 2$ factorial simulation space.

3. :doc:`Trial 3: Multi-Step Cognitive Reasoning, Sandboxed Simulation & Adversarial Verification <demo_ui_trials/trial_3_multistep_blueprint_and_tools>`
   Demonstrates engineering calculations using agentic cognitive blueprints and the Python code execution sandbox, auditing model logic in real time and uncovering decisive causal endurance limits on rubble.

4. :doc:`Trial 4: Counterfactual Branching, Empirical Ontology Synthesis & Protocol Generation <demo_ui_trials/trial_4_branching_and_grips_expansion>`
   Executes an active counterfactual in a branched conversation thread proving feasibility with deployable relays (130.9 min), elaborates the Grips conceptual ontology with genuine UWB physics, and synthesizes an actionable Empirical Intervention Protocol.
