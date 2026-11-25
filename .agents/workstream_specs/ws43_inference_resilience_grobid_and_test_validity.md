# Workstream 43 Spec: Inference Resilience, Grobid Admin Remediation, and High-Validity Testing Regime

**Status**: Completed (511/511 unit tests passing via `./scripts/pre_push_check.sh`; 0 leaked test media files)  
**Branch**: `feat/ws43-inference-resilience-grobid-and-test-validity`  
**Preceding Context**: Critical audit of WS42 regressions, production resource exhaustion (`"sorry, too many clients already"` / `"sorry, too many open files"`), PyTorch host unavailability, and test-suite false positives.

---

## 1. Critical Review of Initial Regression Diagnosis

A line-by-line verification of `regression_diagnosis.md` against the codebase confirmed its core findings, corrected one scope concern, and uncovered four additional deterministic root causes that were previously missed:

### 1.1 Confirmed Findings
1. **`GrobidReadingStrategyInline` silent drop (`background_resources/admin.py:170-174`)**:
   - Because `strategy_description` is the sole inline field and defaults to `"Grobid Semantic Chunking"`, Django's `BaseInlineFormSet` evaluates `form.has_changed() == False` on newly added untouched rows and silently discards them with zero validation errors.
2. **Silent swallowing in Grobid strategy execution (`background_resources/models.py:446-496`)**:
   - `GrobidReadingStrategy.save()` executes `apply_strategy()` synchronously inside the web HTTP request (violating `VERBAL_ROLE=web` process isolation) and swallows failures into `logger.warning`.
   - `GrobidReadingStrategy.read_document()` catches `ValueError` and returns normally, causing `task_process_grobid_reading_strategies` to record `TaskRecordStatus.SUCCESSFUL` even when 0 chunks are created.
3. **Lock-blocking `/internal/ping/` + `runserver` thread/DB connection pileup + missing `LimitNOFILE`**:
   - During PyTorch model loading, `service_registry.ai_service` holds `service_registry._lock` for 15–120s. Every health check to `/internal/ping/` opens a PostgreSQL connection via `SystemConfiguration.get_solo()` and then blocks on `service_registry.ai_service`. Callers time out after `0.25–0.5s`, while `manage.py runserver --noreload` spawns new threads holding open DB connections and file descriptors. Only `reason-web.service` sets `LimitNOFILE=65535`; `reason-inference`, `reason-worker`, and `reason-scheduler` inherit the `1024` OS default.
4. **`Document.save()` file descriptor leak (`background_resources/models.py:148`)**:
   - `f = self.file.open('rb')` is never closed and re-hashes the entire PDF even on metadata-only `save(update_fields=[...])` calls.

### 1.2 Newly Uncovered Deterministic Bugs (Added to Scope)
5. **Missing `"loading"` State Machine Causes Immediate False `"Active Host Unavailable"` (`llm_api/api.py`, `metacognition/context_processors.py`, `llm_api/diagnostics.py`, `llm_api/admin.py`)**:
   - Even with zero DB/FD contention, saving `SystemConfiguration` (`hosting_backend='pytorch'`) triggers async `/internal/reload-model/` and **2ms later** `SystemConfigurationAdmin.save_model()` pings `/internal/ping/`. Because `ai.model` is still `None` while loading in the background thread, `/internal/ping/` returns `HTTP 503 {"error": "Model not loaded in PyTorch VRAM"}`. `context_processors.py` caches this under `pytorch_error`, `diagnostics.py:is_backend_available()` returns `(False, "Model not loaded in PyTorch VRAM")`, and Admin immediately displays **"Local PyTorch (CPU/GPU) is currently unavailable on this host"** and **"⚠️ Active Host Unavailable"**!
6. **`VERBAL_ROLE` Overwrite in `verbal_config/settings.py` & Dual-Logger File Collision**:
   - `settings.py:55` sets `VERBAL_ROLE = os.environ.get("VERBAL_ROLE", "standalone")`, uses it on line 273, and **overwrites it on line 314** with `VERBAL_ROLE = os.environ.get("VERBAL_ROLE", "web")`. Any process without `VERBAL_ROLE` explicitly exported in `os.environ` silently enters `"web"` proxy mode and bypasses PyTorch model loading with zero VRAM usage and zero error logs. Additionally, `start_background_services.sh:55` launches `runtaskscheduler` under `VERBAL_ROLE=worker`.
   - `settings.py:326` attaches a Python `RotatingFileHandler` to `reason-{VERBAL_ROLE}.log`, while `deploy/fabric_deploy.py` pipes `stdout/stderr` via `tee -a` to the **exact same file**, duplicating every log entry and breaking inode log rotation.
   - `internal_reload_model` (`llm_api/api.py:371`) spawns `threading.Thread(target=service_registry.ai_service.load_models)` with no exception wrapper and no `close_old_connections()` cleanup.
