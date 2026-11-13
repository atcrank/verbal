# Workstream 14: Demo UI Shakedown Cruise & Firefighting Robotics Walkthrough

## Objectives & Scope

Execute a thorough "shakedown cruise" of the entire Reason (`demo_ui`) stack using real scientific literature on the topic of **"Use of Drones and Robots in Firefighting"**:
1. **Grobid Infrastructure & Cgroup Remediation**:
   - Resolve the Docker container crash in `verbal_grobid` caused by the cgroups v2 / Java 17 mount mismatch.
   - Verify health check against Grobid API (`/api/isalive`).
2. **Real Paper Ingestion & Semantic Structure-Sensitive Chunking**:
   - Process the 3 PDFs in `demo_ui/demo_ui_trials/doctest_fodder/`:
     - `JAdvRobPenders.pdf`
     - `Journal of Field Robotics - 2023 - Talavera - An autonomous ground robot to support firefighters  interventions in indoor.pdf`
     - `fire-06-00093-with-cover.pdf`
   - Exercise `task_extract_grobid_metadata` and `GrobidReadingStrategy` (`convert_chunk_store_document_grobid`).
   - Retain the extracted semantic `RAGChunks`, bibliographic citations, and metadata as a reproducible fixture without committing the binary PDFs.
3. **Demo UI Doctest Suite (`demo_ui/demo_ui_trials/`)**:
   - Implement modular, executable doctests in `demo_ui/demo_ui_trials/`:
     - **Trial 1: Document Ingestion & Context Dropping** (Uploads, micro status badges, token estimation, and context chips).
     - **Trial 2: Multi-Step Cognitive Reasoning & Sandboxed Tools** (Evaluating study design constraints, running Python sandbox calculations for robot sensor coverage / battery runtime, intermediate thinking traces).
     - **Trial 3: Conversation Branching & Grips Knowledge Graph** (Branching assistant messages into alternative research design forks, inspecting Grips nodes, testing autonomous "Fill Stub" expansion).
   - Hook the doctests and auto-generated run reports into the Sphinx documentation.
4. **Offline Presentation Seeder**:
   - Provide a Django management command (`seed_demo_presentation`) that pre-seeds the firefighting papers, conversation branches, and Grips hierarchy into the database, serving as a failsafe during live presentations.
5. **Shakedown Tracking & Bug Catalog**:
   - Do not mask weaknesses or hack assertions to force false passes. Log all bugs, edge cases, and architectural friction in `notes/20260903_demo_ui_shakedown.md`, fixing immediate bugs directly and cataloging larger items.

---

## Tasks & Implementation Breakdown

### Phase 1: Grobid Infrastructure Repair
- Update `docker-compose.yml` grobid service configuration to resolve cgroups v2 NullPointerException (`cgroup: host` or upgraded image version).
- Test connectivity and API response via `process_pdf_with_grobid`.

### Phase 2: PDF Processing & RAG Chunk Extraction
- Ingest the 3 firefighting PDFs through `task_extract_grobid_metadata`.
- Trigger `GrobidReadingStrategy` to extract section-aware semantic chunks (`is_semantic_chunk=True`).
- Export the ingested state (Document records, References, Citations, RAGChunks) to `demo_ui/demo_ui_trials/fixtures/firefighting_chunks.json`.

### Phase 3: Demo UI Doctests (`demo_ui/demo_ui_trials/`)
- Draft executable `.rst` trials in `demo_ui/demo_ui_trials/`:
  - `trial_1_ingestion_and_context.rst`
  - `trial_2_multistep_blueprint_and_tools.rst`
  - `trial_3_branching_and_grips_expansion.rst`
- Implement report-generation helper for test run recording.
- Register `demo_ui_trials.rst` in Sphinx `documentation/source/index.rst`.

### Phase 4: Offline Presentation Mode
- Create `demo_ui/management/commands/seed_demo_presentation.py` to restore the pre-processed fixture, conversations, and Grips nodes.

### Phase 5: Verification, Notes & Shakedown Catalog
- Run the full doctest suite (`pytest demo_ui/demo_ui_trials/`).
- Build Sphinx documentation and verify rendering.
- Maintain notes in `notes/20260903_demo_ui_shakedown.md` and update `INDEX.md`.
