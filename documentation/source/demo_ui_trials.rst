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
   demo_ui_trials/trial_2_multistep_blueprint_and_tools
   demo_ui_trials/trial_2_multistep_blueprint_and_tools_report
   demo_ui_trials/trial_3_branching_and_grips_expansion
   demo_ui_trials/trial_3_branching_and_grips_expansion_report

Trial Descriptions
------------------

1. :doc:`Trial 1: Document Ingestion and Context Dropping <demo_ui_trials/trial_1_ingestion_and_context>`
   Demonstrates document upload, micro status badge transitions (``INDEXED (N)``), token estimation, and context chips in the prompt interface.

2. :doc:`Trial 2: Multi-Step Cognitive Reasoning and Sandboxed Tools <demo_ui_trials/trial_2_multistep_blueprint_and_tools>`
   Demonstrates engineering calculations using agentic cognitive blueprints and the Python code execution sandbox, capturing live elapsed thinking state.

3. :doc:`Trial 3: Interactive Conversation Branching and Grips Knowledge Graph <demo_ui_trials/trial_3_branching_and_grips_expansion>`
   Demonstrates exploratory branching from assistant messages and navigating/elaborating the Grips conceptual ontology.
