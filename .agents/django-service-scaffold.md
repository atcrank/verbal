---
name: django-service-scaffold
description: Scaffold a new Django app/service for the experiment harness.
---
When this skill is invoked:
1. Generate the standard Django app structure using Python 3.13+ syntax and Django 6.0+ features.
2. Implement business logic using Django and Django-ninja coding styles.
3. This project uses celery and celery-beat to execute tasks in the background, usually in tasks.py
4. This project is run in several instances at the same time, with one instance in each of the following modes:
   - web, launched with start_web.sh, provides the web interfaces
   - worker, launched with start_background_services.sh, runs the celery tasks
   - inference, launched with start_inference.sh, which runs a local AI model or manages to requests to LLM servers
5. In the database architecture, prefer explicit modeled fields to JSON fields holding a variety of properties.
6. The data model should be reflected in the database.
7. Wire the new app into the main Django settings.