7. **Adjacent `DocumentAdmin` Inline Defects (`background_resources/admin.py:152-240`)**:
   - `ReadingStrategyInline`, `RegexStrategyInline`, and `PromptStrategyInline` still include `'document'` in `fields`, and `PromptStrategyInline.Meta.model` is mistakenly set to `AbbreviationsReadingStrategy`.
8. **Unnecessary LLM Fallback Calls During Grobid Extraction (`grobid_client/tasks.py:411-434`)**:
   - `missing_fields = [k for k, v in meta.items() if not v]` triggers two synchronous LLM `generate_outline` HTTP requests to the inference server whenever *any* field (such as `publisher` or `journal` on conference papers/reports) is blank—even when `title`, `authors`, and `year` were already extracted deterministically by Grobid—amplifying load and failure cascades during batch citation extraction.

### 1.3 Explicit Exclusion: Datastar & Server-Sent Events (SSE)
- **Datastar SSE (`text/event-stream` emitting `datastar-patch-elements` and `datastar-patch-signals`) is Core Architectural Invariant 5 and is NOT modified in WS43.**
- Item #4 in the diagnosis had misleading phrasing (*"convert SSE generators to async"*). It referred strictly to an internal Django 6.1 + `granian --interface asgi` behavior when consuming synchronous Python `def` generators (`sync_to_async(list)`), **not** to moving away from SSE or Datastar.
- Because choosing between running Granian in WSGI mode vs. refactoring Django SSE generator functions to `async def` involves careful trade-offs around ORM sync/async boundaries and Datastar stream lifecycles, **zero SSE/Datastar endpoints will be touched in WS43**. That topic is isolated in [20261125_datastar_sse_asgi_vs_wsgi_decision.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/20261125_datastar_sse_asgi_vs_wsgi_decision.md) for dedicated review.

---

## 2. Architecture & Design Specification

### Phase 1: Inference Lifecycle State Machine, Non-Blocking Health Checks & DB/FD Guardrails

1. **Explicit `AIService` Load State Machine (`llm_api/ai_service.py`, `llm_api/apps.py`)**:
   - Add thread-safe state attributes on `AIService`:
     - `load_state: Literal["idle", "loading", "loaded", "error"] = "idle"`
     - `loading_model_id: str | None = None`
     - `load_started_at: float | None = None`
     - `load_error: str | None = None`
   - Provide a thread-safe helper `run_model_load_safely()` used by both `LlmApiConfig.ready()` and `/internal/reload-model/` that:
     - Sets `load_state = "loading"` and `loading_model_id` **before** heavy I/O,
     - Acquires `AIService._lock` to serialize concurrent reload requests,
     - Catches all exceptions (`except Exception as exc:`), sets `load_state = "error"` and `load_error = f"{type(exc).__name__}: {exc}"`, and logs via `logger.exception(...)`,
     - Calls `connections.close_all()` in `finally:` so background loader threads never leak PostgreSQL sessions.
   - Respect Offline-First Invariant 7 in `AIService.load_models()`:
     - Check `check_hf_model_cached(self.model_id)` first. If weights ARE cached locally and `AutoModelForCausalLM.from_pretrained(..., local_files_only=True)` raises an error (e.g., CUDA OOM, `BitsAndBytes` error, config mismatch), **do not** attempt a remote Hugging Face download that masks the real error with a network failure; immediately record `local_err` into `self.load_error` and `load_state = "error"`.

2. **Lock-Free `/internal/ping/` & Honest Admin Readiness (`llm_api/api.py`, `metacognition/context_processors.py`, `llm_api/diagnostics.py`, `llm_api/admin.py`)**:
   - In `internal_ping` (`llm_api/api.py`):
     - Inspect `service_registry._ai_service` directly **without** calling the blocking `service_registry.ai_service` property.
     - If `service_registry._ai_service` is `None` and background startup loading is in progress, or `ai.load_state == "loading"`, return `HTTP 200` (or `202` handled cleanly) with:
       `{"status": "loading", "role": role, "model_loaded": False, "loading": True, "model_name": ...}`.
     - Return `HTTP 503` with `{"status": "error", "error": ...}` **only** when `load_state == "error"` or when `load_state == "idle"` (not loading and no model loaded despite PyTorch backend being configured—in which case provide a one-click / auto-recoverable state rather than a permanent false host failure).
   - Update `metacognition/context_processors.py`, `llm_api/diagnostics.py`, and `llm_api/admin.py`:
     - Track `pytorch_loading` separately from `pytorch_error`.
     - While `pytorch_loading` is `True`, `summary.is_backend_available("pytorch")` returns `(True, "Loading model into VRAM...")` and Admin renders an informational `⏳ Loading into VRAM...` badge instead of flashing `"unavailable on this host"` / `"⚠️ Active Host Unavailable"`.

3. **`SystemConfiguration.get_solo()` Error Transparency (`llm_api/models.py`)**:
   - Narrow exception suppression in `get_solo()`:
     - Only return `None` quietly if the `llm_api_systemconfiguration` table does not yet exist during `migrate`/`makemigrations` (`ProgrammingError` / `OperationalError` matching `"does not exist"` / `"no such table"`).
     - For operational infrastructure failures (`"too many clients already"`, `"too many open files"`, connection refused, etc.), log via `logger.error(...)` and **re-raise** so callers (`load_models`, views, tasks) record an explicit infrastructure failure rather than mistaking `None` for *"no model configured"* and caching a broken singleton forever.

4. **Settings, Logging & Deployment Resource Guardrails (`verbal_config/settings.py`, `deploy/fabric_deploy.py`, `docker-compose.yml`, `start_background_services.sh`, `background_resources/models.py`)**:
   - Remove the duplicate `VERBAL_ROLE = os.environ.get("VERBAL_ROLE", "web")` overwrite at `verbal_config/settings.py:314`; keep a single canonical definition and set `VERBAL_ROLE=scheduler` explicitly before starting `runtaskscheduler` in `start_background_services.sh`.
   - Eliminate duplicate file-descriptor / log-rotation conflict on `reason-{VERBAL_ROLE}.log`: when `REASON_CONSOLE_TEE_ACTIVE=1` (set in systemd units that pipe `2>&1 | tee -a`), rely on console `StreamHandler` -> `tee` (or separate rotated app log vs raw console log) so two processes/handlers never write concurrently to the same file path.
   - Add `OPTIONS: {"application_name": f"reason-{VERBAL_ROLE}", "connect_timeout": 5}` to `DATABASES["default"]` in `verbal_config/settings.py`.
   - Add `LimitNOFILE=65535` to `reason-worker.service`, `reason-scheduler.service`, `reason-inference.service`, and `reason-llamacpp.service` in `deploy/fabric_deploy.py`, and `ulimits.nofile` (`65535`) to `db` in `docker-compose.yml`.
   - Ensure `BackgroundModelLoaderMiddleware._load_services` closes DB connections in `finally:`.
   - Fix `Document.save()` (`background_resources/models.py:140-168`):
     - Use `with self.file.open("rb") as f:` so every file descriptor is deterministically closed.
     - Skip re-reading and SHA-256 hashing the file on partial updates (`update_fields` provided and `'file'` not in `update_fields` and `self.indexed_hash` already populated).

---

### Phase 2: Grobid & Reading Strategy Admin Inlines, Async Dispatch & Honest Task Outcomes

1. **Fix All Five `DocumentAdmin` Inlines (`background_resources/admin.py`)**:
   - Create `GrobidReadingStrategyInlineForm(forms.ModelForm)` for `GrobidReadingStrategyInline`:
     - Override `has_changed(self)` so that a newly added inline form (`self.is_bound and self.instance._state.adding and not self.data.get(self.add_prefix("DELETE"))`) returns `True`, ensuring clicking **"+ Add another Grobid Reading Strategy"** and saving with the default `"Grobid Semantic Chunking"` description reliably persists the new `GrobidReadingStrategy` row (while saving a `Document` with `extra = 0` and zero added forms still creates 0 rows).
   - Remove `'document'` from `ReadingStrategyInline.fields`, `RegexStrategyInline.fields`, and `PromptStrategyInline.fields`.
   - Fix `PromptStrategyInline.Meta.model = PromptStrategy`.

2. **Decouple `GrobidReadingStrategy.save()` from Synchronous Web Execution (`background_resources/models.py`)**:
   - Remove in-request `self.apply_strategy(...)` from `GrobidReadingStrategy.save()`.
   - Instead, schedule background execution cleanly on transaction commit:
     `transaction.on_commit(lambda: task_process_grobid_reading_strategies.enqueue([str(self.id)]))` when created with zero usages (unless `_skip_auto_enqueue=True` is set internally by `ingest_queryset_documents`, which already executes strategies directly inside the worker task).

