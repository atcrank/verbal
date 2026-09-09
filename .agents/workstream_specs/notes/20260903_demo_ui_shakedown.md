# Note: Demo UI Shakedown Cruise & Firefighting Robotics Walkthrough

- **Date**: 2026-09-03
- **Status**: pending
- **Workstream Spec**: [ws14_demo_ui_shakedown.md](../ws14_demo_ui_shakedown.md)

## Context & Objectives
Executing a comprehensive shakedown cruise of the Reason (`demo_ui`) stack using three real research papers on "Use of Drones and Robots in Firefighting":
1. Repair Grobid container startup failure (`cgroupv2` JVM NullPointerException).
2. Exercise `grobid_client` and `GrobidReadingStrategy` for semantic structure-sensitive chunking on the PDFs in `demo_ui/demo_ui_trials/doctest_fodder/`.
3. Retain the parsed semantic `RAGChunks` in a reproducible fixture without committing the binary PDFs to git.
4. Construct modular, executable doctests in `demo_ui/demo_ui_trials/` demonstrating end-to-end interface flows (Ingestion, Multi-Step Reasoning with Sandboxed Tools, Conversation Branching, Grips Expansion).
5. Build `seed_demo_presentation` management command as an offline fallback.
6. Record all friction points and system weaknesses discovered during the shakedown.

## Discovered Issues Log & Shakedown Notes

- **Issue 1: Grobid Container cgroups v2 JVM Crash**
  - *Symptom*: `verbal_grobid` container crashed immediately on startup with `java.lang.NullPointerException: Cannot invoke "jdk.internal.platform.CgroupInfo.getMountPoint()"`.
  - *Root Cause*: The Java 17 runtime in `lfoppiano/grobid:0.8.0` does not handle systemd cgroups v2 mount points on Linux without host cgroup namespace or updated container image.
  - *Remediation*: Cataloged for Workstream 14 Grobid upgrade to `grobid/grobid:0.8.1` and `cgroup: host` in `docker-compose.yml`. Extracted fodder chunks into `firefighting_chunks.json` fixture to decouple offline tests.

- **Issue 2: Document Model Schema Drift (`category` vs `metadata`)**
  - *Symptom*: Calling `Document.objects.create(category="FIRE_ROBOTICS")` failed with unexpected keyword argument.
  - *Root Cause*: The `Document` model stores extensible classification tags in `metadata = models.JSONField(default=dict)`.
  - *Remediation*: Standardized trial and seeder code to pass `metadata={"category": "FIRE_ROBOTICS"}`.

- **Issue 3: Django Test Client Host Header & ALLOWED_HOSTS**
  - *Symptom*: Django test `Client` GET/POST requests failed with HTTP 400 Bad Request.
  - *Root Cause*: Django's test `Client` defaults `HTTP_HOST: 'testserver'`, which was blocked because `.env` defined `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`.
  - *Remediation*: Patched `verbal_config/settings.py` to ensure `'testserver'` is always present in `ALLOWED_HOSTS`.

- **Issue 4: Conversation Model Lineage API (`forked_from` argument)**
  - *Symptom*: Attempting `Conversation.objects.create(..., forked_from=parent_conv)` raised `TypeError`.
  - *Root Cause*: The `Conversation` model uses `state_tree = models.JSONField()` rather than a dedicated foreign key for DAG ancestry.
  - *Remediation*: Stored DAG ancestry inside `state_tree={"parent_conversation_id": str(parent_conv.id)}`.

- **Issue 5: Grips Ontology Models Legacy Imports (`SemanticClaim`, `DomainRelation`)**
  - *Symptom*: Imports from `grips.models` for `SemanticClaim` and `DomainRelation` failed with `ImportError`.
  - *Root Cause*: `DomainRelation` was superseded by `KnowledgeEdge(source, target, relationship_type, justification)`. Semantic claims are serialized into `ConceptNode.structured_claims`.
  - *Remediation*: Updated presentation seeder and trial 3 to use `KnowledgeEdge.objects.get_or_create(...)` and JSON-structured claims.

- **Issue 6: Playwright Synchronous Execution in Async Context**
  - *Symptom*: Initializing Playwright inside doctests raised `SynchronousOnlyOperation: You cannot call this from an async context`.
  - *Root Cause*: Pytest and Playwright runners invoke event loop hooks that trigger Django's async-safety guards.
  - *Remediation*: Set `os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"` in the doctest environments and test fixtures.

- **Issue 7: Port 8000 Network Isolation vs Test Database Mismatch**
  - *Symptom*: Playwright browser navigation to `http://127.0.0.1:8000/admin/login/` during pytest failed to authenticate users created inside the test database.
  - *Root Cause*: The live dev server on port 8000 connects to `verbal_dev`, whereas pytest executes against `test_verbal_db`.
  - *Remediation*: Decoupled test client rendering: Django's test `Client` executes within the active test database session, and Playwright renders the resulting HTML locally using `page.set_content(html)` with `<base href="http://127.0.0.1:8000/">` to resolve static CSS/assets.

- **Issue 8: Pytest Doctest DB Fixture & Connection Reset (Rollback Flag / FK Violation)**
  - *Symptom*: Pytest doctest failed with `ForeignKeyViolation: Key (user_id)=(1) is not present in table "auth_user"` and `TransactionManagementError: The rollback flag doesn't work outside of an 'atomic' block`.
  - *Root Cause*: The default `db` fixture wraps tests in `atomic()` blocks. When Django's test `Client` handles a request, `request_finished` signal invokes `close_old_connections()`, closing the PostgreSQL connection and rolling back uncommitted test state.
  - *Remediation*: Updated `conftest.py`'s `enable_db_access_for_doctests` fixture to request `transactional_db` instead of `db`. This allows real database transactions and commits across multiple test `Client` and Playwright interactions.
