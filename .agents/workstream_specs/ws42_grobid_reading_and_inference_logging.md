# Workstream 42 Spec: Grobid Reading Strategies, Document Provenance Synchronization, and Inference Service Logging & Resilience

## 1. Context & Objectives
During production deployment and administrative testing, two major functional issues were identified:
1. **Grobid Reading Strategy & Document Inline Defects**:
   - In Django Admin, adding a `GrobidReadingStrategy` via the `DocumentAdmin` inline (`GrobidReadingStrategyInline`) silently failed to create the row because `document` was included in the inline form fields instead of relying on the implicit parent relation.
   - Creating a `GrobidReadingStrategy` directly from `/admin/background_resources/grobidreadingstrategy/` generated 0 chunks because `GrobidReadingStrategy.save()` did not trigger strategy application, and `convert_chunk_store_document_grobid` threw a `ValueError` if `tei_xml` was not already pre-cached on the document, which `read_document()` caught and swallowed silently.
   - Ingestion skipped Grobid chunking if LangChain default chunks were already generated or if TEI header metadata was sparse, even when valid section text existed in the body.
   - Grobid metadata extraction in `grobid_client/tasks.py` failed to synchronize academic provenance onto the `Document` model (title was not updated if default/filename, dates, year, DOI, publisher/organization, and citation text were omitted).
2. **Inference Service Crash & Logging Black Hole**:
   - Switching hosting backends in `llm_api/models.py:manage_hosting_backend` only notified the inference server to unload PyTorch VRAM when transitioning to `vllm` or `ollama`, completely omitting `llamacpp`. As a result, switching to `llamacpp` left the PyTorch model allocated in GPU memory.
   - When switching back to PyTorch (e.g. Gemma4-12B), `_resolve_tokenizer_source` incorrectly prioritized `active_mini_model_id` / `llamacpp` utility flag over the active PyTorch backend, returning `None` for the tokenizer.
   - In `llm_api/ai_service.py:load_models`, line 356 evaluated `self.tokenizer.eos_token_id` directly, crashing with `AttributeError: 'NoneType' object has no attribute 'eos_token_id'` when `self.tokenizer` was `None`.
   - The systemd unit for `reason-inference` used standard execution without piping to `tee -a "{log_dir}/reason-inference.log"`, causing all `runserver` stderr, CUDA errors, and background thread crash tracebacks to be lost to `/var/log/reason/reason-inference.log`.

---

## 2. Architecture & Design Decisions

### A. Grobid Admin & Reading Strategy Execution
- **`background_resources/admin.py`**:
  - Update `GrobidReadingStrategyInline.fields` to `('strategy_description',)` with `extra = 0`.
- **`background_resources/models.py`**:
  - In `GrobidReadingStrategy.save()`, if the instance is being created (`self._state.adding`), automatically execute `self.apply_strategy(service_registry.rag_service)` so semantic chunks are generated immediately upon save from both admin forms and inline formsets.
- **`background_resources/rag_service.py`**:
  - In `convert_chunk_store_document_grobid`: if `document.grobid_metadata.tei_xml` is not present, check `is_grobid_available()`. If online and the document is a PDF, execute `task_extract_grobid_metadata.func(document.id)` on demand, refresh the document from the database, and proceed.
  - In `convert_chunk_store_document_grobid`: do not reject TEI XML simply because header metadata is sparse if the XML body contains valid sections or paragraph text.
  - In `ingest_queryset_documents`: if Grobid is online and the document is a PDF without TEI XML, extract Grobid metadata on demand and ensure `GrobidReadingStrategy` is created and ingested.

### B. Document Model Academic Provenance Synchronization
- **`grobid_client/tasks.py`**:
  - Implement a dedicated synchronization helper `update_document_from_grobid_metadata(doc, meta, ref=None)`:
    - `title`: Update if current title is empty, ends in `.pdf`, matches filename, or is a generic default.
    - `author`: Populate if empty or generic.
    - `doi`: Populate `doc.doi` and ensure `doc.source_url` is set to `https://doi.org/{doi}`.
    - `publication_year`: Populate from Grobid year.
    - `publication_date`: Parse valid ISO dates if available.
    - `organization`: Populate from journal or publisher if empty.
    - `document_type`: Set to `Document.DocumentType.ACADEMIC_PAPER`.
    - `citation_text`: Format standardized citation string if empty.

### C. Inference Service Logging & Backend VRAM Coordination
- **`deploy/fabric_deploy.py`**:
  - In `generate_systemd_units()`, wrap `ExecStart` for `reason-web.service`, `reason-worker.service`, `reason-scheduler.service`, and `reason-inference.service` with:
    `/bin/bash -c 'exec {cmd} 2>&1 | tee -a "{log_dir}/reason-{role}.log"'`
  - Ensures all stdout, stderr, request logs, tracebacks, and CUDA errors are captured both in journald and on disk in `/var/log/reason/`.
- **`verbal_config/settings.py`**:
  - Add `"django.server"` to `LOGGING["loggers"]`.
- **`llm_api/models.py`**:
  - In `manage_hosting_backend`:
    - Release PyTorch VRAM whenever switching away from PyTorch: `if old_backend == 'pytorch' and new_backend != 'pytorch':`.
    - When switching to PyTorch or changing `active_local_model`, signal the inference server via `/internal/reload-model/` or `/internal/unload-vram/`.
- **`llm_api/api.py`**:
  - Add `POST /internal/reload-model/` endpoint to unload VRAM and trigger background reload.
- **`llm_api/ai_service.py`**:
  - In `_resolve_tokenizer_source`: when role is inference or standalone and `config.hosting_backend == "pytorch"`, always return `config.active_local_model.hf_model_id` (never `None`).
  - In `load_models`: guard all `self.tokenizer` usages against `NoneType` to prevent `AttributeError`.

---

## 3. Verification Plan
1. **Grobid Reading & Provenance Tests**:
   - Test inline fields on `GrobidReadingStrategyInline`.
   - Test `update_document_from_grobid_metadata` correctly populates `Document` fields (title, author, doi, publication_year, citation_text, organization).
   - Test `GrobidReadingStrategy.save()` triggers chunk generation.
   - Test on-demand Grobid extraction in `convert_chunk_store_document_grobid`.
2. **Inference Service & Logging Tests**:
   - Test `generate_systemd_units` produces `tee -a` wrapped commands for web, worker, scheduler, and inference.
   - Test `manage_hosting_backend` sends unload-vram when switching to `llamacpp`.
   - Test `_resolve_tokenizer_source` returns correct tokenizer ID when PyTorch is active even if mini-model is configured.
   - Test `/internal/reload-model/` API endpoint.
3. **Regression Testing**:
   - Run full test suite via `../../py312/bin/python manage.py test background_resources grobid_client deploy llm_api --noinput`.
   - Run `./scripts/pre_push_check.sh`.
