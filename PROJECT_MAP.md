# Verbal Project Map

## 1. High-Level Architecture
Verbal is a Django-based computational study design assistant that integrates local or external Large Language Models (LLMs) with a RAG pipeline and a structured knowledge graph (Grips). It utilizes a multi-role configuration (`web` for UI, `inference` for CPU/GPU model serving, and `worker` for background tasks) coordinated asynchronously via Celery and Redis.

## 2. Component Directory
* **`verbal_config/`**: Core Django project configuration, settings, ASGI/WSGI routing, and user API schemas.
* **`background_resources/`**: Retrieval-Augmented Generation (RAG) pipeline including document chunking/ingestion, FAISS vector store management, and document processing tasks.
* **`metacognition/`**: Autonomous agent loop execution, LangGraph compilation, action state tracking, and metacognitive tool registries.
* **`llm_api/`**: Local and external LLM inference interface, API endpoints (using Django Ninja), and interaction logging.
* **`grips/`**: Structured wiki and LLM-curated Knowledge Graph linking Markdown narratives to JSON-based propositional claims.
* **`benchmarking/`**: Test harness for evaluating LLM and RAG configuration performance using synthetic Q&A data generators.
* **`demo_ui/`**: Django templates and HTMX-powered frontend views for the chat interface and blueprint logs.
* **`grobid_client/`**: Docker-based GROBID client for deterministic PDF parsing and bibliographic reference extraction.
* **`sandbox_manager/`**: Python sandbox manager enabling secure, isolated code execution for autonomous agent tasks.
* **`verbal/`**: A minimal app holding the project-wide custom test runner.
* **`documentation/`**: Sphinx-based application documentation and usage guides.

## 3. Workflow Cheatsheet
* **To add a new tool for the agent**: Look in `metacognition/actions.py` and `metacognition/meta_tools.py`.
* **To change the UI layout**: Look in `templates/demo_ui/` and `demo_ui/views.py`.
* **To modify background schedules**: Look in `metacognition/tasks.py` and `metacognition/seed.py`.
* **To adjust RAG ingestion or chunking**: Look in `background_resources/rag_service.py` and `background_resources/nlp_service.py`.
* **To run evaluations/benchmarks**: Look in `benchmarking/runner.py` and `benchmarking/generators.py`.