3. **Honest Failure Propagation & Actionable Admin Feedback (`background_resources/models.py`, `background_resources/admin.py`, `grobid_client/tasks.py`)**:
   - Add `strict: bool = False` to `GrobidReadingStrategy.apply_strategy` / `read_document` (enabled when called by `task_process_grobid_reading_strategies`):
     - Instead of catching `ValueError` and returning silently, raise a descriptive `RuntimeError` when TEI XML is unavailable (e.g., Grobid container offline and no cached TEI XML) or when Grobid produces 0 chunks, so `TaskRecord` honestly records `FAILED` with the exact error reason.
   - In `DocumentAdmin.extract_grobid_metadata` and `GrobidReadingStrategyAdmin.process_grobid_reading`:
     - If documents lack cached TEI XML and `is_grobid_available()` is `False`, immediately warn the admin user via `messages.ERROR` (`"Grobid daemon at {GROBID_URL} is unreachable..."`) rather than displaying a misleading green success banner.
   - In `task_extract_grobid_metadata` (`grobid_client/tasks.py:410-434`):
     - Trigger Step 2 & Step 3 LLM fallback queries **only** when core bibliographic identity fields (`title`, `authors`, `year`) remain missing after deterministic TEI header extraction, **and** guard LLM fallback calls behind `try...except Exception` + backend availability check so Grobid TEI & citation graph extraction always succeeds deterministically even when no LLM inference backend is loaded!

---

### Phase 3: Structural Upgrade to the Testing Regime

To prevent passing test suites from masking production regressions, WS43 enforces four structural testing upgrades aligned with `.agents/skills/django_test_evaluator/SKILL.md` and `20261116_test_policy_and_side_effect_verification.md`:

1. **Replace `'test' in sys.argv` Production Bypasses with Explicit Overridable Settings**:
   - Replace hardcoded `'test' in sys.argv` / `'pytest' in sys.modules` guards in:
     - `llm_api/models.py:manage_hosting_backend`
     - `llm_api/apps.py:_sync_hosting_backend_state`
     - `metacognition/context_processors.py:is_primary_backend_available_cached`
     - `grips/models.py`
   - Introduce explicit Django settings (`ENABLE_HOSTING_BACKEND_SYNC`, `ENABLE_LIVE_BACKEND_PING`, `ENABLE_GRIPS_AUTO_SYNC`), defaulted off in the test runner so unrelated unit tests remain fast and offline, and **explicitly enabled via `@override_settings(...)`** in dedicated integration test suites that exercise the real signal/ping/orchestration codepaths!

2. **Automatic Test `MEDIA_ROOT` Isolation (`verbal_tasks/test_runner.py`, `conftest.py`)**:
   - Redirect `settings.MEDIA_ROOT` during test runs to an isolated temporary directory cleaned up automatically on suite teardown, halting fixture pollution in `media/documents/`.

