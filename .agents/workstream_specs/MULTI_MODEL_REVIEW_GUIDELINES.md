**Document Status**: Official Review Protocol  
**Target Output**: Independent Workstream Spec Candidate per Reviewing Agent (`.agents/workstream_specs/candidate_review_<agent_model_name>.md`)  
**Repository Root**: `file:///home/crank/coding/antigrav/verbal`  

---

## 1. Project Overview & Context

### 1.1 Project Identity, Vision & Mission
**Reason** (codebase package `verbal`) is an open-source, sovereign, database-driven computational study design assistant and epistemological co-pilot.

Rather than acting as an ungrounded chatbot or an autonomous "AI theater" generator, Reason is designed to support **working scientists, researchers, and systems engineers** who model and design experiments for **complex sociotechnical systems**.

Sociotechnical systems (e.g., autonomous search-and-rescue swarms, wildfire evacuation logistics, cyber-physical smart grids, hazardous materials handling, pandemic supply networks) sit at the difficult intersection of:
1. **Hard Physical Simulation**: Thermodynamics, kinematics, battery energy depletion, sensor backscatter, structural fluid mechanics, and radio-frequency (RF) attenuation.
2. **Social, Human & Organizational Dynamics**: Human operator cognitive overload, multi-agency communication friction, procedural safety protocols, regulatory compliance, and distributed team coordination.

Reason operates on a core scientific conviction:  
> **"Empirical Co-Pilot, Not Hype Machine."**  
> AI assistants must never fabricate parameters, hallucinate citations, gloss over calculation errors, or substitute generic marketing prose for rigorous physics. When models make mistakes, those errors must be surfaced, caught, critically evaluated, and remediated in verifiable feedback loops.

---

### 1.2 System Architecture & Operational Roles

Reason uses a decoupled, multi-role architecture orchestrated via Django 5 and Python 3.13, backed by PostgreSQL 18 with `pgvector`:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     REASON ARCHITECTURE                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
                                        │
           ┌────────────────────────────┼────────────────────────────┐
           ▼                            ▼                            ▼
   [VERBAL_ROLE=web]           [VERBAL_ROLE=worker]        [VERBAL_ROLE=inference]
   (Port 8000)                 (Background Workers)        (Port 8001 / GPU VRAM)
   • Django UI & Templates     • DatabaseTaskBackend       • HuggingFace / PyTorch
   • HTMX / Datastar SSE       • runtaskworker             • vLLM (Port 8003)
   • Django Ninja REST API     • runtaskscheduler          • Ollama (Port 11434)
   • Light HTTP proxying       • NightManager jobs         • Hardware Profiling
           │                            │                            │
           └────────────────────────────┼────────────────────────────┘
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
                [PostgreSQL + pgvector]       [verbal_sandbox Docker]
                (Port 5433)                   (Port 8002 / FastAPI)
                • Tables & JSON claims        • Isolated execution
                • 384-dim HNSW embeddings     • 1 CPU / 1 GB RAM / No Net
                • Tasks & checkpoints         • numpy/pandas/pgmpy/pyAgrum
```

#### The Three Operational Roles:
1. **`web` (`VERBAL_ROLE=web`)**: Lightweight web application and Django Admin. Does not load heavy LLM weights into memory. Routes generation and tool reasoning requests over local HTTP to the inference role.
2. **`inference` (`VERBAL_ROLE=inference`)**: Dedicated single-process GPU daemon serving local Large Language Models (e.g., Gemma, Qwen, Mistral NeMo) via PyTorch, or coordinating with local containerized inference engines (`vLLM` on port 8003, `Ollama` on port 11434).
3. **`worker` (`VERBAL_ROLE=worker`)**: Asynchronous worker process executing queued background jobs (PDF extraction, knowledge graph linting, benchmark sweeps, NightManager archival) via the native PostgreSQL `DatabaseTaskBackend`.

---

### 1.3 The Data & Reasoning Pipeline

```
  [Academic Literature: PDFs] ──► [Grobid Client: TEI XML]
                                          │
                                          ▼
 [RAG Ingestion: background_resources] ──► [PGVector Embedding: RAGChunk]
                                          │
                                          ▼
         [Super Retriever: Distance Gating + Lineage Deduplication]
                                          │
                                          ▼
     [Grips: Ontological Knowledge Graph & Computable JSON Claims]
                                          │
                                          ▼
 [Metacognition: Database-Driven LangGraph CognitiveBlueprints]
                                          │
                                          ▼
    [verbal_sandbox: AST-Audited Execution (numpy, pgmpy, pandas)]
                                          │
                                          ▼
    [Demo UI / Work Organisation: Branching Timelines & SSE Streaming]
```

1. **Academic Literature Ingestion (`grobid_client` & `background_resources`)**: Real PDFs are parsed into structured TEI XML, extracting authors, abstracts, equations, and references.
2. **Dense Vector Store (`pgvector`)**: Chunks are embedded with all-MiniLM-L6-v2 (384 dimensions) and indexed using native PostgreSQL HNSW indexes.
3. **Super Retriever**: Employs distance thresholding, inverted domain-term specificity (avoiding glossary hijacking), and parent lineage deduplication (suppressing raw text chunks when higher-level concepts match).
4. **Knowledge Graph (`grips`)**: Synthesizes verified knowledge into `ConceptNode` wiki entries backed by `structured_claims` (atomic JSON subject-predicate-object tuples) for symbolic linting and logical inference.
5. **Agentic Reasoning Engine (`metacognition`)**: Compiles database-defined `CognitiveBlueprint` models into LangGraph state machines with human-in-the-loop approval, state checkpointing, and dynamic tool dispatch.
6. **Isolated Sandbox (`sandbox_manager` / `verbal_sandbox`)**: Runs generated Python code inside a restricted Docker container (1 CPU, 1 GB RAM, single-threaded OpenBLAS, 30s CPU limit, path traversal defense) pre-loaded with scientific packages (`numpy`, `pandas`, `networkx`, `pgmpy`, `pyAgrum`).
7. **Collaborative Interface (`demo_ui` & `work_organisation`)**: Delivers conversational branching, real-time Datastar Server-Sent Events (SSE), spatial whiteboard cards, and multi-user group-scoped access control.

---

## 2. Instructions for Reviewing Agents

As a participating model in this multi-model review, you are expected to perform a **deep, honest, unsparing, and constructive scientific and technical evaluation** of Reason. 

### 2.1 Mandate & Mindset
- **No Cheerleading, No Sycophancy**: Do not praise features that are incomplete, stubbed, or superficial. Highlight genuine strengths with the same vigor that you expose vulnerabilities, technical debt, and architectural dead-ends.
- **Inspect Every Single App**: Do not base your review solely on high-level documents. You must inspect the code, models, views, tasks, and test files in **every app** in the repository (see [Section 3: App-by-App Checklist](#3-app-by-app-audit-checklist)).
- **Cite Exact Code Locations**: Every observation, criticism, or compliment must reference specific files and line numbers (e.g., `[metacognition/compiler.py](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L120-L145)`).
- **Ask Clarifying Questions**: If you discover an ambiguous design pattern, an apparent dead code path, or an undocumented trade-off, use the `ask_question` tool or ask the user directly before making ungrounded assumptions.
- **Produce an Independent Workstream Spec Candidate**: Deliver your comprehensive report as a formal markdown document in `.agents/workstream_specs/candidate_review_<agent_model_name>.md`, structured according to [Section 5: Candidate Spec Template](#5-workstream-spec-candidate-template).

### 2.2 Repository Rules & Safety Boundaries
Reviewing agents must strictly adhere to the operational rules defined in [.agents/AGENTS.md](file:///home/crank/coding/antigrav/verbal/.agents/AGENTS.md):
1. **Python Environment**: Always use the Python 3.13 venv at `../../py313/bin/python` to run tests, inspect packages, or execute shell commands.
2. **Syntactic Conventions**: Always expect and use Python 3.13+ syntax and idioms. Prefer functional composition over deep class inheritance in novel AI code.
3. **Protected Boundaries**: **NEVER modify or create files inside `documents/`, `resources/`, `workspaces/`, or `sandbox/`** unless explicitly instructed.
4. **No Database Migrations**: **NEVER run `manage.py migrate` or `makemigrations`**. The Postgres database is shared; test fixtures use atomic transactions or mock databases.
5. **Note Consultation Rule**: Review [.agents/workstream_specs/notes/INDEX.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/notes/INDEX.md) for enduring architectural notes (especially note 17 regarding RAG design hazards and local SLMs).
6. **Package Verification**: Check `requirements.in` and `requirements.txt` to verify whether libraries exist before claiming a dependency is present or missing.

---

## 3. App-by-App Audit Checklist

Reviewing agents must systematically inspect each of the following 12 application components:

| # | Application / Directory | Primary Role & Core Modules | Key Files to Inspect |
|---|---|---|---|
| 1 | [verbal_config/](file:///home/crank/coding/antigrav/verbal/verbal_config) | Core Django settings, role orchestration (`VERBAL_ROLE`), OpenAPI endpoints, logging | `settings.py`, `urls.py`, `asgi.py` |
| 2 | [verbal_tasks/](file:///home/crank/coding/antigrav/verbal/verbal_tasks) | Native PostgreSQL-backed task engine (`django.tasks`), scheduler, telemetry, zombie recovery | `models.py`, `task_backend.py`, `postgres_events.py`, `telemetry.py`, `tests.py` |
| 3 | [llm_api/](file:///home/crank/coding/antigrav/verbal/llm_api) | Inference proxy, PyTorch/vLLM/Ollama dispatch, hardware profiling, conversation logging | `ai_service.py`, `api.py`, `hardware.py`, `models.py`, `tests.py` |
| 4 | [sandbox_manager/](file:///home/crank/coding/antigrav/verbal/sandbox_manager) | Docker container management, execution endpoints, path traversal guards, resource limits | `models.py`, `utils.py`, `tests.py`, `sandbox/main.py`, `sandbox/Dockerfile` |
| 5 | [grobid_client/](file:///home/crank/coding/antigrav/verbal/grobid_client) | Academic PDF parsing via GROBID, TEI XML extraction, bibliographic & citation trees | `models.py`, `tasks.py`, `api.py`, `tests.py` |
| 6 | [background_resources/](file:///home/crank/coding/antigrav/verbal/background_resources) | RAG pipeline, PGVector embeddings, semantic chunking, Super Retriever re-ranking | `models.py`, `rag_service.py`, `retrieval.py`, `nlp_service.py`, `tests.py` |
| 7 | [grips/](file:///home/crank/coding/antigrav/verbal/grips) | Graph Representation of Intelligent Problem Solving: Markdown wiki, computable JSON claims, linting | `models.py`, `wiki_service.py`, `tasks.py`, `services.py`, `tests.py` |
| 8 | [metacognition/](file:///home/crank/coding/antigrav/verbal/metacognition) | Database-compiled LangGraph agent state graphs, actions, meta-tools, NightManager orchestration | `compiler.py`, `actions.py`, `meta_tools.py`, `models.py`, `seed.py`, `tests.py` |
| 9 | [work_organisation/](file:///home/crank/coding/antigrav/verbal/work_organisation) | Multi-user workspaces, Group-scoped access, 4 privacy modes, spatial whiteboard, Datastar SSE | `models.py`, `api.py`, `clustering.py`, `events.py`, `views.py`, `tests.py` |
| 10 | [benchmarking/](file:///home/crank/coding/antigrav/verbal/benchmarking) | Empirical evaluation harness, synthetic question generation, scoring, Unsloth LoRA fine-tuning | `models.py`, `runner.py`, `generators.py`, `exporters.py`, `tests.py` |
| 11 | [demo_ui/](file:///home/crank/coding/antigrav/verbal/demo_ui) | "Reason" research workbench, conversation branching, blueprint streaming, tool approval modals | `views.py`, `urls.py`, `tests.py`, `tests_ui.py`, `templates/demo_ui/` |
| 12 | [documentation/](file:///home/crank/coding/antigrav/verbal/documentation) & Trials | Sphinx docs, executable doctest trials (`demo_ui_trials/`, `metacognition_trials/`) | `source/index.rst`, `demo_ui_trials/trial_*.rst`, `demo_ui_trials/helpers.py` |

---

## 4. The Five Evaluation Pillars

Your review report must comprehensively evaluate Reason across the following five critical dimensions:

---

### Pillar 1: Competitive Landscape & Value Proposition for Sociotechnical Physics Simulation

#### Target User Persona
Working scientists, computational modelers, and systems engineers designing experiments or operational protocols for **complex sociotechnical systems** (e.g. multi-robot disaster response, wildfire evacuation, cyber-physical grid resilience, epidemiological logistics, socio-ecological resource management). These researchers rely on physics-based simulations (e.g. CFD/OpenFOAM, thermal/battery kinematics, discrete-event SimPy/AnyLogic, agent-based Mesa/NetLogo, differential equations SciML/Julia, finite-element/CAD) coupled with social and human factors.

#### Questions to Address:
1. **Competitive Landscape Comparison**:
   - **vs. Generic LLM Frameworks (LangChain, LlamaIndex, CrewAI, AutoGen, DSPy, OpenManus)**: What does Reason’s database-backed state graph (`CognitiveBlueprint`) and local GPU architecture offer that generic agent libraries fail to provide? Where are generic libraries superior (e.g., ecosystem size, pre-built integrations, community maintenance)?
   - **vs. Autonomous Scientific AI Platforms (The AI Scientist / Sakana, ChemCrow, BioNeMo, SciCode, ChatEval)**: How does Reason’s philosophy of "empirical co-pilot with human-in-the-loop and counterfactual branching" contrast with autonomous "end-to-end paper generation" platforms? Does Reason provide higher epistemic reliability?
   - **vs. Modeling, Simulation & Systems Engineering Environments (AnyLogic, NetLogo, Mesa, SimPy, Modelica, OpenFOAM, JupyterLab/SciPy)**: Where does Reason fit in the scientific toolchain? Is Reason currently an active simulation participant, or merely an adjacent documentation tool?
2. **Genuine Advantages**: Where does Reason decisively win? (e.g., Grobid TEI academic paper extraction, grounded parameters vs. parametric memory hallucination, Grips structured claims linting, AST-audited sandboxed code execution, local model sovereignty).
3. **Where Reason is Beaten or Lagging Behind**:
   - Lack of standard simulation co-simulation interfaces (e.g., FMI/FMU standards, direct SimPy or OpenFOAM runners).
   - Lack of automated Parameter Sensitivity Analysis and Uncertainty Quantification (UQ) tooling (e.g., Sobol indices, Monte Carlo sampling).
   - Absence of formal dimensional analysis and physical unit verification (e.g., Pint, SymPy units) during sandbox execution and parameter extraction.
   - Lack of interactive dynamic causal-loop or stock-and-flow diagrams in the user interface.

---

### Pillar 2: Code Quality, Architectural Integration, and Performance

#### Questions to Address:
1. **Python 3.13 Idioms & Software Craftsmanship**:
   - Are modern Python 3.13 features and patterns utilized cleanly?
   - Does novel agent code adhere to functional composition over brittle, deep class inheritance?
   - Are exceptions handled explicitly with informative error contexts, or are there silent `except: pass` antipatterns?
2. **Architectural Cohesion & Boundaries**:
   - How clean is the separation between Django ORM models and Pydantic validation schemas?
   - How effectively does the LangGraph compiler serialize and deserialize complex agent state across PostgreSQL database checkpoints?
   - How complete is the migration from legacy Celery/Redis to native `verbal_tasks` (`DatabaseTaskBackend`)? Are there lingering Celery artifacts, unmigrated tasks, or dead-letter risks?
3. **Concurrency, Async & Thread Safety**:
   - In `demo_ui/views.py`, does blueprint execution or LLM generation block the synchronous web request thread? Could high-iteration blueprints trigger HTTP gateway timeouts?
   - How robust is the Datastar Server-Sent Events (SSE) streaming implementation under multi-user concurrent loads?
4. **Hardware & Resource Performance**:
   - How efficient is GPU VRAM management between the local LLM, local embeddings (SentenceTransformers), and Docker containers?
   - What is the cold-start overhead of executing code in `verbal_sandbox` via FastAPI?
   - Are PGVector HNSW indexes and database queries optimized to avoid N+1 query explosions?

---

### Pillar 3: Test Coverage & Empirical Testing Rigor ("Testing What Matters" vs. "Making Up the Numbers")

#### Questions to Address:
1. **Audit of Test Suites Across All Apps**:
   - Compare deeply tested applications ([metacognition/tests.py](file:///home/crank/coding/antigrav/verbal/metacognition/tests.py) ~58 KB, [background_resources/tests.py](file:///home/crank/coding/antigrav/verbal/background_resources/tests.py) ~33 KB, [verbal_tasks/tests.py](file:///home/crank/coding/antigrav/verbal/verbal_tasks/tests.py) ~28 KB) against untested or stubbed applications:
     * Why is [sandbox_manager/tests.py](file:///home/crank/coding/antigrav/verbal/sandbox_manager/tests.py) essentially empty (60 bytes)?
     * Why is [grobid_client/tests.py](file:///home/crank/coding/antigrav/verbal/grobid_client/tests.py) essentially empty (60 bytes)?
     * What is the test status of [demo_ui/tests_ui.py](file:///home/crank/coding/antigrav/verbal/demo_ui/tests_ui.py)?
2. **Quality & Depth of Assertions**:
   - Are tests verifying mission-critical failure modes: transactional database rollbacks across vector embeddings, concurrent task worker locks, AST security violation blocking, LangGraph breakpoint interruption/resumption, and dead-letter recovery?
   - Or are tests padded with superficial `assert response.status_code == 200` assertions on mock objects that fail to exercise real behavior?
3. **Scientific & Empirical Rigor (Workstream 16 Evaluation)**:
   - Does the test philosophy catch real-world logic errors (such as the optimization inversion bug `.idxmin()` caught in Workstream 16)?
   - How effective are the Playwright-driven browser doctest trials in verifying end-to-end user workflows against real local GPU inference?

---

### Pillar 4: Documentation & Doctest Trial Approaches

#### Questions to Address:
1. **Sphinx Documentation Completeness & Currency**:
   - How accurate and complete is the documentation in [documentation/source/](file:///home/crank/coding/antigrav/verbal/documentation/source)?
   - **Documentation Staleness Hazard**: Are there critical discrepancies between documentation and reality? (e.g., [readme.md](file:///home/crank/coding/antigrav/verbal/readme.md) still referencing SQLite, FAISS, and Celery, while the real stack uses PostgreSQL, `pgvector`, and `verbal_tasks`).
2. **The Executable Doctest Trial Paradigm**:
   - Evaluate the hybrid doctest trial pattern in [demo_ui/demo_ui_trials/](file:///home/crank/coding/antigrav/verbal/demo_ui/demo_ui_trials): Playwright automated browser interaction running real local models, capturing screenshots, executing sandbox code, and compiling into Sphinx reports (`trial_1` to `trial_4`).
   - Is this approach clear, reproducible, and instructive for working scientists? Does it bridge the gap between software testing and empirical research documentation?
3. **Developer & Researcher Guidance**:
   - Are setup scripts ([start_inference.sh](file:///home/crank/coding/antigrav/verbal/start_inference.sh), [start_web.sh](file:///home/crank/coding/antigrav/verbal/start_web.sh), [update_dependencies.sh](file:///home/crank/coding/antigrav/verbal/update_dependencies.sh)) and API docs (`/api/docs/`) clear, robust, and self-explanatory for an external collaborator?

---

### Pillar 5: Growth Paths, Quick Wins, and Growth Blockers

#### Questions to Address:
1. **High-Leverage Quick Wins (Low-Hanging Fruit)**:
   - What are 3–5 immediate, high-impact fixes that can be accomplished with minimal effort? (e.g., rewriting the stale `readme.md`, adding basic unit test suites for `sandbox_manager` and `grobid_client`, purging obsolete Celery scripts).
2. **Strategic Growth Paths for Sociotechnical Simulation**:
   - What architectural enhancements would transform Reason into the premier study design co-pilot for physics-based sociotechnical modeling? (e.g., standard simulation interfaces for SimPy/Mesa/OpenFOAM, Pint dimensional analysis verification, automated Parameter Sensitivity sweeps, dynamic causal-loop visualization in the whiteboard).
3. **Critical Growth Blockers**:
   - What architectural bottlenecks, hardware constraints, or technical debt threaten long-term scalability and user adoption? (e.g., single-GPU VRAM limits, synchronous blueprint execution risks, variant tree explosion in `metacognition`, lack of distributed worker coordination).

---

## 5. Workstream Spec Candidate Template

Each reviewing agent must write their final review as a new markdown document at:  
`.agents/workstream_specs/candidate_review_<agent_model_name>.md`

Reviewing agents must use the following standard format:

```markdown
# Workstream Spec Candidate: Comprehensive Project Review
**Reviewing Agent / Model**: [Model Name, e.g. Gemini 1.5 Pro / Claude 3.5 Sonnet / GPT-4o]  
**Date**: [Current Date]  
**Evaluation Target**: Reason (Verbal) Research Co-Pilot  

---

## 1. Executive Summary & Reviewer Perspective
[High-level evaluation of Reason: core strengths, critical vulnerabilities, overall maturity, and architectural assessment.]

---

## 2. App-by-App Audit Matrix

| Application | Code Quality (1-5) | Test Rigor (1-5) | Documentation (1-5) | Strengths & Key Files | Critical Weaknesses & Tech Debt |
|---|---|---|---|---|---|
| verbal_config | | | | | |
| verbal_tasks | | | | | |
| llm_api | | | | | |
| sandbox_manager | | | | | |
| grobid_client | | | | | |
| background_resources | | | | | |
| grips | | | | | |
| metacognition | | | | | |
| work_organisation | | | | | |
| benchmarking | | | | | |
| demo_ui | | | | | |
| documentation & trials | | | | | |

---

## 3. Pillar 1: Competitive Landscape & Simulation Value Proposition
### 3.1 Comparison vs. Generic LLM Frameworks & Autonomous Science Platforms
### 3.2 Fitness for Working Scientists in Sociotechnical Physics Simulation
### 3.3 Genuine Differentiators vs. Major Deficiencies

---

## 4. Pillar 2: Code Quality, Architectural Integration & Performance
### 4.1 Python 3.13 Idioms & Code Cleanliness
### 4.2 Django ORM, Pydantic & LangGraph Integration
### 4.3 Task Engine Migration & Concurrency / Async Safety
### 4.4 Hardware Utilization & Latency Bottlenecks

