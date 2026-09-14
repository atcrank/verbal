Verbal Tasks - Database-Backed Asynchronous Execution & Scheduling
===================================================================

The **Verbal Tasks** application provides native, database-backed background task execution, cron/interval scheduling, and operational telemetry for the Verbal study design assistant. It operates entirely within PostgreSQL and standard Django database transactions, eliminating the operational complexity and connection failure modes of external message brokers (such as Redis or RabbitMQ) and distributed task queues (such as Celery).

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Purpose & Motivating Problem
-------------------------------

Why Background Tasks are Necessary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Computational study design involves compute-heavy operations that cannot execute synchronously within a standard HTTP request/response cycle:

* Parsing multi-hundred-page research PDFs and chunking them for semantic indexing (`background_resources`).
* Executing multi-step agentic cognitive blueprints with tool calls and reflection loops (`metacognition`).
* Running automated benchmark evaluation suites across dozens of scenarios (`benchmarking`).
* Periodic graph verification, linting, and maintenance routines via NightManager.

If executed in the web thread, these operations would cause HTTP gateway timeouts (504s), freeze user interfaces, and starve concurrent users.

The Hazard of Distributed Brokers on Research Workstations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Earlier iterations of this project utilized Celery with Redis brokers. While standard in cloud deployments, running distributed brokers on single-workstation research environments introduced serious failure modes:

* **Silent Connection Drops**: Local OS sleep cycles, Docker network hiccups, or Redis restarts severed worker connections silently without alerting the user.
* **Process Fragmentation**: Managing multiple daemons (Django web, Celery worker, Celery beat, Redis) increased cognitive load and configuration drift.
* **State Invisibility**: In-memory Redis queues made inspecting task state, token telemetry, and execution history difficult without specialized external monitoring tooling (e.g. Flower).

**Verbal Tasks** was created to resolve these friction points by storing all task queues, periodic schedules, and execution records directly in PostgreSQL using standard transactional guarantees.


2. Architecture & Mechanism
---------------------------

How the Database Queue Operates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The task engine is built on standard relational patterns:

* **Task Enqueueing**: Calling `.enqueue(*args, **kwargs)` on a task decorated with ``@task`` writes a ``TaskRecord`` row into PostgreSQL with ``status='READY'`` and an optional ``run_after`` timestamp.
* **Worker Execution (``runtaskworker``)**: A multi-threaded worker process polls PostgreSQL using row-level locking (`SELECT ... FOR UPDATE SKIP LOCKED`). When a task is claimed, its status transitions to ``RUNNING``, and the payload is dispatched to a thread pool.
* **Transactional Reliability**: Because queue operations participate in database transactions, a task is never enqueued unless the surrounding database transaction successfully commits.

Key Data Models
~~~~~~~~~~~~~~~

* **``TaskRecord``**: Stores individual execution instances, positional/keyword arguments (JSON), priority, execution timestamps, duration in milliseconds, worker hostname, input/output token counts, and full error tracebacks if failed.
* **``ScheduledTask``**: Defines recurring jobs using standard 5-field cron expressions (e.g., ``0 21 * * *`` for nightly 9 PM runs) or fixed interval seconds. Replaces Celery Beat.
* **``WorkerHeartbeat``**: Tracks active worker processes, active thread counts, and last-seen timestamps to enable stale worker detection and automatic failover.

Management Commands
~~~~~~~~~~~~~~~~~~~

The task system is managed via two core CLI commands:

.. code-block:: bash

    # Start the multi-threaded background worker
    python manage.py runtaskworker --threads 4 --queue default

    # Start the periodic cron and interval scheduler
    python manage.py runtaskscheduler

Defining Tasks
~~~~~~~~~~~~~~
Tasks are declared using the ``@task`` decorator:

.. code-block:: python

    from verbal_tasks.task_backend import task

    @task(queue_name="default", priority=10)
    def ingest_document_async(document_id: int):
        # Heavy processing logic...
        return {"status": "complete", "document_id": document_id}

    # Calling synchronously:
    result = ingest_document_async(document_id=42)

    # Dispatching to the database background queue:
    task_record = ingest_document_async.enqueue(document_id=42)


3. Observability & Health Signals
---------------------------------

How to Know the Task System is Working Well
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Active Worker Heartbeats**:
   In Django Admin under **Verbal Tasks > Worker Heartbeats**, healthy worker instances report active heartbeats every 10 seconds. The status badge displays green: ``🟢 Active``.
2. **Low Queue Latency**:
   The ``TaskRecord.queue_latency_formatted`` property measures the time between when a task was enqueued and when a thread picked it up. In a healthy system, queue latency is consistently below 1 second.
3. **Admin Task Telemetry**:
   Every completed task record logs execution duration and LLM token usage (input, output, and total tokens). The change list view allows administrators to filter by queue, status, priority, and task function to verify throughput.


4. Diagnostic Tips & Failure Modes
----------------------------------

When the System Misses the Mark & How to Tune
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Tasks Remain Stuck in ``READY``**:
  * *Cause*: The worker process is not running, or is listening to a different queue name.
  * *Check*: Verify that ``python manage.py runtaskworker`` is actively running in a terminal or daemon service. Check the task's ``queue_name`` in the admin to ensure the worker is subscribed to it.
* **Tasks Remain Stuck in ``RUNNING`` (Zombies)**:
  * *Cause*: The worker process was killed abruptly (e.g. machine reboot or `kill -9`) while executing a job.
  * *Remedy*: The system automatically flags workers whose heartbeat is older than 60 seconds. In Django Admin, select the stuck task records and use the action **Reset Stuck Running Tasks to Ready** to release them back to the queue.
* **Worker Thread Starvation**:
  * *Cause*: All worker threads are blocked on synchronous external operations (e.g., long-running LLM API calls with high timeouts or frozen sandbox scripts).
  * *Remedy*: Increase worker thread pool capacity using ``--threads 8`` or run separate dedicated workers listening to distinct queues (e.g. ``web_interactive`` vs ``nightly_batch``).
* **Database Task Table Growth**:
  * *Remedy*: Over months of continuous study design, hundreds of thousands of completed records accumulate. Use the scheduled archival command to prune successful records older than 30 days while retaining failed records for debugging.


Module Reference
----------------

.. automodule:: verbal_tasks.models
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: verbal_tasks.task_backend
   :members:
   :undoc-members:
   :show-inheritance:
