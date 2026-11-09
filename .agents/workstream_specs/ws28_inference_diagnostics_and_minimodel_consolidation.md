# Workstream 28: Inference Diagnostics, Live Ping Health & Mini-Model Consolidation

## 1. Context & Motivation
1. **500 Errors on `/v1/chat/completions` & Silent PyTorch Loading Failures**:
   - When a model load fails in the PyTorch environment (e.g. on `reason-inference` or standalone), the error is swallowed or logged without tracebacks, resulting in a bare HTTP 500 error on `/v1/chat/completions` with no diagnostic details.
   - Hugging Face `transformers` without `local_files_only=True` queries Artifactory / Hugging Face Hub mirrors for `adapter_config.json` to detect PEFT LoRA adapters. In standard base models (e.g. Gemma 4B), this file does not exist, triggering an HTTP 404 from the mirror and potentially failing offline/cached loads even when the full model snapshot is present on disk.
2. **Misleading Ping & Header Status**:
   - `/api/llm/internal/ping/` unconditionally returned HTTP 200 `{"status": "ok"}` based solely on whether Django was running, ignoring whether PyTorch had failed to load the model or whether VRAM was unpopulated.
   - The header model pill stayed green (`🟢 Online`) and the `SystemConfiguration` admin showed no real-time backend/model health status.
3. **"System Tokenizer" vs "Mini-Model" Terminology & Redundancy**:
   - `SystemConfiguration` historically placed `system_tokenizer` (a `LocalAIModel` Hugging Face reference) right at the top under "Hosting Strategy", creating confusion with `active_mini_model` (`MiniModel` GGUF model served by `llama.cpp`).
   - Terminology and admin layout need consolidation so users configure the primary backend and the single CPU Mini-Model without loading redundant small models.

---

## 2. Technical Architecture & Invariants

### A. PyTorch Model Loading with Local Cache Priority & Diagnostic Visibility
- In `llm_api/ai_service.py` (`load_models`):
  - When loading `self.model_id` via `AutoModelForCausalLM.from_pretrained`:
    - First attempt with `local_files_only=True` to immediately load from `$HF_HOME` without touching Artifactory / Hugging Face mirrors for `adapter_config.json`.
    - If `local_files_only=True` fails (e.g. cache absent), fall back to remote fetch.
  - Wrap model loading in `try...except Exception as e:`:
    - Preserve exception message on `self.load_error = f"{type(e).__name__}: {str(e)}"`.
    - Log complete stack trace via `logger.exception`.
  - In `openai_chat_completions` (`llm_api/api.py`):
    - Catch unhandled exceptions, log with `logger.exception`, and return an informative JSON payload with HTTP 500:
      `{"error": {"message": str(e), "type": type(e).__name__, "code": 500}}`.

### B. Accurate `internal/ping` & Real-Time Health Diagnostics
- In `llm_api/api.py` (`internal_ping`):
  - Check `service_registry.ai_service`.
  - If `config.hosting_backend == 'pytorch'` and `config.active_local_model`:
    - If `ai_service.model is not None`: return HTTP 200 with `status: "ok"`, `model_loaded: True`.
    - If `ai_service.model is None`: return HTTP 503 (Service Unavailable) with `status: "error"`, `model_loaded: False`, `error: load_error`.
- In `metacognition/context_processors.py`:
  - `is_primary_backend_available_cached` treats HTTP 503 or error payloads as offline/error.
  - `active_model_context` surfaces error state:
    - `badge_icon`: `🔴`, `status_color`: `#ef4444`, `status_text`: `Model load failed: <error>`.
- In `llm_api/admin.py` (`SystemConfigurationAdmin`):
  - Add `backend_status_display` to display live backend and Mini-Model health right at the top of System Configuration.

### C. Consolidate "System Tokenizer" into Mini-Model
- In `SystemConfigurationAdmin`:
  - Remove `system_tokenizer` from the primary "Hosting Strategy" fieldset.
  - Feature `active_mini_model` as the definitive CPU Mini-Model for utility inference and token counting.
  - Relocate `system_tokenizer` to a collapsed "Advanced / Legacy Fallback" section with clear explanatory notes.
- In `ai_service.py`:
  - Consolidate token counting log messages and fallback paths to prioritize `active_mini_model`.

---

## 3. Test & Verification Plan
- Unit test verifying `AutoModelForCausalLM.from_pretrained` cache-first priority.
- Unit test verifying `openai_chat_completions` error handling and 500 JSON response format.
- Unit test verifying `internal_ping` returns 503 when PyTorch model fails to load.
- Unit test verifying `metacognition/context_processors.py` surfaces 🔴 error when backend fails.
- Unit test verifying `SystemConfigurationAdmin` displays live backend status and cleans up fieldsets.

