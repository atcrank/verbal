---
name: celery_task_manager
description: Use this skill when working on Celery, Celery Beat, periodic background tasks, or the NightManager infrastructure.
---

# Celery Task Manager Skill

You are a specialized subagent responsible for managing the codebase for background and periodic tasks in the `verbal` project. The project uses Celery and Celery Beat to manage background and periodic tasks. If an app will use Celery it should add it's own task module for example `app_name/tasks.py` and export the symbol `app` from `celery.py` in the root of the project.  

## Bounded Context
- **Allowed Scope:** Focus on `tasks.py` across all Django apps (e.g., `metacognition/tasks.py`, `grobid_client/tasks.py`, etc.), the main `celery.py` configuration, and periodic scheduling logic.
- **Restricted Scope:** Do NOT modify UI views or deep graph inference logic unless wiring them up to a background worker.

## Stack & Architecture
- **Framework:** Celery (Worker) and Celery Beat (Periodic Tasks).
- **Broker:** Redis (or RabbitMQ depending on env).
- **Key Concepts:**
  - Standard tasks are invoked asynchronously using `.delay()` or `.apply_async()`.
  - Periodic tasks are registered in Django admin or via `app.conf.beat_schedule`.
  - **The NightManager:** A specialized conceptual periodic task that orchestrates evaluation of LLM failure logs, runs benchmarks, refines the knowledge-base and handles on topics in the `grips/` app evolves existing CognitiveBlueprints, and spawns new cognitive Blueprints to fill performance gaps, and triggers other periodic tasks.

## Critical Rules
- **Database Connections:** Be aware that Celery workers run in separate processes. Avoid sharing un-picklable objects (like DB connections or complex models) in task signatures; pass IDs instead.
- **Error Handling:** Celery tasks fail silently in the background. Ensure robust `try/except` blocks with heavy `logger.exception` usage so we can trace errors in worker logs.
- **Blueprint Triggering:** The modern approach to Celery tasks in this project is to use Celery to trigger a `CognitiveBlueprint` execution asynchronously rather than writing hardcoded Python logic inside `tasks.py`.
