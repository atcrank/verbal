Production Deployment & Operations Guide
========================================

This guide provides an end-to-end operational manual for deploying, supervising, and maintaining **Reason** (the Verbal cognitive architecture) in production environments.

.. contents:: Table of Contents
   :local:
   :depth: 2


1. Quickstart: Bottom Line Up Front (BLUF)
------------------------------------------

For operators and sysadmins managing an active production instance, these are the essential daily operational controls.

Daily Operations Cheatsheet
~~~~~~~~~~~~~~~~~~~~~~~~~~~

All application commands run under `systemd` as the dedicated unprivileged system user ``reason``, while supporting backend containers run via Docker Compose supervised by ``reason-docker.service``.

The entire stack can be managed using the master systemd unit ``reason.service``.

* **Check Overall System Health**:

  .. code-block:: bash

     # Check status of the entire production stack (master service + all units)
     sudo systemctl status reason

     # Check status of individual application and docker services
     sudo systemctl status reason-docker reason-web reason-worker reason-scheduler reason-inference

     # Check status of Docker infrastructure containers (Postgres, Sandbox, Grobid)
     cd /srv/reason/app && sudo docker compose --profile academic ps

     # Run automated multi-service health validation via Fabric
     fab health-check

* **Start the Entire Production Stack**:

  .. code-block:: bash

     # Single command brings up Docker Compose containers and all application units:
     sudo systemctl start reason

* **Restart the Stack or Application Services**:

  .. code-block:: bash

     # Restart the entire stack (including Docker containers):
     sudo systemctl restart reason

     # Or restart only the application services (retaining running Docker DB / Grobid):
     sudo systemctl restart reason-web reason-worker reason-scheduler reason-inference

* **Stop the Production Stack**:

  .. code-block:: bash

     # Cleanly stops all application processes and stops Docker Compose containers:
     sudo systemctl stop reason

* **Inspect Live Streaming Logs**:

  .. code-block:: bash

     # Web server (Granian ASGI HTTP & WebSocket traffic)
     journalctl -u reason-web -f

     # Task worker (Background jobs, async tool runs, Grobid PDF ingestion)
     journalctl -u reason-worker -f

     # Task scheduler (NightManager periodic routines)
     journalctl -u reason-scheduler -f

     # Inference server (Local LLM generation & tokenizer logs)
     journalctl -u reason-inference -f

* **Database Snapshot & Backup**:

  .. code-block:: bash

     # Triggers a pre-migration gzipped database dump into /srv/reason/backups
     fab backup-database


2. Production Architecture & Deployment Structure
-------------------------------------------------

Reason uses a two-tier operational architecture: **Docker Compose** provides isolated containerized infrastructure, while **Systemd** supervises native, hardened Python application processes running behind an **Nginx** reverse proxy.

High-Level Architecture
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   Internet / Corporate Network
               │
               ▼  (Ports 80, 443 with TLS Termination)
   ┌────────────────────────────────────────────────────────┐
   │                   Nginx Reverse Proxy                  │
   │  - Serves static assets directly (/srv/reason/static)  │
   │  - Enforces HTTPS, HSTS, Rate Limiting & SSL Ciphers   │
   └───────────┬────────────────────────────────────────────┘
               │
               ▼  (HTTP reverse proxy to 127.0.0.1:8008)
   ┌────────────────────────────────────────────────────────┐
   │    Systemd Master Umbrella: [reason.service]           │
   ├────────────────────────────────────────────────────────┤
   │  Application Tier (Hardened unprivileged user 'reason')│
   │  - [reason-web.service]       (Granian ASGI Server)    │
   │  - [reason-worker.service]    (Django Task Worker)     │
   │  - [reason-scheduler.service] (NightManager Beat)      │
   │  - [reason-inference.service] (Local AI Model Service) │
   ├────────────────────────────────────────────────────────┤
   │  Infrastructure Tier (Supervised Docker Compose)       │
   │  - [reason-docker.service]    (Runs compose up/stop)   │
   └───────────┬────────────────────────────────────────────┘
               │
               ▼  (Internal localhost container networking)
   ┌────────────────────────────────────────────────────────┐
   │         Docker Compose Infrastructure (Tier 1)         │
   │                                                        │
   │  verbal_db      (PostgreSQL 18 + PGVector on :5433)    │
   │  verbal_sandbox (Python Code Execution on :8002)       │
   │  verbal_grobid  (Academic PDF TEI Parser on :8070)     │
   └────────────────────────────────────────────────────────┘

