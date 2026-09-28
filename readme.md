# Reason (`verbal`)

**Reason** is a deliberative study design assistant and multi-agent cognitive architecture built on Django. It combines database-backed state graphs (`CognitiveBlueprint`), verifiable academic literature grounding (GROBID + PGVector), structured ontology graphs (Grips), and human-in-the-loop counterfactual branching with real-time SSE streaming.

---

## 1. System Architecture

The system coordinates three operational microservice roles over a native PostgreSQL 18 task queue:

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
┌───────────────────────────────────────┐    ┌───────────────────────────────────┐
│            Task Worker                │    │        Inference Instance         │
│   verbal_tasks: runtaskworker         │    │      llm_api / vLLM (:8001)       │
│   verbal_tasks: runtaskscheduler      │    │  Monopolizes GPU, VRAM Budgeting, │
│   Blueprint execution, RAG, NightMgr  │    │  Outlines Structured Generation   │
└───────────────────┬───────────────────┘    └───────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────┐
│     PostgreSQL 18 + PGVector HNSW     │
│  State Graphs, Chunks, Task Queues    │
└───────────────────────────────────────┘
```

### Core Applications
* **`verbal_config/`**: Core project settings, ASGI/WSGI routing, OpenAPI schemas, and live broadcast service.
* **`verbal_tasks/`**: Native PostgreSQL-backed task queue (`runtaskworker`), cron-like periodic scheduler (`runtaskscheduler`), telemetry, and zombie recovery. Completely replaces Celery/Redis.
* **`metacognition/`**: Multi-agent reasoning framework compiling database-defined `CognitiveBlueprint` models into LangGraph execution graphs with memory, tool routing, and human-in-the-loop approval.
* **`background_resources/`**: Grounded document retrieval engine with Docling/Grobid ingestion, semantic chunking, PGVector HNSW indexes, and cross-encoder re-ranking.
* **`grips/`**: Graph Representation of Intelligent Problem Solving: an ontological knowledge graph linking Markdown narratives to atomic computable claims (`claims_json`) and dependency edges.
* **`llm_api/`**: Hardware inspection (`hardware.py`), dynamic VRAM budgeting, Outlines grammar enforcement, and unified inference endpoints.
* **`sandbox_manager/`**: Isolated Docker container execution environment (`verbal_sandbox` on port 8002) with strict 1 CPU / 1GB RAM limits and timeout enforcement.
* **`grobid_client/`**: Academic PDF document processing client using containerized GROBID (port 8070), extracting TEI XML metadata, author affiliations, and citation networks.
* **`work_organisation/`**: Multi-user collaborative workspaces, spatial whiteboarding, Datastar SSE real-time synchronization, and automated causal factor extraction.
* **`demo_ui/`**: Interactive "Reason" research workbench with conversation branching trees, step streaming, tool approval modals, and Grips ontology visualization.
* **`benchmarking/`**: Evaluation harness for testing LLM and RAG configurations across prompt suites and synthetic scenario generation.

---

## 2. Operational Roles (`VERBAL_ROLE`)

Reason is designed to run with dedicated process separation:

| Role | Script | Port | Responsibilities |
|---|---|---|---|
| `web` | `./start_web.sh` (dev)<br>`./start_production_web.sh` (prod) | `8000`<br>`8008` | Serves the research UI, Django admin, and REST API. Bound strictly to `127.0.0.1`. |
| `inference` | `./start_inference.sh` | `8001` | Dedicated single process monopolizing local GPU(s) with big models via Outlines / Transformers / vLLM. Bound strictly to `127.0.0.1`. |
| `worker` | `./start_background_services.sh` | — | Runs native `verbal_tasks` queue workers (`runtaskworker`) and periodic schedulers (`runtaskscheduler`). |

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
docker compose up -d
```
All container ports are bound strictly to `127.0.0.1`:
* PostgreSQL: `127.0.0.1:5433`
* GROBID: `127.0.0.1:8070`
* Sandbox: `127.0.0.1:8002`

### Step 3: Database Preparation
```bash
source ../../py312/bin/activate
python manage.py migrate
python manage.py createsuperuser
```

### Step 4: Start Services
In separate terminal panes or service managers:

1. **Start the Background Worker & Scheduler**:
   ```bash
   ./start_background_services.sh
   # Or manually:
   # python manage.py runtaskworker
   # python manage.py runtaskscheduler
   ```

2. **Start the Inference Server** (GPU node):
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

# Run core unit and integration tests
python manage.py test verbal_tasks metacognition background_resources benchmarking sandbox_manager grobid_client

# Run metacognition empirical trials and doctests
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
| `AIR_GAPPED`<br>*(Aliases: `TEXT_ONLY`, `LOCKED`)* | 🛡️ TEXT-ONLY | Blocked | Blocked | All model tools blocked. Operates purely through structured reasoning and JSON schemas. Internal platform plumbing remains fully operational. |

### Applying Restricted Run-Modes in `.env`

To set the host-level lockdown mode, edit your `.env` file:

```bash
# Options: DEVELOPMENT | CONTROLLED | RESTRICTED | AIR_GAPPED (or TEXT_ONLY / LOCKED)
VERBAL_LOCKDOWN_LEVEL=RESTRICTED

# Fine-grained master clamps:
ALLOW_MODEL_CODE_EXECUTION=False
ALLOW_TOOL_NETWORK_ACCESS=False
ALLOW_AGENT_SELF_MODIFICATION=False
```

Restart your services (`./start_web.sh` and `./start_background_services.sh`) for the changes to take effect. The active mode is visually indicated in the Demo UI navigation bar and broadcast via the `X-Verbal-Lockdown-Level` HTTP header.