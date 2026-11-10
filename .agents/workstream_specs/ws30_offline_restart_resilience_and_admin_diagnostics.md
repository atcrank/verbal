# Workstream 30: Offline Restart Resilience & SystemConfiguration Admin Diagnostics

## 1. Objective
Ensure the deployed Verbal / Reason stack can be stopped and restarted cleanly via `systemctl stop` / `start` / `restart` across extended offline or Artifactory-blocked periods without checking remote container registries, rebuilding images, or downloading uncommanded Hugging Face assets. Simultaneously, surface clear, readable availability status and notifications directly on the `llm_api` Global System Configuration Admin page when a hosting option (such as vLLM, Ollama, llama.cpp, or PyTorch models) is unavailable on the host, while preserving the ability to save valid preferences in the database.

---

## 2. Architecture & Design

### A. Zero-Rebuild / Zero-Pull `systemctl` Docker Restarts (`deploy/fabric_deploy.py`)
* **`reason-docker.service`**:
  * Update `ExecStart` directives to pass `--no-build --pull never`:
    * `ExecStart=/usr/bin/docker compose up -d --no-build --pull never db sandbox`
    * `ExecStart=-/usr/bin/docker compose --profile academic up -d --no-build --pull never grobid`
  * Ensures routine `systemctl start` / `restart` never attempts image pulls or container rebuilds (e.g. `sandbox/Dockerfile` `apt-get update` / `pip install`). Initial deployment builds continue cleanly through `start_backend_containers()`.

### B. Container Runtime Clients & Sync (`llm_api/vllm_client.py`, `llm_api/ollama_client.py`, `llm_api/apps.py`)
* `vllm_client.start_container(model_target, pull_policy="never") -> tuple[bool, str]`
* `ollama_client.start_container(pull_policy="never") -> tuple[bool, str]`
  * Pass `--no-build --pull never` by default so runtime backend sync and Admin saves never attempt multi-gigabyte image pulls or hang against blocked registries.
  * Return explicit `(ok: bool, message: str)` diagnostics instead of silently swallowing failures, and guard against `FileNotFoundError` when `docker` CLI is absent.
* `LazyServiceRegistry._sync_hosting_backend_state()`:
  * Skip container start/stop orchestration when running in client daemon roles (`VERBAL_ROLE` in `{"web", "worker", "scheduler"}`); only `inference` / `standalone` (or explicit `SystemConfiguration` saves) manage inference containers, using `pull_policy="never"`.

### C. Cache-First Hugging Face & SentenceTransformer Resolution
* **`background_resources/rag_service.py`**:
  * Add functional helper `load_huggingface_embeddings(model_name: str, device: str | None = None) -> HuggingFaceEmbeddings` that attempts `local_files_only=True` **first** for both local directory paths and Hugging Face repository IDs (e.g. `sentence-transformers/all-MiniLM-L6-v2`). Falls back to remote download only if absent from local cache and `not os.path.isdir(model_name)`.
* **`benchmarking/tasks.py` & `benchmarking/training.py`**:
  * Attempt `SentenceTransformer(..., local_files_only=True)` and `AutoTokenizer`/`AutoModelForCausalLM.from_pretrained(..., local_files_only=True)` first before remote fallback.
* **`llm_api/ai_service.py`**:
  * Add `AIService._resolve_tokenizer_source(config) -> tuple[str | None, bool]` returning `(tokenizer_id, allow_remote)`:
    * In `web` / `worker` / `scheduler` roles: if an explicit `system_tokenizer` is not set and `use_mini_model_for_utilities` + `active_mini_model` are configured, skip HF tokenizer loading entirely (`(None, False)` -> delegates to llama.cpp / char heuristic).
    * Otherwise prefer cached active `pytorch` or `vllm` model tokenizer with `local_files_only=True`, and never fall back to remote Hub downloads for the unconfigured default `google/gemma-4-E2B-it`.
  * Apply cache-first resolution in `AIService.label_single()` for `"facebook/bart-large-mnli"`.
* **`llm_api/modality_detector.py`**:
  * Add `allow_remote: bool = False` to `introspect_hf_modality()` so `LocalAIModel.__str__` and Django Admin dropdown rendering use disk cache (`local_files_only=True`) and safe name heuristics only, issuing zero outbound HTTP calls.

### D. `GlobalSystemConfig` Admin Readable Status Markup & Notifications (`llm_api/diagnostics.py`, `llm_api/admin.py`, `llm_api/models.py`)
* **Pure Local Readiness Inspector (`llm_api/diagnostics.py`)**:
  * `check_docker_daemon(timeout=1.5) -> DockerDaemonStatus`
  * `inspect_docker_service_readiness(...) -> DockerServiceReadiness` (`vllm`, `ollama`)
  * `check_hf_model_cached(model_id_or_path) -> HuggingFaceCacheStatus` (pure disk lookup via `try_to_load_from_cache` / `os.path.isdir`)
  * `check_llamacpp_binary() -> tuple[bool, str]`
  * `get_backend_availability_summary(sys_config=None) -> BackendAvailabilitySummary`
* **Inline Markup & Notifications in `SystemConfigurationAdmin`** (no separate large readiness matrix section):
  1. `backend_status_display`: Shows active runtime health plus explicit mismatch alert if the selected backend/model cannot run locally (e.g., vLLM image missing, HF model not cached, llama.cpp binary missing) and embedding cache status.
  2. Inline read-only availability indicators inside each backend section (`pytorch_availability_display`, `vllm_availability_display`, `ollama_availability_display`, `llamacpp_availability_display`).
  3. `SystemConfigurationAdminForm`: Annotates `hosting_backend` choices cleanly with `[Ready]` / `[Unavailable: ...]` while keeping all enum values valid and saveable in the database, plus `[Cached]` / `[Not Cached]` indicators on HF model dropdowns.
  4. `manage_hosting_backend` & `SystemConfigurationAdmin.save_model`: Attaches `_backend_sync_warning` when a container launch fails on save and emits `messages.WARNING` notifications in Django Admin explaining clearly why the saved preference cannot currently activate on the host.

---

## 3. Verification Plan
* `deploy/tests.py`: Assert `--no-build --pull never` in `reason-docker.service`.
* `background_resources/tests.py`: Verify `RAGService` passes `local_files_only=True` first for standard Hub IDs (`sentence-transformers/all-MiniLM-L6-v2`) and falls back on local cache miss.
* `llm_api/tests.py`:
  * Verify `vllm_client.start_container` and `ollama_client.start_container` use `--pull never --no-build` and return `(bool, str)`.
  * Verify `LazyServiceRegistry._sync_hosting_backend_state` does not touch Docker containers in `web`/`worker` roles.
  * Verify `AIService.load_models` skips HF network calls when `MiniModel` is configured and never triggers remote downloads for unconfigured default tokenizer fallbacks.
  * Verify `introspect_hf_modality` does not hit remote `AutoConfig` by default (`allow_remote=False`).
  * Verify `llm_api.diagnostics` probes report unavailable vLLM/Ollama images cleanly without raising.
  * Verify `SystemConfigurationAdmin` renders clear inline availability markup, keeps unavailable options saveable in DB, and emits Django Admin warning notifications on save.