The Systemd Service Units & Master Stack
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All units are generated by :func:`deploy.fabric_deploy.generate_systemd_units` and installed to ``/etc/systemd/system/``:

1. **``reason.service``**:
   The top-level master stack service (``Type=oneshot``, ``RemainAfterExit=yes``). Orchestrates all child units. Operators can control the full stack with ``sudo systemctl start reason``, ``sudo systemctl stop reason``, ``sudo systemctl restart reason``, and ``sudo systemctl enable reason``.
2. **``reason-docker.service``**:
   Supervises Docker Compose infrastructure lifecycle. Defined with ``Type=oneshot`` and ``RemainAfterExit=yes``, executing ``docker compose up -d db sandbox`` followed by optional ``docker compose --profile academic up -d grobid`` on startup, and ``docker compose stop`` on shutdown. Application services declare ``Wants=reason-docker.service`` and ``After=reason-docker.service`` so they automatically start after Docker is initialized without hard cascading lockouts if an optional container encounters an issue.
3. **``reason-web.service``**:
   Executes ``granian --interface asgi --host 127.0.0.1 --port 8008 verbal_config.asgi:application``. Configured with ``VERBAL_ROLE=web`` so that heavy LLM models are **never** loaded into memory on web worker processes.
4. **``reason-worker.service``**:
   Executes ``python manage.py runtaskworker``. Consumes asynchronous tasks from the PostgreSQL-backed task queue, including heavy document parsing, batch embedding jobs, and tool execution. Configured with ``VERBAL_ROLE=worker``.
5. **``reason-scheduler.service``**:
   Executes ``python manage.py runtaskscheduler``. Runs periodic maintenance routines, cleanup jobs, and NightManager scheduled evaluations. Configured with ``VERBAL_ROLE=scheduler``.
6. **``reason-inference.service``**:
   Executes ``python manage.py runserver --noreload 127.0.0.1:8001``. Dedicated local model inference engine running with ``VERBAL_ROLE=inference`` to hold model weights in GPU VRAM without reload duplication.

Security & Sandboxing Directives
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Every systemd unit enforces strict Linux kernel privilege reduction:

* ``NoNewPrivileges=yes``: Prevents privilege escalation via setuid binaries.
* ``ProtectSystem=strict``: Mounts the entire OS filesystem as strictly read-only for the services.
* ``ProtectHome=yes``: Completely hides ``/root`` and ``/home`` from application processes.
* ``PrivateTmp=yes``: Isolates ``/tmp`` so processes cannot observe or tamper with other system tempfiles.
* ``ReadWritePaths=/srv/reason/shared /srv/reason/cache /srv/reason/logs``: Explicitly restricts filesystem write access exclusively to designated shared application directories.

Filesystem Directory Layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~

In production, all files are anchored under ``/srv/reason``:

=========================  ==============================================================
Path                       Purpose
=========================  ==============================================================
``/srv/reason/app``        Active git clone / checkout of the Reason codebase.
``/srv/reason/shared``     Persistent shared data: ``.env``, ``.env.secrets``, ``media/``,
                           and user workspaces (persists across code deployments).
``/srv/reason/venv``       Isolated Python 3.12+ virtual environment.
``/srv/reason/static``     Consolidated static assets collected via ``collectstatic``
                           (read-only access granted to Nginx group ``www-data``).
``/srv/reason/logs``       Application log directory for access and error logs.
``/srv/reason/backups``    Pre-migration database snapshots (retains last 10 snapshots).
``/srv/reason/cache``      Shared cache directory for Hugging Face and model weights.
=========================  ==============================================================

Docker Compose Infrastructure & Profiles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Backend containers are defined in ``docker-compose.yml`` and bound exclusively to loopback (``127.0.0.1``):

* **``db``** (PostgreSQL 18 + ``pgvector``):
  Runs on port ``5433`` to prevent port collisions with any existing host PostgreSQL installation on port 5432.
* **``sandbox``** (Python Code Runner):
  Isolated container on port ``8002`` executing analyst and model scripts with restricted RAM (1GB) and OpenBLAS thread throttling.
* **``grobid``** (Academic Profile):
  Active when using ``--profile academic``. Runs the GROBID TEI XML parser on port ``8070``. Used by ``grobid_client`` for scientific papers, citation graph construction, and figure bounding-box extraction.