---

## 5. Pillar 3: Test Coverage & Empirical Testing Rigor
### 5.1 Deep Test Suites vs. Untested/Stubbed Applications
### 5.2 Real Invariant Verification vs. Superficial "Number Padding"
### 5.3 Workstream 16 Empirical Rigor & Browser Doctest Analysis

---

## 6. Pillar 4: Documentation & Doctest Trial Approaches
### 6.1 Documentation Completeness & Dangerous Staleness
### 6.2 Evaluation of the Executable Doctest Trial Methodology
### 6.3 Onboarding Usability for External Scientists

---

## 7. Pillar 5: Growth Paths, Quick Wins & Growth Blockers
### 7.1 High-Leverage Quick Wins (Immediate Value)
### 7.2 Strategic Growth Paths (Simulation, Units, Causal Graphs)
### 7.3 Critical Growth Blockers & Scalability Threats

---

## 8. Prioritized Workstream Task Proposals (Candidate Tickets)
### Ticket Candidate 1: [Title]
- **Target App(s)**: 
- **Rationale & Problem**: 
- **Proposed Technical Solution**: 
- **Verification Plan**: 

### Ticket Candidate 2: [Title]
...
```

---

## 6. Review Execution Workflow

When convening the multi-model review:
1. **Deploy Prompt**: Supply these guidelines to each reviewing agent/model along with workspace access.
2. **Agent Exploration**: The agent must systematically read and inspect files across all 12 apps using file viewing and search tools.
3. **Interactive Clarification**: The agent asks any clarifying questions regarding undocumented architecture or trade-offs.
4. **Draft Candidate Spec**: The agent writes its completed report to `.agents/workstream_specs/candidate_review_<agent_model_name>.md`.
5. **Synthesis & Alignment**: The maintainers compare the candidate specs from the different models to establish the consensus roadmap for subsequent workstreams.
