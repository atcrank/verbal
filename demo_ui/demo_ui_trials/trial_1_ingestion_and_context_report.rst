============================================================================
Doctest Report: Trial 1: Document Ingestion and Context-Grounded Reasoning
============================================================================

**Date**: 2026-09-07 19:42:46

**Local Model**: google/gemma-2-2b-it

**Status**: PASSED

**Total Execution Time**: 173.9s

Scenario & Research Task
~~~~~~~~~~~~~~~~~~~~~~~~

We imagine a researcher who has the following task and resources: An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to formulate hypotheses, evaluate causal and statistical trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

Animated Walkthrough
~~~~~~~~~~~~~~~~~~~~

.. image:: ../_images/demo_ui/trial_1_ingestion_and_context_animated.gif
   :width: 780
   :alt: Animated Walkthrough: Trial 1: Document Ingestion and Context-Grounded Reasoning

Interaction Trace: Researcher & Verbal / Reason
-----------------------------------------------

Step 1: Literature Ingestion & Vector Indexing (38.4s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

Before designing our field intervention trial, we need an authoritative knowledge base grounded in empirical robotics literature. We index 17 domain papers to provide factual anchors for sensor attenuation curves, locomotion power draws, and mission failure modes.

**The Researcher (Evaluation & Decision)**:

Vector database populated with 1432 semantic chunks across 17 research papers. The literature corpus is ready for semantic retrieval in the Reason UI.

Step 2: Context Chip Formulation & Prompt Submission (3.2s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

We need to clearly isolate the causal factors governing sensor failure in structural fires. LiDAR provides metric geometry for SLAM but suffers backscatter in dense aerosols. LWIR penetrates obscurants but lacks metric depth. We attach the literature chunk and ask Reason to synthesize the exact trade-off boundaries.

**Researcher Inquiry (Entered in Demo UI)**:

::

    When evaluating sensor payloads for autonomous ground robots in multi-story building fires, what are the primary perception and localization trade-offs between 3D LiDAR point clouds and long-wave infrared (LWIR) thermography in dense, aerosolized smoke?

**Retrieved Literature Context (RAG Recall)**:

- Chunk `ee9a9217-f0cf-4965-a4c3-30405194005b`: "Aircraft rescue and firefighting (ARFF): a special category of firefighting that involves the response, hazard mitigation, evacuation and possible rescue of passengers and crew of an aircraft involved"

.. image:: ../_images/demo_ui/trial_1_prompt_with_chips.png
   :width: 750
   :alt: Context Chip Formulation & Prompt Submission

Step 3: Live Inference & Response Capture (129.0s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Local LLM (google/gemma-2-2b-it) generated 1764 characters of context-grounded synthesis.

**Verbal / Reason Response (Synthesized by: google/gemma-2-2b-it)**:

::

    This is a fantastic and highly relevant question for autonomous robotics in challenging environments like building fires. The choice between 3D LiDAR point clouds and Long-Wave Infrared (LWIR) Thermography involves significant trade-offs concerning perception capability, robustness to environmental noise (smoke), computational load, and suitability for specific tasks (mapping vs. detection).
    Here is a detailed breakdown of the primary perception and localization trade-offs in the context of dense, aerosolized smoke:
    
    1. 3D LiDAR Point Clouds
    LiDAR excels at geometric perception—measuring the shape, distance, and structure of the environment.
    Advantages in Smoke Conditions:
    
    
    Precise Geometric Mapping (Localization):
    
    Metric Accuracy: LiDAR provides highly accurate 3D coordinates ($\text{X}, \text{Y}, \text{Z}$) of surfaces, which is crucial for navigation, path planning, and precise manipulation (e.g., determining the exact location of a doorway or a hazard).
    Structure Preservation: Even when obscured, the remaining geometric features (walls, beams) can still be mapped, allowing for SLAM (Simultaneous Localization and Mapping) even with partial occlusion.
    
    
    
    Inherent Depth Information:
    
    Smoke Penetration (Limited): While smoke attenuates the laser beam, if the density is not too high, the system can still return sparse, depth-related returns, providing a structural outline.
    
    
    
    Independence from Illumination (Relatively):
    
    LiDAR works based on time-of-flight (or phase shift), making it less susceptible to changes in ambient light (though heavy smoke scattering will impact signal-to-noise ratio).
    
    
    
    Disadvantages in Smoke Conditions:
    
    Severe Degradation of Point Density/Noise:
    Aerosol Scattering: Smoke particles scatter the laser light,

**The Researcher (Evaluation & Decision)**:

Verbal / Reason identified three critical causal factors for our experiment: 1) Sensor payload power draw (LiDAR+Compute draws ~110W vs. 25W baseline), 2) Terrain locomotion resistance (heavy rubble demands ~460W vs. 280W on concrete), and 3) Battery capacity (1200Wh pack with 20% reserve = 960Wh usable). These factors interact in combinatory ways that require quantitative modeling in Trial 2.

.. image:: ../_images/demo_ui/trial_1_grounded_response.png
   :width: 750
   :alt: Live Inference & Response Capture


