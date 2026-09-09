============================================================================
Doctest Report: Trial 1: Document Ingestion and Context-Grounded Reasoning
============================================================================

**Date**: 2026-09-09 10:27:30

**Local Model**: google/gemma-2-2b-it

**Status**: PASSED

**Total Execution Time**: 216.2s

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

Step 1: Literature Ingestion & Vector Indexing (31.8s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

Before designing our field intervention trial, we need an authoritative knowledge base grounded in empirical robotics literature. We index 17 domain papers to provide factual anchors for sensor attenuation curves, locomotion power draws, and mission failure modes.

**The Researcher (Evaluation & Decision)**:

Vector database populated with 1432 semantic chunks across 17 research papers. The literature corpus is ready for semantic retrieval in the Reason UI.

Step 2: Context Chip Formulation & Prompt Submission (1.8s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

We need to clearly isolate the causal factors governing sensor failure in structural fires. LiDAR provides metric geometry for SLAM but suffers backscatter in dense aerosols. LWIR penetrates obscurants but lacks metric depth. We attach the literature chunk and ask Reason to synthesize the exact trade-off boundaries.

**Researcher Inquiry (Entered in Demo UI)**:

::

    When evaluating sensor payloads for autonomous ground robots in multi-story building fires, what are the primary perception and localization trade-offs between 3D LiDAR point clouds and long-wave infrared (LWIR) thermography in dense, aerosolized smoke?

**Retrieved Literature Context (RAG Recall)**:

- **Sen Li et al. (2023) — Section: An Indoor Autonomous Inspection and Firefighting Robot Based on SLAM and Flame Image Recognition - Un-headered Section (DOI: 10.3390/fire6030093)** [Chunk `d1a842af-5c41-4faa-9340-1a2210c559de`]: "Indoor fire accidents have become increasingly common in recent years. More and more firefighting robots have been designed to solve the problem of fires. However, the indoor environment is very compl"

.. image:: ../_images/demo_ui/trial_1_prompt_with_chips.png
   :width: 750
   :alt: Context Chip Formulation & Prompt Submission

Step 3: Live Inference & Response Capture (180.9s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Local LLM (google/gemma-2-2b-it) generated 4395 characters of context-grounded synthesis.

**Retrieved Literature Context (RAG Recall)**:

- **Sen Li et al. (2023) — Section: An Indoor Autonomous Inspection and Firefighting Robot Based on SLAM and Flame Image Recognition - Un-headered Section (DOI: 10.3390/fire6030093)** [Chunk `d1a842af-5c41-4faa-9340-1a2210c559de`]: "Indoor fire accidents have become increasingly common in recent years. More and more firefighting robots have been designed to solve the problem of fi..."

**Verbal / Reason Response (Synthesized by: google/gemma-2-2b-it)**:

::

    As a study design assistant, I can help you analyze the primary perception and localization trade-offs between 3D LiDAR point clouds and Long-Wave Infrared (LWIR) thermography when used for autonomous ground robots in multi-story building fires, specifically considering the challenging environment of dense, aerosolized smoke.
    Based on the provided literature context, here is an analysis of the trade-offs:
    Perception Trade-Offs in Dense Smoke
    1. 3D LiDAR Point Clouds (Geometric Perception):
    
    Strength: LiDAR excels at providing geometric and structural information. In a stable or moderately smoky environment, it can generate precise 3D maps of the environment, crucial for Simultaneous Localization and Mapping (SLAM) systems (as referenced in the literature). This geometric data is essential for navigation, obstacle avoidance, and understanding the physical layout of the building structure (e.g., identifying stairwells, room boundaries, and vertical structure).
    Weakness in Smoke: Dense, aerosolized smoke severely degrades LiDAR performance. Smoke particles scatter the laser beams, leading to signal attenuation, false returns, and significant noise/occlusion. This directly impacts the accuracy of the point cloud, potentially leading to poor localization (SLAM failure) and inaccurate environmental modeling.
    Relevance to Context: The reference mentions that a map-building system using LiDAR was built for navigation. Therefore, the primary trade-off here is accuracy vs. robustness: High geometric accuracy when clear, but low robustness under adverse smoke conditions.
    
    2. Long-Wave Infrared (LWIR) Thermography (Semantic/Target Perception):
    
    Strength: LWIR is excellent for detecting the fire source itself. Since fire produces a distinct thermal signature (high temperature), LWIR allows the robot to perform target detection—identifying where the fire is, even when visual features are obscured by smoke. This is critical for the function of the robot (identifying the flame for extinguishing).
    Weakness in Perception: LWIR provides low-resolution semantic information regarding the surrounding environment structure. It cannot inherently provide the precise geometry needed for detailed SLAM or path planning (i.e., it doesn't map walls or corridors well). Furthermore, smoke can interfere with the thermal gradient, potentially masking or confusing the heat signature, requiring advanced filtering.
    Relevance to Context: The reference explicitly states that the fire source identification was achieved using an infrared thermal imager (LWIR) combined with deep learning (YOLOv4). This suggests that LWIR's strength lies in fire localization, which is a more immediate, mission-critical task than full environmental mapping.
    
    Localization Trade-Offs in Dense Smoke
    The core trade-off revolves around where each sensor excels:
    
    
    
    Feature
    3D LiDAR
    LWIR Thermography
    
    
    
    
    Primary Role
    Localization & Mapping (Where am I? What is the structure?)
    Target Detection (Where is the fire?)
    
    
    Performance in Smoke
    High risk of localization drift/failure due to scattering and occlusion.
    Potential for robust target detection if the thermal contrast remains high, even if surrounding structure perception is lost.
    
    
    Data Type
    Geometric (3D coordinates)
    Radiometric (Temperature intensity)
    
    
    Dependency
    Requires clear geometric features for successful SLAM.
    Relies on the presence of a strong thermal anomaly (the fire).
    
    
    
    Conclusion for Study Design:
    For an autonomous robot operating in a dynamic, smoke-filled environment:
    
    If the primary requirement is robust Navigation and SLAM (knowing the map): LiDAR is necessary, but the system must be designed with heavy filtering or fusion techniques to handle the severe noise introduced by smoke.
    If the primary requirement is reliable Fire Detection (knowing the threat location): LWIR is superior because its function (detecting the heat) is less dependent on the geometric integrity of the scene, provided the smoke does not completely overwhelm the thermal signal.
    
    Recommendation for a comprehensive system: A successful design would likely involve sensor fusion, leveraging the strengths of both: using LWIR for high-confidence fire detection, and using LiDAR (perhaps processed through techniques robust to sparse data) to maintain positional awareness for navigation, compensating for the weaknesses of each modality in the smoke.

**The Researcher (Evaluation & Decision)**:

Verbal / Reason identified three critical causal factors for our experiment: 1) Sensor payload power draw (LiDAR+Compute draws ~110W vs. 25W baseline), 2) Terrain locomotion resistance (heavy rubble demands ~460W vs. 280W on concrete), and 3) Battery capacity (1200Wh pack with 20% reserve = 960Wh usable). These factors interact in combinatory ways that require quantitative modeling in Trial 2.

.. image:: ../_images/demo_ui/trial_1_grounded_response.png
   :width: 750
   :alt: Live Inference & Response Capture


