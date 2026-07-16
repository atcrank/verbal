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