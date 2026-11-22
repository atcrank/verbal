# WS40: Database Password and Secrets Unification Across Containers, Instances, and Deploy Scripts

## Context & Objectives
In previous deployments, `DB_PASSWORD` was not consistently loaded across all application tiers. When a custom password was set in `/srv/reason/app/.env.secrets` and the database container was recreated, a split-brain occurred:
1. **Instances** (`reason-web.service`, `reason-worker.service`): Systemd read `/srv/reason/app/.env.secrets` via `EnvironmentFile=-...` and set `DB_PASSWORD` to the custom secret.
2. **Containers** (`verbal_db`): `docker-compose.yml` mapped `POSTGRES_PASSWORD: ${DB_PASSWORD:-verbal_password}`, but Docker Compose by default only loads `.env`, and `reason-docker.service` lacked `EnvironmentFile`. Thus `verbal_db` was initialized with the default `verbal_password`.
3. **Deploy Scripts & Django CLI** (`manage.py`, `fab deploy`): `verbal_config/settings.py` only called `load_dotenv(BASE_DIR / ".env")`, ignoring `.env.secrets` entirely during CLI/migration runs.

This workstream unifies secrets loading across all three tiers so that values in `.env.secrets` take precedence consistently everywhere.

---

## Scope of Work

### Phase 1: Django Settings Dotenv Loading
- In `verbal_config/settings.py`, load `.env.secrets` with `override=True` after `.env`, and optionally `.env.production` in production mode.
- Guarantees that any Django invocation (`manage.py`, Granian, task workers, tests) reads `.env.secrets`.

### Phase 2: Systemd Docker Infrastructure Unit
- In `deploy/fabric_deploy.py`, add `EnvironmentFile=-{env_app_file}`, `EnvironmentFile=-{env_app_secrets}`, and `EnvironmentFile=-{env_app_prod}` to `docker_unit` (`reason-docker.service`).
- Ensures that when systemd runs `/usr/bin/docker compose up -d ...`, `docker compose` inherits `DB_PASSWORD` directly in its environment.

### Phase 3: Docker Startup in Fabric Deploy & Shell Scripts
- In `start_backend_containers(c)` in `deploy/fabric_deploy.py`, ensure `docker compose` is executed with `--env-file` targeting `.env` and `.env.secrets` (if present) or environment variables exported.
- In `docker-compose.yml`, declare `env_file:` for the `db` service.
- In `start_background_services.sh`, source `.env.secrets` if present.

### Phase 4: Automated Testing & Verification
- Add unit tests in `deploy/tests.py` verifying:
  - `reason-docker.service` has `EnvironmentFile` directives for `.env` and `.env.secrets`.
  - Settings dotenv loading correctly layers `.env.secrets` over `.env`.
  - Fabric's container startup includes `.env.secrets` when present.
- Run full `./scripts/pre_push_check.sh`.

---

## Status & Results
- **Phase 1 Complete**: `verbal_config/settings.py` updated to load `.env.secrets` and `.env.production` while preserving pre-existing process environment overrides.
- **Phase 2 Complete**: `reason-docker.service` updated with `EnvironmentFile=-{env_app_file}`, `EnvironmentFile=-{env_app_secrets}`, and `EnvironmentFile=-{env_app_prod}`.
- **Phase 3 Complete**: `deploy/fabric_deploy.py` propagates `DB_PASSWORD` during `start_backend_containers`, `docker-compose.yml` configures `env_file:` for the `db` service, and shell scripts source `.env.secrets`.
- **Phase 4 Complete**: Unit tests added in `deploy/tests.py` and passed. Pre-push check running.
