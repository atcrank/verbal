Work Organisation - Collaborative Whiteboards & Assisted Study Design
========================================================================

The **Work Organisation** app provides multi-user collaboration, workshop management, group-scoped access control, and assisted whiteboard ideation for study design. It enables teams to organize research inquiries into hierarchical projects, conduct synchronous or asynchronous ideation sessions, cluster ideas with LLMs, extract causal factors, and stream real-time canvas updates.

.. contents:: Table of Contents
   :local:
   :depth: 2


App Achievements & Core Capabilities
------------------------------------

1. **Hierarchical Project & Workshop Scoping**
   Organizes study design workflows hierarchically: ``Project`` -> ``Workshop`` -> ``WorkshopSession`` -> ``WhiteboardCard`` / ``WhiteboardCluster``.

2. **Group-Scoped Access Control & Privacy Modes**
   Provides automatic query-level permission scoping via ``GroupScopedManager``, supporting granular Django Groups and 4 distinct anonymity modes for sensitive workshops:

   * **Restricted & Tracked (``RESTRICTED_TRACKED``)**: Restricted to group members; participant inputs are tracked and attributed to their user accounts in both UI and database.
   * **Restricted & UI-Anonymized (``RESTRICTED_ANONYMIZED_UI``)**: Restricted to authorized groups, but participant identities are masked in the UI with stable pseudonyms (e.g., *Participant #42*), while maintaining DB auditability.
   * **Restricted & DB-Anonymized (``RESTRICTED_ANONYMIZED_DB``)**: Restricted to authorized groups, but all card author foreign keys are stripped (set to ``NULL``) in the database to guarantee total non-attribution.
   * **Public & Optional User (``PUBLIC_OPTIONAL_USER``)**: Open sessions where authentication is optional and anonymous contributors can participate.

3. **Collaborative Whiteboard Canvas & Thematic Clustering**
   Supports spatial canvas cards (ideas, causal factors, Grips concepts, open questions, hypotheses). Integrates with local LLMs to synthesize unstructured cards into titled, color-coded ``WhiteboardCluster`` bounding boxes.

4. **Causal Graph & Factor Extraction**
   Analyzes whiteboard notes with structured schema output to extract causal variables, candidate discrete states, and influence relationships directly into study design artifacts.

5. **Real-time Event Synchronization via Datastar SSE**
   Streams incremental canvas updates (card movements, additions, clustering events) over Redis pub/sub using the lightweight `Datastar <https://data-star.dev>`_ Server-Sent Events protocol.

6. **Markdown & Mermaid Export**
   Exports whiteboard canvases into clean GitHub-flavored Markdown reports with structured tables and embedded Mermaid diagrams.


Database Models
---------------

The domain model hierarchy is structured as follows:

* **``Project``**: The top-level container for workshops, experiments, and whiteboards. Associated with Django Groups and creator ownership.
* **``Workshop``**: A specific collaborative milestone or study design track under a Project.
* **``WorkshopSession``**: An active collaborative session (whiteboard, interview dialogue, or drafting document) linked to a conversational thread and configured with an access mode.
* **``ConversationMember``**: Manages multi-user roles (``owner``, ``editor``, ``viewer``) and UI display aliases on sessions.
* **``WhiteboardCard``**: Individual spatial cards on the canvas with 2D coordinates, card types (``idea``, ``factor``, ``concept``, ``question``, ``hypothesis``), author attributes, and custom JSON metadata.
* **``WhiteboardCluster``**: Thematic bounding groups created manually or synthesized by LLMs to group related cards.

.. automodule:: work_organisation.models
   :members:
   :undoc-members:
   :show-inheritance:


API Endpoints
-------------

The app exposes Django-Ninja REST and SSE endpoints routed under ``/api/work/``:

Hierarchy & Session Management
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``GET /api/work/projects/``: Lists all projects accessible to the authenticated user.
* ``POST /api/work/projects/``: Creates a new project and attaches authorized Django Groups.
* ``GET /api/work/workshops/``: Lists workshops accessible to the user, optionally filtered by ``project_id``.
* ``POST /api/work/workshops/``: Creates a new workshop within a project.
* ``POST /api/work/sessions/new/``: Quick-launch endpoint that provisions a ``WorkshopSession``, links an underlying ``Conversation``, and assigns ownership roles.
* ``GET /api/work/sessions/{session_id}/``: Retrieves full session state (clusters, cards, member aliases) with active anonymity rules applied.

Canvas Mutations & Real-time Streaming
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``POST /api/work/cards/``: Creates a new whiteboard card and broadcasts a ``card_added`` event.
* ``POST /api/work/cards/move/``: Updates card 2D coordinates and cluster assignment, broadcasting a ``card_moved`` event.
* ``GET /api/work/stream_session/{session_id}/``: Real-time Datastar SSE stream delivering canvas updates over Redis pub/sub.
* ``POST /api/work/stream_response/``: Progressive token-by-token LLM generation stream.

AI Analysis & Export Endpoints
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``POST /api/work/cluster_ideas/``: Triggers LLM thematic clustering to group loose sticky notes into clusters.
* ``POST /api/work/extract_causal_graph/``: Extracts causal factors, state options, and directional influence links.
* ``GET /api/work/export_summary/{session_id}/``: Generates and returns a formatted Markdown report of the session.

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


Real-time Event Streaming
-------------------------

.. automodule:: work_organisation.events
   :members:
   :undoc-members:
   :show-inheritance:
