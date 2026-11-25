# WS45: Inference Role Integrity, Library Network/Telemetry Lockdown, and ASGI Lifespan Support

## Status
Completed (`feat/ws45-inference-role-telemetry-and-lifespan`)

## Background & Diagnosed Production Issues

Following deployment of WS43 and WS44, three production runtime issues were observed in logs and Admin health checks:

1. **Persistent PyTorch VRAM 503 & `Role: web` Inside `reason-inference.log`**:
   - Switching to `llama.cpp` reported healthy, whereas switching back to Gemma 4 in PyTorch VRAM logged in `reason-inference.log`:
     - `System config requests PyTorch load for: [model name]`
     - `Bypassing Hugging Face tokenizer load (CPU mini-model active)`
     - `Running in proxy mode (Role: web), bypassing heavy model load`
   - Subsequently, `/api/llm/internal/ping/` returned HTTP 503 (`Active Host Unavailable: Model not loaded in PyTorch VRAM`) every 10–15 seconds.
   - **Root Cause (`systemd.exec(5)` Precedence + Missing Systemd Sync in `fab deploy`)**:
     - Per `systemd.exec(5)`, **settings read from `EnvironmentFile=` override settings made with `Environment=`** regardless of line order in the unit file.
     - `generate_systemd_units()` in `deploy/fabric_deploy.py` includes `EnvironmentFile=-/srv/reason/app/.env` alongside `Environment=VERBAL_ROLE=inference` (and `worker` / `scheduler`). Whenever `/srv/reason/app/.env` (or `.env.production`) contains `VERBAL_ROLE=web`, systemd overwrites `VERBAL_ROLE` to `"web"` in `reason-inference`, `reason-worker`, and `reason-scheduler` before Python starts.
     - `verbal_config/settings.py` snapshots `_initial_env = dict(os.environ)` at process entry, locking in `VERBAL_ROLE="web"`.
     - Inside `reason-inference`, `AIService.load_models()` saw `self.role == "web"`, skipped tokenizer loading, skipped PyTorch weight loading, set `self.load_state = "idle"` with `self.model = None`, and exited—leaving `internal_ping` returning HTTP 503 on every poll.
     - Compounding this, `fab deploy` did not invoke `setup_systemd`, so systemd unit fixes were never applied during routine deployments unless `fab setup-systemd` was run separately.

2. **Uncommanded `GET https://artifactory/huggingface/api/agent-harnesses` (HTTP 404)**:
   - On Granian worker startup (`reason-web`), `httpx` logged `GET https://artifactory/huggingface/api/agent-harness(es) HTTP 404`.
   - **Root Cause**:
     - `huggingface_hub` (`>= 1.x`, `huggingface_hub/utils/_headers.py` & `_detect_agent.py`) calls `detect_agent()` -> `_fetch_registry()` -> `GET {HF_ENDPOINT}/api/agent-harnesses` when constructing Hub request headers unless `HF_HUB_DISABLE_TELEMETRY=1` is set. When `HF_ENDPOINT` points to an internal Artifactory mirror, `/api/agent-harnesses` returns 404 and is never cached on disk, causing every worker that touches Hub HTTP headers to repeat the 404 probe.
     - Furthermore, `AIService._resolve_tokenizer_source()` (`config.system_tokenizer` returning `allow_remote=True`), `AIService.load_models()` fallback, and `load_huggingface_embeddings()` fallback allowed runtime processes to fall back to uncommanded remote Hub queries on local cache misses rather than strictly confining network downloads to explicit admin/provisioning actions (`download_model_cache` / `provision_models`).

3. **`[WARNING] ASGI Lifespan errored, continuing without Lifespan support`**:
   - Granian (`--interface asgi`) sends ASGI `{"type": "lifespan"}` (`lifespan.startup` / `lifespan.shutdown`) events when starting workers.
   - `verbal_config/asgi.py` wraps `get_asgi_application()` in `ASGIStaticFilesHandler`; Django's `ASGIHandler` only accepts `http` scopes and raises `ValueError("Django can only handle ASGI/HTTP connections, not lifespan.")`.

---

## Architectural Remediation Plan

### 1. Systemd & Runtime `VERBAL_ROLE` Integrity (Defense in Depth)
- **Systemd `ExecStart` Role Pinning (`deploy/fabric_deploy.py`)**:
  - Preserve `Environment=VERBAL_ROLE=<role>` in each `[Service]` stanza for unit readability, AND enforce `VERBAL_ENV=prod`, `VERBAL_ROLE=<role>`, and `REASON_CONSOLE_TEE_ACTIVE=1` via `/usr/bin/env` inside `ExecStart=/bin/bash -c 'exec /usr/bin/env ...'` across `reason-web`, `reason-worker`, `reason-scheduler`, `reason-inference`, and `reason-llamacpp`. Because `/usr/bin/env` runs inside the spawned command after systemd finishes applying `EnvironmentFile=`, `.env` files can never override per-daemon roles.
- **Synchronize Systemd Units During `fab deploy` (`deploy/fabric_deploy.py`)**:
  - Add `("setup_systemd", lambda: setup_systemd(c), "fab setup-systemd")` immediately before `restart_services` in `deploy()` so routine `fab deploy` runs always install updated unit files and run `systemctl daemon-reload`.
- **Runtime Command-Role Guard (`verbal_config/settings.py`)**:
  - Implement `resolve_runtime_role(raw_role: str, argv: list[str], inference_url: str) -> str`:
    - If `manage.py runserver` binds to the configured `INFERENCE_URL` port (e.g. `:8001`) while `raw_role` is a client role (`web`, `worker`, `scheduler`), log an explicit warning and enforce `VERBAL_ROLE = "inference"` (updating `os.environ["VERBAL_ROLE"]`).
    - If `manage.py runtaskworker` runs with `raw_role in ("web", "scheduler", "inference")`, enforce `"worker"`.
    - If `manage.py runtaskscheduler` runs with `raw_role in ("web", "worker", "inference")`, enforce `"scheduler"`.
    - Leaves `test`, `pytest`, `granian`, `shell`, `dumpdata`, and `migrate` invocations untouched.
- **Dynamic `AIService` Role & Actionable Inference Diagnostics (`llm_api/ai_service.py`, `llm_api/api.py`)**:
  - Ensure `AIService.__init__` dynamically reads `settings.VERBAL_ROLE` when `AIService.role` has not been explicitly monkeypatched on the class (preserving `conftest.py`'s `AIService.role = "web"` fixture while honoring `@override_settings(VERBAL_ROLE=...)`).
  - In `internal_reload_model` and `internal_ping`, explicitly detect and report if an inference endpoint process is ever misconfigured in a client proxy role (`web`, `worker`, `scheduler`) instead of silently transitioning to `idle` and returning a generic `"Model not loaded in PyTorch VRAM"` message.

### 2. Library Telemetry & Uncommanded Outbound Network Lockdown
- **Early Telemetry & Agent-Probe Quarantine (`llm_api/torch_compat.py`)**:
  - Add `enforce_library_network_guardrails()` invoked inside `apply_torch_compatibility_shims()` (executed at top of `verbal_config/settings.py` before app imports) and export telemetry-disable variables in `generate_systemd_units()`:
    - `HF_HUB_DISABLE_TELEMETRY=1`, `DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`
    - `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` (prevents implicit OAuth token network refresh in `huggingface_hub`)
    - `TRANSFORMERS_NO_ADVISORY_WARNINGS=1`
    - `UNSLOTH_DISABLE_STATISTICS=1`
    - `ANONYMIZED_TELEMETRY=False`, `SCARF_NO_ANALYTICS=true`, `LANGCHAIN_TRACING_V2=false`
  - Also idempotently patch `huggingface_hub.constants` (`HF_HUB_DISABLE_TELEMETRY = True`, `HF_HUB_DISABLE_IMPLICIT_TOKEN = True`) and pre-seed `huggingface_hub.utils._detect_agent._registry = {"standardEnvVars": [], "harnesses": {}}` if `huggingface_hub` is already imported or upon import, guaranteeing zero `GET .../api/agent-harnesses` requests even during explicit `download_model_cache` runs.
- **Strict Local-Cache-Only Runtime Model/Tokenizer/Embedding Resolution**:
  - Introduce `ALLOW_UNCOMMANDED_HF_DOWNLOADS` in `verbal_config/settings.py` (default `False`, controlled via `VERBAL_ALLOW_UNCOMMANDED_HF_DOWNLOADS`).
  - Update `AIService._resolve_tokenizer_source()`, `AIService.load_models()`, and `load_huggingface_embeddings()` (`background_resources/rag_service.py`) to disallow uncommanded remote Hub fallback during normal daemon startup/request handling unless `ALLOW_UNCOMMANDED_HF_DOWNLOADS=True`. Explicit user-initiated downloads (`llm_api/tasks.py:download_model_cache` and `deploy/fabric_deploy.py:provision_models`) remain unaffected.

### 3. ASGI Lifespan Protocol Support (`verbal_config/asgi.py`)
- Wrap the ASGI application in `ASGILifespanWrapper`:
  - Delegate all non-`lifespan` scopes directly to `ASGIStaticFilesHandler(get_asgi_application())`.
  - Handle `scope["type"] == "lifespan"` cleanly: acknowledge `lifespan.startup` with `lifespan.startup.complete`, call `django.db.close_old_connections()` on `lifespan.shutdown`, and acknowledge with `lifespan.shutdown.complete`.

---

## Verification & Test Plan (TDD)
1. **`deploy/tests.py`**:
   - Verify `generate_systemd_units()` enforces per-unit `VERBAL_ROLE` inside `ExecStart` via `/usr/bin/env` (immune to `EnvironmentFile=` overrides) and sets `HF_HUB_DISABLE_TELEMETRY=1` / `DO_NOT_TRACK=1`.
   - Verify `deploy()` executes `setup_systemd` prior to `restart_services`.
   - Verify `resolve_runtime_role()` overrides stale `VERBAL_ROLE=web` when invoked as `runserver 127.0.0.1:8001`, `runtaskworker`, or `runtaskscheduler`, while preserving `web` for `test`/`granian`.
2. **`llm_api/tests.py`**:
   - Verify `enforce_library_network_guardrails()` disables `huggingface_hub` telemetry, sets `_detect_agent._registry` to an empty registry, and causes `build_hf_headers()` / `detect_agent()` to make **zero** `_fetch_registry` HTTP calls.
   - Verify `AIService._resolve_tokenizer_source()` and `AIService.load_models()` never initiate remote Hub fetches when `ALLOW_UNCOMMANDED_HF_DOWNLOADS=False`.
   - Verify `AIService()` picks up `@override_settings(VERBAL_ROLE="inference")`, and verify `internal_reload_model` / `internal_ping` surface explicit role-mismatch errors if `role == "web"`.
   - Verify `verbal_config.asgi.application` handles `lifespan.startup` and `lifespan.shutdown` events cleanly without raising `ValueError`.
3. **`background_resources/tests.py`**:
   - Verify `load_huggingface_embeddings()` respects `ALLOW_UNCOMMANDED_HF_DOWNLOADS=False` on local cache miss (raising a clear actionable error rather than making uncommanded remote HTTP requests) while still allowing fallback when `ALLOW_UNCOMMANDED_HF_DOWNLOADS=True`.
