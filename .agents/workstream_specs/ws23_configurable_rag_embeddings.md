# WS23: Configurable RAG Embedding Model & Offline Path Resolution

## Goal
Decouple the RAG and vector embedding system from the hardcoded `"all-MiniLM-L6-v2"` string by introducing an explicit environment variable and Django configuration setting (`REASON_EMBEDDING_MODEL` / `settings.EMBEDDING_MODEL_NAME`). Support canonical namespace identifiers (e.g. `sentence-transformers/all-MiniLM-L6-v2`), Artifactory remote mirrors, and offline local filesystem directories while strictly validating and documenting the 384-dimensional PostgreSQL `pgvector` constraint.

---

## Architectural Problems Identified

1. **Hardcoded Legacy Shorthand**:
   `background_resources/rag_service.py` hardcoded `model_name="all-MiniLM-L6-v2"`. In modern Hugging Face and enterprise Artifactory mirrors, the shorthand without the organisation prefix (`sentence-transformers/`) fails to resolve, returning `404 Not Found`.

2. **Inflexible Deployment & Air-Gapped Environments**:
   Deployments without direct internet access or using mirrored local model directories on disk (e.g. `/srv/reason/models/embeddings/all-MiniLM-L6-v2`) had no configuration mechanism to point `RAGService` or `benchmarking/tasks.py` to a local path.

3. **Database Schema Invariant (384 Dimensions)**:
   The database schema in both `background_resources.models.RAGChunk` and `grips.models.ConceptNode` enforces `dimensions=384` on PostgreSQL `VectorField`. While users must be able to specify the source (local path, Artifactory namespace, or alternative 384-dim model), embeddings cannot be dynamically selectable per user query without risking database crashes due to dimensionality mismatch.

---

## Solution Design

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Configuration Resolution                        │
│                                                                        │
│  1. settings.EMBEDDING_MODEL_NAME                                      │
│  2. os.environ["REASON_EMBEDDING_MODEL"]                               │
│  3. Default: "sentence-transformers/all-MiniLM-L6-v2"                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 RAGService & Helper (rag_service.py)                   │
│                                                                        │
│  - get_embedding_model_name() -> str                                   │
│  - If path is existing local directory (os.path.isdir):                │
│      embed_kwargs["local_files_only"] = True                           │
│  - HuggingFaceEmbeddings(model_name=..., model_kwargs=...)             │
│  - RAGService(model_name=...) parameter injection for testing          │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│     PostgreSQL pgvector (384-dim)    │  │       benchmarking/tasks     │
│  - RAGChunk.embedding (384)          │  │  - Reuses resolved model     │
│  - ConceptNode.embedding (384)       │  │    for semantic diversity    │
└──────────────────────────────────────┘  └──────────────────────────────┘
```

---

## Key Files & Scope of Work

| File | Scope of Work |
| :--- | :--- |
| `verbal_config/settings.py` | Add `EMBEDDING_MODEL_NAME` setting reading `REASON_EMBEDDING_MODEL` or `EMBEDDING_MODEL_NAME`, defaulting to `"sentence-transformers/all-MiniLM-L6-v2"`. |
| `background_resources/rag_service.py` | 1. Implement `get_embedding_model_name()` helper.<br>2. Support `model_name` parameter in `RAGService.__init__`.<br>3. Automatically set `local_files_only=True` if `model_name` is an existing local directory. |
| `benchmarking/tasks.py` | Update `calculate_dataset_metrics` to use `get_embedding_model_name()` instead of hardcoded string. |
| `deploy/fabric_deploy.py` | Add `EMBEDDING_MODEL = os.environ.get("REASON_EMBEDDING_MODEL", ...)` to `DeploymentConfig` and propagate to systemd environment directives. |
| `background_resources/tests.py` | Add test suite verifying environment variable override, settings override, local directory detection, and 384-dimension output. |
| `deploy/tests.py` | Verify `REASON_EMBEDDING_MODEL` is rendered into systemd service templates. |
