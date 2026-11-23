# Workstream 41: Fabric Deployment Django Environment Export & Secrets Propagation

## Context & Motivation
During `fab setup-all` (and standard deployments), Django migrations were failing with a database password error:
`django.db.utils.OperationalError: password authentication failed for user "verbal_user"`

Investigation revealed multiple subtle architectural flaws across the deployment pipeline:
1. **Bash Prefix Scoping Trap in Container Startup**:
   In `deploy/fabric_deploy.py:start_backend_containers`:
   `sudo_bash(c, f"{env_prefix}cd {APP_DIR} && docker compose up -d db sandbox", warn=True)`
   In bash, inline variable prefixes (`VAR=val cmd1 && cmd2`) only set `VAR` in the execution environment of `cmd1` (`cd`), NOT `cmd2` (`docker compose`). As a result, `docker compose` was executed without `DB_PASSWORD`, causing it to interpolate `${DB_PASSWORD:-verbal_password}` with the default fallback value.
2. **Environment Wiping in `run_as_deploy_user`**:
   `run_as_deploy_user` runs `su - {DEPLOY_USER} -c '{escaped}'`. The `-` flag explicitly initiates a clean login shell that wipes all exported environment variables from the parent shell.
3. **Missing Environment Ingestion in Django CLI Tasks**:
   Phase 4 and Phase 5 commands in `fabric_deploy.py` (`backup_database`, `build_and_migrate`, `check_or_create_superuser`, and standalone `createsuperuser`) invoked `{VENV_DIR}/bin/python {APP_DIR}/manage.py ...` without sourcing `.env`, `.env.secrets`, or `.env.production`. Commands like `manage.py migrate --noinput` did not even set `VERBAL_ROLE=web`.
4. **Shared vs App Directory Secrets**:
   `verbal_config/settings.py` only looked for `.env.secrets` in `BASE_DIR / ".env.secrets"`. However, deployed setups often place shared secrets in `/srv/reason/shared/.env.secrets`.

## Goals
1. Introduce a unified `run_django_as_deploy_user` helper in `deploy/fabric_deploy.py`:
   - Automatically sources `{SHARED_DIR}/.env`, `{SHARED_DIR}/.env.secrets`, `{APP_DIR}/.env`, `{APP_DIR}/.env.secrets`, and `{APP_DIR}/.env.production`.
   - Propagates explicit environment variables from Fabric's `get_remote_env(c)` (`DB_PASSWORD`, `DB_USER`, `DB_NAME`, `DB_PORT`, `DJANGO_SECRET_KEY`, `VERBAL_ROLE=web`).
2. Refactor all `manage.py` calls in `deploy/fabric_deploy.py` to use `run_django_as_deploy_user`.
3. Fix the bash scoping bug in `start_backend_containers` so `DB_PASSWORD` and container parameters are passed directly to `docker compose`.
4. Update `verbal_config/settings.py` to also load secrets from `/srv/reason/shared/` when present.
5. Include `sync_env` in `setup_all` if configuration has not yet been placed on remote.
6. Provide comprehensive unit tests covering all modified behaviors.
