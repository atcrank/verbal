========================================================================================
Doctest Report: Work Organisation Whiteboard Ideation, AI Clustering & Re-Organization
========================================================================================

**Date**: 2026-11-16 13:14:11

**Local Model**: google/gemma-4-E2B-it

**Outcome**: PASSED

**Total Execution Time**: 4.4s

Scenario & Motivating Task
~~~~~~~~~~~~~~~~~~~~~~~~~~

An autonomous robotics research team is formulating sensor configurations and spatial
perception strategies for a search-and-rescue rover operating in collapsed structures. The
researcher uses the Work Organisation whiteboard to brainstorm ideas one by one, applies
AI clustering to identify redundancies and synthesize thematic categories, and exercises
the canvas affordances to re-organize and dissolve clusters.

Animated Walkthrough
~~~~~~~~~~~~~~~~~~~~

.. image:: ../_images/work_organisation/trial_whiteboard_clustering_animated.gif
   :width: 780
   :alt: Animated Walkthrough: Work Organisation Whiteboard Ideation, AI Clustering & Re-Organization

Interaction Trace: Researcher & Whiteboard Canvas
-------------------------------------------------

Step 1: Canvas Initialization (0.2s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Researcher Intent**::

    We initialize our brainstorming workspace for Project Vulcan. We need an open, uncluttered
    canvas to rapidly capture competing hypotheses regarding rover perception in aerosolized
    soot.

**Affordance & Action**: Navigated to mindmap canvas at http://localhost:59497/work/session/1/

Canvas loaded successfully for session: 'Sensor Configuration & Hazard Ideation'.

**Evaluation & State Verification**::

    Session root node verified. Unclustered container ready for card entry.

.. image:: ../_images/work_organisation/01_canvas_empty_state.png
   :width: 720
   :alt: Step 1 Screenshot: Canvas Initialization

Step 2: Sequential Idea Entry (1.2s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Researcher Intent**::

    Entering our candidate sensor concepts into the unclustered pool. We purposefully include
    near-duplicate variations (thermal vs FLIR, rotating vs solid-state LiDAR) to test AI
    deduplication.

**Affordance & Action**: Added 4 notes sequentially via modal input and keyboard shortcuts (Ctrl+Enter).

Input 4 notes sequentially into the Unclustered pool without layout shift.

**Evaluation & State Verification**::

    All 4 cards rendered cleanly in #cluster-cards-0.

.. image:: ../_images/work_organisation/02_unclustered_ideas_entered.png
   :width: 720
   :alt: Step 2 Screenshot: Sequential Idea Entry

Step 3: AI Thematic Clustering & Deduplication (1.1s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Researcher Intent**::

    We trigger AI clustering to organize the brainstorming canvas. The model should recognize
    that the two thermal cameras are conceptually redundant and should be consolidated into
    one vision subsystem, and the two LiDAR ideas form a coherent SLAM subsystem.

**Affordance & Action**: Clicked '✨ Cluster Ideas' button to dispatch thematic synthesis.

AI formed 2 thematic clusters: Thermal Infrared Vision System, 3D Metric LiDAR SLAM.

**AI Model Synthesis & Deduplication**::

    Cluster 1: Thermal Infrared Vision System - Synthesizes thermal imaging and FLIR sensing
    into a unified optical pipeline.
    Cluster 2: 3D Metric LiDAR SLAM - Combines rotating and solid-state LiDAR concepts for
    structural mapping.

**Evaluation & State Verification**::

    Both clusters formed and cards properly assigned to their thematic branches.

.. image:: ../_images/work_organisation/03_thematic_clusters_synthesized.png
   :width: 720
   :alt: Step 3 Screenshot: AI Thematic Clustering & Deduplication

Step 4: Interactive Card Re-organization (0.8s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Researcher Intent**::

    Human-in-the-loop validation: The model deduplicated the thermal camera concepts in the
    cluster summary. We test moving the FLIR camera note back to Unclustered to evaluate
    alternative sensor configurations.

**Affordance & Action**: Selected Card #2 and moved it to '📌 Unclustered' using the contextual action bar.

Card #2 moved to Unclustered container dynamically without page reload.

**Evaluation & State Verification**::

    Card re-positioned and SVG connector lines redrawn.

.. image:: ../_images/work_organisation/04_card_reorganization_and_deduplication.png
   :width: 720
   :alt: Step 4 Screenshot: Interactive Card Re-organization

Step 5: Reverse Clustering (Dissolve All) (0.3s)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Researcher Intent**::

    The team decides to reset the thematic structure and try a function-based taxonomy rather
    than a sensor-hardware taxonomy. We use '↩️ Dissolve Clusters' to return all cards to the
    open canvas.

**Affordance & Action**: Clicked '↩️ Dissolve Clusters' header button.

All thematic clusters dissolved. All 4 cards returned to Unclustered.

**Evaluation & State Verification**::

    Canvas returned to pristine open state ready for a new ideation cycle.

.. image:: ../_images/work_organisation/05_clusters_dissolved_reorganized.png
   :width: 720
   :alt: Step 5 Screenshot: Reverse Clustering (Dissolve All)

Verification & Invariants Check
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table:: Invariant Verification Matrix
   :header-rows: 1
   :widths: 40 15 45

   * - Invariant
     - Status
     - Details
   * - Initial canvas load and session display
     - PASS
     - Session node loaded correctly
   * - Sequential card entry without reload
     - PASS
     - 4 cards created with Ctrl+Enter and modal
   * - AI thematic clustering & deduplication
     - PASS
     - 2 clusters formed with deduplicated summaries
   * - Human-in-the-loop card re-organization
     - PASS
     - Card moved back to unclustered
   * - Reverse clustering via Dissolve All
     - PASS
     - Clusters deleted and all cards preserved in unclustered

