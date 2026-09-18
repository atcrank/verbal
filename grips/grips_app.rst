Grips - Structured Knowledge Graph & Propositional Claims
=========================================================

The **Grips** (Graph Representation of Intelligent Problem Solving) application provides an LLM-curated Knowledge Graph and structured research wiki for Verbal. It equips the assistant with formal conceptual "handles" or "grips" on dense research domains, pairing human-readable Markdown narratives with machine-readable propositional claims in JSON.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why a Knowledge Graph is Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
While standard RAG retrieves raw text excerpts, study design requires understanding how concepts interlock: which experimental factors *depend on* physical constraints, which methodologies *contradict* prior assumptions, and what definitions are strictly authoritative.

Relying solely on unstructured Markdown wikis or conversational summaries introduces serious failure modes over time:

* **Semantic Drift**: As an LLM edits a document across multiple turns, subtle factual distortions accumulate, turning precise empirical findings into fuzzy generalities.
* **Hidden Contradictions**: An assistant may assert Proposition A in one conversation turn and an incompatible Proposition B in another without detecting the conflict.
* **Unverifiable Assertions**: In pure text, claims are entangled with rhetorical prose, making automated mathematical or logical verification impossible.

**Grips** resolves this by enforcing a dual-layer representation: a natural language narrative for human readers, backed by explicit, verifiable propositional claims and directed relationship edges.


2. Architecture & Mechanism
---------------------------

The Ontology Structure
~~~~~~~~~~~~~~~~~~~~~~
The graph is organized into three core models:

* **``ConceptDomain``**: High-level thematic boundaries (e.g. *Robotic Firefighting*, *Clinical Pharmacology*, *Autonomous Navigation*) that scope queries and prevent cross-domain conceptual confusion.
* **``ConceptNode``**: An individual conceptual entry containing:
  * **Title & Slug**: Canonical identifier.
  * **Narrative Content**: Human-readable Markdown providing context, background, and syntheses.
  * **Structured Claims (``claims_json``)**: A list of discrete, machine-evaluable propositions (e.g. `{"subject": "LiDAR", "predicate": "degraded_by", "object": "dense_smoke", "confidence": 0.95, "source_chunk_id": 104}`).
* **``KnowledgeEdge``**: Directed relational links connecting two `ConceptNode` instances with typed semantic relationships:
  * ``DEPENDS_ON``: Indicates a functional or causal prerequisite.
  * ``INCLUDES``: Taxonomic parent/child containment.
  * ``EXEMPLIFIES``: Concrete experimental instance of a general principle.
  * ``RELATED_TO``: Associative domain connection.
  * ``CONTRADICTS``: Explicit conflict requiring designer attention.

Automated Curation & Linting Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Grips is actively maintained through background tasks (`verbal_tasks`):

* **Concept Synthesis**: Given an uploaded document, tasks extract core concepts, synthesize the narrative, and formulate discrete claims.
* **Automated Node Linting (``lint_concept_node``)**: An automated critique pass inspects `claims_json` against the document store to flag unanchored assertions, vague predicates, or factual contradictions.
* **Edge Linting (``lint_knowledge_edge``)**: Verifies that relationship edges between nodes are logically justified, improving edge annotations or recommending edge pruning.

Interactive OKF Wiki & Bidirectional Linking
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To replace external standalone wiki containers (like SilverBullet.md) without adding runtime overhead or unapproved ports, Verbal integrates a native, Git-backed wiki for ``workspaces/grips_okf`` directly into Django and Granian:

* **Vendored Frontend Editor**: Utilizes a standalone build of **Milkdown Crepe** (``crepe.bundle.mjs`` and ``crepe.css`` vendored in ``static/vendor/milkdown/`` via ``manage_vendored_resources.sh``). No Node.js or npm dependencies are required in deployment.
* **Read-Only Default with In-Place Edit**: Wiki pages render read-only by default with styled typography and clickable ``[[wikilinks]]``. Clicking **Edit Page** toggles seamlessly into Milkdown Crepe WYSIWYG Markdown editing.
* **Responsive Wikilink Autocomplete**: Typing ``[[`` in the editor activates an inline autocomplete dropdown querying ``/wiki/api/query-links/?q=...``, allowing users to rapidly cross-reference concepts, documents, and references.
* **Two-Way Database Synchronization**: When a user saves a wiki page:
  1. The Markdown file in ``workspaces/grips_okf`` is updated.
  2. Git stages and commits the change with author metadata and commit message.
  3. The corresponding ``ConceptNode`` and ``KnowledgeEdge`` records in PostgreSQL are updated atomically, triggering vector re-indexing for PGVector search.
* **Git Version Tracking & Reflog**:
  * Per-document commit history and diffs under ``/wiki/history/<path>/``.
  * Concurrency safety with 3-way merges (``git merge-file``) if a user edits against an older commit.
  * Global workspace activity reflog under ``/wiki/activity/`` displaying chronological commit logs, author attributions, and unified diffs.
* **Strict Path Sandboxing**: Enforces that all file access resides strictly within ``workspaces/grips_okf``, preventing exposure of LLM scratch directories or host system paths.
* **Network Graph Explorer (``/wiki/graph/``)**:
  * Interactive network visualization powered by vendored **vis-network.js** (no npm/Node runtime dependency).
  * Three visualization modes:
    * **Knowledge Concepts**: Clusters concept nodes by domain taxonomy and highlights directional semantic edges (``DEPENDS_ON``, ``INCLUDES``, etc.).
    * **Citation Network**: Visualizes bibliometric citation lineages between ingested literature and external cited references.
    * **Document-Concept Hybrid**: Bridges literature sources directly to the extracted concepts derived from them.
  * Physics toggle, node search filter, fit-to-screen controls, interactive node inspector drawer, and exportable Mermaid graph syntax modal.
* **Grobid Literature Clarification & Citation Contexts**:
  * Clear differentiation between **🟢 Full Document in Library** (with PDF view button) and **🟡 Cited Reference** (extracted from bibliographies without ingested full text).
  * Direct blockquote rendering of citing document context snippets (``Citation.context_text``), showing exact sentences where works were referenced.
  * Easy pathways to ingest missing PDFs via Django Admin.
* **Academic Research Analytics**:
  * **Reading Prioritization (``/wiki/research/reading/``)**: Identifies seminal literature ranked by bibliometric in-degree and generates an acquisition wishlist of highly-cited works missing full PDFs.
  * **Synthesis Matrix (``/wiki/research/matrix/``)**: Cross-tabulates ingested documents against core concepts to map empirical coverage and knowledge gaps.
* **Title Sanitization & Defective Node Cleanup**:
  * Human-readable title formatting (``clean_human_title``) strips technical prefixes (e.g., ``doc-11-c3-``) and formats proper titles.
  * Automated pruning command (``python manage.py cleanup_grips_wiki``) eliminates corrupt placeholder nodes and dead directories.


3. Observability & Health Signals
---------------------------------

How to Know the Knowledge Graph is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Interactive OKF Wiki & Activity Reflog**:
   Navigating to ``/wiki/`` presents the domain hierarchy and top concepts, while ``/wiki/activity/`` provides an audit stream of all human and automated knowledge revisions.
2. **Integrated Admin Wiki Experience**:
   In Django Admin under **Grips > Concept Nodes**, the interface renders dynamic Markdown previews with contextual URL links, mirroring an Obsidian-like research wiki.
3. **Dense Propositional Grounding**:
   Healthy concept nodes feature rich `claims_json` where each claim maps to specific citations or `source_chunk_id` references rather than ungrounded generalities.
4. **Graph Connectivity & Backlinks**:
   Navigating to a node displays its incoming backlinks and outgoing `KnowledgeEdge` links. Concepts form coherent, navigable clusters within their `ConceptDomain`.


4. Diagnostic Tips & Failure Modes
----------------------------------

When the Graph Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Graph Bloat (Explosion of Redundant Nodes)**:
  * *Hazard*: Automated LLM extraction can generate dozens of near-synonymous nodes (e.g., *"Smoke Density"*, *"Aerosol Concentration"*, *"Particulate Obscuration"*).
  * *Remedy*: In Django Admin, inspect the domain's concept list. Merge synonymous nodes into a single canonical entry, placing the alternative expressions into the node's narrative or aliases.
* **Circular Dependencies in Causal Paths**:
  * *Hazard*: Two experimental variables are accidentally linked cyclically (A `DEPENDS_ON` B and B `DEPENDS_ON` A), confusing downstream causal graph extraction.
  * *Remedy*: Run the edge linting task or inspect the graph neighbors to ensure dependency edges form a directed acyclic graph (DAG).
* **Unanchored or Hallucinated Claims**:
  * *Hazard*: The LLM formulates claims that sound authoritative but do not exist in the source literature.
  * *Remedy*: Trigger the **Lint Concept Node** action in the Django Admin. The linter checks claims against the document store and flags ungrounded propositions with `improved_justification` notes.


Module Reference
----------------

.. automodule:: grips.models
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: grips.tasks
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: grips.wiki_service
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: grips.wiki_views
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

