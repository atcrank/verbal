# Workstream 25: Docker CE Defaulting & Step Skipping/Deferral in `setup-all` / `deploy`

## 1. Objective
1. **Docker CE (`docker-ce`, `docker-ce-cli`, `docker-compose-plugin`) Default & Compatibility**:
   - Detect existing Docker Compose installations accurately across both user and root contexts (`docker compose version`, `sudo docker compose version`, `/usr/libexec/docker/cli-plugins/docker-compose`, `/usr/lib/docker/cli-plugins/docker-compose`, `which docker-compose`, and `dpkg -s docker-compose-plugin`) so hosts with `docker-ce` + `docker-compose-plugin` never trigger a conflicting `docker-compose-v2` installation.
   - When installing Docker or Compose on a blank Ubuntu system, default to the official Docker CE stack (`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin`), automatically bootstrapping Docker's official `apt` repository if needed, and falling back to `docker.io` / `docker-compose-v2` only when Docker CE packages are unavailable and `docker-ce` is not installed.
   - Decouple `apt-get update` from `apt-get install -y` and run under `sudo_bash` with `DEBIAN_FRONTEND=noninteractive` so minor apt repository warnings or debconf prompts never prevent package installation (such as `cmake`).
2. **Step Skipping, Resuming, and Deferral in `setup-all` and `deploy`**:
   - Add `--skip` (comma-separated step names/aliases, e.g. `--skip=preflight,nginx` or `--skip=apt`), `--start-at` (resume pipeline from a specific step, e.g. `--start-at=deploy`), and `--continue-on-error` to `setup_all`, `deploy`, and `preflight_check`.
   - When a step triggers an `Exit` during `setup_all`, allow deferring the step via `--continue-on-error` (or interactive operator confirmation when running in an interactive terminal) and print an actionable summary of deferred steps at completion.

## 2. API & Signature Changes (`deploy/fabric_deploy.py`)
- `ensure_docker_ce_repo(c)`: Ensures `/etc/apt/keyrings/docker.asc` and `/etc/apt/sources.list.d/docker.list` are configured if Docker CE packages are needed and not yet in `apt-cache`.
- `preflight_check(c, skip_apt=False, continue_on_error=False)`:
  - Checks Docker and Docker Compose via multi-path detection (`docker compose version` as user/root, `/usr/libexec/docker/cli-plugins/docker-compose`, `dpkg -s docker-compose-plugin`).
  - Prefers `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, `docker-compose-plugin` over `docker.io` / `docker-compose-v2`.
  - Runs `DEBIAN_FRONTEND=noninteractive apt-get update -y` separately from `DEBIAN_FRONTEND=noninteractive apt-get install -y ...` via `sudo_bash`.
- `run_pipeline_steps(steps, skip=None, start_at=None, continue_on_error=False)`:
  - Helper that filters steps by `skip` and `start_at`, executes each step, and handles `Exit` exceptions by either deferring (when `continue_on_error=True` or operator confirms interactively) or re-raising.
- `setup_all(c, ..., skip=None, start_at=None, continue_on_error=False)` and `deploy(c, ..., skip=None, start_at=None, continue_on_error=False)`.

## 3. Verification Plan
- Unit tests in `deploy/tests.py`:
  - Verify `preflight_check` recognizes existing `docker-ce` + `docker-compose-plugin` (even when unprivileged `docker compose version` fails) and never installs `docker-compose-v2`.
  - Verify `preflight_check` installs `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin` when missing on a blank system.
  - Verify `setup_all` and `deploy` support `--skip`, `--start-at`, and `--continue-on-error` to skip or defer steps that raise `Exit`.

