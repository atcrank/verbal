# Workstream Spec Candidate: Comprehensive Project Review
**Reviewing Agent / Model**: Claude Sonnet 4 (Antigravity)  
**Date**: 2026-09-21  
**Evaluation Target**: Reason (Verbal) Research Co-Pilot  

---

## 1. Executive Summary & Reviewer Perspective

**Reason** is a technically audacious and philosophically principled research platform that successfully synthesizes a remarkable number of moving parts—academic literature ingestion, vector-indexed RAG, ontological knowledge graphs, database-compiled agentic state machines, containerized code execution, and collaborative multi-user workspaces—into a coherent, sovereignty-preserving whole. The "Empirical Co-Pilot, Not Hype Machine" positioning is **not marketing**; it is architecturally enforced through grounded retrieval, AST-audited sandboxes, human-in-the-loop tool approval, and structured claim linting.

The codebase demonstrates the work of a builder who genuinely understands the problem domain—not someone assembling off-the-shelf LLM wrappers. The PostgreSQL task backend replacing Celery ([`verbal_tasks/task_backend.py`](file:///home/crank/coding/antigrav/verbal/verbal_tasks/task_backend.py)) is a particularly impressive infrastructure bet. The database-compiled LangGraph blueprints ([`metacognition/compiler.py`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py)) are genuinely novel. The executable doctest trial methodology is best-in-class for bridging software verification and scientific reproducibility.

### Honest Assessment of Maturity

However, Reason is **not a product**. It is a highly capable research prototype with uneven polish. My review diverges from the Gemini review in several important respects:

1. **The security surface area is larger than acknowledged.** The `django_shell_script` meta-tool ([`metacognition/meta_tools.py:L285-L333`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L285-L333)) executes arbitrary Python via `exec()` in the Django host process. The AST import blocklist (`os`, `sys`, `subprocess`) is trivially bypassable (e.g., `__import__('os')`, `importlib.import_module('os')`, `getattr(builtins, '__import__')('subprocess')`). This is not a sandbox—it is a privilege escalation vector gated only by a string-matching heuristic.

2. **The Celery migration is incomplete at the test level.** Four test files still reference `CELERY_TASK_ALWAYS_EAGER=True` ([`benchmarking/tests.py:L36`](file:///home/crank/coding/antigrav/verbal/benchmarking/tests.py#L36), [`llm_api/tests.py:L34`](file:///home/crank/coding/antigrav/verbal/llm_api/tests.py#L34), [`background_resources/tests.py:L101`](file:///home/crank/coding/antigrav/verbal/background_resources/tests.py#L101), [`metacognition/tests.py:L364,L501`](file:///home/crank/coding/antigrav/verbal/metacognition/tests.py#L364)). While these may be harmless overrides, they indicate incomplete cleanup and risk confusing future developers about the canonical task infrastructure.

3. **The `manage_dynamic_tools` function** ([`meta_tools.py:L544-L607`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L544-L607)) writes Python files to the filesystem and registers them as callable tool definitions. Combined with the `django_shell_script` issue, this creates a persistent code injection path: the NightManager agent can write a tool, then later tools can call it.

4. **The README is not merely "stale"—it is actively misleading.** It references SQLite, Redis, Celery, and FAISS ([`readme.md:L4-L26`](file:///home/crank/coding/antigrav/verbal/readme.md#L4-L26)). None of these are the current stack. An external contributor following this README will waste hours.

5. **The `clone_and_modify_blueprint` meta-tool is a stub** ([`meta_tools.py:L99-L112`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L99-L112)): the body contains a comment `# (Omitted for brevity in prototype, would deep-copy ReasoningSteps)` and returns a placeholder string. This is dead code masquerading as a feature.

---

## 2. App-by-App Audit Matrix

| Application | Code Quality (1-5) | Test Rigor (1-5) | Documentation (1-5) | Strengths & Key Files | Critical Weaknesses & Tech Debt |
|---|---|---|---|---|---|
| [verbal_config](file:///home/crank/coding/antigrav/verbal/verbal_config) | 3 | 3 | 2 | Role-based architecture (`VERBAL_ROLE`) is clean and well-enforced; broadcast SSE views are properly auth-gated ([`broadcast_views.py:L51-L88`](file:///home/crank/coding/antigrav/verbal/verbal_config/broadcast_views.py#L51-L88)). | Auth views/forms embedded in `urls.py`; `stream_broadcasts` uses `time.sleep(2)` polling in a synchronous view ([`broadcast_views.py:L32`](file:///home/crank/coding/antigrav/verbal/verbal_config/broadcast_views.py#L32))—will hold a worker thread for 55 seconds per connection. |
| [verbal_tasks](file:///home/crank/coding/antigrav/verbal/verbal_tasks) | 5 | 5 | 4 | Genuinely innovative PostgreSQL-native task backend; excellent cron evaluator; `LISTEN`/`NOTIFY` pub/sub; 685-line test suite with zombie recovery and telemetry tests. | `supports_async_task = False`; synchronous polling loop; no distributed worker coordination (single-machine assumption). |
| [llm_api](file:///home/crank/coding/antigrav/verbal/llm_api) | 4 | 4 | 3 | Hardware profiler with runtime VRAM budgeting and compute capability detection ([`hardware.py:L127-L198`](file:///home/crank/coding/antigrav/verbal/llm_api/hardware.py#L127-L198)); Outlines structured generation; LoRA hot-swap. Clean frozen dataclasses with inline doctests. | `ai_service.py` is monolithic (1,179 lines); `CELERY_TASK_ALWAYS_EAGER` in tests; synchronous API generation blocks HTTP workers. |
| [sandbox_manager](file:///home/crank/coding/antigrav/verbal/sandbox_manager) | 4 | **1** | 3 | `RLIMIT_FSIZE` and `RLIMIT_CPU` enforcement; deliberate avoidance of `RLIMIT_AS` with well-documented rationale ([`sandbox/main.py:L21-L27`](file:///home/crank/coding/antigrav/verbal/sandbox/main.py#L21-L27)); path traversal defense. PEP-508 validation on save. | **3-line test file** with zero tests; `SandboxConfiguration.save()` writes directly to filesystem ([`models.py:L49-L52`](file:///home/crank/coding/antigrav/verbal/sandbox_manager/models.py#L49-L52))—side effect in model save is an antipattern; timeout `stderr` decoding assumes bytes but `text=True` returns str ([`main.py:L66`](file:///home/crank/coding/antigrav/verbal/sandbox/main.py#L66)). |
| [grobid_client](file:///home/crank/coding/antigrav/verbal/grobid_client) | 4 | **1** | 3 | Sophisticated 3-stage metadata extraction pipeline: deterministic TEI → heuristic scrape → LLM OCR fallback ([`tasks.py:L122-L313`](file:///home/crank/coding/antigrav/verbal/grobid_client/tasks.py#L122-L313)); citation graph construction with in-text context mapping. | **3-line test file** with zero tests; no retry/circuit-breaker for GROBID server failures; `Reference.save()` performs field truncation as a side effect—should be a `clean()` concern. |
| [background_resources](file:///home/crank/coding/antigrav/verbal/background_resources) | 4 | 4 | 4 | Super Retriever with lineage deduplication ([`retrieval.py:L32-L189`](file:///home/crank/coding/antigrav/verbal/background_resources/retrieval.py#L32-L189)); `get_deep_context_report` combining semantic + lexical + conversation history; NLP intent detection with dynamic domain matcher. | `NLPService.domain_terms` is a hardcoded placeholder list ([`nlp_service.py:L15`](file:///home/crank/coding/antigrav/verbal/background_resources/nlp_service.py#L15)); Grips similarity re-query pattern (`grips_k * 2`) is a workaround, not a real scored retrieval interface. |
| [grips](file:///home/crank/coding/antigrav/verbal/grips) | 4 | 4 | 4 | Hybrid narrative + symbolic knowledge representation via `structured_claims` JSON; PGVector HNSW embedding index with proper `vector_cosine_ops`; `source_chunk` FK for lineage traceability; Wikipedia-style issue flag taxonomy. | No formal reasoner over `structured_claims`; risk of LLM-generated circular concept inflation; dual-state (DB + filesystem markdown) introduces sync hazards. |
| [metacognition](file:///home/crank/coding/antigrav/verbal/metacognition) | 4 | 5 | 4 | Database-compiled LangGraph with variant selection, cancellation, token budget summarization, sub-blueprint delegation, and human-in-the-loop tool approval. `DjangoCheckpointer` ([`checkpointer.py`](file:///home/crank/coding/antigrav/verbal/metacognition/checkpointer.py)) is a clean LangGraph integration. 1,304-line test suite. | `compiler.py` (952 lines) and `actions.py` (1,009 lines) are very large; `django_shell_script` has **bypassable AST security** ([`meta_tools.py:L285-L333`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L285-L333)); `clone_and_modify_blueprint` is a stub; `put_writes` is a no-op. |
| [work_organisation](file:///home/crank/coding/antigrav/verbal/work_organisation) | 5 | 4 | 4 | `GroupScopedQuerySet` is an elegant, reusable permission system ([`models.py:L11-L81`](file:///home/crank/coding/antigrav/verbal/work_organisation/models.py#L11-L81)); four access modes including anonymized DB scrubbing; rich Markdown export with causal factors. | Causal factors are persisted as static cards, not interactive simulations; whiteboard positioning is computed server-side with fixed layout offsets. |
| [benchmarking](file:///home/crank/coding/antigrav/verbal/benchmarking) | 4 | 3 | 3 | `Investigation.to_dataframe()` using `pd.json_normalize` for multi-index statistical analysis; `FineTuningDataset.is_stale` property for cache invalidation; scenario-group → dataset provenance chain. | Unsloth fine-tuning is incomplete; `CELERY_TASK_ALWAYS_EAGER` in tests; sweep runs monopolize the single GPU. |
| [demo_ui](file:///home/crank/coding/antigrav/verbal/demo_ui) | 4 | 3 | 4 | Clean HTMX partials with conversation branching; robust document ingestion pipeline with status tracking across TaskRecord states ([`views.py:L354-L401`](file:///home/crank/coding/antigrav/verbal/demo_ui/views.py#L354-L401)); unified search endpoint. | Synchronous LLM generation at [`views.py:L296`](file:///home/crank/coding/antigrav/verbal/demo_ui/views.py#L296) blocks HTTP worker; `tests_ui.py` is only 133 lines—thin coverage of a complex UI surface. |
| [documentation & trials](file:///home/crank/coding/antigrav/verbal/documentation) | 4 | 5 | 2 | The executable doctest trial methodology (Playwright + real GPU + Sphinx reports) is genuinely novel and scientifically valuable; `conftest.py` report writer with recursive sub-monologue rendering is thorough. | **README is dangerously wrong** about the tech stack; trial execution requires 15+ minutes of GPU runtime with no incremental mode; `_report_doctest_run` in `conftest.py` writes to `metacognition/metacognition_trials/` hardcoded path. |

---

## 3. Pillar 1: Competitive Landscape & Simulation Value Proposition

### 3.1 Comparison vs. Generic LLM Frameworks & Autonomous Science Platforms

**vs. Generic Agent Frameworks (LangChain, LlamaIndex, CrewAI, AutoGen, DSPy)**:

Reason's genuine architectural advantage is **database-native state persistence**. Where LangChain graphs are compiled from Python code and hold state in memory (or at best in Redis), Reason compiles graphs from Django ORM records (`CognitiveBlueprint` → `ReasoningStep` → `ToolDefinition`) with PostgreSQL-backed checkpointing ([`compiler.py:L894-L951`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L894-L951)). This means:
- Blueprints can be edited by non-programmers through Django Admin.
- Step variants can be A/B tested with weighted probabilistic selection ([`compiler.py:L19-L57`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L19-L57)).
- Sessions survive server restarts without state loss.

Where generic frameworks **win decisively**: ecosystem breadth (hundreds of pre-built tool integrations), async-first designs, multi-provider streaming, and orders-of-magnitude larger contributor communities. Reason carries the maintenance burden of custom infrastructure that generic libraries provide out-of-the-box.

**vs. Autonomous Science Platforms (Sakana AI Scientist, ChemCrow, SciCode)**:

Reason's human-in-the-loop architecture provides genuinely higher epistemic reliability. The tool approval modal system ([`compiler.py:L591-L600`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L591-L600)), conversation branching, and AST-audited execution are concrete safeguards against the "AI theater" failure mode. However, the autonomous science platforms typically integrate with established scientific APIs (PubChem, UniProt, arXiv) at a deeper level than Reason's GROBID + local PDF pipeline.

### 3.2 Fitness for Working Scientists in Sociotechnical Physics Simulation

Reason is **architecturally well-positioned** but **functionally underdeveloped** for active simulation:

- ✅ **Literature grounding**: GROBID TEI extraction + RAG ensures parameters trace to published sources.
- ✅ **Epistemic discipline**: The sandbox + structured claims + evaluation criteria pipeline catches errors that pure LLM generation would miss.
- ❌ **No co-simulation interfaces**: Cannot drive SimPy, Mesa, OpenFOAM, or AnyLogic. The sandbox executes standalone scripts, not simulation harnesses.
- ❌ **No dimensional analysis**: The sandbox includes `numpy` and `pandas` but not `Pint` or `SymPy.physics.units`. Silent unit conflation is a real danger for the target user persona.
- ❌ **No uncertainty quantification**: No Sobol indices, Morris screening, or built-in Monte Carlo parameter sweep infrastructure.
- ❌ **No interactive causal diagrams**: `CausalFactorItem` objects are extracted but rendered as static markdown tables, not dynamic causal-loop or stock-and-flow visualizations.

### 3.3 Genuine Differentiators vs. Major Deficiencies

**Genuine differentiators** (things Reason does that competitors fundamentally do not):
1. Database-compiled agentic state graphs with variant exploration and checkpointed resumption.
2. GROBID-based academic provenance with automatic citation graph construction.
3. Lineage-aware retrieval deduplication between RAG chunks and Grips concept nodes.
4. AST-audited Docker sandboxed execution with OS-level resource limits.
5. Executable doctest trials as a scientific verification methodology.

**Major deficiencies** (things competitors do well that Reason lacks):
1. No standard co-simulation protocols (FMI/FMU, direct SimPy/Mesa bindings).
2. No physical unit verification in sandboxed computation.
3. No async execution for web-facing LLM calls (gateway timeout risk).
4. No distributed worker coordination (single-machine assumption).
5. No formal reasoning over structured_claims (e.g., Z3, Prolog, Answer Set Programming).

---

## 4. Pillar 2: Code Quality, Architectural Integration & Performance

### 4.1 Python 3.13 Idioms & Code Cleanliness

The codebase is **generally well-crafted** with consistent use of modern Python syntax:
- Union types: `float | None`, `int | str` used throughout ([`hardware.py:L91`](file:///home/crank/coding/antigrav/verbal/llm_api/hardware.py#L91), [`clustering.py:L43`](file:///home/crank/coding/antigrav/verbal/work_organisation/clustering.py#L43)).
- Built-in generics: `list[str]`, `dict[str, Any]`, `tuple[int, int]` consistently preferred over `typing.List`, `typing.Dict`.
- Frozen dataclasses for value objects: `GPUDeviceInfo`, `HostCPUInfo`, `BackendRecommendation` ([`hardware.py:L43-L124`](file:///home/crank/coding/antigrav/verbal/llm_api/hardware.py#L43-L124)) are textbook examples.
- Pydantic models for structured LLM output schemas: `ResearchEvaluation`, `IdeaClusteringPlan`, `CausalGraphExtractionPlan`.

**Exceptions to the quality standard**:
- `actions.py` (1,009 lines) and `compiler.py` (952 lines) are too large. Both mix multiple concerns: the compiler interleaves sub-blueprint execution, tool dispatch, summarization, and cancellation within a single enormous closure (`_make_action_node`).
- Some `except Exception: pass` patterns persist, particularly in [`compiler.py:L363-L364`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L363-L364) when fetching the state_tree—silently swallowing database errors that could indicate a stale conversation reference.
- The `meta_tools.py` file mixes genuine production tools (`document_reader`, `django_shell_script`) with stubs (`clone_and_modify_blueprint`), making it hard to distinguish implemented from aspirational functionality.

### 4.2 Django ORM, Pydantic & LangGraph Integration

The ORM-to-LangGraph compilation pipeline is the project's crown jewel:

1. **Blueprint → Graph**: `compile_graph_from_blueprint()` ([`compiler.py:L894-L951`](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L894-L951)) reads Django model records and emits a `StateGraph` with action nodes, evaluation nodes, and conditional routers. This is clean and well-structured.

2. **DjangoCheckpointer**: The LangGraph `BaseCheckpointSaver` implementation ([`checkpointer.py:L18-L188`](file:///home/crank/coding/antigrav/verbal/metacognition/checkpointer.py#L18-L188)) correctly handles base64 serialization of checkpoint state. However, `put_writes` is a no-op ([`checkpointer.py:L181-L187`](file:///home/crank/coding/antigrav/verbal/metacognition/checkpointer.py#L181-L187))—this means intermediate writes during parallel step execution are silently dropped, which could cause state loss in fan-out scenarios.

3. **Pydantic ↔ ORM boundary**: The separation is mostly clean. Pydantic schemas define LLM output contracts (`ResearchEvaluation`, `CandidateEvaluation`), while Django models handle persistence. The `_parse_bibl_struct` helper in [`grobid_client/tasks.py:L70-L119`](file:///home/crank/coding/antigrav/verbal/grobid_client/tasks.py#L70-L119) is a good example of keeping parsing logic functional.

### 4.3 Task Engine Migration & Concurrency / Async Safety

**Migration completeness**: The `verbal_tasks` backend is fully operational and well-tested. However:
- Four test files still set `CELERY_TASK_ALWAYS_EAGER=True`, creating confusion about which task infrastructure is canonical.
- The `delegate_task` meta-tool ([`meta_tools.py:L255-L271`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L255-L271)) calls `task_run_blueprint_async.enqueue()`, which is correct, but the function hardcodes `blueprint_id=1` with a comment "Needs name → ID resolution, simplified here."
- `requirements.in` still lists `celery` and `django-celery-beat` ([`requirements.in:L4-L5`](file:///home/crank/coding/antigrav/verbal/requirements.in#L4-L5))—should these be removed?

**Concurrency hazards**:
1. **SSE worker thread exhaustion**: `stream_broadcasts()` ([`broadcast_views.py:L15-L48`](file:///home/crank/coding/antigrav/verbal/verbal_config/broadcast_views.py#L15-L48)) holds a synchronous worker thread for 55 seconds with `time.sleep(2)` polling. Under even modest concurrent usage (10 connected browsers), this consumes 10 worker threads indefinitely.
2. **Synchronous LLM generation**: The demo_ui native generation path ([`views.py:L296`](file:///home/crank/coding/antigrav/verbal/demo_ui/views.py#L296)) calls `generate_response2()` synchronously. A 30-second inference on a consumer GPU will hold the HTTP worker for the entire duration, potentially triggering gateway timeouts with Granian or nginx.
3. **Blueprint execution in API**: `run_blueprint` called from [`llm_api/api.py`](file:///home/crank/coding/antigrav/verbal/llm_api/api.py) is synchronous—multi-step blueprints with 10+ node visits will block the caller for minutes.

### 4.4 Hardware Utilization & Latency Bottlenecks

The hardware profiler ([`hardware.py:L127-L304`](file:///home/crank/coding/antigrav/verbal/llm_api/hardware.py#L127-L304)) is well-engineered:
- Correct use of `torch.cuda.mem_get_info()` for dynamic VRAM budgeting.
- Appropriate clamping of `vllm_gpu_memory_utilization` between 0.30 and 0.90.
- Smart avoidance of `RLIMIT_AS` in the sandbox to prevent OpenBLAS false-positive crashes.

**Bottleneck analysis**:
- The `unified_retrieve` function ([`retrieval.py:L32-L189`](file:///home/crank/coding/antigrav/verbal/background_resources/retrieval.py#L32-L189)) performs **two** similarity searches per source (once for docs, once for scores), doubling PGVector query load. This is acknowledged in comments as a workaround but should be refactored.
- The `get_deep_context_report` function ([`retrieval.py:L212-L288`](file:///home/crank/coding/antigrav/verbal/background_resources/retrieval.py#L212-L288)) executes three separate PostgreSQL full-text search queries sequentially. These should be batched or parallelized.
- `SandboxConfiguration.save()` performs a filesystem write ([`models.py:L49-L52`](file:///home/crank/coding/antigrav/verbal/sandbox_manager/models.py#L49-L52)) as a model save side-effect, creating a coupling between ORM state and filesystem state that can silently drift.

---

## 5. Pillar 3: Test Coverage & Empirical Testing Rigor

### 5.1 Deep Test Suites vs. Untested/Stubbed Applications

| Application | Test File Lines | Assessment |
|---|---|---|
| `metacognition/tests.py` | 1,304 | Comprehensive: blueprint compilation, variant selection, tool approval, state checkpointing |
| `background_resources/tests.py` | 747 | Strong: RAG ingestion, retrieval, chunking |
| `verbal_tasks/tests.py` | 685 | Excellent: task lifecycle, zombie recovery, telemetry, cron scheduling |
| `work_organisation/tests.py` | 585 | Good: access control modes, whiteboard operations |
| `llm_api/tests.py` | 574 | Adequate: inference proxy, hardware profiling. Contains legacy `CELERY_TASK_ALWAYS_EAGER` |
| `grips/tests.py` | 452 | Adequate: concept node CRUD, embedding indexing |
| `demo_ui/tests.py` | 302 | Basic: view rendering, conversation creation |
| `benchmarking/tests.py` | 300 | Basic: scenario creation, runner invocation |
| `demo_ui/tests_ui.py` | 133 | Thin: Playwright browser tests, constrained by GPU latency |
| **`sandbox_manager/tests.py`** | **3** | **Empty: zero test coverage on a security-critical component** |
| **`grobid_client/tests.py`** | **3** | **Empty: zero test coverage on the academic ingestion pipeline** |

The test asymmetry is the project's most dangerous technical debt. The sandbox and GROBID client are **boundary systems** where bugs have the highest consequence:
- A path traversal bypass in `sandbox/main.py` would allow arbitrary file reads from the Docker host.
- A malformed TEI XML from GROBID could crash the citation graph builder or inject incorrect metadata into the knowledge base.

### 5.2 Real Invariant Verification vs. Superficial "Number Padding"

The deeply tested modules demonstrate genuine invariant verification:
- `metacognition/tests.py` tests blueprint compilation with variant selection, verifies that `max_steps` brakes halt runaway execution, and validates human-in-the-loop interruption/resumption.
- `verbal_tasks/tests.py` tests zombie task recovery (detecting tasks that became stuck), cron expression evaluation, and telemetry context propagation.

These are **not** superficial `assert response.status_code == 200` tests. They exercise real business logic with meaningful assertions. The testing philosophy is sound—it is the coverage distribution that needs correction.

### 5.3 Workstream 16 Empirical Rigor & Browser Doctest Analysis

The executable doctest trial methodology in [`demo_ui/demo_ui_trials/`](file:///home/crank/coding/antigrav/verbal/demo_ui/demo_ui_trials) is genuinely innovative:
- `trial_1_ingestion_and_context.rst` → Document upload, RAG indexing, context retrieval verification.
- `trial_3_multistep_blueprint_and_tools.rst` → Factorial experiment design, sandbox code execution, result tabulation.
- `trial_4_branching_and_grips_expansion.rst` → Conversation branching, knowledge graph expansion.

The `helpers.py` (18,248 bytes) orchestrates Playwright interactions with the real running application, including GPU inference. The `_report_doctest_run` function in `conftest.py` recursively renders sub-blueprint execution traces into structured Sphinx reports.

**Limitations**: Trials require a running inference server and 15+ minutes of GPU time. There is no incremental execution mode, no mock-based fallback for CI, and no way to run trials without the full hardware stack.

---

## 6. Pillar 4: Documentation & Doctest Trial Approaches

### 6.1 Documentation Completeness & Dangerous Staleness

The root [`readme.md`](file:///home/crank/coding/antigrav/verbal/readme.md) is **actively harmful**:
- Line 4: "set up a virtual environment and install packages from requirements.txt" — correct but misleading given the complex multi-role setup.
- Line 3: "ensure docker is available on your system to provide redis for celery" — **Redis and Celery are no longer the canonical infrastructure**. The real backend is `verbal_tasks` with PostgreSQL.
- Line 8: "rename db-dev.sqlite3 to db.sqlite3" — **SQLite is not supported**. The project requires PostgreSQL 18 with `pgvector`.
- Lines 25-26: "The system uses a FAISS vector store" — **FAISS has been fully replaced by PGVector HNSW indexes**.

Every single technology claim in the README is wrong. This is the highest-priority fix in the entire project.

### 6.2 Evaluation of the Executable Doctest Trial Methodology

The trial methodology is the project's **strongest documentation artifact**. It solves a real problem: how do you verify that an AI research assistant actually works, end-to-end, with real models, on real hardware?

The answer—Playwright browser automation driving real inference, capturing screenshots, and compiling Sphinx reports—is a genuine methodological contribution. The `_report_doctest_run` function in [`conftest.py:L108-L224`](file:///home/crank/coding/antigrav/verbal/conftest.py#L108-L224) is thorough: it renders execution traces, tool outputs, sub-blueprint monologues, and generated workspace files into structured RST.

**However**: The trial approach is not documented anywhere outside the trial files themselves. There is no "How to Write a Trial" guide, no explanation of the `helpers.py` API, and no CI integration. The trials exist as executable knowledge locked in the heads of the current developers.

### 6.3 Onboarding Usability for External Scientists

For an external researcher attempting to use Reason:
1. **README**: Misleading (see above).
2. **start_inference.sh, start_web.sh**: These exist but are not referenced in the README's quickstart.
3. **Django Admin**: Functional and accessible at `/admin/`, but requires understanding the `CognitiveBlueprint` → `ReasoningStep` → `ToolDefinition` data model.
4. **API Docs**: Django Ninja generates Swagger docs at `/api/docs/`—this is the most reliable entry point.
5. **Sphinx Docs**: In [`documentation/source/`](file:///home/crank/coding/antigrav/verbal/documentation/source)—unknown currency relative to the codebase.

**Verdict**: An external collaborator would struggle significantly. The onboarding path requires a complete rewrite of the README and a "Getting Started for Researchers" guide.

---

## 7. Pillar 5: Growth Paths, Quick Wins & Growth Blockers

### 7.1 High-Leverage Quick Wins (Immediate Value)

1. **Rewrite `readme.md`**: Replace every technology reference with the current stack (PostgreSQL 18 + pgvector, verbal_tasks, PGVector HNSW). Include the multi-role startup sequence (`start_web.sh`, `start_inference.sh`, `toggle_background_task_service.sh`). **Effort: 2 hours. Impact: Critical.**

2. **Add basic unit tests for `sandbox_manager` and `grobid_client`**: Test path traversal blocking in `sandbox/main.py`, PEP-508 validation in `SandboxConfiguration.clean()`, TEI XML parsing in `_extract_grobid_deterministic()`, and `_parse_bibl_struct()` with sample XML fixtures. **Effort: 1 day. Impact: High (covers the two most dangerous untested boundaries).**

3. **Fix `django_shell_script` AST security bypass**: The current import blocklist is string-based and trivially bypassable. Options: (a) run the script in the Docker sandbox instead of `exec()`, (b) use a proper AST visitor that blocks `__import__`, `importlib`, `getattr` on builtins, and `eval`/`exec` calls, (c) remove the tool entirely and rely on the sandbox. **Effort: 4 hours. Impact: Critical (security).**

4. **Remove `CELERY_TASK_ALWAYS_EAGER` from all test files**: This is dead configuration from the pre-migration era. Remove from `benchmarking/tests.py:L36`, `llm_api/tests.py:L34`, `background_resources/tests.py:L101`, `metacognition/tests.py:L364,L501`. **Effort: 30 minutes. Impact: Moderate (reduces confusion).**

5. **Delete or mark `clone_and_modify_blueprint` as explicitly unimplemented**: The stub at [`meta_tools.py:L99-L112`](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L99-L112) should either raise `NotImplementedError` or be removed. **Effort: 5 minutes. Impact: Low (code hygiene).**

### 7.2 Strategic Growth Paths (Simulation, Units, Causal Graphs)

1. **Physical Unit Verification Layer**: Integrate `Pint` into the sandbox container's default packages. Add a `VerifyUnits` tool that accepts parameter names + values and validates dimensional consistency. This directly addresses the target user persona's core risk (conflating W and Wh, m/s and km/h).

2. **Co-Simulation Protocol Adapter**: Build a `SimulationRunner` tool that can instantiate and drive SimPy discrete-event simulations or Mesa agent-based models from within the sandbox. Start with SimPy (pure Python, no external binaries) as a proof-of-concept.

3. **Interactive Causal Diagram Canvas**: Extend `work_organisation/clustering.py`'s `CausalFactorItem` extraction to render interactive causal-loop diagrams in the whiteboard using D3.js or Cytoscape.js, with real-time Datastar SSE updates as factors are added or modified.

4. **Automated Parameter Sensitivity Sweeps**: Add a `ParameterSweep` tool that generates Sobol or Morris sampling plans, dispatches execution runs to the sandbox via `verbal_tasks`, and collects results into a `BenchmarkResult`-like table for analysis.

5. **Async Web Generation**: Migrate the synchronous LLM generation path in `demo_ui/views.py` to an async pattern: enqueue generation as a `TaskRecord`, return immediately with a polling/SSE endpoint for the client, and stream results back via Datastar.

### 7.3 Critical Growth Blockers & Scalability Threats

1. **Single-GPU VRAM Constraint**: The entire inference pipeline assumes a single CUDA device. Multi-GPU support, model sharding, or cloud API fallback would require significant refactoring of `ai_service.py` and `hardware.py`.

2. **Synchronous Web Architecture**: Django's synchronous request/response model is fundamentally incompatible with long-running LLM inference. The current `time.sleep()` SSE implementation and synchronous `generate_response2()` calls will not scale beyond a handful of concurrent users without ASGI migration.

3. **Variant Tree Explosion**: The `ReasoningStep.create_variant()` mechanism allows unbounded proliferation of step variants. Without automatic pruning based on selection_weight decay or benchmark performance, the variant tree could become unmanageable.

4. **`put_writes` No-Op**: The `DjangoCheckpointer.put_writes()` no-op means that parallel step executions (fan-out via `parallel_steps`) cannot persist intermediate state, risking silent state loss on crash recovery.

5. **Monolithic File Size**: `compiler.py` (952 lines), `actions.py` (1,009 lines), `meta_tools.py` (930 lines), and `ai_service.py` (1,179 lines) are all approaching or exceeding 1,000 lines. These files mix multiple concerns and will become increasingly difficult to maintain, test, and refactor.

---

## 8. Prioritized Workstream Task Proposals (Candidate Tickets)

### Ticket Candidate 1: Rewrite `readme.md` to Reflect Current Architecture
- **Target App(s)**: Root repository
- **Rationale & Problem**: Every technology claim in the current README is factually wrong. External contributors will waste hours following incorrect instructions.
- **Proposed Technical Solution**: Complete rewrite documenting PostgreSQL 18 + pgvector, `verbal_tasks` DatabaseTaskBackend, multi-role startup (`web`/`inference`/`worker`), PGVector HNSW embeddings, and correct dependency installation via `update_dependencies.sh`.
- **Verification Plan**: Manual review; CI lint check that README references match `settings.py` database backend and `requirements.in` packages.

### Ticket Candidate 2: Harden `django_shell_script` AST Security
- **Target App(s)**: `metacognition/meta_tools.py`
- **Rationale & Problem**: The current `exec()`-based script execution with string-matching import blocklist is trivially bypassable via `__import__()`, `importlib`, `getattr(builtins, ...)`, `eval()`, or `compile()`.
- **Proposed Technical Solution**: Replace `exec()` in the host process with execution in the Docker sandbox container. Alternatively, implement a comprehensive AST visitor that blocks: all `Import`/`ImportFrom` of dangerous modules, all uses of `__import__`, `importlib`, `eval`, `exec`, `compile`, and attribute access on `__builtins__`.
- **Verification Plan**: Unit tests with known bypass vectors; fuzz testing with adversarial script inputs.

### Ticket Candidate 3: Add Test Suites for `sandbox_manager` and `grobid_client`
- **Target App(s)**: `sandbox_manager/tests.py`, `grobid_client/tests.py`
- **Rationale & Problem**: These are the project's two most dangerous untested boundary systems. Path traversal bugs in the sandbox or malformed XML handling in GROBID could have severe consequences.
- **Proposed Technical Solution**: For `sandbox_manager`: test path traversal blocking, `RLIMIT` enforcement, PEP-508 validation, timeout handling. For `grobid_client`: test deterministic TEI XML extraction with fixture data, `_parse_bibl_struct` with edge cases, field truncation in `Reference.save()`, citation graph construction.
- **Verification Plan**: `pytest sandbox_manager/ grobid_client/` passing with >80% line coverage of core parsing and security logic.

### Ticket Candidate 4: Async LLM Generation for Web Requests
- **Target App(s)**: `demo_ui/views.py`, `llm_api/api.py`
- **Rationale & Problem**: Synchronous `generate_response2()` calls block HTTP worker threads for 10-90 seconds, risking gateway timeouts and worker exhaustion under concurrent load.
- **Proposed Technical Solution**: Enqueue generation as a `TaskRecord` via `verbal_tasks`, return a task ID to the client immediately, and stream results back via Datastar SSE. The client polls or subscribes to the task's completion event.
- **Verification Plan**: Load test with 5 concurrent generation requests; verify no HTTP 502/504 errors; measure p95 time-to-first-byte.

### Ticket Candidate 5: Physical Unit Verification in Sandbox
- **Target App(s)**: `sandbox_manager`, `metacognition/actions.py`
- **Rationale & Problem**: The sandbox executes scientific Python code without dimensional analysis. Silent unit conflation (W vs. Wh, m/s vs. km/h) directly undermines the "Empirical Co-Pilot" mission.
- **Proposed Technical Solution**: Add `pint` to the sandbox's `requirements.txt`. Create a `VerifyUnits` tool definition that accepts parameter dictionaries with unit annotations and validates dimensional consistency using Pint's dimensional analysis. Inject a `UNIT_REGISTRY` into the sandbox's execution namespace.
- **Verification Plan**: Test with known dimensionally inconsistent calculations (e.g., adding Watts and Watt-hours) and verify the error is caught and reported.

### Ticket Candidate 6: Purge Legacy Celery Artifacts
- **Target App(s)**: All test files, `requirements.in`
- **Rationale & Problem**: `CELERY_TASK_ALWAYS_EAGER` references in 4 test files and `celery`/`django-celery-beat` in `requirements.in` create confusion about the canonical task infrastructure and bloat the dependency tree.
- **Proposed Technical Solution**: Remove all `CELERY_TASK_ALWAYS_EAGER` overrides from test files. Evaluate whether `celery` and `django-celery-beat` can be removed from `requirements.in` entirely, or if any import paths still reference them.
- **Verification Plan**: Full test suite passes after removal; `grep -r celery` returns zero hits in production code.
