

## [2026-11-25] WS43: Inference Resilience, DocumentAdmin Inlines & Test-Regime Validity Reforms
- **Systemic Blindspots Eliminated**:
  - Purged all `'test' in sys.argv` / `'pytest' in sys.modules` production bypasses across `llm_api/models.py`, `llm_api/apps.py`, `metacognition/context_processors.py`, and `grips/models.py`, replacing them with explicit Django settings (`ENABLE_HOSTING_BACKEND_SYNC`, `ENABLE_LIVE_BACKEND_PING`, `ENABLE_GRIPS_AUTO_SYNC`) exercised via `@override_settings(...)`.
  - Isolated `MEDIA_ROOT` in `ForceTeardownTestRunner` (`verbal_tasks/test_runner.py`) and `conftest.py` to ephemeral temp directories cleaned up on teardown (verified `media/documents` file count remained strictly unchanged across all 511 unit tests).
- **High-Validity Tests Added**:
  - `background_resources/tests.py` (`WS43DocumentAdminAndGrobidExecutionTests`): Real `django.test.Client` HTTP POST formset submissions across all 5 `DocumentAdmin` strategy inlines (including untouched default `GrobidReadingStrategyInline`), end-to-end `runtaskworker --burst` chunk verification, honest `TaskRecordStatus.FAILED` verification when Grobid is offline with missing TEI XML, and `Document.save()` file-descriptor / metadata-only save verification.
  - `grobid_client/tests.py` (`WS43GrobidDeterministicIndependenceTests`): Verified deterministic Grobid TEI & citation extraction executes zero LLM `generate_outline` calls when core fields (`title`, `authors`, `year`) are already extracted.
  - `llm_api/tests.py` (`WS43InferenceStateMachineAndResilienceTests`): 12-thread concurrent `/internal/ping/` non-blocking latency & zero-DB-query test while holding `AIService._lock` + `service_registry._lock`, `SystemConfigurationAdmin` `"loading"` vs `"Active Host Unavailable"` verification, and `SystemConfiguration.get_solo()` / `AIService.run_model_load_safely()` fault-injection tests for `"sorry, too many clients already"` and `"Too many open files"`.
  - `deploy/tests.py` (`TestWS43SystemdAndSettingsGuardrails`): Verified `LimitNOFILE=65535` across all 5 systemd daemon units, `DATABASES["default"]["OPTIONS"]["application_name"]`, and single-source `VERBAL_ROLE` integrity.
- **Verification**: `./scripts/pre_push_check.sh` passed 100% (`511` tests in `302.8s`, `makemigrations --check` clean, `check --deploy` clean).
