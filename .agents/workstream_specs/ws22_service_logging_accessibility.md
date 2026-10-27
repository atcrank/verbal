# WS22: Service Logging Accessibility, Traceback Visibility & Dual Output Architecture

## Goal
Transform service logging across the Reason / Verbal platform to ensure operational transparency, immediate visibility of Python exceptions / tracebacks during startup and runtime failures, and friction-free log inspection from both local developer terminals and the target server.

---

## Architectural Problems Identified

1. **Hidden Tracebacks Behind Restart Churn**:
   Services were configured with `Restart=always` and `RestartSec=5s` without systemd burst rate limiting. If an application service encounters an unhandled exception or missing library during initialization (e.g. `sentence-transformers` / `spacy` import failure), it crashes and restarts indefinitely. Within 60 seconds, `systemctl status` fills with 10 lines of systemd restart boilerplate (`Scheduled restart job, restart counter is at 24`), completely obscuring the original Python traceback.

2. **Unbuffered Output Latency**:
   Python processes running under systemd block-buffer standard output by default unless `PYTHONUNBUFFERED=1` is explicitly set in the environment. This can cause in-flight log statements and tracebacks to be delayed or lost when a process terminates unexpectedly.

3. **Absence of Disk-Based Application Logs**:
   Although `/srv/reason/logs/` is created during provisioning, Django's `settings.LOGGING` was configured exclusively with a console `StreamHandler`. No logs were directed to disk. Operators expecting standard file-based logs at `/srv/reason/logs/` or `/var/log/reason/` found empty directories.

4. **Missing Deployment CLI Tooling for Logs**:
   Operators had to manually construct complex `journalctl` invocations (`sudo journalctl -u reason-inference.service -n 100 --no-pager`) over SSH rather than having a high-level `fab logs` deployment command.

---

## Solution Design

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Django Application                              │
│  - Environment: PYTHONUNBUFFERED=1                                     │
│  - Django LOGGING: StreamHandler (Console) + RotatingFileHandler       │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │ stdout / stderr                │ /srv/reason/logs/
                    ▼                                ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│       systemd-journald               │  │    /srv/reason/logs/         │
│  - StartLimitIntervalSec=60s         │  │    (and /var/log/reason/     │
│  - StartLimitBurst=5                 │  │     symlink)                 │
│  - Restart=on-failure                │  │  - reason-web.log            │
│  - Retains fatal crash tracebacks    │  │  - reason-inference.log      │
│    without infinite restart churn    │  │  - reason-worker.log         │
└───────────────────┬──────────────────┘  │  - reason-scheduler.log      │
                    │                     │  - reason-llamacpp.log       │
                    ▼                     │  - 10MB rotation, 5 backups  │
┌──────────────────────────────────────┐  └──────────────────────────────┘
│     fab logs CLI Command             │
│  - fab logs -s reason-inference      │
│  - fab logs -s reason-web -f         │
│  - fab logs --errors                 │
└──────────────────────────────────────┘
```

---

## Key Files & Scope of Work

| File | Scope of Work |
| :--- | :--- |
| `verbal_config/settings.py` | Add `RotatingFileHandler` to Django `LOGGING` targeting `REASON_LOG_DIR` (`/srv/reason/logs`), dynamically named per `VERBAL_ROLE` (`reason-{role}.log`), with automatic 10MB rotation and 5 backups. |
| `deploy/fabric_deploy.py` | 1. In `generate_systemd_units`: Add `PYTHONUNBUFFERED=1` to environment directives; add `StartLimitIntervalSec=60s`, `StartLimitBurst=5`, and `Restart=on-failure` to prevent restart spam.<br>2. In `provision_system`: Create symlink `/var/log/reason -> /srv/reason/logs` for standard filesystem conventions.<br>3. Implement `@task def logs(c, service='all', lines=50, follow=False, errors_only=False)` task. |
| `fabfile.py` | Expose the new `logs` task. |
| `deploy/tests_deploy.py` / `tests.py` | Add unit tests verifying `generate_systemd_units` output, log file path resolution, and `fab logs` arguments. |

---

## Implementation Steps

### Phase 1: Django Logging Engine Configuration
- Inspect `REASON_LOG_DIR` or fallback to `/srv/reason/logs`.
- If directory exists and is writable (or in prod mode), instantiate a `logging.handlers.RotatingFileHandler`.
- Attach the file handler to root logger, `django`, `llm_api`, `metacognition`, `demo_ui`, and `background_resources`.
- Ensure console logger remains active for journald ingestion.

### Phase 2: Systemd Unit Hardening in Fabric
- In `deploy/fabric_deploy.py`:
  - Update `env_directives` to include `Environment=PYTHONUNBUFFERED=1`.
  - Update `web_unit`, `worker_unit`, `scheduler_unit`, `inference_unit`, and `llamacpp_unit`:
    - Add `StartLimitIntervalSec=60s` and `StartLimitBurst=5` to `[Unit]`.
    - Change `Restart=always` to `Restart=on-failure`.
- Update `provision_system` to ensure `/srv/reason/logs` has correct permissions and `/var/log/reason` symlinks cleanly to `/srv/reason/logs`.

### Phase 3: Fabric `logs` CLI Command
- Implement `@task def logs(c, service="all", lines=50, follow=False, errors_only=False)`.
- Support targeting specific units (`web`, `inference`, `worker`, `scheduler`, `llamacpp`, or full names like `reason-inference`).
- Format journalctl flags (`-n <lines>`, `-f`, `-p err..alert`, `--no-pager`).
- Provide fallback to reading disk files in `/srv/reason/logs/` if journalctl is unavailable.
- Register task in `fabfile.py` and `task_map`.

### Phase 4: Validation & Review
- Test unit generation doctests and functions.
- Verify logging settings with mock log directory.
- Review for PEP 8 compliance.
