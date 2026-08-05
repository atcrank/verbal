Background Resources - context documents
========================================

This app is responsible for the entire Retrieval-Augmented Generation (RAG) pipeline, from document ingestion and chunking to vector storage and retrieval.

App achievements
----------------

* **Multi-Format Ingestion:** Natively parses PDFs, Word Documents, PowerPoints, raw HTML/ZIP archives, and Jupyter Notebooks (preserving code blocks).
* **Advanced Reading Strategies:** Allows applying customizable processing layers to documents, including Regex Extraction, LLM Prompts, and Abbreviation identification.
* **Robust Database Integration:** Utilizes PGVector via PostgreSQL for seamless, scalable semantic vector indexing paired with standard relational data.
* **Background Processing:** Ingestion relies on Celery tasks, allowing the user to queue massive document libraries without locking the UI.
* **Vector Index Explorer:** A comprehensive Django Admin dashboard that evaluates data integrity, spots orphaned chunks/vectors, and allows live semantic search testing.

App enhancement
---------------

* **Hybrid Search:** Combine the current dense semantic search (PGVector) with a sparse keyword search (like BM25 or Postgres Full-Text) to improve precise term retrieval.
* **Search target selection enhancement:** Focus search on least common terms or expressions
* **Structurally aware chunking:**  better traction for section breaks of different kinds.
* **Visual Layout Parsing:** Upgrade the PDF parser to something layout-aware (like ``unstructured`` or specialized OCR) to better handle complex tables and multi-column formats.
* **Graph-RAG Integration:** Bridge the ingested chunks with the ``grips`` app to build relational metadata for chunks (e.g., "Chunk A contradicts Chunk B").

Unified Retrieval & Deep RAG Design Decisions
-------------------------------------------

Recent updates overhauled the retrieval architecture to prioritize token efficiency, novelty, and completeness using a "Super Retriever" approach (`get_deep_context_report`). Key design decisions include:

* **Token Efficiency & Relevance:** Only material strictly aligned with the prompt and the active conversation `state_tree` is injected into the LLM context.
* **Completeness & Fast Deterministic Paths:** We rely on `ConceptNode`s for complete ideas and summaries (via `PromptStrategy` generation), and utilize fast deterministic paths like PostgreSQL's `SearchVector` for robust lexical fallback.
* **Lineage-Aware Deduplication & Boosting:** When raw `RAGChunk`s overlap semantically with their derived `ConceptNode` summaries, the unified retrieval system (`unified_retrieve`) prefers the higher-level Grips concept and drops the redundant raw chunk. The remaining concept receives a ranking distance boost to signify its higher informational density.
* **The NM_Deep_Reader Sub-Blueprint:** A specialized sub-blueprint that digests the "Super Retriever" report—fusing Semantic, Lexical, Citation Graph, and Private User Conversation Logs—into a dense, high-signal prompt injection.

Database models
---------------

.. automodule:: background_resources.models
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

RAG Service
-----------

.. automodule:: background_resources.rag_service
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

NLP Service
----------------

.. automodule:: background_resources.nlp_service
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

Background Tasks
----------------

.. automodule:: background_resources.tasks
   :members:
   :undoc-members:
   :member-order: bysource