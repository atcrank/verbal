Work Organisation - Collaborative Whiteboards & Assisted Study Design
========================================================================

The **Work Organisation** app provides multi-user workspace management, group-scoped access control, interactive whiteboarding, and assisted ideation for study design. It enables multidisciplinary research teams to structure exploratory questions, capture hypotheses, cluster brainstorming notes with local AI assistance, and extract formal causal factors into study artifacts.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

During early-stage research and experimental design, teams frequently encounter cognitive and organizational hurdles:

* **Fragmented Ideation vs. Formal Specification**: Brainstorming sticky notes, questions, and hypotheses rarely translate smoothly into rigorous empirical variables or formal causal models without tedious manual translation.
* **Evaluation Apprehension & Attribution Bias**: In sensitive domains or interdisciplinary teams, junior participants or domain specialists may hesitate to propose unconventional ideas or challenge established hypotheses if every note is visibly attributed to their identity.
* **Cognitive Overload from Sprawl**: Unstructured whiteboard canvases quickly degrade into visual clutter where recurring themes, contradictions, and causal relationships are obscured.

To alleviate these challenges without falling into naive automation traps, the Work Organisation module combines human-in-the-loop spatial organization with structured LLM synthesis:

.. note::
   **A Note on AI Facilitation**:
   LLM-assisted clustering and causal factor extraction are not authoritative oracles. A local model can easily force artificial analogies, group contradictory claims into the same cluster, or infer directional causality where only correlation was mentioned. The Work Organisation canvas treats AI outputs as editable proposals: cards remain independent, clusters are repositionable bounding boxes, and extracted factors must be validated by human experiment designers before promotion to formal Grips causal graphs.


2. Architecture & Mechanism
---------------------------

The module is structured around a four-tier project hierarchy, group-scoped permissions, four distinct privacy modes, real-time event distribution, and structured Pydantic extraction pipelines.

Domain Hierarchy
~~~~~~~~~~~~~~~~

.. code-block:: text

   Project (Top-level container; Django Groups & owner permissions)
     └── Workshop (Specific research track, workshop milestone, or study inquiry)
           └── WorkshopSession (Collaborative canvas session with specific access mode)
                 ├── WhiteboardCard (Spatial 2D notes: ideas, factors, hypotheses, questions)
                 └── WhiteboardCluster (Thematic bounding boxes grouping related cards)

Access Control & Anonymity Modes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Query-level security is enforced across all models using a custom ``GroupScopedManager``, preventing cross-tenant data leakage. When creating a ``WorkshopSession``, facilitators configure one of four access modes to match the session's privacy requirements:

1. **Restricted & Tracked (``RESTRICTED_TRACKED``)**:
   Restricted to authorized group members. All contributions are linked to participant user accounts in both the database and the canvas UI.
