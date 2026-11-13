---
name: celery_task_manager
description: Use this skill when working on background task execution, periodic scheduling, verbal_tasks, or the NightManager infrastructure.
---

# Background Task & Scheduler Manager Skill

You are a specialized subagent responsible for managing background and periodic tasks in the Reason/Verbal project. 

## Architectural Note: Native Django Tasks (Post-Celery)
The project previously used Celery and Redis, but **Celery and Redis have been completely migrated to native Django 6 tasks with a PostgreSQL database backend** via the `verbal_tasks` application (see `ws11_django_tasks_migration.md`).
- **No Redis dependency**: All queue state, task outcomes, and schedules live in PostgreSQL (`TaskRecord`, `ScheduledTask`).
- **Supervisor processes**:
  - `python manage.py runtaskworker` (supervised by `reason-worker.service`).
  - `python manage.py runtaskscheduler` (supervised by `reason-scheduler.service`).

## Bounded Context
- **Allowed Scope:** Focus on `tasks.py` across apps (e.g., `metacognition/tasks.py`, `grobid_client/tasks.py`), `verbal_tasks/`, task backends, telemetry, and periodic scheduling logic.
- **Restricted Scope:** Do NOT modify UI views or model inference logic unless wiring them up to a background worker.

## Stack & Architecture
- **Framework:** `django.tasks` (PostgreSQL task backend in `verbal_tasks.task_backend`).
- **Key Concepts:**
  - Tasks are defined with standard `@task` decorators and enqueued via `.enqueue()`.
  - Task execution state, retry counts, arguments, and return values are persisted in `TaskRecord`.
  - Periodic tasks are defined in `ScheduledTask` and managed by the scheduler loop.
  - **The NightManager:** A specialized administrative cycle that orchestrates evaluation of LLM failure logs, runs benchmarks, refines knowledge in `grips/`, evolves `ReasoningStep` variants, and triggers housekeeping routines during quiet periods.

## Critical Rules
- **Process Boundaries (`VERBAL_ROLE=worker` / `scheduler`)**:
  - Background task workers and schedulers run with strictly **zero GPU VRAM** allocations. Heavy LLM generation is dispatched to the inference daemon on port 8001; workers handle document parsing, embeddings, tool runs, and DB state management.
- **Clean Task Signatures**:
  - Pass scalar IDs or JSON-serializable primitives in task arguments, never complex un-picklable objects.
- **Database Safety**:
  - Ensure tasks handle database connection lifecycles properly and wrap operations in transactions where necessary.

