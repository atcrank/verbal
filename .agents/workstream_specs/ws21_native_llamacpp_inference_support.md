# WS21: Native llama.cpp CPU-First Inference Support & GGUF Integration

## Goal
Implement first-class support for **llama.cpp** (`llama-server`) as a dedicated, CPU-first hosting backend in the `llm_api` application alongside existing PyTorch, vLLM, and Ollama backends.

This allows high-RAM, multi-core CPU workstations (and low-VRAM secondary nodes on home networks) to execute large quantized GGUF models with deep context windows (e.g. 16k–128k tokens) for long-running batch tasks without requiring dedicated GPU acceleration.

---

## Architecture & Integration Scope

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Django LLM API Routing                          │
├────────────────────────────────────────────────────────────────────────┤
│ SystemConfiguration.hosting_backend:                                   │
│  - PYTORCH   -> In-process HuggingFace / Outlines                      │
│  - VLLM      -> Containerized PagedAttention GPU (:8003)               │
│  - OLLAMA    -> Ollama container (:11434)                             │
│  - LLAMACPP  -> llama.cpp Server (:8080) [NEW]                         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP /v1/chat/completions
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      llama-server (CPU or GPU)                         │
│  - AVX2 / AVX-512 Vectorization                                        │
│  - OpenMP Thread Scaling (-t physical_cores - 1)                       │
│  - GGUF Quantized Models (Q4_K_M, Q5_K_M, Q8_0) in System RAM           │
│  - Native OpenAI Chat Completions & Grammar / JSON Schema API          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Key Files & Impact Scope

| App / Component | Files Affected | Scope & Responsibility |
| :--- | :--- | :--- |
| **Data Models** | `llm_api/models.py`<br>`llm_api/migrations/` | 1. Add `LLAMACPP = 'llamacpp', 'Local llama.cpp Server (CPU/GGUF)'` to `SystemConfiguration.HostingBackend`.<br>2. Add `active_llamacpp_model` FK in `SystemConfiguration`.<br>3. Extend `LocalAIModel.QuantizationMode` to include standard GGUF quantization formats (`GGUF_Q4_K_M`, `GGUF_Q5_K_M`, `GGUF_Q8_0`). |
| **Settings & Config** | `verbal_config/settings.py`<br>`.env.example` | Declare `LLAMACPP_BASE_URL` (default: `http://127.0.0.1:8080`), `LLAMACPP_DEFAULT_THREADS`, and `LLAMACPP_DEFAULT_CTX_SIZE`. |
| **Inference Engine** | `llm_api/ai_service.py`<br>`llm_api/llamacpp_client.py` (New) | 1. Create client utility for health checks (`/health`), model inspection, and server process status.<br>2. Wire `generate_openai_completion` and SSE streaming generator in `ai_service.py` to route to `LLAMACPP_BASE_URL`.<br>3. Handle JSON schema / regex grammar forwarding to `llama-server`. |
| **Hardware Inspector** | `llm_api/hardware.py` | Add CPU-specific hardware telemetry: physical core count, AVX/AVX2/AVX-512 support flags, total host RAM, free system memory, and recommended thread / context parameters. |
| **Admin & UI** | `llm_api/admin.py`<br>`templates/admin/llm_api/` | Add llama.cpp status indicator to `SystemConfigurationAdmin` hardware banner. Allow selecting GGUF files from `/srv/reason/models/` or Hugging Face GGUF hub IDs. |
| **Container & Deploy** | `docker-compose.yml`<br>`deploy/fabric_deploy.py` | 1. Add `llamacpp` service under `--profile cpu-inference` using `ghcr.io/ggerganov/llama.cpp:server`.<br>2. Add `reason-llamacpp.service` template to Fabric deployment automation. |
| **Tests & Docs** | `llm_api/tests.py`<br>`llm_api/llm_api_app.rst` | Comprehensive unit tests for routing, hardware detection, fallback, and updated Sphinx architecture docs. |

---

## Phased Implementation Plan

### Phase 1: Data Model & Settings Foundation
1. **Extend `HostingBackend`**:
   - In `llm_api/models.py`, add `LLAMACPP` choice to `SystemConfiguration.HostingBackend`.
   - Add `active_llamacpp_model = models.ForeignKey('LocalAIModel', null=True, blank=True, related_name='llamacpp_configurations')`.
   - Add `LocalAIModel.QuantizationMode` choices: `GGUF_Q4_K_M`, `GGUF_Q5_K_M`, `GGUF_Q8_0`, `GGUF_OTHER`.
2. **Settings**:
   - Add `LLAMACPP_BASE_URL = os.environ.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080")` in `verbal_config/settings.py`.
