.. _metacognition_trials_page:

Metacognition Trials & Reports
==============================

To ensure the reliability and correctness of the Metacognition agentic workflows, we maintain a suite of numbered doctests. These are ordered by ascending difficulty and provide a rigorous, verifiable demonstration of the system's capabilities.

When these trials are run, they automatically generate detailed reports showcasing the step-by-step reasoning, tool execution, and standard output of the LLM.

Trial Specifications
--------------------
These files contain the actual user prompts, retrieved context, and the expected evaluation criteria.

* :doc:`1. Counting Letters <metacognition_trials/1. counting_letters>`
* :doc:`2. Design Compute <metacognition_trials/2. design_compute>`
* :doc:`3. Code Discuss Compute <metacognition_trials/3. code_discuss_compute>`
* :doc:`4. Modelling Simulation <metacognition_trials/4. modelling_simulation>`
* :doc:`5. Causal Modeling <metacognition_trials/5. causal_modeling>`

Generated Execution Reports
---------------------------
These generated reports capture the complete cognitive loop of the agent attempting to solve the trials, including its Git commit tracking, sandbox stdout, and generated workspace files.

* :doc:`1. Counting Letters (Report) <metacognition_trials/counting_letters_report>`
* :doc:`2. Design Compute (Report) <metacognition_trials/design_compute_report>`
* :doc:`3. Code Discuss Compute (Report) <metacognition_trials/code_discuss_compute_report>`
* :doc:`4. Modelling Simulation (Report) <metacognition_trials/modelling_simulation_report>`
* :doc:`5. Causal Modeling (Report) <metacognition_trials/causal_modelling_report>`
