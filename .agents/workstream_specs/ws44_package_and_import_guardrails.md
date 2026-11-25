# Workstream 44 Spec: `torchao` Runtime/Deploy Quarantine & Worker `sentence-transformers` Import Diagnostics

**Status**: Completed (256/256 tests passing)  
**Branch**: `feat/ws44-package-and-import-guardrails`  
**Context**: Eliminating recurring manual `pip uninstall torchao` workarounds that break Gemma 4 12B BitsAndBytes NF4 quantization, and resolving misleading `"Could not import sentence_transformers python package"` errors in `reason-worker` when `sentence-transformers` is already installed in `/srv/reason/venv`.

---

## 1. Root-Cause Findings

1. **Why `torchao` Kept Breaking BitsAndBytes NF4 Quantization**:
   - `llm_api/torch_compat.py` previously tested `try: import torchao` and only quarantined `torchao` if that top-level import raised an exception. Because `torchao/__init__.py` uses lazy submodule loading, top-level `import torchao` succeeds cleanly—leaving `transformers.utils.import_utils.is_torchao_available()` as `True`.
   - Later, during `AutoModelForCausalLM.from_pretrained(..., quantization_config=BitsAndBytesConfig(bnb_4bit_quant_type="nf4"))`, `transformers 5.21` imports `torchao.quantization` / `torchao.dtypes` (`AffineQuantizedTensor` subclass checks), triggering ABI/symbol errors against `torch 2.6–2.8` or conflicting with `bitsandbytes` `Params4bit`.
   - Additionally, `deploy/fabric_deploy.py` executes `uv pip install -r requirements.txt` rather than `uv pip sync`, so once `torchao` enters `/srv/reason/venv` (e.g. via `unsloth`), routine deploys never uninstall it.

2. **Why `reason-worker` Reported Missing `sentence-transformers` Despite Being Installed**:
   - `langchain_huggingface.HuggingFaceEmbeddings.__init__` wraps `import sentence_transformers` inside:
     ```python
     try:
         import sentence_transformers
     except ImportError as exc:
         raise ImportError("Could not import sentence_transformers python package. Please install it with `pip install sentence-transformers`.") from exc
     ```
   - `sentence_transformers==6.2.0` eagerly imports `SentenceTransformerTrainer` -> `transformers`, `accelerate`, `peft`, `datasets`, and `torch`. Any transitive `ImportError` inside that dependency tree (most notably a broken `torchao` or `torchvision` C++ ABI in `/srv/reason/venv`) is caught by `langchain_huggingface` and rewritten into `"Could not import sentence_transformers python package"`.
   - `background_resources/rag_service.py:load_huggingface_embeddings` then caught `Exception as local_err`, discarded `local_err.__cause__` (hiding the real broken dependency), misclassified the `ImportError` as a Hugging Face Hub cache miss (`"not cached locally; attempting remote fetch..."`), and retried a second time.
   - Finally, `RAGService.__init__` only forced `device="cpu"` when `VERBAL_ROLE == "web"`, leaving `reason-worker` and `reason-scheduler` to default to `cuda:0` and contend with Gemma 4 12B NF4 for the 16 GB GPU (violating Core Invariant 4: zero VRAM outside `inference`).

---

## 2. Guardrail Design

1. **Unconditional PEP 451 `sys.modules` Quarantine of `torchao` (`llm_api/torch_compat.py`, `verbal_config/settings.py`)**:
   - Unless explicitly opted in via `VERBAL_ALLOW_TORCHAO=1`, `apply_torch_compatibility_shims()`:
     - Purges any already-imported `torchao` / `torchao.*` entries from `sys.modules` and sets `sys.modules["torchao"] = None` (per PEP 451, `importlib.util.find_spec("torchao")` returns `None` and `import torchao` raises `ModuleNotFoundError` without executing a single line of `torchao`),
     - Also patches `transformers.utils.import_utils.is_torchao_available` and `transformers.utils.is_torchao_available` if `transformers` was already imported earlier in the process.
   - Invoke `apply_torch_compatibility_shims()` at the top of `verbal_config/settings.py` as well as `verbal_config/__init__.py`, `llm_api/ai_service.py`, `background_resources/rag_service.py`, and `benchmarking/tasks.py`.

2. **Transparent `sentence_transformers` Pre-Flight & CPU Enforcement for Client Roles (`background_resources/rag_service.py`, `benchmarking/tasks.py`)**:
   - Add `verify_sentence_transformers_import()` called before `HuggingFaceEmbeddings`:
     - Imports `sentence_transformers` directly (bypassing `langchain_huggingface`'s misleading wrapper).
     - On `ImportError`, checks whether `sentence_transformers`'s package directory actually exists in `sys.path` (`Path` check on `site-packages`) and logs an actionable `ERROR` distinguishing **transitive dependency failure** (reporting `exc.name`, `exc.__cause__`, and traceback) vs. **wrong Python interpreter** (`sys.executable`), raising immediately without triggering a bogus remote Hub download retry.
   - Enforce `device="cpu"` across all non-inference client roles (`web`, `worker`, `scheduler`) unless overridden via `REASON_EMBEDDING_DEVICE`.

3. **Automated `torchao` Pruning in Deploy & Sync Scripts (`deploy/fabric_deploy.py`, `update_dependencies.sh`)**:
   - Add `build_torchao_prune_cmd(...)` in `deploy/fabric_deploy.py` and invoke it right after `requirements.txt` installation in `build_and_migrate` (when `VERBAL_ALLOW_TORCHAO` is not set), plus in the `pip install` fallback branch of `update_dependencies.sh`.
