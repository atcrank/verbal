# Inefficiencies and Workarounds Log

This file tracks limitations in the backend architecture and the corresponding UI workarounds implemented to maintain correct state rendering.

## 1. Document Index Status (`currently_indexed` is always False)

### Inefficiency
The `Document` model contains a boolean field `currently_indexed` (defaulting to `False`) to track whether a file has been processed and added to the FAISS vector index. However, the backend ingestion pipeline (specifically `RAGService.ingest_queryset_documents` and the strategy execution methods) never sets this field to `True` upon successful chunking and indexing. As a result, all documents uploaded and processed show up with `currently_indexed = False` in the database.

### Workaround
To correctly display the indexing status (`🟢 Indexed` vs `🟡 Processing`) in the UI, the `list_documents` view evaluates document chunk coverage dynamically using:
```python
document.readingstrategy_set.filter(usages__isnull=False).exists()
```
This checks if the database contains any chunk usages generated for the document's reading strategies.

### Handoff / Future Fix
The backend ingestion logic in `background_resources.rag_service.RAGService` should be modified at the completion of document processing to mark `currently_indexed = True` and persist the chunking scheme in metadata before saving the model.
