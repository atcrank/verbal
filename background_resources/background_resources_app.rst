Background Resources - Retrieval-Augmented Generation & Document Ingestion
==========================================================================

The **Background Resources** application provides the document processing, semantic vector storage, and multi-path retrieval-augmented generation (RAG) pipeline for Verbal. It enables experiment designers to ground their research inquiries in peer-reviewed literature, domain glossaries, and experimental protocols while mitigating the attention failure modes of local language models.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Grounded Retrieval is Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Large Language Models possess general linguistic fluency but lack reliable knowledge of specialized research findings, lab-specific protocols, and exact empirical parameters. Without grounding in authoritative source documents, models generate plausible-sounding but factually ungrounded study designs.

The Dual Hazards of RAG on Small Local Models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Naive RAG architectures—which simply fetch the top-$k$ nearest text chunks by cosine similarity and dump them into the prompt—routinely break down when paired with small open-weights models (2B–8B parameters). Verbal's RAG architecture was specifically designed to navigate two opposing failure modes:

* **Hazard 1: Prompt Hijacking & Chunk Captivity**:
  Small models have limited attention bandwidth. When a concise user prompt (e.g. 30 tokens asking for a control group specification) is prepended with massive raw text chunks (e.g. 1,500 tokens of engineering background), the model's self-attention is overwhelmed by the chunk. The model falls captive to the retrieved text, outputting rambling summaries of the document while completely ignoring the user's question.
* **Hazard 2: The Superficiality Trap & Word-Match Crowding**:
  In early attempts to mitigate Chunk Captivity, developers favored short glossary definitions. However, broad search terms ("smoke", "sensor", "trial") caused single-sentence dictionary entries to leapfrog substantive empirical papers, starving the model of real experimental data.

**Background Resources** resolves this tension through **salience windowing**, **discriminative glossary matching**, and **lineage-aware concept promotion**.


2. Architecture & Mechanism
---------------------------

The Document Ingestion Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. **Multi-Format Ingestion**: The system ingests research PDFs, Word documents (.docx), presentations (.pptx), text archives, and Jupyter Notebooks (preserving code blocks).
2. **Deterministic Pre-Parsing**: Academic PDFs are routed through the containerized `grobid_client` to extract structured TEI XML, isolating headings, body paragraphs, and bibliographies.
3. **Configurable Reading Strategies**: Processing layers attached to documents:
   * **``ReadingStrategy`` (Default)**: Normalizes text and chunks body paragraphs respecting sentence and section boundaries.
   * **``RegexStrategy``**: Extracts structured entities (e.g. equipment codes, chemical formulas) matching defined regular expressions.
   * **``PromptStrategy``**: Passes chunks through an LLM to synthesize high-level conceptual summaries.
   * **``AbbreviationStrategy``**: Builds an authoritative glossary of domain acronyms and expanded forms.
4. **Vector Indexing with PGVector**: Computes dense vector embeddings and stores them natively in PostgreSQL using `pgvector.django.VectorField` with cosine distance indexing.
5. **Background Execution**: Ingestion, OCR, and vectorization are executed asynchronously via `verbal_tasks`, ensuring file uploads never block the user interface.

Unified Retrieval & Deep RAG Architecture
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Rather than performing naive nearest-neighbor lookups, the system uses a **Super Retriever** (`unified_retrieve` and `get_deep_context_report`):

* **Dense Semantic Search**: Cosine similarity matching over PGVector chunk embeddings.
* **Lexical Fallback**: PostgreSQL `SearchVector` full-text search, ensuring exact technical terms, acronyms, or proper names are captured even if embedding models fail to place them close in vector space.
* **Lineage-Aware Deduplication & Concept Promotion**: When raw `RAGChunk` records semantically overlap with higher-level `ConceptNode` entities in `grips` derived from the same source, the system drops the redundant raw chunk and injects the curated concept node with a ranking priority boost.
* **Salience Windowing**: Instead of injecting 1,000-word sections, the retriever extracts a focused excerpt window (150–250 tokens) centered on the highest concept density, labeled with an academic citation header: `[Source: Author (Year) — Section: Name]`.


3. Observability & Health Signals
---------------------------------

How to Know Retrieval is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Vector Index Explorer**:
   In Django Admin under **Background Resources > Vector Index Explorer**, administrators can:
   * Test live queries and inspect the similarity score distribution.
   * Verify embedding coverage and spot orphaned chunks lacking vector representations.
   * Review chunk boundaries to ensure sentences are not abruptly severed mid-word.
2. **Balanced Context Ratios in Prompt Logs**:
   In `PromptResponseLog`, healthy generations show retrieved context comprising between 20% and 50% of the total prompt tokens—providing adequate grounding without drowning the user's prompt.
3. **Grounded Attributions in AI Responses**:
   The assistant's outputs explicitly cite retrieved sources (e.g., *"Following Li et al. (2023), sensor placement requires..."*) rather than speaking in generic generalities.


4. Diagnostic Tips & Failure Modes
----------------------------------

When Retrieval Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Prompt Hijacking (AI Ignores Prompt to Talk About Document)**:
  * *Hazard*: The retrieved chunk is too large or too tangentially related, causing the LLM to fixate on the excerpt.
  * *Remedy*: In the retrieval call, reduce `max_chunks` (e.g. from 5 down to 2 or 3). Ensure the context framing prompt in `demo_ui/views.py` includes the guardrail: *"Use the following reference excerpts to ground your response. If an excerpt is not directly relevant to the specific experimental question, ignore it."*
* **Terminology Misses (Exact Technical Keywords Overlooked)**:
  * *Hazard*: Dense embedding models occasionally fail on niche domain abbreviations (e.g. *FMCW*, *UWB*, *IMU*).
  * *Remedy*: Create an `AbbreviationStrategy` for the document, or ensure the query triggers the lexical PostgreSQL `SearchVector` fallback by matching exact keyword tokens.
* **Chunk Fragmentation (Key Concepts Broken Across Chunks)**:
  * *Hazard*: Chunk size is too small, splitting an experimental method across two halves so neither chunk contains the full context.
  * *Remedy*: In the document's `ReadingStrategy`, increase `chunk_size` (e.g., from 300 to 500 characters) and adjust `chunk_overlap` (e.g., 50 to 100 characters), then trigger the Django Admin action **"Re-read Document"**.


Module Reference
----------------

.. automodule:: background_resources.models
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: background_resources.rag_service
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: background_resources.nlp_service
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource

.. automodule:: background_resources.tasks
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource