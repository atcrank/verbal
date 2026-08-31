# Note: Migration from Celery / Redis to Native Django Tasks (django.tasks)

- **Date**: 2026-08-31
- **Status**: pending
- **Workstream Spec**: [ws11_django_tasks_migration.md](../ws11_django_tasks_migration.md)

## Context & Motivation
The deployed environment has strict security lockdown policies on Docker containers that prevent the Redis container from operating properly. Celery and Celery-Beat require Redis (or RabbitMQ) as a message broker and results backend.

With Django 6.0's introduction of the native `django.tasks` module (DEP 14), we can leverage a PostgreSQL-backed database task backend directly within `verbal_db`.

## Key Objectives
1. Eliminate the Redis container dependency entirely.
2. Migrate all 18 Celery tasks across 6 Django apps (`metacognition`, `grips`, `llm_api`, `background_resources`, `benchmarking`, `grobid_client`) to `@task` / `.enqueue()`.
3. Provide a database-backed periodic scheduler for the NightManager and routine maintenance.
4. Refactor the admin task dashboard to monitor PostgreSQL task queue records directly.
