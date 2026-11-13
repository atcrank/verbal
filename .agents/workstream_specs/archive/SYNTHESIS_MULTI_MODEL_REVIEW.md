# Multi-Model Comprehensive Review Synthesis
**Document Status**: Official Synthesis & Consensus Roadmap  
**Target Output**: Master Workstream Roadmap for Reason (`verbal`)  
**Date**: 2026-09-22  
**Inputs**:
- [candidate_review_claude_sonnet_4.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/candidate_review_claude_sonnet_4.md) (Claude Sonnet 4)
- [candidate_review_gemini_3_1_pro.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/candidate_review_gemini_3_1_pro.md) (Gemini 3.1 Pro)
- [candidate_review_gemini_3_8_flash.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/candidate_review_gemini_3_8_flash.md) (Gemini 3.8 Flash)
- Guidelines: [MULTI_MODEL_REVIEW_GUIDELINES.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/MULTI_MODEL_REVIEW_GUIDELINES.md)

---

## 1. Executive Meta-Summary & Reviewer Perspectives

Three frontier language models independently conducted comprehensive audits of the Reason codebase (`verbal`) across all 12 core applications, guided by the five evaluation pillars. 

| Model Reviewer | Character & Standout Contribution | Primary Stance |
|---|---|---|
| **Claude Sonnet 4** | **Security & Code Auditor**: Uncovered critical privilege escalation vulnerability in `django_shell_script`, discovered silent write drops in `DjangoCheckpointer.put_writes()`, identified stubbed meta-tools, and flagged worker thread exhaustion in broadcast SSE. | *“Technically audacious prototype with severe security and testing asymmetries. Must not add features until critical vulnerabilities and stubs are resolved.”* |
| **Gemini 3.1 Pro** | **Systems Strategist**: High-level structural synthesis emphasizing architectural durability vs. extreme app unevenness, highlighting the bimodal test crisis and web worker synchronous blocking. | *“On the precipice of greatness, but suffering from severe unevenness. Consolidate and test before expanding.”* |
| **Gemini 3.8 Flash** | **Scientific & Empirical Specialist**: Deep technical evaluation of sociotechnical physics simulation, hardware VRAM budgeting, `verbal_tasks` mechanics, and Workstream 16 adversarial verification. | *“Exceptional architectural foundation and empirical philosophy; critically hampered by lack of dimensional analysis, co-simulation runners, and synchronous web blocking.”* |

Despite differences in depth and analytical focus, **all three reviewers arrived at an identical overarching verdict**:
> Reason has solved the hardest architectural problem in agentic AI—replacing ungrounded, ephemeral "AI theater" with durable, database-backed state graphs (`CognitiveBlueprint`), verifiable academic grounding (GROBID + PGVector), and human-in-the-loop counterfactual branching. However, the codebase is in a **fragile bimodal state**: world-class rigor in core reasoning and task queuing, contrasted with critical zero-coverage blind spots (`sandbox_manager`, `grobid_client`), an actively misleading README, lingering Celery debt, synchronous HTTP worker blocking, and a severe host-process code execution vulnerability.

---

## 2. Comparative App-by-App Audit Matrix

The table below compiles and harmonizes the scores (Code Quality / Test Rigor / Documentation out of 5) and consolidates consensus findings across all three evaluations.

| Application | Claude Sonnet 4 (Q/T/D) | Gemini 3.1 Pro (Q/T/D) | Gemini 3.8 Flash (Q/T/D) | Harmonized Consensus (Q/T/D) | Core Strengths & Key Files | Critical Tech Debt & Consensus Risks |
|---|:---:|:---:|:---:|:---:|---|---|
| [verbal_config](file:///home/crank/coding/antigrav/verbal/verbal_config) | 3 / 3 / 2 | 4 / 3 / 2 | 4 / 3 / 4 | **3.7 / 3.0 / 2.7** | Role orchestration (`VERBAL_ROLE`), clean ASGI/WSGI, OpenAPI/Swagger integration. | Auth views/forms declared directly in `urls.py`; `stream_broadcasts` holds worker thread via `time.sleep(2)`. |
| [verbal_tasks](file:///home/crank/coding/antigrav/verbal/verbal_tasks) | 5 / 5 / 4 | 5 / 5 / 4 | 5 / 5 / 4 | **5.0 / 5.0 / 4.0** | PostgreSQL-native task queue (`DatabaseTaskBackend`), cron matching, `LISTEN`/`NOTIFY`, telemetry, 686-line test suite. | `supports_async_task = False`; polling worker loop; single-machine assumption. |
| [llm_api](file:///home/crank/coding/antigrav/verbal/llm_api) | 4 / 4 / 3 | 4 / 4 / 3 | 4 / 4 / 4 | **4.0 / 4.0 / 3.3** | Hardware profiler (`hardware.py`), dynamic VRAM budgeting, Outlines structured generation, LoRA hot-swap. | Monolithic `ai_service.py` (1,179 lines); synchronous inference in REST endpoints; legacy `CELERY_TASK_ALWAYS_EAGER` in tests. |
| [sandbox_manager](file:///home/crank/coding/antigrav/verbal/sandbox_manager) | 4 / **1** / 3 | 3 / **1** / 2 | 4 / **1** / 3 | **3.7 / 1.0 / 2.7** | Docker jail on Port 8002, path traversal defense, `RLIMIT_CPU`/`RLIMIT_FSIZE`, avoiding `RLIMIT_AS` OpenBLAS bug. | **Zero test coverage** (4 lines / 60 bytes); `SandboxConfiguration.save()` writes to filesystem; timeout stderr decoding bug; no dimensional analysis. |
| [grobid_client](file:///home/crank/coding/antigrav/verbal/grobid_client) | 4 / **1** / 3 | 3 / **1** / 2 | 4 / **1** / 3 | **3.7 / 1.0 / 2.7** | 3-stage metadata extraction (TEI XML → heuristic → LLM fallback), citation graph generation. | **Zero test coverage** (4 lines / 60 bytes); no circuit breaker/retry for external GROBID daemon; side-effect truncation in `Reference.save()`. |
| [background_resources](file:///home/crank/coding/antigrav/verbal/background_resources) | 4 / 4 / 4 | 4 / 5 / 3 | 4 / 5 / 4 | **4.0 / 4.7 / 3.7** | PGVector HNSW 384-dim index, Super Retriever with lineage deduplication, deep context reporting. | Monolithic `rag_service.py` (932 lines); hardcoded stub `domain_terms` in `nlp_service.py`; dual similarity query pattern. |
| [grips](file:///home/crank/coding/antigrav/verbal/grips) | 4 / 4 / 4 | 4 / 4 / 3 | 4 / 4 / 4 | **4.0 / 4.0 / 3.7** | Hybrid narrative Markdown + computable atomic JSON claims (`structured_claims`), Git-locked filesystem sync. | Lack of formal symbolic solver (Z3/ASP); risk of LLM circular concept inflation; dual DB/disk sync hazards. |
| [metacognition](file:///home/crank/coding/antigrav/verbal/metacognition) | 4 / 5 / 4 | 5 / 5 / 4 | 5 / 5 / 4 | **4.7 / 5.0 / 4.0** | Database-compiled LangGraph (`CognitiveBlueprint`), `DjangoCheckpointer`, human-in-the-loop approvals, 1,305-line test suite. | **Critical security hole** in `django_shell_script` (bypassable AST + `exec()`); `put_writes` is a no-op; `clone_and_modify_blueprint` is a stub; state tree bloat. |
| [work_organisation](file:///home/crank/coding/antigrav/verbal/work_organisation) | 5 / 4 / 4 | 4 / 4 / 3 | 5 / 4 / 4 | **4.7 / 4.0 / 3.7** | `GroupScopedQuerySet` access control, 4 privacy modes, Datastar SSE updates, automated causal factor extraction. | Causal factors are static database cards, not interactive simulation graphs; SSE multi-connection worker saturation risk. |
| [benchmarking](file:///home/crank/coding/antigrav/verbal/benchmarking) | 4 / 3 / 3 | 4 / 4 / 3 | 4 / 3 / 4 | **4.0 / 3.3 / 3.3** | Synthetic scenario generation from RAG, LLM-as-a-judge with Outlines, Pandas DataFrame export with multi-index. | Unsloth fine-tuning runner incomplete; sweeps monopolize single GPU; legacy `CELERY_TASK_ALWAYS_EAGER` in tests. |
| [demo_ui](file:///home/crank/coding/antigrav/verbal/demo_ui) | 4 / 3 / 4 | 3 / 2 / 4 | 4 / 4 / 4 | **3.7 / 3.0 / 4.0** | "Reason" research workbench, conversation branching tree, Datastar SSE step streaming, workspace file sync. | Synchronous LLM generation in `views.py:L296` blocks HTTP worker thread; monolithic HTML templates; thin browser UI tests. |
| [documentation & trials](file:///home/crank/coding/antigrav/verbal/documentation) | 4 / 5 / 2 | 3 / 4 / 1 | 4 / 5 / 3 | **3.7 / 4.7 / 2.0** | Best-in-class executable doctest trials (Playwright + real GPU + Sphinx); robust `update_dependencies.sh` script. | **README is dangerously wrong** (SQLite, Redis, Celery, FAISS); trials require 15+ minutes of GPU runtime with no mock mode. |

---

## 3. The Five Universal Consensus Pillars

All three models reached 100% consensus on the following core diagnoses:

### Consensus 1: The README is Actively Dangerous Tech Debt
The root [readme.md](file:///home/crank/coding/antigrav/verbal/readme.md) documents an entirely obsolete technology stack:
- Instructs users to use SQLite (`rename db-dev.sqlite3 to db.sqlite3`). Reality: PostgreSQL 18 with `pgvector`.
- Instructs users to install Docker for Redis/Celery. Reality: native `verbal_tasks` (`DatabaseTaskBackend`).
- Claims the vector store is FAISS. Reality: native `pgvector` HNSW indexes with `vector_cosine_ops`.
- Documents obsolete run scripts (`sh toggle_background_task_service.sh`).
*Consensus Action*: Immediate top-to-bottom rewrite of `readme.md`.

### Consensus 2: The Bimodal Testing Crisis (`sandbox_manager` & `grobid_client`)
While `metacognition` (1,305 lines), `background_resources` (748 lines), and `verbal_tasks` (686 lines) have exemplary test suites that test real database invariants, the system boundaries have been completely neglected:
- [sandbox_manager/tests.py](file:///home/crank/coding/antigrav/verbal/sandbox_manager/tests.py) has 4 lines (60 bytes) and zero tests.
- [grobid_client/tests.py](file:///home/crank/coding/antigrav/verbal/grobid_client/tests.py) has 4 lines (60 bytes) and zero tests.
*Consensus Action*: Create comprehensive unit test suites for path traversal, OS resource bounds, PEP-508 parsing, TEI XML fixtures, and citation relationship building before touching production code.

### Consensus 3: Synchronous Web Request Blocking
Both `demo_ui/views.py:L296` (native LLM generation) and `llm_api/api.py:L77` (`run_blueprint`) execute synchronously within the Django HTTP worker process. A 30–90 second inference run on a local GPU blocks the HTTP thread entirely, creating severe gateway timeout risks (504s under Nginx/Granian) and starving other users.
*Consensus Action*: Offload generation and blueprint runs to `verbal_tasks` via `DatabaseTaskBackend`, returning a task ID and streaming tokens/events asynchronously via Datastar SSE.

### Consensus 4: Purge Residual Celery/Redis Dependencies
Although `verbal_tasks` has been in production use, residual Celery artifacts linger across the repo:
- `requirements.in:L4-L7` still pins `celery`, `django-celery-beat`, and `redis`.
- Four test suites (`benchmarking/tests.py`, `llm_api/tests.py`, `background_resources/tests.py`, `metacognition/tests.py`) still declare `CELERY_TASK_ALWAYS_EAGER = True`.
*Consensus Action*: Remove Celery from `requirements.in`, recompile `requirements.txt`, and strip obsolete test flags.

### Consensus 5: Sociotechnical Physics Modeling Gaps
Reason has a clear philosophical alignment ("Empirical Co-Pilot, Not Hype Machine"), but currently functions as an upstream study designer rather than an active simulation participant:
- **No Physical Unit / Dimensional Analysis**: The sandbox lacks `Pint` or `SymPy Units`. Models can silently conflate Watts and Watt-hours or meters and kilometers without triggering errors.
- **No Co-Simulation Interfaces**: No bindings for SimPy (discrete-event), Mesa (agent-based), or FMI/FMU standards.
- **No Parameter Sensitivity / UQ**: No Sobol variance decomposition, Morris screening, or Monte Carlo sampling tooling.
- **Static Causal Graphs**: Causal factors in `work_organisation` are static cards, not interactive feedback loop diagrams.

---

## 4. Key Divergences, Unique Insights & Critical Findings

Beyond the consensus points, each model contributed specialized insights that must be integrated:

### 4.1 Claude Sonnet 4: Critical Security & Execution Bugs
1. **Critical Privilege Escalation in `django_shell_script`** ([metacognition/meta_tools.py:L285-L333](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L285-L333)):
   - The tool executes arbitrary Python in the Django host process via `exec()`.
   - Its AST blocklist (`os`, `sys`, `subprocess`) is trivially bypassable using `__import__('os')`, `importlib.import_module('os')`, or `getattr(__builtins__, '__import__')('subprocess')`.
   - Combined with `manage_dynamic_tools` ([meta_tools.py:L544-L607](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L544-L607)), which writes Python files to the filesystem, an autonomous agent (or an adversarial prompt injection) can achieve persistent arbitrary host code execution.
   - *Remedy*: Move execution to the Docker sandbox container, or replace with a strict AST visitor that forbids all builtin imports and dynamic code execution.
2. **`DjangoCheckpointer.put_writes` is a Silent No-Op** ([checkpointer.py:L181-L187](file:///home/crank/coding/antigrav/verbal/metacognition/checkpointer.py#L181-L187)):
   - Intermediate writes generated during parallel step fan-out (`parallel_steps`) are silently dropped. In crash recovery scenarios, parallel branch state will be lost.
3. **SSE Polling Thread Exhaustion** ([broadcast_views.py:L15-L48](file:///home/crank/coding/antigrav/verbal/verbal_config/broadcast_views.py#L15-L48)):
   - `stream_broadcasts` holds a synchronous worker thread for 55 seconds using `time.sleep(2)`. Ten connected users will tie up 10 Django threads.
4. **Dead Code & Prototype Stubs**:
   - `clone_and_modify_blueprint` ([meta_tools.py:L99-L112](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L99-L112)) is an empty stub returning a string with `# (Omitted for brevity in prototype)`.
   - `delegate_task` ([meta_tools.py:L255-L271](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L255-L271)) hardcodes `blueprint_id=1`.
   - `sandbox/main.py:L66` timeout stderr decoding assumes `bytes`, but `subprocess.run(..., text=True)` returns `str`.

### 4.2 Gemini 3.8 Flash: Systems Rigor & Empirical Heritage
1. **Precise VRAM Budgeting & Hardware Introspection**:
   - Evaluated `llm_api/hardware.py` and confirmed the sophistication of dynamic VRAM budgeting (`torch.cuda.mem_get_info()`), Outlines grammar enforcement, and PEFT LoRA hot-swapping.
2. **Task Engine Concurrency Mechanics**:
   - Noted that `verbal_tasks` sets `supports_async_task = False` ([task_backend.py:L25](file:///home/crank/coding/antigrav/verbal/verbal_tasks/task_backend.py#L25)) and relies on synchronous DB polling loops. Under large worker counts, row-locking contention must be monitored.
3. **Hardcoded Domain Terms**:
   - Located the placeholder `domain_terms = ["term1", "term2", "term3"]` in [nlp_service.py:L15](file:///home/crank/coding/antigrav/verbal/background_resources/nlp_service.py#L15), which weakens intent detection.
4. **Workstream 16 Adversarial Integrity**:
   - Highlighted the architectural importance of capturing and surfacing model reasoning bugs (such as the `.idxmin()` duration inversion bug) in doctest trials, rather than hiding model failure modes.
5. **Build System Excellence**:
   - Commended `update_dependencies.sh` (376 lines) as an enterprise-grade script that handles PyTorch wheels, Artifactory 403 errors, and pip-tools compilation.

### 4.3 Gemini 3.1 Pro: Consolidation Over Expansion
1. **The Risk of Feature Creep**:
   - Emphasized that adding complex co-simulation or new agent blueprints prior to resolving test coverage, README staleness, and Celery cleanup risks collapsing the maintainability of the project.

---

## 5. Master Roadmap & Prioritized Workstream Tasks

Synthesizing all three proposals, the immediate work is divided into three sequential, logically ordered phases:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   PHASE 1: CRITICAL REMEDIATION & SECURITY             │
│   (README Rewrite, Celery Purge, Sandbox/Grobid Tests, AST Hardening)  │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   PHASE 2: ASYNC DECOUPLING & CORE RESILIENCE          │
│   (Async LLM Generation, Datastar SSE Decoupling, Checkpointer Fix)    │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   PHASE 3: SOCIOTECHNICAL SIMULATION EXPANSION         │
│   (Pint Dimensional Analysis, Interactive Causal Canvas, SimPy Runner) │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Phase 1: Critical Remediation, Security & Test Boundaries (Immediate P0)

#### Ticket 1.1: Root Documentation Modernization & Total Celery Purge
- **Target Apps**: `root`, `verbal_config`, `requirements.in`, `benchmarking`, `llm_api`, `background_resources`, `metacognition`
- **Scope**:
  1. Completely rewrite [readme.md](file:///home/crank/coding/antigrav/verbal/readme.md) to accurately document: PostgreSQL 18 + `pgvector`, native `verbal_tasks` (`runtaskworker`, `runtaskscheduler`), the 3 operational roles (`VERBAL_ROLE=web|inference|worker`), PGVector HNSW embeddings, and correct startup scripts.
  2. Remove `celery`, `django-celery-beat`, and `redis` from `requirements.in`. Recompile `requirements.txt` via `bash update_dependencies.sh --no-upgrade`.
  3. Purge all legacy `CELERY_TASK_ALWAYS_EAGER = True` settings from test files.
- **Verification**: `grep -rn "celery" .` yields zero matches in active code/config; README steps spin up the system cleanly from scratch.

#### Ticket 1.2: Close `django_shell_script` Privilege Escalation & Sandbox Jailing
- **Target Apps**: `metacognition/meta_tools.py`
- **Scope**:
  1. Eliminate host `exec()` in `django_shell_script`. Route execution requests to the isolated `verbal_sandbox` Docker container via `sandbox_manager`.
  2. Implement an AST visitor that rejects: dynamic imports (`__import__`, `importlib`), builtin attribute introspection (`__builtins__`, `getattr`), and dynamic code evaluation (`eval`, `exec`, `compile`).
  3. Audit `manage_dynamic_tools` to ensure tool definitions are validated and cannot inject arbitrary unvetted modules into the running process.
- **Verification**: Unit tests with known bypass vectors (`__import__('os').system(...)`, `getattr(...)`) assert that code execution is rejected with an explicit security error.

#### Ticket 1.3: Comprehensive Unit Test Suite for `sandbox_manager`
- **Target Apps**: `sandbox_manager`
- **Scope**:
  1. Test path traversal blocking in `sandbox/main.py` (reject `../`, `/etc/passwd`, paths outside workspace).
  2. Test OS limit enforcement (`RLIMIT_CPU` timeout code 124, `RLIMIT_FSIZE` file bounds).
  3. Fix the timeout stderr decoding bug in `sandbox/main.py:L66` (`text=True` returns `str`, not `bytes`).
  4. Test `SandboxConfiguration` PEP-508 validation on `save()` and remove direct filesystem side effects from model saves.
- **Verification**: `python manage.py test sandbox_manager` passes with >85% code coverage.

#### Ticket 1.4: Comprehensive Unit Test Suite for `grobid_client`
- **Target Apps**: `grobid_client`
- **Scope**:
  1. Add cached TEI XML fixture files in `grobid_client/test_data/`.
  2. Test deterministic TEI XML parsing (`_extract_grobid_deterministic`): titles, abstracts, authors, affiliations, DOIs.
  3. Test `_parse_bibl_struct` citation network extraction and reference links.
  4. Test string truncation safeguards in `Reference.save()` and move truncation logic to `clean()`.
  5. Test graceful handling when GROBID server is unreachable.
- **Verification**: `python manage.py test grobid_client` passes with >85% code coverage without requiring a live GROBID network daemon.

#### Ticket 1.5: Code Hygiene & Stub Remediation
- **Target Apps**: `metacognition`, `background_resources`, `verbal_config`
- **Scope**:
  1. Fix `DjangoCheckpointer.put_writes`: persist intermediate writes to prevent parallel fan-out state loss.
  2. Remove or explicitly raise `NotImplementedError` on `clone_and_modify_blueprint` stub.
  3. Replace hardcoded `domain_terms = ["term1", "term2", "term3"]` in `nlp_service.py:L15` with dynamic domain queries against `grips.models.Domain`.
  4. Refactor `stream_broadcasts` in `broadcast_views.py` to avoid holding synchronous worker threads in `time.sleep(2)`.
- **Verification**: Clean test run; no placeholder strings in production code paths.

---

### Phase 2: Asynchronous Decoupling & System Scalability (P1)

#### Ticket 2.1: Asynchronous LLM Generation & Blueprint Execution
- **Target Apps**: `demo_ui`, `llm_api`, `verbal_tasks`
- **Scope**:
  1. Refactor `demo_ui/views.py:L296` native generation to enqueue a background task via `verbal_tasks` (`DatabaseTaskBackend`).
  2. Use Datastar SSE to stream partial tokens and step updates dynamically back to the client interface.
  3. Refactor `llm_api/api.py:generate_response` and `run_blueprint` to support async mode returning task IDs.
- **Verification**: HTTP requests return in <200ms with a task tracker; long-running blueprints stream updates over SSE without thread timeouts.

#### Ticket 2.2: State Tree Compaction & Variant Tree Pruning
- **Target Apps**: `metacognition`, `verbal_tasks` (NightManager)
- **Scope**:
  1. Implement compression/compaction for `AgentState.state_tree` JSON checkpoints to prevent unbounded PostgreSQL database growth.
  2. Implement automated pruning in NightManager to cap `ReasoningStep` variant trees at 3–4 generations based on empirical evaluation scores.
- **Verification**: Automated test verifies that long-running conversations compress state history and prune sub-optimal variants.

---

### Phase 3: Sociotechnical Simulation Capabilities (P2)

#### Ticket 3.1: Dimensional Analysis & Unit Verification in `verbal_sandbox`
- **Target Apps**: `sandbox_manager`, `metacognition`
- **Scope**:
  1. Add `pint` and `sympy` to `sandbox/requirements.txt` and rebuild container.
  2. Introduce physical unit verification in `metacognition/actions.py`: AST pre-check validating that quantitative equations declare compatible units (e.g. `ureg.watt` vs `ureg.watt_hour`).
  3. Return explicit `DimensionalityError` to the agent's self-correcting evaluation loop if units are inconsistent.
- **Verification**: Sandboxed test fails when attempting to add Watts to Watt-hours, surfacing a clear dimensional mismatch error to the model.

#### Ticket 3.2: Interactive Causal Loop & System Dynamics Canvas
- **Target Apps**: `work_organisation`, `grips`
- **Scope**:
  1. Upgrade the whiteboard from static causal factor cards to an interactive directed graph canvas using Cytoscape.js or Dagre.
  2. Support positive (`+`) and negative (`-`) feedback loop connections and delay indicators.
  3. Allow direct compilation from whiteboard causal diagrams into `grips.ConceptNode` structured claims.
- **Verification**: Playwright browser test builds a 3-node causal loop on the whiteboard and exports verified claims to Grips.

#### Ticket 3.3: Co-Simulation Interface Adapter (SimPy Proof-of-Concept)
- **Target Apps**: `sandbox_manager`, `metacognition`
- **Scope**:
  1. Pre-install `simpy` and `scipy` in the sandbox container.
  2. Create a standardized `SimulationRunner` meta-tool capable of orchestrating discrete-event simulation sweeps and returning statistical summaries (means, percentiles, confidence intervals) to the agent.
- **Verification**: A doctest trial runs a discrete-event queue or swarm simulation, tabulating empirical metrics for study design.