2. **Restricted & UI-Anonymized (``RESTRICTED_ANONYMIZED_UI``)**:
   Restricted to authorized group members. Participant usernames are masked in the UI with stable session pseudonyms (e.g., *Participant #3*), preserving psychological safety while maintaining database auditability for session administrators.
3. **Restricted & DB-Anonymized (``RESTRICTED_ANONYMIZED_DB``)**:
   Restricted to authorized group members, but card author foreign keys are permanently set to ``NULL`` upon creation. Neither other participants nor database administrators can deanonymize authorship.
4. **Public & Optional User (``PUBLIC_OPTIONAL_USER``)**:
   Open collaboration sessions where authentication is optional and unauthenticated guests can contribute anonymously alongside registered users.

Real-Time Event Distribution (Datastar SSE)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To enable collaborative multi-user editing without heavy JavaScript frameworks, the app uses `Datastar <https://data-star.dev>`_ Server-Sent Events (SSE) paired with Redis Pub/Sub:

* Canvas mutations (``POST /api/work/cards/``, ``POST /api/work/cards/move/``) persist updates to PostgreSQL and broadcast an event payload to channel ``verbal:whiteboard:{session_id}``.
* Connected browsers listen to ``GET /api/work/stream_session/{session_id}/``, which yields real-time SSE events (e.g., ``card_added``, ``card_moved``, ``clustered``) or HTML fragment merges.
* Heartbeat comments (``: heartbeat``) are sent every 15 seconds to prevent intermediate proxy timeouts.

.. note::
   **Redis Fallback**:
   If the Redis broker configured in ``CELERY_BROKER_URL`` is unavailable, standard REST persistence continues to work seamlessly. However, real-time synchronization between concurrent browser tabs will be disabled, returning an error event instructing clients to refresh.

LLM Synthesis & Causal Factor Extraction
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two specialized synthesis routines use structured Pydantic schemas via ``ai_service.generate_outline``:

* **Thematic Idea Clustering (``cluster_whiteboard_cards``)**:
  Extracts all session cards, prompts the local model with the session objective, and requests an ``IdeaClusteringPlan`` containing 2 to 5 thematic clusters. Cards are automatically repositioned into spatial cluster bounding boxes (width 340px, dynamic height).
* **Causal Factor Extraction (``extract_causal_factors_from_session``)**:
  Parses card contents against ``CausalGraphExtractionPlan``, identifying named variables, discrete state options (e.g., ``["Low", "High"]``), upstream causal drivers, and textual justification. Extracted variables are converted into specialized ``factor`` cards on the canvas ready for export to Grips or Blueprint design.

Export Capabilities
~~~~~~~~~~~~~~~~~~~

The endpoint ``GET /api/work/export_summary/{session_id}/`` compiles the entire session into a Markdown document complete with cluster breakdown tables, author attribution (honoring active anonymity modes), extracted causal dynamic tables, and open questions.


3. Observability & Health Signals
---------------------------------

To monitor workshop collaboration and verify proper operational health:

1. **Django Admin Inspection**:
   * Inspect **Work Organisation > Projects** to verify attached Django Groups and owner assignments.
   * Inspect **Workshop Sessions** to verify ``access_mode``, active ``Conversation`` link, and member list.
   * Inspect **Whiteboard Cards** to verify coordinate values (``pos_x``, ``pos_y``), ``card_type``, and ``metadata`` JSON payloads.

2. **SSE Stream Verification**:
   Inspect the browser network tab or test using ``curl``:

   .. code-block:: bash

      curl -N -H "Accept: text/event-stream" http://127.0.0.1:8000/api/work/stream_session/<session_id>/

   A healthy stream immediately yields:

   .. code-block:: text

      event: connected
      data: {"session_id": "...", "status": "active"}

      : heartbeat

3. **Clustering & Extraction Logs**:
   Look for structured synthesis messages in the web process log:

   .. code-block:: text

      INFO:work_organisation.clustering: Clustering completed for session 12: 3 clusters formed.
      INFO:work_organisation.events: Published whiteboard event 'clustered' to channel verbal:whiteboard:12


4. Diagnostic Tips & Common Failure Modes
-----------------------------------------

* **Redis Broker Offline**:
  If the Redis service is down or unreachable, users will see an error message in the real-time stream. Database operations still succeed. To restore multi-user live synchronization, verify Redis is running (e.g. ``redis-cli ping``) and check the ``CELERY_BROKER_URL`` setting in ``settings.py``.
* **Irreversible Anonymity in DB-Anonymized Mode**:
  When a session is configured as ``RESTRICTED_ANONYMIZED_DB``, author IDs are set to ``NULL`` immediately on write. If facilitators later need to trace authorship for accountability, they cannot do so. If auditability is required, use ``RESTRICTED_ANONYMIZED_UI`` instead.
* **Over-aggressive LLM Clustering**:
  Small language models (SLMs) may occasionally group fundamentally distinct ideas under vague titles (e.g., *"General Considerations"*). If clustering results are suboptimal, refine the Workshop's ``objective`` text to give the model stronger domain context before re-running clustering.
* **Extracted Causal Links Lacking Evidence**:
  The causal factor extraction pipeline asks the model to suggest upstream causes based on session notes. SLMs can hallucinate causal links between variables that merely co-occurred in discussion. Always inspect the ``justification`` column in the export summary before adopting causal links in study design.
* **Canvas Coordinate Overlap**:
  If multiple users drag cards simultaneously while offline or under high network latency, cards may overlap. Running the automated clustering routine or manually dragging cards resets their spatial bounding boxes.


Database Models Reference
-------------------------

.. automodule:: work_organisation.models
   :members:
   :undoc-members:
   :show-inheritance:


API Endpoints Reference
-----------------------

.. automodule:: work_organisation.api
   :members:
   :undoc-members:
   :show-inheritance:


Clustering & Synthesis Utilities
---------------------------------

.. automodule:: work_organisation.clustering
   :members:
   :undoc-members:
   :show-inheritance:


Event Streaming Reference
-------------------------

.. automodule:: work_organisation.events
   :members:
   :undoc-members:
   :show-inheritance:
