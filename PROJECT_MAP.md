# Verbal Project Map

## 1. High-Level Architecture
Verbal is a Django-based computational study design assistant that integrates local and external Large Language Models (LLMs) with a rigorous Retrieval-Augmented Generation (RAG) pipeline and a structured conceptual knowledge graph (Grips). It utilizes a multi-role configuration (`web` for UI and API, `inference` for local GPU/CPU model serving, and `worker` for background tasks) coordinated asynchronously via a native database-backed task engine (`verbal_tasks`).

## 2. Component Directory
* **`verbal_config/`**: Core Django project configuration, settings, ASGI/WSGI routing, and base OpenAPI schemas.
* **`verbal_tasks/`**: Native database-backed asynchronous task execution engine (`runtaskworker`), periodic cron-like scheduler (`runtaskscheduler`), execution telemetry, and dead-letter/zombie recovery.
* **`llm_api/`**: Multi-role LLM inference interface (`web` proxy vs. `inference` server), Django-Ninja REST endpoints, hardware capability inspection (`hardware.py`), quantization presets (FP16, 8-bit, 4-bit, AWQ, GPTQ, ExLlamaV2), and prompt/response logging.
* **`sandbox_manager/`**: Secure, isolated Docker container execution environment (`verbal-sandbox`) with strict 1 GB memory caps, 1.0 CPU throttling, single-threaded OpenBLAS controls, and timeout enforcement for agent-generated code.
* **`grobid_client/`**: Academic PDF document processing client using containerized GROBID, extracting structured TEI XML, bibliographic metadata, author affiliations, and citation references.
* **`background_resources/`**: Grounded knowledge retrieval engine featuring multi-strategy document ingestion (Docling, Grobid, text), semantic chunking, PGVector cosine search, cross-encoder re-ranking (Super Retriever), and concept promotion into Grips.
* **`grips/`**: Graph Representation of Intelligent Problem Solving: an ontological knowledge graph linking Markdown domain narratives to structured JSON claims (`claims_json`), concept hierarchies, dependency edges, and automated graph linting.
* **`metacognition/`**: Deliberative multi-agent reasoning framework compiling database-defined `CognitiveBlueprint` models into LangGraph state graphs with tool execution, memory management, and human-in-the-loop approval.
* **`work_organisation/`**: Multi-user collaborative workspace management, group-scoped access control, four privacy/anonymity modes, interactive spatial whiteboarding, Datastar SSE real-time sync, and LLM-assisted idea clustering and causal factor extraction.
* **`benchmarking/`**: Rigorous evaluation harness for testing LLM and RAG configurations across prompt suites and scenarios using synthetic Q&A generation, mathematical scoring, and Unsloth LoRA fine-tuning workflows.
* **`demo_ui/`**: Interactive "Reason" research workbench built on Django templates, HTMX, and Datastar SSE, providing conversation branching, multi-step blueprint streaming, tool approval modals, and Grips exploration.
* **`verbal/`**: Project-wide test utilities, custom test runner, and global database fixtures.
* **`documentation/`**: Sphinx-based technical documentation, application module guides, and empirical rigor trial reports.
* **`.agents/`**: Custom agent skills, architectural blueprints, workstream specifications, and developer notes.

## 3. Workflow Cheatsheet
* **To add a new tool for the agent**: Look in `metacognition/actions.py` and `metacognition/meta_tools.py`.
* **To inspect or modify background tasks and schedules**: Look in `verbal_tasks/models.py`, `metacognition/tasks.py`, and `runtaskscheduler`.
* **To adjust RAG ingestion, chunking, or retrieval**: Look in `background_resources/rag_service.py`, `background_resources/retrieval.py`, and `background_resources/nlp_service.py`.
* **To modify cognitive blueprint structure or node compilation**: Look in `metacognition/models.py` and `metacognition/compiler.py`.
* **To explore or test UI features**: Look in `templates/demo_ui/`, `demo_ui/views.py`, and `demo_ui/demo_ui_trials/`.
* **To run evaluations or train LoRA adapters**: Look in `benchmarking/runner.py`, `benchmarking/generators.py`, and `benchmarking/train.py`.