* **``ollama`` / ``vllm``** (Inference Profile):
  Active when using ``--profile inference`` on systems utilizing external containerized GPU inference.


3. Automated Deployment with Fabric
-----------------------------------

The deployment workflow is automated via Fabric 3.x using `deploy/fabric_deploy.py` and the root `fabfile.py`.

Prerequisites
~~~~~~~~~~~~~

To execute deployments from your local machine:

.. code-block:: bash

   pip install fabric

Ensure your SSH user on the target machine has passwordless `sudo` rights or that you supply the sudo password when prompted.

Full Server Bootstrap (``setup-all``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For a brand-new, freshly-installed Linux host (Ubuntu 22.04+ recommended), run the complete bootstrap pipeline:

.. code-block:: bash

   # Using fab CLI:
   fab setup-all -H user@production.example.com --domain=reason.andrew.com

   # Or via Python entrypoint:
   python fabfile.py setup-all production.example.com

What ``setup-all`` executes sequentially:
1. **``preflight_check``**: Validates target OS architecture, disk space, and essential tooling.
2. **``provision_system``**: Creates system user ``reason``, group ``www-data``, and provisions all ``/srv/reason/`` directory trees.
3. **``setup_nginx_and_ssl``**: Generates hardened Nginx virtual host configurations, installs security headers, and binds TLS certificates.
4. **``setup_systemd``**: Renders and writes the 6 systemd units (``reason.service``, ``reason-docker.service``, and the 4 application services), runs ``daemon-reload``, and enables them for boot startup.
5. **``deploy``**: Triggers the initial code pull, container startup, migration run, and service activation.

Routine Application Deployments (``deploy``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For daily updates, code pushes, and routine releases, use the ``deploy`` task:

.. code-block:: bash

   fab deploy -H user@production.example.com --branch=main

The deployment pipeline executes with zero downtime using the following sequence:

1. **Preflight Verification**: Confirms SSH access, sudo privileges, and target system availability.
2. **Code Synchronization (``update_code``)**: Fetches updates from Git (or synchronizes a local directory if using ``--local-source``).
3. **Backend Container Startup (``start_backend_containers``)**:
   * Launches ``db``, ``sandbox``, and (if ``ENABLE_GROBID=true``) the ``academic`` Grobid container.
   * Polls ``pg_isready`` with retries to guarantee PostgreSQL is actively accepting connections before touching database state.
4. **Build & Maintenance (``build_and_migrate``)**:
   * Resolves and verifies vendored wheel dependencies (e.g. ``en_core_web_sm`` spaCy model).
   * Runs ``pip install`` with wheel link caching.
   * Runs ``python manage.py check --deploy`` to verify production settings.
   * Creates an automated, verified pre-migration database snapshot in ``/srv/reason/backups/``.
   * Executes ``python manage.py migrate --noinput``.
   * Checks for or prompts superuser creation.
   * Runs ``collectstatic`` directly into ``/srv/reason/static`` and tightens permissions (``0755`` dirs, ``0644`` files).
5. **Process Restart (``restart_services``)**:
   Gracefully restarts the application services via ``systemctl restart reason-web reason-worker reason-scheduler reason-inference`` (or the full stack via ``systemctl restart reason``).
6. **Health Validation (``health_check``)**:
   Polls ``systemctl is-active`` across all application units and executes an HTTP check against the web frontend until HTTP 200/302 is received.

Granular Fabric Task Reference
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Individual tasks can be called independently for targeted operations:

===========================  ==============================================================
Fabric Task                  Description
===========================  ==============================================================
``fab preflight-check``      Validates remote server readiness and prerequisites.
``fab sync-env``             Copies local ``.env`` and ``.env.secrets`` to ``/srv/reason/app/``.
``fab backup-database``      Creates an immediate gzipped JSON ORM dump in ``/srv/reason/backups/``.
``fab build-and-migrate``    Installs Python dependencies and runs Django migrations.
``fab restart-services``     Restarts the supervised systemd service units.
``fab health-check``         Probes systemd unit status and tests HTTP frontend responsiveness.
``fab setup-systemd``        Re-generates and registers systemd unit files and master service.
``fab setup-nginx-and-ssl``  Updates Nginx virtual host configuration and reloads Nginx.
===========================  ==============================================================

Air-Gapped & Custom Mirror Options
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When deploying into corporate or air-gapped environments, pass custom mirror flags to the deployment command:

.. code-block:: bash

   fab deploy \
     --find-links="https://artifactory.local/artifactory/wheels" \
     --index-url="https://artifactory.local/api/pypi/simple" \
     --trusted-host="artifactory.local" \
     --pytorch-index-url="https://artifactory.local/api/pypi/pytorch-wheels"


4. Configuration & "Bring Your Own Secrets"
-------------------------------------------

Reason enforces a strict separation of code and configuration. In production (``VERBAL_ENV=prod``), the system refuses to boot if default or insecure credentials are used.

Application Environment Configuration & Preservation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Reason stores configuration files directly within the application directory (``/srv/reason/app/``).

* **Preservation Guarantee**:
  During deployments (``fab deploy``), any physical ``.env``, ``.env.secrets``, ``.env.production``, or ``.env.deploy`` files present in ``/srv/reason/app/`` are **strictly preserved** and never overwritten by git pulls or deleted by rsync (protected via ``--exclude='.env*'``).
* **Systemd Loading Hierarchy**:
  Systemd units evaluate environment files directly from the application root:
  1. ``/srv/reason/app/.env``
  2. ``/srv/reason/app/.env.secrets``
  3. ``/srv/reason/app/.env.production``
  Later files override earlier ones, allowing production and local secrets overrides to take effect cleanly without complex symlinks.

Required Environment Files
~~~~~~~~~~~~~~~~~~~~~~~~~~

Operators **must supply their own production configuration files** placed in ``/srv/reason/shared/`` or ``/srv/reason/app/``:

1. **``/srv/reason/shared/.env`` or ``/srv/reason/app/.env``**:
   Contains non-secret operational parameters:

   .. code-block:: bash

      VERBAL_ENV=prod
      DJANGO_ALLOWED_HOSTS=reason.example.com,127.0.0.1,localhost
      DJANGO_CSRF_TRUSTED_ORIGINS=https://reason.example.com
      DB_PASSWORD=your_secure_database_password_here
      DB_PORT=5433
      ENABLE_GROBID=true
      INFERENCE_URL=http://127.0.0.1:8001/api/llm
      SANDBOX_URL=http://127.0.0.1:8002/execute

2. **``/srv/reason/shared/.env.secrets`` or ``/srv/reason/app/.env.secrets``**:
   Contains cryptographic keys and API tokens. This file must be locked down to file permissions ``0640`` (or ``0600``):

   .. code-block:: bash

      DJANGO_SECRET_KEY=your-high-entropy-random-secret-key-here
      HUGGING_FACE_HUB_TOKEN=hf_...
      OPENAI_API_KEY=sk-...

3. **``.env.production`` or ``.env.deploy``** (Local Operator Machine or Server):
   Optional environment files containing deployment overrides (such as ``PIP_INDEX_URL`` or ``SPACY_WHEEL_URL``) used by Fabric during deployment.

Enforced Production Invariants
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``VERBAL_ENV=prod`` is set in `verbal_config/settings.py`:

* **Insecure Secret Key Rejection**:
  If ``DJANGO_SECRET_KEY`` is empty or starts with ``django-insecure-``, an :class:`django.core.exceptions.ImproperlyConfigured` exception is thrown immediately.
* **Default Database Password Rejection**:
  If ``DB_PASSWORD`` equals ``verbal_password``, startup is aborted.
* **Standalone Role Rejection**:
  ``VERBAL_ROLE=standalone`` is prohibited in production. Services must declare an explicit role (``web``, ``worker``, ``scheduler``, or ``inference``).
* **Cookie Hardening**:
  ``SESSION_COOKIE_SECURE = True`` and ``CSRF_COOKIE_SECURE = True`` are enforced host-wide.


5. TLS/SSL Certificates: "Bring Your Own Certificates"
------------------------------------------------------

Nginx acts as the TLS termination endpoint. In production, **you must supply valid, trusted SSL/TLS certificates**.

Certificate File Locations
~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, Nginx looks for certificates matching your domain or the fallback application name:

* **Certificate / Full Chain**: ``/etc/ssl/certs/reason.crt`` (or ``/etc/ssl/certs/<your_domain>.crt``)
* **Private Key**: ``/etc/ssl/private/reason.key`` (or ``/etc/ssl/private/<your_domain>.key``)

Key Security & Permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~

Private keys must be strictly protected:

.. code-block:: bash

   sudo chown root:ssl-cert /etc/ssl/private/reason.key
   sudo chmod 0600 /etc/ssl/private/reason.key
   sudo chmod 0644 /etc/ssl/certs/reason.crt

Testing / Internal Staging Certificates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If deploying to an internal test or staging host where trusted CA certificates are not yet issued, Fabric can provision a temporary self-signed certificate:

.. code-block:: bash

   fab setup-nginx-and-ssl --domain=reason.local


6. Troubleshooting & Failure Modes
----------------------------------

PostgreSQL Readiness, Version Compatibility & Port Collisions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: Migrations fail with ``connection to server at "127.0.0.1", port 5433 failed: Connection refused``, or ``verbal_db`` container enters a crash loop restarting every 20 seconds.
* **Cause 1: Container Still Starting**: The container is initializing. Inspect logs via ``docker logs verbal_db``.
* **Cause 2: PostgreSQL Major Version Incompatibility**:
  PostgreSQL data directories are strictly locked to the major version that initialized them. If the ``postgres_data`` volume was initialized by PostgreSQL 16 (``pgvector/pgvector:pg16``), running an image with a different major version (such as PostgreSQL 18) will trigger a fatal error:

  .. code-block:: text

     FATAL: database files are incompatible with server
     DETAIL: The data directory was initialized by PostgreSQL version 16, which is not compatible with this version 18.
     HINT: You must either run "pg_upgrade" or dump/restore the data directory to upgrade...

  **Resolution**:
  - Keep the container image set to ``DOCKER_POSTGRES_IMAGE=pgvector/pgvector:pg16`` in your ``.env`` and ``docker-compose.yml`` (with volume mount ``postgres_data:/var/lib/postgresql/data``).
  - Alternatively, if setting up a fresh deployment or staging server without data to preserve, reset the volume:

    .. code-block:: bash

       cd /srv/reason/app
       sudo docker compose stop db
       sudo docker volume rm reason_postgres_data
       sudo docker compose up -d db

* **Diagnostic**:

  .. code-block:: bash

     docker ps -a --filter name=verbal_db
     docker logs verbal_db
     docker exec verbal_db pg_isready -U verbal_user -d verbal_db

* **Note on Port 5432**: Standard host PostgreSQL uses 5432. The Reason Docker container is mapped to ``5433:5432`` to avoid collisions. Ensure ``DB_PORT=5433`` is set in your ``.env``.

Grobid Academic PDF Parser Offline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: Uploaded PDFs extract raw unformatted text without citations or figure crops; logs warn ``Grobid unavailable or blocked by security policies``.
* **Diagnostic**:

  .. code-block:: bash

     # Check container status
     docker ps -a --filter name=verbal_grobid

     # Test GROBID health probe
     curl -s http://127.0.0.1:8070/api/isalive
     # Expected output: true

     # Test Python circuit breaker
     /srv/reason/venv/bin/python -c "from grobid_client.api import is_grobid_available; print(is_grobid_available())"

* **Remedy**:
  Grobid requires approximately **1.5 GB – 3 GB RAM**. If the container was terminated by the Linux OOM (Out Of Memory) killer, inspect ``dmesg -T | grep -i oom`` and increase host RAM or swap space. Start the container with:

  .. code-block:: bash

     cd /srv/reason/app && sudo docker compose --profile academic up -d grobid

Code Execution Sandbox Architecture & Troubleshooting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: Analyst script runs report ``[EXECUTE_SCRIPT Error] Code execution sandbox is offline``, or the container logs ``cannot start virtual environment`` and restarts repeatedly.
* **Architecture & Isolation Design**:
  The hardened ``sandbox/Dockerfile`` builds a clean virtual environment at ``/opt/sandbox`` with explicit executable invocation:
  1. Dependencies are installed directly into ``/opt/sandbox`` via ``/opt/sandbox/bin/pip install --no-cache-dir -r requirements.txt``.
  2. No shell virtualenv activation scripts (``activate``) are used; the server is launched explicitly via ``/opt/sandbox/bin/python -m uvicorn main:app``.
  3. Script execution uses ``sys.executable`` to guarantee that all child analyst processes execute under the dedicated sandbox interpreter and its installed scientific packages (pyAgrum, NetworkX, Pint, etc.).
  4. Runs as unprivileged user ``USER 1000:0`` with ``group_add: ["${WEB_GID:-33}"]`` in ``docker-compose.yml`` so the container can access mounted workspace volumes owned by the host web group.
  5. Clears any inherited base entrypoint with ``ENTRYPOINT []`` to avoid legacy S2I or container wrapper failures.
* **Diagnostic**:

  .. code-block:: bash

     docker ps -a --filter name=verbal_sandbox
     docker logs verbal_sandbox
     curl -s http://127.0.0.1:8002/health || curl -s http://127.0.0.1:8002/execute

* **Remedy**:
  Rebuild and restart the sandbox container:

  .. code-block:: bash

     cd /srv/reason/app && sudo docker compose build sandbox && sudo docker compose up -d sandbox

Nginx "502 Bad Gateway"
~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: Navigating to the Reason web portal returns an Nginx ``502 Bad Gateway`` error.
* **Cause**: Nginx is active, but the backend ASGI application server (``reason-web.service`` on ``127.0.0.1:8008``) is stopped, crashing, or unable to connect to the database.
* **Diagnostic**:

  .. code-block:: bash

     # Check reason-web service status and recent crash tracebacks
     sudo systemctl status reason-web
     sudo journalctl -u reason-web -n 50 --no-pager

     # Check if Granian is listening on port 8008
     sudo ss -tulpn | grep 8008

     # Check Docker backend containers that reason-web requires
     cd /srv/reason/app && sudo docker compose ps

* **Remedy**:
  Once the underlying issue in ``verbal_db`` or ``verbal_sandbox`` is resolved, restart the application services:

  .. code-block:: bash

     sudo systemctl restart reason

Systemd Service Resilience: ``Wants=`` vs ``Requires=``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Resilience Invariant**:
  All Reason application units declare ``Wants=reason-docker.service`` and ``After=reason-docker.service`` rather than ``Requires=``. This ensures the services wait for Docker infrastructure on boot, while preventing cascading startup lockouts across web, worker, and scheduler units if an optional container (such as Grobid) exits or fails.
* **Diagnostic**:

  .. code-block:: bash

     # Check reason-docker status and exit code
     sudo systemctl status reason-docker.service

     # Check detailed journal output from docker compose
     sudo journalctl -u reason-docker.service -n 50 --no-pager

* **Remedy**:
  1. Inspect container status via ``sudo docker compose -f /srv/reason/app/docker-compose.yml ps``.
  2. Clear any systemd failed unit locks and restart:

     .. code-block:: bash

        sudo systemctl reset-failed
        sudo systemctl restart reason-docker.service
        sudo systemctl restart reason

Host Network Neutrality
~~~~~~~~~~~~~~~~~~~~~~~

* **Core Operational Rule**:
  Deployment scripts and application tasks must **never** modify host network interfaces, ``systemd-networkd``, UFW, or iptables rules, nor restart host network daemons.
* **Container Interface Dynamics**:
  When Docker spins up containers, host ``veth*`` interfaces are automatically created and bridged by Docker's internal networking stack. Transient kernel log notices regarding virtual ethernet interfaces are normal and should not be addressed by tampering with host network configurations, which risks disconnecting operator SSH sessions.

Systemd Sandboxing "Permission Denied"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: Application crashes with ``PermissionError: [Errno 13] Permission denied`` when attempting to write to disk.
* **Cause**: ``ProtectSystem=strict`` prevents systemd services from writing outside authorized directories.
* **Remedy**:
  Ensure the process is attempting to write to ``/srv/reason/shared/``, ``/srv/reason/logs/``, or ``/srv/reason/cache/``. If a new persistent directory is introduced, add it to ``ReadWritePaths`` in :func:`deploy.fabric_deploy.generate_systemd_units` and re-run ``fab setup-systemd``.

Django ``ImproperlyConfigured`` on Startup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Symptom**: ``reason-web.service`` fails to start; ``journalctl -u reason-web`` shows ``ImproperlyConfigured: In production (VERBAL_ENV != 'dev'), a secure DJANGO_SECRET_KEY must be configured``.
* **Remedy**:
  Ensure ``/srv/reason/shared/.env.secrets`` exists and contains an actual secret key, and that ``/srv/reason/shared/.env`` contains your custom ``DB_PASSWORD``. Check that both files are readable by user ``reason``:

  .. code-block:: bash

     sudo chown -R reason:www-data /srv/reason/shared
     sudo chmod 0640 /srv/reason/shared/.env
     sudo chmod 0600 /srv/reason/shared/.env.secrets