3. **Specification-First High-Validity Test Suites (Written Before Code Fixes per TDD Rule)**:
   - **`background_resources/tests.py` — Real Admin HTTP POST & Worker Execution Tests**:
     - `test_document_admin_post_adds_default_grobid_inline_row`: Submits an authentic POST to `/admin/background_resources/document/{id}/change/` via `django.test.Client` with a newly added `GrobidReadingStrategyInline` form retaining default `"Grobid Semantic Chunking"`; asserts `GrobidReadingStrategy.objects.count() == 1` (fails on current `main`, passes after fix).
     - `test_document_admin_post_without_adding_grobid_inline_creates_zero_rows`: Submits Document edit with `TOTAL_FORMS=0`; asserts 0 `GrobidReadingStrategy` rows created.
     - `test_document_admin_all_five_strategy_inlines_submit_cleanly`: Submits `ReadingStrategyInline`, `GrobidReadingStrategyInline`, `RegexStrategyInline`, `PromptStrategyInline`, and `AbbreviationStrategyInline` via `DocumentAdmin` POST; asserts HTTP 302 redirect and row creation for every inline model.
     - `test_grobid_reading_action_executes_via_worker_and_creates_chunks`: Runs Admin action `process_grobid_reading` + `call_command("runtaskworker", "--burst")` on a Document with TEI XML; asserts `TaskRecord.status == SUCCESSFUL`, `RAGChunk.objects.count() > 0`, and `StrategyChunkUsage.objects.count() > 0`.
     - `test_grobid_reading_task_fails_honestly_when_grobid_offline_and_no_tei`: Runs `task_process_grobid_reading_strategies` via `runtaskworker --burst` when Grobid is unreachable and TEI XML is missing; asserts `TaskRecord.status == FAILED` with `"Grobid"` in `error_traceback` (never silent `SUCCESSFUL`).
     - `test_document_save_closes_file_descriptor_and_skips_rehash_on_metadata_update`: Asserts file handle is closed after `Document.save()` and `file.open` is not called during `doc.save(update_fields=["title"])`.
   - **`grobid_client/tests.py` — Deterministic Grobid Extraction Independence**:
     - `test_extract_grobid_metadata_skips_llm_when_core_fields_present`: Verifies `ai_service.generate_outline` is **not** called when TEI XML contains `title`, `authors`, and `year` (even if `publisher` is empty), and still extracts all `Reference` and `Citation` rows cleanly if LLM backend is offline.
   - **`llm_api/tests.py` — Concurrency, State Machine, Fault Injection & Signal Orchestration Tests**:
     - `test_internal_ping_non_blocking_during_concurrent_model_load`: Holds `AIService._lock` with `load_state = "loading"`, fires 15 concurrent threads at `/api/llm/internal/ping/`, asserts every call completes in `< 0.2s` returning `status == "loading"`, and asserts DB connection count does not grow.
     - `test_admin_save_during_loading_does_not_report_host_unavailable`: Simulates `SystemConfigurationAdmin.save_model` with `ENABLE_LIVE_BACKEND_PING=True` while `internal_ping` returns `"loading"`; asserts no `"unavailable on this host"` warning or `"Active Host Unavailable"` banner is emitted, whereas `load_state == "error"` surfaces the exact exception string.
     - `test_get_solo_raises_and_logs_error_on_connection_or_fd_exhaustion`: Injects `OperationalError("sorry, too many clients already")` and `OSError(24, "Too many open files")` into `SystemConfiguration.objects.get_or_create`; asserts `ERROR` log emission, exception propagation (not silent `None`), and verifies `AIService.load_models()` records `load_state = "error"` and recovers on subsequent reload.
     - `test_manage_hosting_backend_signal_transitions_enabled`: With `@override_settings(ENABLE_HOSTING_BACKEND_SYNC=True)`, verifies real `post_save` signal transitions (`pytorch -> llamacpp` unloads VRAM, `llamacpp -> pytorch` triggers `/internal/reload-model/`).
   - **`deploy/tests.py` — Systemd Limits, Role Integrity & DB Attribution**:
     - Asserts `LimitNOFILE=65535` on `reason-web`, `reason-worker`, `reason-scheduler`, `reason-inference`, and `reason-llamacpp`.
     - Asserts `settings.VERBAL_ROLE` single-source consistency and `DATABASES["default"]["OPTIONS"]["application_name"]`.

---

## 3. Execution Order & Verification Gate

1. **Step 1 (TDD Red Phase)**: Implement the new high-validity tests across `background_resources/tests.py`, `grobid_client/tests.py`, `llm_api/tests.py`, and `deploy/tests.py`, plus test-runner `MEDIA_ROOT` isolation, and confirm the new tests reproduce the exact bugs (fail before code fixes).
2. **Step 2 (Phase 1 Remediation)**: Implement `AIService` state machine, lock-free `/internal/ping/`, `get_solo()` error handling, `VERBAL_ROLE` & logging fixes, DB `application_name`, systemd `LimitNOFILE`, background thread connection cleanup, and `Document.save()` FD fix.
3. **Step 3 (Phase 2 Remediation)**: Implement `DocumentAdmin` inline fixes (`GrobidReadingStrategyInlineForm` + sibling inline cleanup), `on_commit` async strategy enqueueing, honest `TaskRecord` failure propagation, and Grobid LLM-fallback gating.
4. **Step 4 (Verification & Code Review)**:
   - Run targeted app test suites via `../../py312/bin/python manage.py test background_resources grobid_client llm_api deploy metacognition --noinput`.
   - Run full `./scripts/pre_push_check.sh` gateway (migrations check, `--deploy` check, full unit test suite, clean working tree check) and verify PEP 8 compliance.
   - Note: Zero `manage.py migrate` runs against dev DB (no schema migrations required, or if any model help_text/meta changes occur, verify via `makemigrations --check`).
