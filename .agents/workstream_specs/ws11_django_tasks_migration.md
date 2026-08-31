# WS11: Migration from Celery / Redis to Native Django Tasks (django.tasks)

## Goal

Migrate the asynchronous background task and periodic scheduling infrastructure from **Celery / Celery Beat / Redis** to the native **Django Tasks (`django.tasks`)** framework in Django 6.0+ backed by PostgreSQL.

This eliminates the locked-down `verbal_redis` Docker container, unifies task management and state within PostgreSQL, enables transactional task enqueueing, and simplifies worker and test setups.

---

## Architectural Comparison

| Component | Legacy (Celery + Redis) | Target (Django 6.0 `django.tasks` + PostgreSQL) |
| :--- | :--- | :--- |
| **Broker / Storage** | Redis (`verbal_redis` container) | PostgreSQL (Directly in `verbal_db` database) |
| **Containers** | Requires Redis container | **Postgres only** (Redis eliminated completely) |
| **Task API** | `@shared_task`, `.delay()`, `.apply_async()` | `@task`, `.enqueue()` |
| **Periodic Engine** | `django-celery-beat` daemon | DB-backed Task Scheduler |
| **Transaction Safety** | Independent Redis queues | Enqueue inside Django DB transactions (`on_commit`) |
| **Test Execution** | Requires running broker or complex mocks | Native `immediate` backend (synchronous, 0 broker) |

---

## Key Files & Impact Scope

| App / Component | Files Affected | Scope & Responsibility |
| :--- | :--- | :--- |
| **Configuration & Core** | `verbal_config/settings.py`<br>`verbal_config/celery.py` (Delete)<br>`requirements.in`<br>`requirements.txt`<br>`docker-compose.yml` | Remove Celery/Redis dependencies; configure `django.tasks` database backend and `immediate` test backend; remove `verbal_redis` container. |
| **Metacognition** | `metacognition/tasks.py`<br>`metacognition/governance.py`<br>`metacognition/seed.py` | Migrate `night_manager_task`, blueprint runner, and governance tasks to `@task`. Replace `django_celery_beat.models.PeriodicTask` with DB scheduler model. |
| **Grips** | `grips/tasks.py`<br>`grips/models.py`<br>`grips/admin.py`<br>`templates/admin/grips/` | Migrate 9 knowledge graph tasks. Refactor `CeleryStatusAdmin` dashboard to query DB task status directly. |
| **LLM API** | `llm_api/tasks.py`<br>`llm_api/api.py`<br>`templates/admin/llm_api/` | Migrate `download_model_cache` task; track download progress via DB task metadata/cache. |
| **Background Resources** | `background_resources/tasks.py`<br>`background_resources/models.py` | Migrate document chunking, NLP extraction, and embedding background tasks. |
| **Benchmarking** | `benchmarking/tasks.py`<br>`benchmarking/runner.py` | Migrate synthetic benchmark execution and evaluation tasks. |
| **Grobid Client** | `grobid_client/tasks.py` | Migrate PDF parsing background task. |
| **Scripts & Docs** | `start_background_services.sh`<br>`stop_background_services.sh`<br>`toggle_background_task_service.sh`<br>`start_redis.sh` (Delete)<br>`.agents/skills/celery_task_manager/` | Update runner scripts for `django.tasks` worker & scheduler. Update skills and docs. |

---

## Phased Implementation Plan

### Phase 1: Core Framework & Settings Setup
1. **Dependency Cleanup**:
   - Remove `celery`, `django-celery-beat`, `kombu`, `billiard`, `amqp`, `vine` from `requirements.in` and `requirements.txt`.
   - Remove `verbal_redis` container and `redis_data` volume from `docker-compose.yml`.
   - Delete `verbal_config/celery.py` and `start_redis.sh`.
2. **Backend Configuration**:
   - Configure `TASKS` in `verbal_config/settings.py` pointing to a PostgreSQL-backed database task backend with robust polling / notification (`SKIP LOCKED`).
   - Configure `immediate` backend when `VERBAL_ROLE=standalone` or during testing.
   - Remove `django_celery_beat` from `INSTALLED_APPS` and `CELERY_*` settings.

### Phase 2: Task Definitions & Invocations Migration (~18 Tasks)
1. **Decorator & Signature Migration**:
   - Replace `@shared_task` with `@task` from `django.tasks`.
   - Update retry logic: replace Celery-specific `autoretry_for` / `retry_backoff` with standard task retry handling.
2. **Caller Migration**:
   - Replace all `.delay(*args, **kwargs)` and `.apply_async(...)` with `.enqueue(*args, **kwargs)`.
   - Update any direct task result checks to use `TaskResult` / task ID schema.

### Phase 3: Periodic Scheduling & NightManager Engine
1. **Schedule Model & Engine**:
   - Implement a lightweight, robust database-backed scheduled tasks model (`ScheduledTask`) supporting crontab and interval specifications.
   - Implement worker/scheduler command (`python manage.py runscheduler` or combined worker-scheduler loop) that evaluates due schedules and enqueues corresponding `@task` functions.
2. **Seed & Maintenance Integration**:
   - Update `metacognition/seed.py` to register the NightManager daily maintenance (3:00 AM) and scoring tasks in the new schedule model.

### Phase 4: Admin Dashboard & Task Monitoring
1. **Task Monitoring Dashboard**:
   - Refactor `CeleryStatus` model / admin in `grips/admin.py` to `TaskStatusAdmin` / `TaskQueueAdmin`.
   - Query task states (`PENDING`, `RUNNING`, `SUCCESSFUL`, `FAILED`) directly from the PostgreSQL task queue table.
   - Update `celery_status_dashboard.html` template for real-time task queue visibility.

### Phase 5: Shell Scripts, Tooling & Documentation
1. **Script Updates**:
   - Update `start_background_services.sh`, `stop_background_services.sh`, and `toggle_background_task_service.sh` to run the native task worker/scheduler.
   - Update `backup_data.sh` to adjust table exclusions.
2. **Skill & Documentation Update**:
   - Refactor `.agents/skills/celery_task_manager/` to `task_manager` with updated rules and workflows.
   - Update `PROJECT_MAP.md` and Sphinx documentation.

---

## Verification & Testing Plan

1. **Unit & Integration Tests**:
   - Run existing test suites (`pytest grips/tests.py`, `pytest metacognition/tests.py`, `pytest llm_api/tests.py`) using `immediate` task backend.
2. **Worker Execution Verification**:
   - Start task worker with `python manage.py runtaskworker` (or background runner script).
   - Enqueue test tasks from `llm_api`, `background_resources`, and `grips` and verify status transitions to `SUCCESSFUL`.
3. **Scheduler Verification**:
   - Trigger scheduled task cycle and verify `NightManager` execution log in database.
4. **Admin Dashboard**:
   - Inspect the task queue dashboard in Django admin.
