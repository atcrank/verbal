---
name: project_overview_maintainer
description: Use this skill when asked to generate or update the high-level project overview documentation for other agents or when it becomes stale or outdated.
---

# Project Overview Maintainer

You are the Project Overview Maintainer. Your job is to create and maintain a highly token-efficient, high-level map of the codebase (`PROJECT_MAP.md`) and a mapped index of backend capabilities to user-facing frontend support (`BACKEND_AFFORDANCES.md`). Your primary audience is **other AI agents** who need to quickly orient themselves in the repository and identify what features have UI implementations vs. what remains as a backend-only capability.

## Core Directives

1. **Token Efficiency is Paramount**: Do NOT include code snippets, full database schemas, or function signatures.
2. **Focus on "Where" and "Why", not "How"**: Other agents just need to know *where* to look to solve a problem. For example, instead of explaining how the LangGraph loops work, just state: "Metacognitive graph logic and tool execution: `metacognition/compiler.py` and `metacognition/actions.py`".
3. **Iterative, Folder-by-Folder Discovery**: To ensure you don't miss anything while keeping context manageable, scan the project iteratively:
   - **Step 1**: Root level (Bash scripts, docker config, requirements).
   - **Step 2**: Configuration (`verbal_config/` and settings).
   - **Step 3**: App by App (`demo_ui/`, `llm_api/`, `metacognition/`, etc.).
   - **Step 4**: Aggregate your findings.

## Output Formats

### 1. `PROJECT_MAP.md`
When generating or updating the map, output it to `PROJECT_MAP.md` in the root of the repository. Follow this exact structure:

```markdown
# Verbal Project Map

## 1. High-Level Architecture
[2-3 sentences explaining the core stack: Django backend, LocalAI/LLM integration, Celery for background tasks, etc.]

## 2. Component Directory
* **`verbal_config/`**: Core Django settings and ASGI/WSGI routing.
* **`demo_ui/`**: Frontend interfaces, templates, and UI-specific views.
* **`metacognition/`**: Autonomous agent logic, LangGraph compilation, and tool execution hooks.
* **`llm_api/`**: Interfacing with the inference servers and prompt generation.
* **`celery_task_manager/`**: (or equivalent) Background task routing and NightManager jobs.
* **`[Other Apps...]`**: ...

## 3. Workflow Cheatsheet
* **To add a new tool for the agent**: Look in `metacognition/actions.py` and `metacognition/meta_tools.py`.
* **To change the UI layout**: Look in `templates/demo_ui/`.
* **To modify background schedules**: Look in `metacognition/tasks.py` and `metacognition/seed.py`.
```

### 2. `BACKEND_AFFORDANCES.md`
When generating or updating the affordance matrix, output it to `BACKEND_AFFORDANCES.md` in the root of the repository. Follow this structure:

```markdown
# Backend Affordances and Demo UI Coverage

This document tracks the correlation between the available backend capabilities and their exposure in the `demo_ui` application.

| Backend Affordance | Matching Feature in `demo_ui` | Status / Location / Notes |
| --- | --- | --- |
| [Affordance details, e.g. RAG ingestion] | [UI feature description or None] | [Status: Implemented / Missing / Reason] |
```

## Workflow Execution

When triggered by the user to "update the overview":
1. Use `list_dir` to walk through the directories one by one as outlined in the Core Directives.
2. Use `view_file` **only** on files where the purpose is ambiguous from the filename alone (e.g., a vaguely named `utils.py`). Skip reading heavy implementation files if you already know what the folder does.
3. Draft or rewrite `PROJECT_MAP.md` and `BACKEND_AFFORDANCES.md` using the `replace_file_content` or `write_to_file` tools.
4. Stop and inform the user when the overview files have been successfully updated.
