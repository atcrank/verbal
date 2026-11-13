# Enduring Architectural Invariants & Harness Design Philosophy

## Timestamp
2026-11-13 11:00:00 AEST

## Status
Enduring Architectural Standard & Core Cognitive Contract

---

## 1. Executive Context & Harness Taxonomy

### What Reason Is (and Is Not)
Most contemporary agentic frameworks are designed around narrow archetypes:
* **Coding Harnesses** (e.g., Claude Code, Cursor, Aider, Devin): Built around local filesystem git trees, diff application, language server protocols, and terminal loop iteration.
* **Research & Reading Harnesses** (e.g., Perplexity, NotebookLM, basic RAG chains): Built around query transformation, chunk retrieval, vector reranking, and zero-shot synthesis.
* **Flat-File Agent Frameworks** (e.g., AutoGPT, CrewAI, Agent Protocol): Built around directories of markdown files (`SKILL.md`, `agents/*.yaml`) where self-improvement means prompting an LLM to overwrite text files on disk.

**Reason is a normalized, database-driven, reflective cognitive architecture.** 

It is designed to simulate, analyze, and reason through complex sociotechnical, empirical, and scientific problems using local small-to-medium language models (2B–4B parameter class, such as Gemma 4 E2B) operating under strict hardware and privilege constraints. 

To achieve high-quality cognitive outcomes on small models without cloud API dependencies, Reason rejects many conventional agent harness shortcuts and enforces a set of enduring architectural invariants.

---

## 2. Comparative Harness Matrix

| Architectural Dimension | Mainstream Agent Frameworks (LangChain, AutoGen, CrewAI) | Reason Cognitive Platform |
|---|---|---|
| **Cognitive Definition** | Hardcoded Python scripts or directories of flat `.md` / `.yaml` files | **Normalized PostgreSQL relational tables** (`CognitiveBlueprint`, `ReasoningStep`, `ToolDefinition`, `ResponseSchema`) |
| **Self-Improvement & Reflection** | Prompting an LLM to rewrite text files on disk | **Relational mutations & evolutionary speciation** (`PromptVariant`, `family_success_probability`, Bayesian tracking) |
| **State & Memory** | Ephemeral in-memory message lists stuffed into context window | **Persistent state trees** (`Conversation.state_tree`) with PostgreSQL checkpoints (`DjangoCheckpointer`) and DAG lineage |
| **Graph Topologies** | Rigid DAGs (disallowing cycles) or unbounded while-loops | **Cyclical state graphs** (`route_to: SELF`) with in-state loop counters and milestone grounding |
| **Step Execution & Verification** | Single ReAct node; model self-declares completion via sentinel tools (`TASK_COMPLETE`) | **Dual-Node Contract** (`{step}_call` + `{step}_eval`); action is decoupled from critique/verification |
| **Hardware & Process Boundary** | Single monolithic process or unconstrained cloud API quotas | **Multi-role process isolation** (`VERBAL_ROLE=web,worker,scheduler,inference`) with dedicated GPU serialization |
| **Frontend State Flow** | Heavy client-side SPAs (React/Vue) polling REST APIs or maintaining client websockets | **Hypermedia SSE streaming** (Datastar) morphing DOM patches via PostgreSQL pub/sub (`NOTIFY/LISTEN`) |
| **Knowledge Substrate** | Vector-only RAG (dense embeddings) | **Dual-Engine Retrieval**: Dense RAG (pgvector) + Symbolic Ontological Graph (**GRIPS** typed edges & claims) |
| **Model Resolution & Network** | Uncommanded polling to Hugging Face Hub / external APIs | **Offline-first, zero-polling** (`local_files_only=True`, Docker `pull_policy="never"`) |
| **Testing Philosophy** | Assert exact string outputs, leading to synthetic mock overfitting | **Software correctness tests vs. honest behavioral trials**; failures and debunkings are documented transparently |

---

