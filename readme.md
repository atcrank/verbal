# Reason (`verbal`)

**Reason** is a deliberative study design assistant and multi-agent cognitive architecture built on Django. It combines database-backed state graphs (`CognitiveBlueprint` with dual-node action-evaluation contracts), verifiable academic literature grounding (GROBID + PGVector), structured ontology graphs with computable claims (Grips), and human-in-the-loop counterfactual branching with real-time Datastar SSE streaming.

The primary reference model is **Gemma 4 E2B** (`google/gemma-4-E2B-it`), supported by dynamic multi-backend orchestration (Ollama with `pull_policy="never"`, vLLM, and in-process PyTorch), zero-VRAM multimodal architecture introspection, and a dedicated CPU mini-model service for rapid token counting and lightweight extraction.

---

## 1. System Architecture

The system coordinates four operational microservice roles and an auxiliary CPU mini-model over a native PostgreSQL 18 task queue:

```
                            ┌────────────────────────────────────────┐
                            │              Web Instance              │
                            │    Granian / Django ASGI (:8000/8008)  │
                            │       Demo UI, Admin, REST APIs        │
                            └───────┬────────────────────────┬───────┘
                                    │                        │
                         Database Task Enqueue         REST Model Proxy
                                    │                        │
                                    ▼                        ▼
┌──────────────────────────┐        │             ┌───────────────────────────────────┐
│     CPU Mini-Model       │        │             │        Inference Instance         │
│  llama-server (:8080)    │        │             │   Ollama / vLLM / PyTorch (:8001) │
│ Quantized GGUF Tokenizer │        │             │  Monopolizes GPU, Gemma 4 E2B,    │
│ & Rapid CPU Extractions  │        │             │  VLM visual reasoning & Outlines  │
└─────────────┬────────────┘        │             └───────────────────────────────────┘
              │                     │                                │
              │                     │                                │
              ▼                     ▼                                ▼
┌───────────────────────────────────────┐        ┌──────────────────────────────────┐
│             Task Worker               │        │         Task Scheduler           │
│     verbal_tasks: runtaskworker       │        │   verbal_tasks: runtaskscheduler │
│  Async blueprints, document ingestion,│        │   Periodic maintenance sweeps,   │
│  batch embeddings, tool execution     │        │   NightManager self-reflection   │
└───────────────────┬───────────────────┘        └──────────────────┬───────────────┘
                    │                                               │
                    └─────────────────────┬─────────────────────────┘
                                          ▼
                      ┌───────────────────────────────────────┐
                      │     PostgreSQL 18 + PGVector HNSW     │
                      │  State Graphs, Chunks, Task Queues    │
                      └───────────────────────────────────────┘
```

### Core Applications
* **`verbal_config/`**: Core project settings, ASGI/WSGI routing, OpenAPI schemas, and live broadcast service.
* **`verbal_tasks/`**: Native PostgreSQL-backed task queue (`runtaskworker`), cron-like periodic scheduler (`runtaskscheduler`), telemetry, and zombie recovery. Completely replaces Celery/Redis.
* **`metacognition/`**: Multi-agent reasoning framework compiling database-defined `CognitiveBlueprint` models into LangGraph execution graphs with dual-node (`_call` + `_eval`) action-evaluation contracts, memory state trees, tool routing, and human-in-the-loop approval.
* **`background_resources/`**: Grounded document retrieval engine with Docling/Grobid ingestion, semantic chunking, PGVector HNSW indexes, and cross-encoder re-ranking.
* **`grips/`**: A research wiki summarizing and organizing knowledge from uploaded documents with traceability to sources and a growth path to a true computable knowledge graph, pairing Markdown narratives with atomic computable claims (`claims_json`) and typed dependency edges.
* **`llm_api/`**: Hardware inspection (`hardware.py`), dynamic VRAM budgeting, Outlines grammar enforcement, zero-VRAM multimodal architecture introspection, and unified inference endpoints.
* **`sandbox_manager/`**: Isolated Docker container execution environment (`verbal_sandbox` on port 8002) with strict 1 CPU / 1GB RAM limits and timeout enforcement.
* **`grobid_client/`**: Academic PDF document processing client using containerized GROBID (port 8070), extracting TEI XML metadata, author affiliations, and citation networks.
* **`work_organisation/`**: Multi-user collaborative workspaces, spatial whiteboarding, Datastar SSE real-time synchronization, and automated causal factor extraction.
* **`demo_ui/`**: Interactive "Reason" research workbench with conversation branching trees, step streaming, tool approval modals, and Grips ontology visualization.
* **`benchmarking/`**: Evaluation harness for testing LLM and RAG configurations across prompt suites and synthetic scenario generation.

---

## 2. Operational Roles (`VERBAL_ROLE`)

Reason enforces dedicated process and privilege separation across services:

| Role | Supervisor / Script | Port | Responsibilities | VRAM Footprint |
|---|---|---|---|---|
| `web` | `./start_web.sh` (dev)<br>`./start_production_web.sh` (prod / `reason-web.service`) | `8000`<br>`8008` | Serves the research UI, Django admin, and REST API via Granian ASGI behind Nginx. Bound strictly to `127.0.0.1`. | **0 MB** (No model weights loaded) |
| `worker` | `python manage.py runtaskworker`<br>(`reason-worker.service`) | — | Consumes asynchronous background jobs (document ingestion, batch embeddings, tool execution) from the PostgreSQL task queue. | **0 MB** (Dispatches LLM calls via HTTP) |
| `scheduler` | `python manage.py runtaskscheduler`<br>(`reason-scheduler.service`) | — | Runs periodic maintenance routines, housekeeping sweeps, and NightManager self-improvement cycles. | **0 MB** |
| `inference` | `./start_inference.sh`<br>(`reason-inference.service`) | `8001` | Dedicated single-process GPU daemon serving local LLMs/VLMs (Gemma 4 E2B) via Ollama (`pull_policy="never"`), vLLM, or in-process PyTorch with Outlines structured generation. | **Monopolizes GPU** |
| `llamacpp` | `llama-server`<br>(`reason-llamacpp.service`) | `8080` | Dedicated CPU-first service hosting a quantized GGUF mini-model for instant CPU token counting, structured extraction, and offline fallback. | **0 MB GPU** (Pure CPU RAM) |

---

## 3. Quickstart Guide

### Prerequisites
* **Linux / WSL2** (Ubuntu 22.04+ recommended)
* **Python 3.12+** (managed via `uv`)
* **Docker & Docker Compose** (for PostgreSQL 18 + `pgvector`, GROBID, and isolated sandbox)
* **NVIDIA GPU** with CUDA 12.1+ (for local LLM inference)

### Step 1: Environment Setup & Dependencies
```bash
# Clone the repository
git clone <repo-url>
cd verbal

# Create configuration from template
cp .env.example .env

# Sync virtual environment dependencies (uses uv)
bash update_dependencies.sh --no-upgrade
```

### Step 2: Spin Up Infrastructure Containers
Start PostgreSQL 18 with `pgvector`, containerized GROBID, and the Docker execution sandbox:
```bash
docker compose up -d db sandbox
```
*(Note: `./start_background_services.sh` also launches required containers automatically).*

All container ports are bound strictly to `127.0.0.1`:
* PostgreSQL: `127.0.0.1:5433`
* Sandbox: `127.0.0.1:8002`
* GROBID (optional, academic profile): `127.0.0.1:8070`
* Ollama (optional, inference profile): `127.0.0.1:11434`

### Step 3: Database Preparation
```bash
source ../../py312/bin/activate
python manage.py migrate
python manage.py createsuperuser
```

### Step 4: Start Services
In separate terminal panes or service managers:

1. **Start Background Infrastructure, Task Worker & Scheduler**:
   ```bash
   ./start_background_services.sh
   # Automatically executes:
   # 1. docker compose up -d db sandbox (core infrastructure)
   # 2. python manage.py runtaskworker (queue consumer)
   # 3. python manage.py runtaskscheduler (periodic beat engine)
   ```

2. **Start the Inference Server** (GPU daemon):
   ```bash
   ./start_inference.sh
   ```

3. **Start the Web Application**:
   ```bash
   ./start_web.sh
   ```

Open your browser to:
* **Research Workbench**: `http://127.0.0.1:8000/demo_ui/`
* **Django Admin**: `http://127.0.0.1:8000/admin/`
* **Interactive API Documentation**: `http://127.0.0.1:8000/api/docs/`

---

## 4. Testing & Verification

Reason maintains comprehensive test suites verifying database invariants, multi-agent reasoning graphs, and UI workflows:

```bash
# Activate virtual environment
source ../../py312/bin/activate

# 1. One-stop pre-push validation gate (migrations check, deploy check, unit tests)
./scripts/pre_push_check.sh

# 2. Run fast unit and integration tests (excluding live inference e2e tests)
python manage.py test --exclude-tag=e2e --noinput

# 3. Run live end-to-end tests against the active inference server
python manage.py test --tag=e2e --noinput

# 4. Run metacognition empirical trials and doctests
pytest
```

---

## 5. Security & Tool Governance (Lockdown Modes)

Reason enforces a 3-tier security governance architecture to restrict autonomous agent capabilities and prevent unauthorized model-written code execution, external network calls, or self-modification:

| Lockdown Level | Badge | Code Execution | Model Meta-Tools | Permitted Capabilities |
|---|---|---|---|---|
| `DEVELOPMENT` | 🧪 DEVELOPMENT | Allowed | Active | All tools active; self-modification allowed for AI development. |
| `CONTROLLED` | ⚡ CONTROLLED | Clearance-Gated | Blocked | Meta-tools blocked. Code execution requires `TRUSTED` or `ADMIN` clearance. Base domain model writes allowlisted. |
| `RESTRICTED` | 🔒 RESTRICTED | Blocked | Blocked | Model code execution and outbound network tools blocked host-wide. Deterministic domain tools (RAG search, Grips ontology) active. |
| `TEXT_ONLY`<br>*(Alias: `LOCKED`)* | 🛡️ TEXT-ONLY | Blocked | Blocked | All model tools blocked. Operates purely through structured reasoning and JSON schemas without external or code tools. Internal platform plumbing remains fully operational. |

### Applying Restricted Run-Modes in `.env`

To set the host-level lockdown mode, edit your `.env` file:

```bash
# Options: DEVELOPMENT | CONTROLLED | RESTRICTED | TEXT_ONLY (or LOCKED)
VERBAL_LOCKDOWN_LEVEL=RESTRICTED

# Fine-grained master clamps:
ALLOW_MODEL_CODE_EXECUTION=False
ALLOW_TOOL_NETWORK_ACCESS=False
ALLOW_AGENT_SELF_MODIFICATION=False
```

Restart your services (`./start_web.sh` and `./start_background_services.sh`) for the changes to take effect. The active mode is visually indicated in the Demo UI navigation bar and broadcast via the `X-Verbal-Lockdown-Level` HTTP header.
