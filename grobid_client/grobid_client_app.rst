Grobid Client - Deterministic Scientific PDF Parsing & Citation Extraction
===========================================================================

The **Grobid Client** application integrates the specialized open-source `GROBID <https://github.com/kermitt2/grobid>`_ (GeneRation Of BIbliographic Data) service into Verbal. It performs deterministic structural parsing of academic PDF research papers into structured TEI XML, extracting publication metadata (titles, authors, abstracts, journals, DOIs) and constructing relational citation graphs.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Raw Text Extraction Fails on Academic PDFs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Scientific papers are visually dense documents characterized by multi-column layouts, marginal notes, embedded formulas, figure captions, author affiliations, and extensive bibliographic lists. 

Standard PDF text extractors (such as `pypdf` or `pdfminer`) read character streams geometrically. On multi-column academic papers, they produce severe failure modes:

* **Column Interleaving**: Reading across columns produces jumbled sentences where left-hand and right-hand paragraphs merge into nonsensical word salads.
* **Header and Footnote Noise**: Running headers, page numbers, and license disclaimers are repeatedly injected into paragraph text, polluting downstream vector embeddings.
* **Unstructured Bibliography**: Reference lists are scraped as raw strings without separating author names, publication years, or DOIs, preventing automated cross-referencing.

Attempting to fix these structural failures using raw LLM prompts is computationally expensive, prone to hallucinated titles, and token-inefficient. **GROBID** solves this by applying deterministic sequence-labeling models trained specifically on academic typography.


2. Architecture & Mechanism
---------------------------

GROBID Microservice Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The service operates as a containerized microservice defined in [docker-compose.yml](file:///home/crank/coding/antigrav/verbal/docker-compose.yml) (`verbal_grobid` on port 8070, using image `grobid/grobid:0.8.1`):

1. When a research PDF is uploaded to `background_resources`, a background task in `grobid_client.tasks` sends the binary file to GROBID's `/api/processFulltextDocument` endpoint.
2. GROBID returns standard **TEI XML** (Text Encoding Initiative), cleanly separating front-matter metadata, body section headings, paragraph blocks, and bibliography entries.
3. The parser extracts the structured elements and populates relational database records.

Key Data Models
~~~~~~~~~~~~~~~

* **``Reference``**: Represents the parsed metadata for a document in the library. Stores the raw cached `tei_xml`, cleaned title, comma-separated authors, abstract, publication journal, year, volume, issue, page numbers, and resolved DOI.
* **``Citation``**: A directed graph edge representing one document citing another (`source_reference` $\rightarrow$ `target_reference`). When a paper cites another document already present in the local database, an explicit relational link is established.

Coordination with RAG
~~~~~~~~~~~~~~~~~~~~~
The structured TEI XML directly informs `background_resources`:

* The extracted title and abstract provide high-signal concept summaries for `ConceptNode` generation in `grips`.
* Paragraph bodies are chunked respecting section boundaries rather than arbitrary character counts.
* The citation graph provides a third retrieval path alongside dense semantic search and lexical matching.


3. Observability & Health Signals
---------------------------------

How to Know the GROBID Parser is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Populated Metadata Fields**:
   In Django Admin under **Grobid Client > References**, inspect parsed documents. A successful parse exhibits:
   * **Title**: Accurately matches the paper's title without trailing author names or institutional addresses.
   * **Authors**: Cleanly formatted comma-delimited author list (e.g., *Li, Wei, Chen, Jing, Wang, Hua*).
   * **DOI**: Contains a valid, resolvable DOI string (e.g., `10.1145/3544548.3581388`).
2. **Citation Edge Creation**:
   For documents containing rich bibliographies, inspecting the **Citations** table reveals outgoing citation links resolving to target publications.
3. **Container Responsiveness**:
   Pinging `http://127.0.0.1:8070/api/isalive` returns an immediate HTTP 200 confirming the GROBID JVM service is ready.


4. Diagnostic Tips & Failure Modes
----------------------------------

When the Parser Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Scanned or Image-Only PDFs (Empty TEI Output)**:
  * *Hazard*: GROBID requires an embedded digital text layer. When presented with scanned historical papers or faxed documents, it returns empty body text.
  * *Remedy*: Pre-process image-based PDFs with an OCR tool (e.g. `tesseract` or `ocrmypdf`) before importing them into Verbal, or configure the RAG pipeline's fallback text extractor.
* **GROBID Service Timeouts on Massive Monographs**:
  * *Hazard*: Processing a 300-page book or thesis through GROBID fulltext extraction can exceed the HTTP client timeout (defaulting to 60–120s).
  * *Remedy*: For long monographs, split the PDF into individual chapters, or configure the background task to call `/api/processHeaderDocument` rather than parsing all 300 pages of body text at once.
* **Missing Target References in Citation Graph**:
  * *Hazard*: A paper lists 40 citations, but no ``Citation`` records appear in the database.
  * *Explanation*: The system only creates `Citation` database rows when the cited work matches an existing `Reference` already present in your local library. If the cited papers have not been imported, the raw citation is stored in `extended_metadata` without a relational database edge.


Module Reference
----------------

.. automodule:: grobid_client.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: grobid_client.tasks
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: grobid_client.api
   :members:
   :undoc-members:
   :show-inheritance:
