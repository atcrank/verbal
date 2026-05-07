Grips (Knowledge Graph)
=======================

This app is an LLM-curated Knowledge Graph and highly structured wiki. It provides the AI with "handles" or "grips" on dense conceptual areas, acting as a structured compensating context engine.

App achievements
=================

* **Structured Ontology:** Separates human-readable markdown narrative from machine-readable JSON propositions (structured claims).
* **Automated Curation:** Allows creating stub entries and dispatching background Celery tasks to write dense, encyclopedic entries based on the RAG document store.
* **Semantic Relationships:** Defines hard relationship edges (``DEPENDS_ON``, ``INCLUDES``, ``EXEMPLIFIES``, ``RELATED_TO``) to build a traversable graph.
* **Admin Wiki Experience:** Integrates dynamic Markdown rendering and contextual URL linking directly into the Django Admin to mimic an Obsidian-like wiki.

App enhancement
===============

* **Document summary:** Develop pipeline to summarise a whole document into a set of related concept nodes informed primarily from that document.
* **Automated Linting Pipeline:** Develop background linters that actively evaluate new ``ConceptNodes`` for contradictions, missing cross-references, and style-guide violations.
* **Edge Extraction:** Create LLM tasks that read narratives and automatically suggest new ``KnowledgeEdge`` relationships between existing concepts.
* **Graph Visualization:** Build a frontend module (e.g., using D3.js or Cytoscape) to allow users and developers to visually navigate the concept network.
* **RAG Interception:** Intercept user queries to first traverse the ``grips`` graph for high-level context before executing standard semantic document search.
* **Metacognitive Verification:** Allow the AI to query its own generated claims against the ``benchmarking`` logic to score its own wiki entries for faithfulness.