## 3. The Core Invariants

### Invariant 1: The Normalized Database as Cognitive Substrate
**Reason treats cognitive architecture as data, not code.**
* **Relational Schema**: Blueprints, steps, tools, schemas, and variants are first-class Django models backed by PostgreSQL tables. 
* **Queryability for Meta-Reflection**: Because cognitive topologies are normalized database records, meta-cognitive agents (such as the NightManager) can execute SQL/ORM queries across reasoning steps (`ReasoningStep.objects.filter(...)`) to calculate failure rates, latency distributions, and variant performance without fragile regex parsing of markdown files.
* **ACID Guarantees & Referential Integrity**: Foreign keys ensure that tool definitions, schemas, and parent steps cannot be silently orphaned or broken by runtime modifications.
* **Canonical Immutability (`bypass_canonical_lock`)**: Core, battle-tested blueprints are flagged `is_canonical=True`. The database layer actively rejects runtime writes or edits to canonical records unless explicitly unlocked via `bypass_canonical_lock()`, ensuring seed stability while allowing experimental user variants to branch and speciate.

### Invariant 2: The Double-Node (`_call` + `_eval`) ReasoningStep Contract
**Every reasoning step is compiled into a decoupled action-critic pair.**
* **Decoupled Verification**: A single node (`{step}_call`) is responsible for generating thoughts, drafting responses, or invoking tools. A completely separate evaluation node (`{step}_eval`) is responsible for judging the output against explicit `evaluation_criteria`.
* **Semantic Verification vs. Escape Hatches**: In typical ReAct harnesses, agents are given a sentinel tool like `TASK_COMPLETE`. Small models consistently abuse such tools as "escape hatches"—invoking `TASK_COMPLETE` immediately to avoid difficult decomposition, calculation, or search. In Reason, `TASK_COMPLETE` is completely purged. The actor proposes; the impartial evaluator determines whether criteria are satisfied.
* **Structured Critique & Routing**: The `_eval` node produces a structured `eval_critique` and selects the routing edge (`SUCCESS`, `SELF`, or explicit failure step).

### Invariant 3: Cyclical State Graphs & Multi-Try Convergence
**Complex problem solving requires iterative cycles, not linear DAGs.**
* **Cycles by Design**: Small local models (2B–4B) cannot solve non-trivial scientific or analytical problems zero-shot. Reason's LangGraph compiler natively supports cyclical edges (`route_to: SELF`) and conditional rewinds to earlier graph states.
* **Bounded In-State Working Memory**: To prevent infinite loops and quadratic context explosion, loops are bounded by `max_retries` and tracked via scratchpad state variables (`loop_count`, `active_task`, `settled_milestones`).
* **Progressive Decomposition**: Multi-step blueprints use structured decomposition schemas (`TaskQueue`) to break objectives into active and pending tasks, systematically resolving them across successive turns.

### Invariant 4: Multi-Role Process Boundaries & GPU VRAM Protection
**Hardware constraints dictate operational process separation.**
* **Role Separation (`VERBAL_ROLE`)**:
  - `web` (Granian ASGI): Handles HTTP and WebSocket/SSE traffic; strictly **zero VRAM** allocation.
  - `worker` (Django Tasks): Consumes asynchronous background jobs, document parsing, and batch embeddings; strictly **zero VRAM** allocation.
  - `scheduler` (NightManager Beat): Executes periodic maintenance routines and housekeeping sweeps; strictly **zero VRAM** allocation.
  - `inference` (Port 8001 Daemon): Dedicated single-process GPU daemon holding model weights in VRAM without multi-worker concurrency race conditions.
* **Dynamic Backend Orchestration**: The inference daemon dynamically manages container backends (Ollama with `pull_policy="never"`, vLLM) or in-process PyTorch subject to [GlobalSystemConfig](file:///home/crank/coding/antigrav/verbal/llm_api/models.py#L584). Docker infrastructure containers (`db`, `sandbox`) remain decoupled from dynamic model loading.

### Invariant 5: Hypermedia-Driven Real-Time State Streaming
**The server owns state; the client renders declarative patches.**
* **Datastar SSE Architecture**: Instead of heavy client-side JavaScript frameworks reconciling state, Reason uses Datastar over Server-Sent Events. The backend renders server-side HTML fragments and morphs the browser DOM in real time.
* **PostgreSQL Pub/Sub**: Real-time events are backed by synchronous PostgreSQL `NOTIFY/LISTEN` channels (`subscribe_pg_events_sync`), ensuring that background worker progress and streaming tokens reach the user interface reliably without WebSocket connection drops.
* **Authentic DAG Lineage**: Every turn and intermediate trace is logged to `PromptResponseLog` with authentic foreign keys (`blueprint_id`, `reasoning_step_id`, `parent_log_id`), discriminating conversational turns (`log_kind='TURN'`) from internal tool node executions (`log_kind='TRACE'`).

### Invariant 6: Dual-Engine Knowledge (Dense RAG + Symbolic GRIPS Graph)
**Semantic similarity must be paired with structured causal logic.**
* **Dense Vector Search (pgvector)**: Fast semantic text chunk retrieval for unstructured literature and documentation.
* **GRIPS (Graph Representation of Intelligent Problem Solving)**: A typed knowledge graph containing Domains, ConceptNodes, and typed KnowledgeEdges (`is_alternative_to`, `constrains/modifies`, `is_excluded_by`).
* **Structured Claims**: GRIPS concepts contain formal structured claims enabling models to verify logical consistency, causal constraints, and counterfactual implications rather than relying purely on vector proximity.

### Invariant 7: Offline-First, Zero-Polling Resilience
**Reason must run stably in air-gapped, firewalled, or offline environments.**
* **Never Pull on Restart**: System restarts (`sudo systemctl restart reason`) must never attempt remote container pulls (`--pull never`) or rebuilds (`--no-build`).
* **Cache-First Model & Tokenizer Resolution**: All Hugging Face and tokenizer lookups strictly enforce `local_files_only=True` to prevent uncommanded network calls or background update stalls.
* **Multimodal Introspection Safeguards**: The system introspects model architectures with zero VRAM overhead. If an active model lacks a vision tower, image payloads are intercepted and grounded discussion context (Grobid figure captions and discussion) is substituted to protect text tokenizers from base64 pollution ("model mayhem").

### Invariant 8: Empirical Behavioral Trials vs. Overfitted Unit Tests
**Honest transparency is preferred over synthetic green checkmarks.**
* **Software Correctness Tests (`tests/`)**: Must pass 100% unconditionally, verifying that code runs, schemas serialize, and migrations apply cleanly.
* **Behavioral Trials (`metacognition_trials/*.rst`)**: Evaluate authentic model capabilities, reasoning quality, and failure modes on small models.
* **Zero Tolerance for Overfitting**: Developers and agents must never write synthetic bypasses (`if "models.py" in prompt: return True`) or fake mock answers to pass a trial. An honest trial report that clearly debunks a plausible small-model error in a separate review conversation provides immense educational and architectural value.

---

## 4. Architectural Rules for Agents & Developers
1. **Never reintroduce `TASK_COMPLETE`**: Terminate blueprints via LangGraph terminal edges and `_eval` node decisions.
2. **Never store core cognitive strategies in loose flat files**: Persist blueprints and reasoning steps via normalized Django models and maintain them via `seed.py`.
3. **Never break `VERBAL_ROLE` boundaries**: Never import heavy PyTorch model weights into `web` or `worker` processes.
4. **Always preserve authentic DAG parentage**: When generating execution logs, ensure `parent_log_id`, `blueprint`, and `reasoning_step` are populated.
5. **Always check `INDEX.md`**: Consult historical notes and constraints before modifying core compiler, inference, or graph components.

