# Workstream 33: Launch Hardening, Architectural Codification, and Hygiene

## Context & Objectives

Following the completion of WS32 (rigorous trial reporting, honest self-evaluation, and full repository migration to Gemma 4 E2B), the codebase is functionally sound with 100% passing unit tests, e2e tests, and doctests.

Before initiating the final production deployment push, this workstream addresses key pre-launch items identified during operational review:
1. **Architectural Codification**: Preserve and deeply document Reason's core architectural patterns and design invariants (double-node reasoning step pair, cyclical graphs / multi-try loops, backend-to-frontend state flow, dynamic backend orchestration) into an enduring architecture document and updated agent skills before archiving historical workstreams.
2. **Schema & Migration Cleanup**: Purge the legacy unmanaged `CeleryStatus` model from `grips`, apply singleton plural metadata migrations to `llm_api`, and reconcile `sandbox_manager` default package dependencies so `makemigrations --check` passes cleanly.
3. **Repository & Git Hygiene**: Untrack binary Sphinx build artifacts (`.doctree`, `environment.pickle`) from git, update `.gitignore`, and resolve Sphinx documentation warnings.
4. **Operational Resilience & Deployment Tuning**: Consolidate multimodal checking into `modality_detector.py`, update `fabric_deploy.py:health_check` with backend-aware model warm-up checks, and provide a local `pre_push_check.sh` script.
5. **Workstream Archival**: Safely archive completed historical workstreams (`ws1` through `ws28`, `ws30`, `ws31`, `ws32`) into `.agents/workstream_specs/archive/`.

---

## Detailed Task Breakdown

### Phase 1: Architectural Codification & Skill Deepening

#### Task 1.1: Author Enduring Architecture Document
- Create `.agents/workstream_specs/notes/20261113_enduring_architectural_invariants.md` and index it in `INDEX.md`.
- Codify the 5 core architectural invariants of Reason:
  1. **Double-Node ReasoningStep Architecture (`_call` & `_eval`)**:
     - Every `ReasoningStep` compiles into an action node (`{step}_call`) and an evaluation node (`{step}_eval`).
     - The eval node semantically inspects outputs against `evaluation_criteria`, updates scratchpad memory, and decides next routing (`SUCCESS`, `SELF`, or failure step).
     - Replaces artificial sentinel exit tools (`TASK_COMPLETE`) with intrinsic semantic graph evaluation.
  2. **Cyclical State Graphs & Multi-Try Loops**:
     - Non-linear, iterative execution graphs that support routing to `SELF` for progressive refinement.
     - In-state loop counters and budget tracking prevent runaway recursion while granting small local models (e.g., Gemma 4-2B) multiple opportunities to converge.
  3. **Multi-Role Process Boundaries & Backend-to-Frontend State Flow**:
     - Strict `VERBAL_ROLE` privilege and memory separation (`web`, `worker`, `scheduler`, `inference`).
     - Real-time morphing DOM updates over Server-Sent Events via Datastar and PostgreSQL notifications (`subscribe_pg_events_sync`).
     - Persistent working memory (`state_tree`) with authentic DAG parentage (`parent_log_id`, `log_kind="TURN"` vs `"TRACE"`).
  4. **Dynamic Backend Orchestration & Zero-Polling**:
     - `SystemConfiguration` singleton dynamically orchestrating local containerized models (Ollama with `pull_policy="never"`, vLLM) vs. in-process PyTorch vs. GGUF CPU mini-models vs. external APIs.
     - Docker container sync managed dynamically by the inference service registry (`LazyServiceRegistry._sync_hosting_backend_state`), not static systemd units.
     - Cache-first Hugging Face model and tokenizer resolution (`local_files_only=True`).
  5. **Multimodal Inspection Safeguards**:
     - Zero-VRAM vision tower inspection preventing base64 payload routing to text-only tokenizers.
     - Discussion-context fallback (Grobid figure captions and context) for text models.

#### Task 1.2: Deepen Agent Skills
- Update `.agents/skills/metacognition_architect/SKILL.md` to reference the new enduring architecture invariants and emphasize the `_call`/`_eval` dual-node contract.
- Update `.agents/skills/celery_task_manager/SKILL.md` (renaming or refocusing to native `django.tasks` and `verbal_tasks` background infrastructure).

---

### Phase 2: Schema, Migration & Legacy Model Cleanup

#### Task 2.1: Purge Legacy `CeleryStatus` Model
- Remove `class CeleryStatus` from `grips/models.py`.
- Remove `CeleryStatusAdmin` from `grips/admin.py`.
- Ensure no lingering references exist in `grips` templates or views (the task dashboard is natively served by `verbal_tasks`).

#### Task 2.2: Reconcile Django Migrations
- Generate migration for `llm_api`: apply `SystemConfiguration` meta option updates.
- Generate migration for `sandbox_manager`: reconcile `SandboxConfiguration.requirements_txt` default string with current code.
- Verify that `python manage.py makemigrations --check` exits with code 0.

---

### Phase 3: Git Hygiene & Documentation Cleanup

#### Task 3.1: Untrack Documentation Build Artifacts
- Add `documentation/build/` to `.gitignore`.
- Remove existing tracked files from the git index: `git rm -r --cached documentation/build/`.
- Verify `git status` no longer reports binary `.doctree` or `.pickle` changes on docs rebuilds.

#### Task 3.2: Resolve Sphinx Documentation Warnings
- Identify and fix duplicate cross-reference labels and missing document targets in `documentation/source/demo_ui_trials.rst` and `metacognition_trials/*.rst`.
- Verify `make -C documentation clean html` builds with zero or minimal standard warnings.

---

### Phase 4: Operational Resilience & Deployment Pipeline Hardening

#### Task 4.1: Consolidate Multimodal Checking
- Ensure `llm_api/modality_detector.py` is the single source of truth for modality determination.
- Remove duplicate or redundant regexes across `ai_service.py` and `models.py`.

#### Task 4.2: Add Model Pre-Warming & Backend-Aware Readiness in Fabric
- In `deploy/fabric_deploy.py`, update `health_check` to perform a non-blocking model readiness ping subject to active `SystemConfiguration` backend (Ollama, vLLM, or PyTorch in-process).
- Include an operator-configurable warm-up pause to prevent initial 504 Gateway Timeouts after fresh reboots.

#### Task 4.3: Local Pre-Push Sanity Script
- Create `scripts/pre_push_check.sh`:
  - Runs `makemigrations --check`
  - Runs `check --deploy`
  - Runs `manage.py test --exclude-tag=e2e --noinput`
  - Checks for uncommitted changes

---

### Phase 5: Historical Workstream Archival

#### Task 5.1: Archive Historical Specs
- Create `.agents/workstream_specs/archive/`.
- Move completed workstreams (`ws1` through `ws28`, `ws30`, `ws31`, `ws32`, `candidate_review_*`, `SYNTHESIS_*`) into `archive/`.
- Retain active operational specs: `ws33_launch_hardening_and_architectural_codification.md`, `TICKETS.md`, `MULTI_MODEL_REVIEW_GUIDELINES.md`, and the `notes/` directory.
- Update `TICKETS.md` and `INDEX.md` paths.

---

## Verification & Acceptance Criteria
1. `python manage.py makemigrations --check` exits with code 0.
2. `git status` shows clean tracking without `documentation/build/` artifacts.
3. Unit test suite passes: `python manage.py test --exclude-tag=e2e --noinput`.
4. Sphinx builds cleanly without duplicate label errors.
5. Enduring architectural principles are fully documented in `notes/` and skills.

