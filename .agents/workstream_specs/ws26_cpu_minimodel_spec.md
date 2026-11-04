# Workstream 26 Specification: CPU Mini-Model (`MiniModel`) & `llama.cpp` Integration

## 1. Objective
Provide a first-class, resilient architecture for single-file `.gguf` CPU models served by `llama.cpp` (`llama-server` on port `8080`, files stored in `/srv/reason/models/`):
1. **Dedicated `MiniModel` Django Model & GGUF Directory Selector**:
   - Define `MiniModel` in `llm_api/models.py` representing a single `.gguf` file in `REASON_MODELS_DIR` (`/srv/reason/models/`) without requiring a Hugging Face repository ID (`hf_model_id`).
   - Provide a dynamic dropdown selector in Django Admin (`MiniModelForm` / `MiniModelAdmin`) populated from the `.gguf` files discovered in `/srv/reason/models/` (with graceful fallback to text entry if the directory is empty on a dev workstation).
   - Link `SystemConfiguration.active_mini_model` to `MiniModel` and ensure `reason-llamacpp.service` automatically resolves and passes `-m <gguf_path>` to `llama-server`.
2. **Non-Blocking Utility Role (Conversation Title Maintenance & Token Counting) with Fail-Safe Resilience**:
   - Use the active `MiniModel` via `llama-server` (`http://127.0.0.1:8080`) for:
     - **Conversation Title generation/refinement** (replacing naive string truncation when `llama-server` is online).
     - **Fast token counting** (`POST /tokenize` on `llama-server`), avoiding uncommanded Hugging Face tokenizer network requests and providing a zero-VRAM CPU tokenizer.
     - **Primary CPU inference** when `SystemConfiguration.hosting_backend == 'llamacpp'`.
   - **Strict Non-Blocking Guarantee**: If `llama.cpp` is unconfigured, missing, or shut down, all operations must fail open instantaneously (sub-second circuit breaker / connection check + immediate local fallback) so web requests and background workers never block.
3. **Independent Header-Bar Indicators (`Local:` and `Mini:`)**:
   - Update `metacognition/context_processors.py` and `templates/includes/active_model_pill.html` so the header bar displays **`Local: <model>`** (or active External provider) and **`Mini: <model>`** (`Mini: None` when unconfigured) independently.

---

## 2. Data Model Changes (`llm_api/models.py`)
- **`MiniModel`**:
  - `name`: `models.CharField(max_length=255)` — Friendly display name (auto-derived from filename if omitted, e.g. `Gemma-4-e4B-it-Q5_K_M`).
  - `gguf_filename`: `models.CharField(max_length=255, unique=True)` — Filename inside `REASON_MODELS_DIR` (`/srv/reason/models/`, e.g. `Gemma-4-e4B-it-Q5_K_M.gguf`) or absolute path.
  - `context_window`: `models.IntegerField(default=4096)` — Context length (`-c` flag for `llama-server`).
  - `threads`: `models.IntegerField(null=True, blank=True)` — Optional CPU thread limit (`-t` flag for `llama-server`).
  - `is_active`: `models.BooleanField(default=True)`
  - `created_at` / `updated_at`: timestamps.
  - Properties/Helpers:
    - `gguf_path`: Resolves full path against `getattr(settings, "REASON_MODELS_DIR", "/srv/reason/models")`.
    - `file_exists`: Returns `Path(self.gguf_path).is_file()`.
- **`discover_gguf_files(models_dir=None) -> list[str]`**:
  - Scans `REASON_MODELS_DIR` (`/srv/reason/models`) for `*.gguf` files, sorted alphabetically. Never raises if directory does not exist.
- **`SystemConfiguration`**:
  - `active_mini_model = models.ForeignKey(MiniModel, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_configurations", help_text="Lightweight single-file GGUF CPU mini-model in /srv/reason/models/ served by llama.cpp for utility tasks (conversation titles, token counting) and/or CPU inference.")`
  - `use_mini_model_for_utilities = models.BooleanField(default=True, help_text="When enabled and llama.cpp is online, use the active Mini-Model for non-blocking conversation title generation and token counting.")`

---

## 3. Admin & Service Integration
- **`llm_api/admin.py`**:
  - `MiniModelForm`: Populates `gguf_filename` widget with a choices dropdown of discovered `.gguf` files in `/srv/reason/models/` (plus any existing saved value) when `.gguf` files are present on disk, or a text input with datalist/help text when no `.gguf` files exist locally yet.
  - `MiniModelAdmin`: Lists `name`, `gguf_filename`, `context_window`, `threads`, `file_status`, and action `"Set as Active Mini-Model (CPU / llama.cpp)"`.
  - `SystemConfigurationAdmin`: Exposes `active_mini_model`, `use_mini_model_for_utilities`, and `active_llamacpp_model` in the System Configuration fieldsets.
- **`deploy/fabric_deploy.py`**:
  - Update `reason-llamacpp.service` so `ExecStart` automatically locates the configured `.gguf` file (via `LLAMACPP_MODEL` env var or the first `.gguf` in `/srv/reason/models/`) and passes `-m "$MODEL_PATH" --host 127.0.0.1 --port 8080`.

---

## 4. Non-Blocking Utility Functions (`llm_api/llamacpp_client.py`, `llm_api/ai_service.py`, `llm_api/tasks.py`)
- **`llamacpp_client.py`**:
  - Fast cached availability probe (`is_llamacpp_available_cached(ttl_seconds=5.0, timeout=0.3)`) so repeated template renders or utility checks never block when `llama-server` is stopped.
  - `tokenize_with_llamacpp(text: str, base_url: str | None = None, timeout: float = 0.8) -> int | None`: Calls `POST /tokenize` with `{"content": text}`; returns token count or `None` on any error/timeout.
  - `generate_title_with_minimodel(user_prompt: str, base_url: str | None = None, timeout: float = 2.0) -> str | None`: Checks availability first; requests a 3–6 word title (`max_tokens=24`); strips quotes/punctuation and returns `None` immediately on any error/timeout.
  - `fallback_conversation_title(user_prompt: str) -> str`: Clean deterministic fallback title when `llama-server` is offline.
- **Background / Non-Blocking Title Update**:
  - When a new `Conversation` is created in `demo_ui/views.py` (`send_message`) or `llm_api/api.py` (`generate_response`), it is immediately assigned `fallback_conversation_title(user_prompt)` so the HTTP request never waits.
  - During `task_generate_response` (or non-blocking utility helper `maybe_refine_conversation_title(conversation, user_prompt)`), if `SystemConfiguration.use_mini_model_for_utilities` is enabled and `llama-server` is online, the title is refined via `generate_title_with_minimodel`.

---

## 5. Header Bar (`metacognition/context_processors.py` & `templates/includes/active_model_pill.html`)
- `active_model_context(request)` returns both `active_model_info` (`Local: <name>` or External provider) and `mini_model_info` (`Mini: <name>` or `Mini: None`, plus online/offline status badge).
- `templates/includes/active_model_pill.html` renders the `Local:` and `Mini:` badges side-by-side inside `#active-model-pill-container` so both initial page loads and HTMX provider switches keep both badges up to date.

