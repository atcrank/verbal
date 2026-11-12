============================================================================
Doctest Report: Trial 1: Document Ingestion and Context-Grounded Reasoning
============================================================================

**Date**: 2026-11-12 13:57:28

**Local Model**: google/gemma-4-E2B-it

**Model Task Outcome**: PASSED

**Total Execution Time**: 49.0s

Scenario & Research Task
~~~~~~~~~~~~~~~~~~~~~~~~

We imagine a researcher who has the following task and resources: An autonomous robotics
research team is designing an empirical intervention trial for a tracked search-and-rescue
robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The
researcher uses the Verbal / Reason interface to formulate hypotheses, evaluate causal and
statistical trade-offs, compute factorial parameters in the sandbox, and structure the
domain knowledge graph.

Animated Walkthrough
~~~~~~~~~~~~~~~~~~~~

.. image:: ../_images/demo_ui/trial_1_ingestion_and_context_animated.gif
   :width: 780
   :alt: Animated Walkthrough: Trial 1: Document Ingestion and Context-Grounded Reasoning

Interaction Trace: Researcher & Verbal / Reason
-----------------------------------------------

Step 1: Literature Ingestion & Vector Indexing (2.0s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

Before designing our field intervention trial, we need an authoritative knowledge base
grounded in empirical robotics literature. We load and index the firefighting robotics
literature fixture to provide factual anchors for sensor capabilities and mobility
constraints.

**The Researcher (Evaluation & Decision)**:

Vector database populated with 51 semantic chunks across 3 research papers. The literature
corpus is ready for semantic retrieval in the Reason UI.

Step 2: Context Chip Formulation & Prompt Submission (2.1s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**The Researcher (Thinking & Formulation)**:

We need to clearly isolate the causal factors governing sensor failure in structural
fires. LiDAR provides metric geometry for SLAM but suffers backscatter in dense aerosols.
LWIR penetrates obscurants but lacks metric depth. We attach the literature chunk and ask
Reason to synthesize the exact trade-off boundaries.

**Researcher Inquiry (Entered in Demo UI)**:

::

    When evaluating sensor payloads for autonomous ground robots in multi-story building
    fires, what are the primary perception and localization trade-offs between 3D LiDAR point
    clouds and long-wave infrared (LWIR) thermography in dense, aerosolized smoke?

**Retrieved Literature Context (RAG Recall)**:

- **Sen Li et al. (2023) — Section: An Indoor Autonomous Inspection and Firefighting Robot Based on SLAM and Flame Image Recognition - ' - (DOI: 10.3390/fire6030093)** [Chunk `cd86439c-b542-4fc3-97eb-d154f6f342a9`]: "Firefighting robots mostly use visual sensors for flame detection and location, and they carry out firefighting and rescue according to the flame location information [30]. Thermal imaging cameras and"

.. image:: ../_images/demo_ui/trial_1_prompt_with_chips.png
   :width: 750
   :alt: Context Chip Formulation & Prompt Submission

Step 3: Live Inference & Response Capture (23.4s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Local LLM (google/gemma-4-E2B-it) generated 4064 characters of context-grounded synthesis.

**Retrieved Literature Context (RAG Recall)**:

- **Sen Li et al. (2023) — Section: An Indoor Autonomous Inspection and Firefighting Robot Based on SLAM and Flame Image Recognition - ' - (DOI: 10.3390/fire6030093)** [Chunk `cd86439c-b542-4fc3-97eb-d154f6f342a9`]: "Firefighting robots mostly use visual sensors for flame detection and location, and they carry out firefighting and rescue according to the flame loca..."

**Verbal / Reason Response (Synthesized by: google/gemma-4-E2B-it)**:

::

    The evaluation of 3D LiDAR point clouds versus Long-Wave Infrared (LWIR) thermography in
    dense, aerosolized smoke environments presents a fundamental trade-off between geometric
    perception and thermal detection. The choice between the two sensors depends entirely on
    whether the primary mission requires mapping the physical structure (localization) or
    identifying the active threat (perception).
    
    Based on the provided context, the key distinction lies in their resilience to smoke and
    their functional output.
    
    1. 3D LiDAR Point Clouds: Geometric Perception and Localization
    
    Role: LiDAR is fundamentally a geometry-based sensor, providing precise spatial data
    necessary for Simultaneous Localization and Mapping (SLAM) and detailed environmental
    modeling.
    
    Aspect	Advantage in Smoke	Trade-off in Smoke
    Perception      Provides detailed 3D structural mapping, identifying obstacles, fire
    boundaries, and debris geometry.   Severe Attenuation and Scattering: Dense, aerosolized
    smoke severely scatters and attenuates the laser beams, leading to noisy, sparse, or
    entirely unreliable point clouds.
    Localization    Excellent for accurate robot localization (SLAM) by matching environmental
    features.    Reduced Feature Density: The loss of geometric features makes robust feature
    matching difficult, compromising the robot's ability to maintain accurate position within
    the dynamic environment.
    Primary Output	High-resolution spatial coordinates 
    (
    𝑥
    ,
    𝑦
    ,
    𝑧
    )
    (x,y,z).        Unreliable Data: The output data quality degrades rapidly, making it
    difficult to distinguish structural elements from smoke artifacts.
    
    Trade-off Summary: LiDAR excels at localization and mapping but suffers a critical failure
    in perceptual accuracy due to smoke interference. Its utility is limited to environments
    where smoke density is manageable or where the robot operates in open areas.
    
    2. Long-Wave Infrared (LWIR) Thermography: Thermal Perception
    
    Role: LWIR is a passive sensor that detects thermal radiation, providing information about
    the heat sources present, which is critical for fire detection and firefighting decisions.
    
    Aspect	Advantage in Smoke	Trade-off in Smoke
    Perception      Smoke Immunity: As noted in the context, thermal imaging cameras
    "passively pick up the object's thermal radiation" and are unaffected by the smoke. This
    allows for reliable flame detection in smoke-filled atmospheres.      Lack of Geometric
    Data: LWIR provides thermal intensity maps, not precise spatial coordinates. It cannot
    generate a detailed 3D map of the structure.
    Localization    Useful for identifying the precise location of the heat source (the
    flame), guiding the robot to the threat.    Ambiguity: Localization is limited to the
    thermal signature. If multiple heat sources exist, or if the thermal signature is diffuse
    (e.g., hot smoke), precise localization is challenging.
    Primary Output  Thermal intensity maps and flame location data. Limited Structural
    Insight: Cannot provide the geometric context needed for complex navigation or structural
    assessment.
    
    Trade-off Summary: LWIR excels at thermal perception and threat detection in smoke,
    offering robust detection capabilities. Its limitation is the inability to provide the
    detailed geometric context required for precise localization and mapping.
    
    Primary Perception and Localization Trade-Off
    
    The core trade-off is: Geometric Fidelity vs. Thermal Reliability.
    
    If the priority is accurate navigation and structural assessment (Localization): 3D LiDAR
    is preferred, but the design must account for the inevitable degradation of the point
    cloud in smoke (e.g., utilizing robust filtering techniques or relying on complementary
    sensors).
    If the priority is immediate threat detection and fire location (Perception): LWIR
    Thermography is the superior choice, as the provided context confirms it is effective in
    smoke environments. The robot uses the thermal data to guide its actions, even if its
    spatial map is degraded.
    
    In a multi-story building fire scenario, a robust autonomous system would ideally use
    **LWIR for mission-critical fire detection

**The Researcher (Evaluation & Decision)**:

Observed live synthesis (4064 chars): addresses LiDAR=True, thermal/LWIR=True,
smoke/obscurants=True. (Note: quantitative power/endurance modeling factors are
constructed separately in Trials 2-3 rather than assumed here.)

.. image:: ../_images/demo_ui/trial_1_grounded_response.png
   :width: 750
   :alt: Live Inference & Response Capture

Expected vs. Observed Comparison (Independent Review)
-----------------------------------------------------

Expected Outcome Specification:
Contrast 3D LiDAR metric SLAM/point clouds (degraded by smoke scattering/backscatter)
against LWIR/thermal imaging (penetrates smoke/obscurants without direct metric depth)
based accurately on the attached literature context.

Deterministic Verification Summary:
- Model Task Outcome: PASSED
- Route / Status: n/a (error=False)
- Workspace Files: None
- Check [PASS] covers 3D LiDAR characteristics
- Check [PASS] covers thermal / LWIR imaging
- Check [PASS] addresses smoke/aerosol attenuation trade-offs

Independent Review (Separate Conversation):
The model successfully achieved the expected result. It accurately contrasted the
capabilities of 3D LiDAR (geometric perception/localization) against LWIR thermography
(thermal perception) in smoke, correctly identifying that LiDAR suffers from severe
scattering/attenuation while LWIR penetrates obscurants to detect heat. No factual errors,
calculation failures, or hallucinations were detected in the response.

For a larger model, this performance suggests strong grounding in technical domains,
effectively synthesizing trade-off analysis based on the provided context. For a smaller
model, this demonstrates a solid capacity for structured comparison when given sufficient
context.


