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


3. Observability & Health Signals
---------------------------------

How to Know the Knowledge Graph is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Integrated Admin Wiki Experience**:
   In Django Admin under **Grips > Concept Nodes**, the interface renders dynamic Markdown previews with contextual URL links, mirroring an Obsidian-like research wiki.
2. **Dense Propositional Grounding**:
   Healthy concept nodes feature rich `claims_json` where each claim maps to specific citations or `source_chunk_id` references rather than ungrounded generalities.
3. **Graph Connectivity**:
   Navigating to a node displays its incoming and outgoing `KnowledgeEdge` links. Concepts should form coherent, navigable clusters within their `ConceptDomain`.


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