3. **Database Migration**:
   - Generate and apply Django migration `llm_api/migrations/000X_llamacpp_support.py`.

### Phase 2: Inference Client & Proxy Routing
1. **`llm_api/llamacpp_client.py`**:
   - Helper functions: `check_llamacpp_health()`, `get_available_llamacpp_models()`, `get_llamacpp_server_props()`.
   - Fast timeout (1.5s) to prevent web thread blocking when service is offline.
2. **`llm_api/ai_service.py`**:
   - Update `generate_openai_completion()`:
     ```python
     elif config.hosting_backend == 'llamacpp':
         llamacpp_url = getattr(settings, 'LLAMACPP_BASE_URL', 'http://127.0.0.1:8080')
         api_url = f"{llamacpp_url.rstrip('/')}/v1/chat/completions"
         target_model = config.active_llamacpp_model.hf_model_id if config.active_llamacpp_model else "llama-cpp"
     ```
   - Wire streaming generator (`generate_openai_completion_stream`) to consume tokens from `llama-server`.
   - Forward `response_format={"type": "json_object", "schema": ...}` when structured output is requested.

### Phase 3: Hardware Inspector & Resource Advisor
1. **CPU & RAM Inspection (`llm_api/hardware.py`)**:
   - Detect physical cores vs logical threads via `psutil`.
   - Check CPU flags (`/proc/cpuinfo` on Linux) for `avx512f`, `avx2`, `fma`.
   - Calculate recommended thread count: `threads = max(1, physical_cores - 1)`.
   - Calculate maximum safe GGUF model size: e.g. for a 32GB RAM system with 24GB free, recommend up to a 14B Q4/Q8 model while reserving 4GB for OS and Postgres.
2. **Admin Card**:
   - Display a "CPU Compute & llama.cpp Advisor" badge in `GlobalSystemConfigurationAdmin`.

### Phase 4: Bare-Metal Compilation, Systemd Supervision & Deployment Automation
1. **Source Bundle Detection (`resources/sources/` or `resource/sources/`)**:
   - Fabric task `build_llamacpp` checks for a local source archive (`llama*.tar.gz`, `llama*.zip`) or source directory in `resources/sources/` (or fallback `resource/sources/`).
   - If missing, prints an actionable guide banner explaining where to download the source bundle and place it in the untracked directory.
2. **Build Prerequisites & Preflight Checks**:
   - Verifies target host has `gcc`/`g++`, `make`, and `cmake`.
   - If missing, displays clear, helpful error messages showing the exact remediation:
     * Standard apt: `sudo apt update && sudo apt install -y build-essential cmake`
     * Or PyPI/Artifactory fallback for cmake: `pip install cmake`
3. **In-Place Compilation & Installation**:
   - Safely uploads and extracts the bundle into `/srv/reason/llama.cpp/`.
   - Compiles via `cmake -B build -DGGML_CUDA=OFF -DGGML_NATIVE=ON && cmake --build build --config Release -j $(nproc) --target llama-server`.
   - Installs the compiled binary into `/srv/reason/bin/llama-server` (executable by `verbal_run`).
4. **Systemd Process Supervision (`reason-llamacpp.service`)**:
   - Generates `/etc/systemd/system/reason-llamacpp.service` running `/srv/reason/bin/llama-server` with configurable model path (`/srv/reason/models/`), context size, and CPU thread count.
   - Enforces standard Linux sandboxing (`NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`).
5. **Docker Compose Alternative**:
   - Optional `llamacpp` service under `--profile cpu-inference` for container-centric hosts.

### Phase 5: Verification & Testing
1. **Unit Tests**:
   - Mocked tests for `llamacpp_client.py` and `ai_service.py` completion and streaming pathways.
   - Settings resolution tests with fallback handling when the server is unreachable.
   - Hardware detection unit tests on CPU environments.
2. **Admin & UI Smoke Tests**:
   - Switching `SystemConfiguration.hosting_backend` to `llamacpp` in Django Admin.
   - Verifying zero uncommanded network calls to external endpoints (respecting offline invariants).

---

## Invariants & Design Constraints
1. **Local-Only Guarantee**: Models and tokenizers must resolve from local disk storage (`/srv/reason/models/` or HF cache) with `local_files_only=True`; no background HTTP polling to HuggingFace or remote registries without explicit operator action.
2. **Graceful Degradation**: If `LLAMACPP_BASE_URL` is offline, requests must fail fast with a descriptive error rather than hanging HTTP worker threads.
3. **No Breaking Changes**: Existing PyTorch, vLLM, and Ollama workflows remain 100% backward compatible.
