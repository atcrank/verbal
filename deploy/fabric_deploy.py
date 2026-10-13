"""
Fabric deployment pipeline for Verbal / Reason.

Provides modular, idempotent tasks for server provisioning, preflight checks,
SSL/Nginx configuration, systemd service management, pre-migration database backups,
and zero-downtime updates.

Usage examples:
    # Full bootstrap and initial deployment:
    fab -H localhost setup-all

    # Routine update / redeploy:
    fab -H localhost deploy

    # Individual modular tasks:
    fab -H localhost preflight-check
    fab -H localhost backup-database
    fab -H localhost setup-nginx-and-ssl
    fab -H localhost restart-services
    fab -H localhost health-check
"""

import getpass
import io
import os
import time
import uuid
from pathlib import Path
try:
    from fabric import task
    from invoke import Exit
    from invoke.watchers import StreamWatcher
except ImportError:
    # Graceful fallback to allow testing and configuration templating without fabric installed
    def task(f):
        return f

    class Exit(Exception):
        pass

    class StreamWatcher:
        def __init__(self):
            pass

# ==============================================================================
# ENVIRONMENT & CONFIGURATION LOADER
# ==============================================================================
def _parse_dotenv(target_path):
    """Parses a dotenv file into a dictionary of key-value pairs."""
    parsed = {}
    if not target_path or not Path(target_path).is_file():
        return parsed
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(target_path)
        return {k: v for k, v in vals.items() if v is not None}
    except ImportError:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key:
                        parsed[key] = val
        return parsed


def load_deploy_env(deploy_env_file=None, base_env_file=None):
    """
    Discovers and imports configuration for deployment:
    1. First imports application environment from .env (if present),
       providing DB settings, Django keys, and pip flags without duplication.
    2. Then imports deployment orchestration settings from .env.deploy
       (or .env.prod for backward compatibility).
    3. Preserves any variables already exported in the calling shell.
    """
    repo_root = Path(__file__).resolve().parent.parent
    cwd = Path.cwd()
    initial_keys = set(os.environ.keys())

    # 1. Base application environment (.env)
    base_candidates = [
        Path(base_env_file) if base_env_file else None,
        repo_root / ".env",
        cwd / ".env",
    ]
    loaded_base = None
    for p in base_candidates:
        if p and p.is_file():
            loaded_base = p.resolve()
            for k, v in _parse_dotenv(loaded_base).items():
                if k not in initial_keys:
                    os.environ[k] = v
            break

    # 2. Deployment orchestration environment (.env.deploy / .env.prod)
    deploy_candidates = [
        Path(deploy_env_file) if deploy_env_file else None,
        Path(os.environ["REASON_DEPLOY_ENV_FILE"]) if os.environ.get("REASON_DEPLOY_ENV_FILE") else None,
        Path(os.environ["REASON_ENV_FILE"]) if os.environ.get("REASON_ENV_FILE") else None,
        repo_root / ".env.deploy",
        cwd / ".env.deploy",
        repo_root / ".env.prod",
        cwd / ".env.prod",
    ]
    loaded_deploy = None
    for p in deploy_candidates:
        if p and p.is_file():
            loaded_deploy = p.resolve()
            for k, v in _parse_dotenv(loaded_deploy).items():
                if k not in initial_keys:
                    os.environ[k] = v
            break

    return str(loaded_deploy) if loaded_deploy else (str(loaded_base) if loaded_base else None)


LOADED_ENV_FILE = load_deploy_env()


def reload_deploy_config(deploy_env_file=None, base_env_file=None):
    """
    Re-evaluates the environment file and reloads all module-level configuration
    variables in globals(). Useful for tests and dynamic configuration updates.
    """
    loaded = load_deploy_env(deploy_env_file=deploy_env_file, base_env_file=base_env_file)
    global APP_NAME, BASE_DIR, REPO_DIR, APP_DIR, VENV_DIR, SHARED_DIR, PUBLIC_DIR
    global STATIC_DIR, MEDIA_DIR, BACKUP_DIR, LOG_DIR, HF_CACHE_DIR, DEPLOY_USER
    global DJANGO_USER, WEB_GROUP, DOMAIN, GRANIAN_PORT, INFERENCE_PORT, SSL_DIR
    global SSL_CERT_PATH, SSL_KEY_PATH, DB_CONTAINER, DB_NAME, DB_USER, DB_PORT, DB_PASSWORD
    global LOADED_ENV_FILE

    LOADED_ENV_FILE = loaded
    APP_NAME = os.environ.get("REASON_APP_NAME", "reason")
    BASE_DIR = os.environ.get("REASON_BASE_DIR", f"/srv/{APP_NAME}")
    REPO_DIR = os.environ.get("REASON_REPO_DIR", f"{BASE_DIR}/repo")
    APP_DIR = os.environ.get("REASON_APP_DIR", f"{BASE_DIR}/app")
    VENV_DIR = os.environ.get("REASON_VENV", f"{BASE_DIR}/venv")
    SHARED_DIR = os.environ.get("REASON_SHARED_DIR", f"{BASE_DIR}/shared")
    PUBLIC_DIR = os.environ.get("REASON_PUBLIC_DIR", f"{BASE_DIR}/public")
    STATIC_DIR = os.environ.get("REASON_STATIC_DIR", f"{PUBLIC_DIR}/static")
    MEDIA_DIR = os.environ.get("REASON_MEDIA_DIR", f"{PUBLIC_DIR}/media")
    BACKUP_DIR = os.environ.get("REASON_BACKUP_DIR", f"{BASE_DIR}/backups")
    LOG_DIR = os.environ.get("REASON_LOG_DIR", f"{BASE_DIR}/logs")

    HF_CACHE_DIR = os.environ.get("REASON_HF_CACHE", "/var/lib/hf_home")
    DEPLOY_USER = os.environ.get("REASON_DEPLOY_USER", "verbal_deploy")
    DJANGO_USER = os.environ.get("REASON_APP_USER", "verbal_run")
    WEB_GROUP = os.environ.get("REASON_WEB_GROUP", "www-data")

    DOMAIN = os.environ.get("REASON_DOMAIN", "reason.andrew.com")
    GRANIAN_PORT = int(os.environ.get("REASON_GRANIAN_PORT", "8008"))
    INFERENCE_PORT = int(os.environ.get("REASON_INFERENCE_PORT", "8001"))
    SSL_DIR = os.environ.get("REASON_SSL_DIR", f"{BASE_DIR}/ssl")
    SSL_CERT_PATH = os.environ.get("REASON_SSL_CERT_PATH", f"{SSL_DIR}/{DOMAIN}.crt")
    SSL_KEY_PATH = os.environ.get("REASON_SSL_KEY_PATH", f"{SSL_DIR}/{DOMAIN}.key")

    DB_CONTAINER = os.environ.get("REASON_DB_CONTAINER", "verbal_db")
    DB_NAME = os.environ.get("DB_NAME", os.environ.get("REASON_DB_NAME", "verbal_db"))
    DB_USER = os.environ.get("DB_USER", os.environ.get("REASON_DB_USER", "verbal_user"))
    DB_PASSWORD = os.environ.get("DB_PASSWORD", os.environ.get("REASON_DB_PASSWORD", "verbal_password"))
    DB_PORT = int(os.environ.get("DB_PORT", os.environ.get("REASON_DB_PORT", "5433")))

    return loaded


# Initialize module configuration
reload_deploy_config()

# Standard rsync exclusion patterns
RSYNC_EXCLUDES = (
    "--exclude='.git*' "
    "--exclude='*.pyc' "
    "--exclude='__pycache__' "
    "--exclude='.env*' "
    "--exclude='*.log' "
    "--exclude='deploy/ssl/' "
    "--exclude='media/' "
    "--exclude='staticfiles/' "
    "--exclude='workspaces/' "
    "--exclude='documents/' "
    "--exclude='backups/' "
    "--exclude='.idea/' "
    "--exclude='.pytest_cache/' "
    "--exclude='doctest_reports/' "
    "--exclude='test_*.json'"
)


# ==============================================================================
# SUDO & SECURITY SESSION HARDENING
# ==============================================================================
class StrictSingleAttemptWatcher(StreamWatcher):
    """
    Guarantees that any sudo authentication failure aborts immediately on the
    very first rejected attempt to prevent account lockout on enterprise/PAM systems.
    """
    def __init__(self):
        super().__init__()
        self.index = 0

    def submit(self, stream):
        new_ = stream[self.index:]
        lowered = new_.lower()
        if any(bad in lowered for bad in ["sorry, try again", "incorrect password", "authentication failure"]):
            print("\n🚨 [SECURITY] Sudo password rejected! Aborting immediately on first failure to prevent account lockout.")
            raise Exit("Sudo password rejected on first attempt.")
        self.index = len(stream)
        return []


def ensure_sudo(c):
    """
    Safely establishes and validates sudo credentials:
    1. Disables PTY to prevent credential echoing.
    2. Cleans prompt formatting so regex strings do not leak.
    3. Prompts once for password with getpass.
    4. Validates credentials immediately with a single-shot test (sudo -k -S true).
    5. Aborts immediately if invalid to avoid account lockouts.
    """
    c.config.run.pty = False
    c.config.sudo.pty = False

    if hasattr(c.config, "sudo"):
        c.config.sudo.prompt = "[sudo] password: "

    existing_pwd = getattr(c.config.sudo, "password", None) if hasattr(c.config, "sudo") else None
    if not existing_pwd:
        env_pass = os.environ.get("SUDO_PASSWORD")
        if env_pass:
            password = env_pass
        else:
            try:
                password = getpass.getpass("🔑 [sudo] password: ")
            except (EOFError, KeyboardInterrupt):
                raise Exit("Password entry cancelled.")

        if not password:
            raise Exit("No sudo password provided.")

        print("🔍 Verifying sudo credentials (single-shot test)...")
        test_res = c.run(
            "sudo -k -S -p '[sudo] password: ' true",
            in_stream=io.StringIO(f"{password}\n"),
            warn=True,
            hide=True,
        )

        if test_res.failed:
            print("\n🚨 [SECURITY] Sudo password verification failed!")
            print("🛑 Aborting immediately on first failure to protect your account from lockout.")
            raise Exit("Invalid sudo password.")

        print("✅ Sudo credentials verified successfully.")
        c.config.sudo.password = password

    if hasattr(c.config.run, "watchers"):
        if not any(isinstance(w, StrictSingleAttemptWatcher) for w in c.config.run.watchers):
            c.config.run.watchers.append(StrictSingleAttemptWatcher())


def sudo_bash(c, command, **kwargs):
    """
    Executes a shell command/pipeline as root with bash, ensuring that
    compound operators (&&, ||, |, >) retain full elevated privileges.
    """
    escaped = command.replace("'", "'\\''")
    return c.sudo(f"bash -c '{escaped}'", **kwargs)


def write_sudo_file(c, dest_path, content, mode="0644", owner="root:root"):
    """
    Safely writes content to a privileged destination file without breaking
    Fabric's sudo -S password stdin channel. Writes to a private unprivileged /tmp
    file first using c.run, then installs and chowns it at dest_path with sudo.
    """
    owner_user, owner_group = (owner.split(":") + ["root"])[:2]
    tmp_path = f"/tmp/fabric_{uuid.uuid4().hex[:8]}"
    c.run(f"umask 077 && cat << 'EOF' > {tmp_path}\n{content}\nEOF")
    sudo_bash(c, f"install -m {mode} -o {owner_user} -g {owner_group} {tmp_path} {dest_path}")
    c.run(f"rm -f {tmp_path}")


def run_as_deploy_user(c, command, cwd=None, **kwargs):
    """
    Executes a shell command as DEPLOY_USER (verbal_deploy):
    1. If the current SSH session is already connected as DEPLOY_USER, runs
       directly via c.run(command) without sudo, preserving user SSH keys
       (~/.ssh/) and avoiding unnecessary sudoers user-switching checks.
    2. If connected as another user, escalates via root sudo into a login shell
       (`su - {DEPLOY_USER} -c '...'`), ensuring HOME=/home/verbal_deploy and
       SSH keys are active.
    3. If cwd is specified, ensures the command executes within that working directory.
    """
    current_user = getattr(c, "_cached_deploy_user", None)
    if not isinstance(current_user, str):
        try:
            res = c.run("whoami", hide=True, warn=True)
            current_user = res.stdout.strip() if res and res.ok else getattr(c, "user", None)
        except Exception:
            current_user = getattr(c, "user", None) or os.environ.get("USER", "")
        c._cached_deploy_user = current_user

    full_cmd = f"cd {cwd} && {command}" if cwd else command
    escaped = full_cmd.replace("'", "'\\''")
    if current_user == DEPLOY_USER:
        return c.run(f"bash -c '{escaped}'", **kwargs)
    else:
        return sudo_bash(c, f"su - {DEPLOY_USER} -c '{escaped}'", **kwargs)


# ==============================================================================
# CONFIGURATION TEMPLATING (PURE FUNCTIONS FOR AUDITABILITY & TESTING)
# ==============================================================================
def generate_nginx_conf(
    domain=DOMAIN,
    ssl_cert_path=SSL_CERT_PATH,
    ssl_key_path=SSL_KEY_PATH,
    static_dir=STATIC_DIR,
    media_dir=MEDIA_DIR,
    granian_port=GRANIAN_PORT,
):
    """
    Generates Nginx configuration including:
    - Default server rejection (HTTP 444) for unrecognized Host headers.
    - Canonical domain HTTP -> HTTPS redirection.
    - SSE streaming proxy without buffering.
    - Internal and unauthenticated LLM route blocking (/api/llm/internal/, /hardware/, /v1/).
    - Complete security headers.

    Examples:
        >>> conf = generate_nginx_conf(domain="example.org", granian_port=9000)
        >>> "server 127.0.0.1:9000;" in conf
        True
        >>> "location ~ ^/api/llm/(internal/|hardware/|v1/)" in conf
        True
        >>> "return 301 https://example.org$request_uri;" in conf
        True
    """
    return f"""# Auto-generated by Fabric for {domain}
map $http_upgrade $connection_upgrade {{
    default upgrade;
    '' close;
}}

upstream granian_backend {{
    server 127.0.0.1:{granian_port};
    keepalive 32;
}}

# Catch-all default server: drop connections with unrecognized Host headers
server {{
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}}

# HTTP -> HTTPS canonical redirect
server {{
    listen 80;
    listen [::]:80;
    server_name {domain} localhost 127.0.0.1;

    location / {{
        return 301 https://{domain}$request_uri;
    }}
}}

# HTTPS Gateway & Reverse Proxy
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain} localhost 127.0.0.1;

    ssl_certificate {ssl_cert_path};
    ssl_certificate_key {ssl_key_path};

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    client_max_body_size 100M;

    # Static Assets (collected via collectstatic)
    location /static/ {{
        alias {static_dir}/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
    }}

    # Uploaded Media & Artifacts
    location /media/ {{
        alias {media_dir}/;
        expires 7d;
        access_log off;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
    }}

    # Shield internal AI inference & hardware management endpoints from public internet/intranet
    location ~ ^/api/llm/(internal/|hardware/|v1/) {{
        return 404;
    }}

    # Granian ASGI Application Proxy (SSE streaming + WebSockets)
    location / {{
        proxy_pass http://granian_backend;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port 443;

        # WebSocket & Datastar streaming signals
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;

        # Critical: proxy_buffering off ensures live LLM tokens reach UI instantly
        proxy_buffering off;
    }}
}}
"""


def generate_systemd_units(
    app_dir=APP_DIR,
    venv_dir=VENV_DIR,
    shared_dir=SHARED_DIR,
    static_dir=STATIC_DIR,
    log_dir=LOG_DIR,
    hf_cache_dir=HF_CACHE_DIR,
    django_user=DJANGO_USER,
    web_group=WEB_GROUP,
    granian_port=GRANIAN_PORT,
    inference_port=INFERENCE_PORT,
):
    """
    Generates dictionary of systemd unit configurations with:
    - Explicit VERBAL_ROLE assignment per service.
    - Systemd sandboxing and privilege reduction.
    - --noreload on inference runserver to prevent duplicate model instantiation.

    Examples:
        >>> units = generate_systemd_units(granian_port=9000, inference_port=9001)
        >>> "reason-web.service" in units
        True
        >>> "Environment=VERBAL_ROLE=web" in units["reason-web.service"]
        True
        >>> "--noreload" in units["reason-inference.service"]
        True
    """
    env_file = f"{shared_dir}/.env"
    env_secrets = f"{shared_dir}/.env.secrets"

    sandboxing = f"""NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths={shared_dir} {hf_cache_dir} {log_dir}"""

    web_unit = f"""[Unit]
Description=Reason Web Server (Granian ASGI)
After=network.target docker.service postgresql.service
Wants=network.target

[Service]
Type=simple
User={django_user}
Group={web_group}
WorkingDirectory={app_dir}
EnvironmentFile=-{env_file}
EnvironmentFile=-{env_secrets}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=web
Environment=DJANGO_STATIC_ROOT={static_dir}
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/granian --interface asgi --host 127.0.0.1 --port {granian_port} verbal_config.asgi:application
Restart=always
RestartSec=3s
LimitNOFILE=65535
{sandboxing}

[Install]
WantedBy=multi-user.target
"""

    worker_unit = f"""[Unit]
Description=Reason Background Task Worker
After=network.target docker.service postgresql.service
Wants=network.target

[Service]
Type=simple
User={django_user}
Group={web_group}
WorkingDirectory={app_dir}
EnvironmentFile=-{env_file}
EnvironmentFile=-{env_secrets}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=worker
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runtaskworker
Restart=always
RestartSec=5s
{sandboxing}

[Install]
WantedBy=multi-user.target
"""

    scheduler_unit = f"""[Unit]
Description=Reason Task Scheduler (NightManager)
After=network.target docker.service postgresql.service
Wants=network.target

[Service]
Type=simple
User={django_user}
Group={web_group}
WorkingDirectory={app_dir}
EnvironmentFile=-{env_file}
EnvironmentFile=-{env_secrets}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=scheduler
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runtaskscheduler
Restart=always
RestartSec=10s
{sandboxing}

[Install]
WantedBy=multi-user.target
"""

    inference_unit = f"""[Unit]
Description=Reason Local AI Model Inference Service
After=network.target
Wants=network.target

[Service]
Type=simple
User={django_user}
Group={web_group}
WorkingDirectory={app_dir}
EnvironmentFile=-{env_file}
EnvironmentFile=-{env_secrets}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=inference
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runserver --noreload 127.0.0.1:{inference_port}
Restart=always
RestartSec=5s
{sandboxing}

[Install]
WantedBy=multi-user.target
"""

    return {
        "reason-web.service": web_unit,
        "reason-worker.service": worker_unit,
        "reason-scheduler.service": scheduler_unit,
        "reason-inference.service": inference_unit,
    }


# ==============================================================================
# PHASE 1: PREFLIGHT AUDIT & SYSTEM PROVISIONING
# ==============================================================================
@task
def preflight_check(c):
    """
    Validates server environment, production .env configuration, and permissions.
    """
    ensure_sudo(c)
    print("📋 [Preflight] Verifying server environment and production configuration...")

    env_path = f"{SHARED_DIR}/.env"
    env_exists = c.sudo(f"test -f {env_path}", warn=True, hide=True).ok
    if not env_exists:
        print(f"⚠️ [WARNING] Production environment file '{env_path}' not found!")
        repo_root = Path(__file__).resolve().parent.parent
        if (repo_root / ".env").is_file():
            print("   💡 Tip: Run 'fab sync-env' to push your local .env to the server.")
        else:
            print("   Create .env from .env.example with secure keys, then run 'fab sync-env'.")
    else:
        # Check permissions on .env (must not be world-readable)
        perm = sudo_bash(c, f"stat -c %a {env_path}", hide=True).stdout.strip()
        if perm in ["777", "666", "775", "664"]:
            print(f"🔒 Tightening permissions on {env_path} (was {perm})...")
            sudo_bash(c, f"chmod 0640 {env_path} && chown root:{WEB_GROUP} {env_path}")

    # Check python3
    py_check = c.run("python3 --version", warn=True, hide=True)
    if py_check.failed:
        raise Exit("Python 3 is not installed on target host.")

    print("✅ Preflight checks completed.")


@task
def provision_system(c):
    """
    Provisions system users, directories under /srv, shared caches, and
    establishes the 3-tier ownership model.
    """
    ensure_sudo(c)
    print("🚀 [1/4] Provisioning system users, groups, and directory hierarchy...")

    # 1. Ensure users exist
    check_deploy = c.sudo(f"id -u {DEPLOY_USER}", warn=True, hide=True)
    if check_deploy.failed:
        print(f"👤 Creating deployment user '{DEPLOY_USER}'...")
        home_exists = c.sudo(f"test -d /home/{DEPLOY_USER}", warn=True, hide=True).ok
        useradd_flag = f"-d /home/{DEPLOY_USER}" if home_exists else "-m"
        c.sudo(f"useradd {useradd_flag} -s /bin/bash {DEPLOY_USER}")

    check_django = c.sudo(f"id -u {DJANGO_USER}", warn=True, hide=True)
    if check_django.failed:
        print(f"👤 Creating system execution user '{DJANGO_USER}'...")
        c.sudo(f"useradd -r -s /bin/false {DJANGO_USER}")

    # 2. Add both to web group for controlled traversal
    c.sudo(f"usermod -aG {WEB_GROUP} {DEPLOY_USER}")
    c.sudo(f"usermod -aG {WEB_GROUP} {DJANGO_USER}")

    # 3. Create core directory structure
    c.sudo(f"mkdir -p {BASE_DIR} {APP_DIR} {STATIC_DIR} {MEDIA_DIR} {BACKUP_DIR} {LOG_DIR} {SHARED_DIR}")
    c.sudo(f"mkdir -p {SHARED_DIR}/workspaces {SHARED_DIR}/media {HF_CACHE_DIR}")

    # 4. Strict base directory lockdown (root owned)
    c.sudo(f"chown root:root {BASE_DIR}")
    c.sudo(f"chmod 0755 {BASE_DIR}")

    # 5. Persistent shared directories & HF Cache
    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {HF_CACHE_DIR}")
    sudo_bash(c, f"chmod -R 2775 {HF_CACHE_DIR}")

    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {SHARED_DIR}")
    sudo_bash(c, f"find {SHARED_DIR} -type d -exec chmod 2775 {{}} +")
    sudo_bash(c, f"find {SHARED_DIR} -type f -exec chmod 0660 {{}} +")

    # Secure .env file if already present
    if c.sudo(f"test -f {SHARED_DIR}/.env", warn=True, hide=True).ok:
        sudo_bash(c, f"chmod 0640 {SHARED_DIR}/.env && chown root:{WEB_GROUP} {SHARED_DIR}/.env")

    # 6. Public static (owned by deployer, readable by web group)
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {STATIC_DIR}")
    c.sudo(f"chmod 0755 {STATIC_DIR}")

    # 7. Public media (symlinked to shared media, SetGID)
    c.sudo(f"rm -rf {MEDIA_DIR}")
    c.sudo(f"ln -sfn {SHARED_DIR}/media {MEDIA_DIR}")
    c.sudo(f"chown -h {DJANGO_USER}:{WEB_GROUP} {MEDIA_DIR}")

    # 8. Backups and logs
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {BACKUP_DIR} {LOG_DIR}")
    c.sudo(f"chmod 0750 {BACKUP_DIR} {LOG_DIR}")

    # 9. Repo & App directories
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {APP_DIR}")
    c.sudo(f"chmod 0750 {APP_DIR}")
    sudo_bash(c, f"if [ -d {REPO_DIR} ]; then chown -R {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR} && chmod 0750 {REPO_DIR}; fi")

    # 10. Python Virtual Environment
    venv_pip = c.sudo(f"test -f {VENV_DIR}/bin/pip", warn=True)
    if venv_pip.failed:
        print("🐍 Creating dedicated Python virtual environment...")
        c.sudo(f"rm -rf {VENV_DIR}")
        c.sudo(f"python3 -m venv {VENV_DIR}")
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {VENV_DIR}")
    sudo_bash(c, f"chmod -R 0750 {VENV_DIR}")

    print("✅ System paths, users, and ownership hierarchy established.")


@task
def sync_env(c, local_env=None):
    """
    Safely pushes local application configuration (.env) to the remote server
    at /srv/reason/shared/.env with locked-down 0600 permissions.
    """
    ensure_sudo(c)
    repo_root = Path(__file__).resolve().parent.parent
    candidate = local_env or (repo_root / ".env") or (repo_root / ".env.deploy") or LOADED_ENV_FILE
    env_path = Path(candidate)
    if not env_path.is_file():
        raise Exit(f"Local environment file not found at: {env_path}")

    content = env_path.read_text(encoding="utf-8")
    dest_path = f"{SHARED_DIR}/.env"
    print(f"📤 Uploading {env_path.name} to {dest_path} on remote server...")
    c.sudo(f"mkdir -p {SHARED_DIR}")
    write_sudo_file(c, dest_path, content, mode="0600", owner=f"{DEPLOY_USER}:{WEB_GROUP}")
    print(f"✅ Production environment configuration synchronized at {dest_path} (mode: 0600).")


# ==============================================================================
# PHASE 2: SSL CERTIFICATES & NGINX
# ==============================================================================
@task
def setup_nginx_and_ssl(c, domain=None, source_ssl_dir=None, remove_default_site=False):
    """
    Installs SSL certificates into /srv/reason/ssl/, generates an optimized Nginx site
    configuration with SSE streaming and WebSocket support, and reloads Nginx safely.
    Supports ad-hoc domain override via domain parameter.
    """
    ensure_sudo(c)
    target_domain = domain or DOMAIN
    target_ssl_dir = f"{BASE_DIR}/ssl"
    target_cert = f"{target_ssl_dir}/{target_domain}.crt"
    target_key = f"{target_ssl_dir}/{target_domain}.key"

    print(f"🔒 [2/4] Configuring SSL certificates and Nginx reverse proxy for {target_domain}...")

    # Determine SSL certificate sources
    if source_ssl_dir is None:
        local_ssl = Path(__file__).resolve().parent / "ssl"
        if local_ssl.exists() and (local_ssl / f"{target_domain}.crt").exists():
            source_ssl_dir = str(local_ssl)
        else:
            source_ssl_dir = f"{REPO_DIR}/deploy/ssl"

    # Install SSL certs into isolated /srv/reason/ssl directory
    c.sudo(f"mkdir -p {target_ssl_dir}")
    sudo_bash(c, f"chmod 0750 {target_ssl_dir} && chown root:{WEB_GROUP} {target_ssl_dir}")

    if c.sudo(f"test -f {source_ssl_dir}/{target_domain}.crt", warn=True, hide=True).ok:
        print(f"📋 Installing certificate from {source_ssl_dir}...")
        c.sudo(f"cp {source_ssl_dir}/{target_domain}.crt {target_cert}")
    if c.sudo(f"test -f {source_ssl_dir}/{target_domain}.key", warn=True, hide=True).ok:
        c.sudo(f"cp {source_ssl_dir}/{target_domain}.key {target_key}")

    # Generate fallback self-signed cert if neither exists
    cert_check = c.sudo(f"test -f {target_cert} && test -f {target_key}", warn=True)
    if cert_check.failed:
        print(f"⚠️ Generating self-signed SSL certificates for {target_domain}...")
        sudo_bash(
            c,
            f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 "
            f"-keyout {target_key} -out {target_cert} "
            f"-subj '/CN={target_domain}/O=Reason/C=AU'"
        )

    # Secure SSL private key permissions: only root can read key
    sudo_bash(
        c,
        f"chmod 0644 {target_cert} && chown root:{WEB_GROUP} {target_cert} && "
        f"chmod 0600 {target_key} && chown root:root {target_key}"
    )

    # Generate and write Nginx configuration
    nginx_conf = generate_nginx_conf(
        domain=target_domain,
        ssl_cert_path=target_cert,
        ssl_key_path=target_key,
        static_dir=STATIC_DIR,
        media_dir=MEDIA_DIR,
        granian_port=GRANIAN_PORT,
    )

    conf_dest = f"/etc/nginx/sites-available/{target_domain}.conf"
    enabled_dest = f"/etc/nginx/sites-enabled/{target_domain}.conf"

    write_sudo_file(c, conf_dest, nginx_conf, mode="0644", owner="root:root")
    c.sudo(f"ln -sfn {conf_dest} {enabled_dest}")

    # Remove default site if explicitly requested or configured via environment
    if remove_default_site or os.environ.get("REASON_REMOVE_DEFAULT_NGINX", "").lower() in ("true", "1"):
        print("🗑️ Removing default Nginx site configuration...")
        c.sudo("rm -f /etc/nginx/sites-enabled/default")
    else:
        print("ℹ️ Preserving /etc/nginx/sites-enabled/default (pass remove_default_site=True to unlink).")

    # Test and reload
    print("🔍 Testing Nginx syntax...")
    c.sudo("nginx -t")
    print("🔄 Reloading Nginx service...")
    reload_res = c.sudo("systemctl reload nginx", warn=True)
    if reload_res.failed:
        c.sudo("systemctl restart nginx")
    print("✅ Nginx and SSL configuration active.")


# ==============================================================================
# PHASE 3: SYSTEMD PROCESS SUPERVISION
# ==============================================================================
@task
def setup_systemd(c):
    """
    Creates and enables 4 modular systemd units for Reason/Verbal:
    1. reason-web.service       (Granian ASGI web server)
    2. reason-worker.service    (Background task worker)
    3. reason-scheduler.service (Periodic task scheduler / beat)
    4. reason-inference.service (Dedicated local AI inference endpoint)
    """
    ensure_sudo(c)
    print("⚙️ [3/4] Installing systemd service units...")

    services = generate_systemd_units(
        app_dir=APP_DIR,
        venv_dir=VENV_DIR,
        shared_dir=SHARED_DIR,
        static_dir=STATIC_DIR,
        log_dir=LOG_DIR,
        hf_cache_dir=HF_CACHE_DIR,
        django_user=DJANGO_USER,
        web_group=WEB_GROUP,
        granian_port=GRANIAN_PORT,
        inference_port=INFERENCE_PORT,
    )

    for unit_name, content in services.items():
        write_sudo_file(c, f"/etc/systemd/system/{unit_name}", content, mode="0644", owner="root:root")

    sudo_bash(c, "systemctl daemon-reload && systemctl enable reason-web reason-worker reason-scheduler reason-inference")
    print("✅ Systemd services installed and registered for automatic boot startup.")


# ==============================================================================
# PHASE 4: PRE-MIGRATION DATABASE BACKUP
# ==============================================================================
@task
def backup_database(c, use_dumpdata=True):
    """
    Creates an automated pre-migration database snapshot prior to applying migrations,
    retaining the last 10 snapshots in /srv/reason/backups. Aborts deploy if backup fails.

    By default, uses Django's native ORM `dumpdata` with gzip compression:
    - Completely self-contained within Python/Django (no external pg_dump binary required).
    - Avoids dependency on custom/stripped container images or missing host client utilities.
    - Excludes transient ContentTypes and Permissions to prevent fixture collisions on restore.
    - Verified immediately for file integrity with `gzip -t`.

    Set use_dumpdata=False to fall back to native pg_dump if available.
    """
    ensure_sudo(c)
    print("💾 Creating pre-migration database snapshot...")
    timestamp = c.run("date +%Y%m%d_%H%M%S", hide=True).stdout.strip()

    manage_py = f"{APP_DIR}/manage.py"
    has_app = c.sudo(f"test -f {manage_py}", warn=True, hide=True).ok
    if not has_app:
        print(f"ℹ️ No existing application found at {APP_DIR}; skipping pre-deploy backup on initial bootstrap.")
        return

    has_django = run_as_deploy_user(c, f"{VENV_DIR}/bin/python -c 'import django'", warn=True).ok
    if not has_django:
        print(f"ℹ️ Django is not installed in {VENV_DIR} (virtual environment not populated yet); skipping pre-deploy backup.")
        return

    # Check if database has any existing tables to dump
    check_tables_cmd = (
        f"VERBAL_ROLE=web {VENV_DIR}/bin/python {APP_DIR}/manage.py shell -c \""
        f"import sys; from django.db import connection; "
        f"tables = connection.introspection.table_names(); "
        f"sys.exit(0 if 'django_migrations' in tables else 1)\""
    )
    has_tables = run_as_deploy_user(c, check_tables_cmd, cwd=APP_DIR, warn=True).ok
    if not has_tables:
        print(f"ℹ️ Database has no existing migrated tables; skipping pre-deploy backup on initial bootstrap.")
        return

    if use_dumpdata:
        backup_file = f"{BACKUP_DIR}/pre_deploy_{timestamp}.json.gz"
        print(f"📦 Dumping database via Django ORM (dumpdata -> {backup_file})...")
        dump_cmd = (
            f"VERBAL_ROLE=web {VENV_DIR}/bin/python {APP_DIR}/manage.py dumpdata "
            f"--natural-foreign --natural-primary "
            f"-e contenttypes -e auth.Permission "
            f"--output {backup_file}"
        )
        res = run_as_deploy_user(c, dump_cmd, cwd=APP_DIR, warn=True)

        # Strictly verify backup file exists, is non-empty, and gzip archive is valid
        verify_res = sudo_bash(
            c,
            f"test -s {backup_file} && [ $(stat -c%s {backup_file}) -gt 100 ] && gzip -t {backup_file}",
            warn=True,
        )
        if verify_res.failed or (res and res.failed):
            print(f"🚨 [ERROR] Pre-deploy database dumpdata failed or produced corrupt archive at {backup_file}!")
            raise Exit("Pre-deploy database backup failed. Aborting to prevent data loss.")

        print("🔍 Database backup verified: gzip archive integrity is valid and uncorrupted.")
    else:
        # Native pg_dump fallback
        backup_file = f"{BACKUP_DIR}/pre_deploy_{timestamp}.dump"
        docker_check = c.sudo(f"docker ps -q -f name={DB_CONTAINER}", warn=True)
        if docker_check.ok and docker_check.stdout.strip():
            print(f"📦 Dumping database from container '{DB_CONTAINER}'...")
            res = sudo_bash(c, f"docker exec {DB_CONTAINER} pg_dump -U {DB_USER} -Fc {DB_NAME} > {backup_file}", warn=True)
        else:
            print("📦 Container offline or local DB, running host pg_dump...")
            res = sudo_bash(c, f"PGPASSWORD=\"${{DB_PASSWORD:-verbal_password}}\" pg_dump -h 127.0.0.1 -p {DB_PORT} -U {DB_USER} -Fc {DB_NAME} > {backup_file}", warn=True)

        verify_res = sudo_bash(c, f"test -s {backup_file} && [ $(stat -c%s {backup_file}) -gt 100 ]", warn=True)
        if verify_res.failed or (res and res.failed):
            print(f"🚨 [ERROR] Pre-deploy database backup failed or produced an empty file at {backup_file}!")
            raise Exit("Pre-deploy database backup failed. Aborting to prevent data loss.")

        toc_check = sudo_bash(
            c,
            f"pg_restore -l {backup_file} >/dev/null 2>&1 || "
            f"(docker ps -q -f name={DB_CONTAINER} | grep -q . && docker exec -i {DB_CONTAINER} pg_restore -l < {backup_file} >/dev/null 2>&1)",
            warn=True,
        )
        if toc_check.ok:
            print("🔍 Database backup TOC verified: archive is valid and uncorrupted.")

    # Prune older backups (both .dump and .json.gz), keeping the most recent 10
    sudo_bash(c, f"ls -1t {BACKUP_DIR}/pre_deploy_* 2>/dev/null | tail -n +11 | xargs -r rm -f")
    print(f"✅ Pre-deploy snapshot secured: {backup_file}")


# ==============================================================================
# PHASE 5: CODE SYNC, MIGRATION & CACHE LINKING
# ==============================================================================
@task
def update_code(c, git_url=None, branch="main", local_source=None):
    """
    Synchronizes code into the active production directory, symlinks shared
    assets and caches, and enforces the read-only code boundary.
    Defaults to REASON_GIT_URL and REASON_GIT_BRANCH if configured.
    """
    ensure_sudo(c)
    target_branch = branch or os.environ.get("REASON_GIT_BRANCH", "main")
    target_git_url = git_url or os.environ.get("REASON_GIT_URL")
    print(f"📦 Synchronizing application code (branch: {target_branch})...")

    if local_source:
        print(f"🔄 Syncing directly from local directory {local_source}...")
        sudo_bash(c, f"rsync -ax --delete {RSYNC_EXCLUDES} {local_source}/ {APP_DIR}/")
    else:
        git_ssh_cmd = 'GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"'
        # Ensure safe.directory is configured before running any git commands
        run_as_deploy_user(c, f"git config --global --add safe.directory {REPO_DIR}", warn=True)

        is_git_repo = run_as_deploy_user(c, f"git -C {REPO_DIR} rev-parse --is-inside-work-tree", warn=True).ok
        if not is_git_repo:
            if not target_git_url:
                raise Exit(f"No git repository found at {REPO_DIR} and no git_url / REASON_GIT_URL provided.")
            # If REPO_DIR exists (e.g. from previous aborted clone or non-git dir), wipe it for a clean clone
            if c.sudo(f"test -e {REPO_DIR}", warn=True).ok:
                print(f"🧹 Clearing non-git or uninitialized directory at {REPO_DIR} for clean clone...")
                c.sudo(f"rm -rf {REPO_DIR}")

            print(f"📥 Cloning repository from {target_git_url} as {DEPLOY_USER}...")
            run_as_deploy_user(c, f"{git_ssh_cmd} git clone --branch {target_branch} {target_git_url} {REPO_DIR}")
            c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR}")
            sudo_bash(c, f"chmod 0750 {REPO_DIR}")
        else:
            print(f"📥 Updating existing git repository at {REPO_DIR} as {DEPLOY_USER}...")
            if target_git_url:
                run_as_deploy_user(c, f"git -C {REPO_DIR} remote set-url origin {target_git_url}", warn=True)
            run_as_deploy_user(c, f"rm -f {REPO_DIR}/.git/index.lock", warn=True)
            run_as_deploy_user(c, f"{git_ssh_cmd} git -C {REPO_DIR} fetch --all --prune")
            run_as_deploy_user(c, f"git -C {REPO_DIR} checkout -B {target_branch} origin/{target_branch}")
            run_as_deploy_user(c, f"git -C {REPO_DIR} reset --hard origin/{target_branch}")
            run_as_deploy_user(c, f"git -C {REPO_DIR} clean -fd")

        sudo_bash(c, f"rsync -ax --delete {RSYNC_EXCLUDES} {REPO_DIR}/ {APP_DIR}/")

    # Symlink shared workspaces and media
    c.sudo(f"ln -sfn {SHARED_DIR}/workspaces {APP_DIR}/workspaces")
    c.sudo(f"ln -sfn {SHARED_DIR}/media {APP_DIR}/media")

    # Symlink persistent .env if present
    env_source = f"{SHARED_DIR}/.env"
    if c.sudo(f"test -f {env_source}", warn=True, hide=True).ok:
        c.sudo(f"ln -sfn {env_source} {APP_DIR}/.env")

    # Enforce Read-Only Code Boundaries
    print("🔒 Enforcing read-only code execution permissions...")
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {APP_DIR}")
    sudo_bash(c, f"find {APP_DIR} -type d -exec chmod 0750 {{}} +")
    sudo_bash(c, f"find {APP_DIR} -type f -exec chmod 0640 {{}} +")
    c.sudo(f"chmod +x {APP_DIR}/manage.py")

    print("✅ Application code updated and permission boundaries enforced.")


@task
def build_and_migrate(c, find_links=None, index_url=None, trusted_host=None, use_dumpdata=True):
    """
    Installs Python dependencies, verifies deployment health, executes
    Django migrations, and collects static assets into STATIC_DIR.
    Supports Artifactory wheel links (--find-links) and custom package mirrors.
    """
    ensure_sudo(c)
    print("⚡ Running Python dependency updates and Django maintenance...")

    # Ensure vendored spaCy model wheel (en_core_web_sm) is available and valid for pip
    spacy_wheel_name = "en_core_web_sm-3.8.0-py3-none-any.whl"
    spacy_wheel_app = f"{APP_DIR}/resources/wheels/{spacy_wheel_name}"
    spacy_wheel_repo = f"{REPO_DIR}/resources/wheels/{spacy_wheel_name}"
    env_file = f"{SHARED_DIR}/.env"

    # 1. Check if the wheel in app directory exists and is an uncorrupted zip archive
    has_valid_wheel = False
    if run_as_deploy_user(c, f"test -f {spacy_wheel_app}", warn=True).ok:
        if run_as_deploy_user(c, f"python3 -m zipfile -t {spacy_wheel_app}", warn=True, hide=True).ok:
            has_valid_wheel = True
        else:
            print(f"⚠️ Existing wheel at {spacy_wheel_app} is corrupt or not a valid zip archive (e.g. HTML error page). Purging...")
            c.sudo(f"rm -f {spacy_wheel_app}")

    # 2. Check if a valid wheel is present in repo directory
    if not has_valid_wheel:
        if run_as_deploy_user(c, f"test -f {spacy_wheel_repo}", warn=True).ok:
            if run_as_deploy_user(c, f"python3 -m zipfile -t {spacy_wheel_repo}", warn=True, hide=True).ok:
                print(f"📦 Copying verified spaCy wheel from {spacy_wheel_repo} to {spacy_wheel_app}...")
                run_as_deploy_user(c, f"mkdir -p {APP_DIR}/resources/wheels && cp {spacy_wheel_repo} {spacy_wheel_app}")
                has_valid_wheel = True

    # 3. If missing from server, upload verified wheel directly from local development environment
    if not has_valid_wheel:
        local_wheel = Path(__file__).resolve().parent.parent / "resources" / "wheels" / spacy_wheel_name
        if local_wheel.is_file():
            print(f"🚀 Uploading local verified wheel ({local_wheel.name}) directly to server...")
            remote_tmp = f"/tmp/{spacy_wheel_name}"
            try:
                c.put(str(local_wheel), remote_tmp)
                c.sudo(f"mkdir -p {APP_DIR}/resources/wheels")
                c.sudo(f"install -m 0644 -o {DEPLOY_USER} -g {WEB_GROUP} {remote_tmp} {spacy_wheel_app}")
                c.run(f"rm -f {remote_tmp}", warn=True)
                if run_as_deploy_user(c, f"python3 -m zipfile -t {spacy_wheel_app}", warn=True, hide=True).ok:
                    print(f"✅ Successfully uploaded and verified {spacy_wheel_name} on server.")
                    has_valid_wheel = True
            except Exception as e:
                print(f"⚠️ Could not upload local wheel directly: {e}")

    # 4. Fallback: Download via configured URL (Artifactory or upstream) with fail-safe curl
    if not has_valid_wheel:
        print(f"🔍 Checking for external/Artifactory source for {spacy_wheel_name}...")
        spacy_download_cmd = (
            f'URL="{os.environ.get("SPACY_WHEEL_URL", "")}"; '
            f'if [ -z "$URL" ] && [ -f {env_file} ]; then '
            f'URL=$(grep -E "^[[:space:]]*SPACY_WHEEL_URL=" {env_file} | cut -d= -f2- | tr -d "\\"" | tr -d "\'"); '
            f'fi; '
            f'if [ -z "$URL" ]; then '
            f'URL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/{spacy_wheel_name}"; '
            f'fi; '
            f'mkdir -p {APP_DIR}/resources/wheels && '
            f'(curl -fSL "$URL" -o {spacy_wheel_app} || wget -q "$URL" -O {spacy_wheel_app})'
        )
        dl_res = run_as_deploy_user(c, spacy_download_cmd, warn=True)
        if dl_res.ok and run_as_deploy_user(c, f"python3 -m zipfile -t {spacy_wheel_app}", warn=True, hide=True).ok:
            print(f"✅ Successfully downloaded and verified {spacy_wheel_name} at {spacy_wheel_app}")
            has_valid_wheel = True
        else:
            c.sudo(f"rm -f {spacy_wheel_app}")
            print(
                f"⚠️ [WARNING] Could not secure valid {spacy_wheel_name} at {spacy_wheel_app}.\n"
                f"If pip install fails, configure SPACY_WHEEL_URL in .env.deploy or commit the wheel to resources/wheels/."
            )

    # Build pip installation flags (honoring CLI args, environment vars, and shared .env)
    pip_install_cmd = (
        f'cd {APP_DIR} && '
        f'FL="{find_links or os.environ.get("PIP_FIND_LINKS", "")}"; '
        f'IU="{index_url or os.environ.get("PIP_INDEX_URL", "")}"; '
        f'TH="{trusted_host or os.environ.get("PIP_TRUSTED_HOST", "")}"; '
        f'EXTRA_FLAGS="--find-links {APP_DIR}/resources/wheels"; '
        f'if [ -f {env_file} ]; then '
        f'[ -z "$FL" ] && FL=$(grep -E "^[[:space:]]*PIP_FIND_LINKS=" {env_file} | cut -d= -f2- | tr -d "\\"" | tr -d "\'"); '
        f'[ -z "$IU" ] && IU=$(grep -E "^[[:space:]]*PIP_INDEX_URL=" {env_file} | cut -d= -f2- | tr -d "\\"" | tr -d "\'"); '
        f'[ -z "$TH" ] && TH=$(grep -E "^[[:space:]]*PIP_TRUSTED_HOST=" {env_file} | cut -d= -f2- | tr -d "\\"" | tr -d "\'"); '
        f'fi; '
        f'[ -n "$FL" ] && EXTRA_FLAGS="$EXTRA_FLAGS --find-links $FL"; '
        f'[ -n "$IU" ] && EXTRA_FLAGS="$EXTRA_FLAGS --index-url $IU"; '
        f'[ -n "$TH" ] && EXTRA_FLAGS="$EXTRA_FLAGS --trusted-host $TH"; '
        f'{VENV_DIR}/bin/pip install --no-cache-dir $EXTRA_FLAGS -r {APP_DIR}/requirements.txt'
    )
    run_as_deploy_user(c, pip_install_cmd, cwd=APP_DIR)

    # Run deployment verification checks
    print("🔍 Running Django deployment check...")
    check_res = run_as_deploy_user(
        c,
        f"VERBAL_ROLE=web DJANGO_STATIC_ROOT={STATIC_DIR} {VENV_DIR}/bin/python {APP_DIR}/manage.py check --deploy",
        cwd=APP_DIR,
        warn=True,
    )
    if check_res.failed:
        print("⚠️ [WARNING] Django check --deploy identified potential deployment warnings.")

    # Pre-migration database snapshot (safe now that virtual environment is populated with Django)
    backup_database(c, use_dumpdata=use_dumpdata)

    # Run migrations as deployer
    print("🗄️ Applying Django database migrations...")
    run_as_deploy_user(c, f"{VENV_DIR}/bin/python {APP_DIR}/manage.py migrate --noinput", cwd=APP_DIR)

    # Collect static assets directly into STATIC_DIR
    print(f"🎨 Collecting static assets into {STATIC_DIR}...")
    run_as_deploy_user(
        c,
        f"DJANGO_STATIC_ROOT={STATIC_DIR} {VENV_DIR}/bin/python {APP_DIR}/manage.py collectstatic --noinput",
        cwd=APP_DIR,
    )

    # Lock down static asset permissions for Nginx read-only access
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {STATIC_DIR}")
    sudo_bash(c, f"find {STATIC_DIR} -type d -exec chmod 0755 {{}} +")
    sudo_bash(c, f"find {STATIC_DIR} -type f -exec chmod 0644 {{}} +")

    print("✅ Build, migrations, and static asset collection completed.")


# ==============================================================================
# PHASE 6: PROCESS RESTART & HEALTH VALIDATION
# ==============================================================================
@task
def restart_services(c):
    """
    Gracefully restarts the 4 supervised systemd services.
    """
    ensure_sudo(c)
    print("🔄 Restarting Reason systemd services...")
    sudo_bash(c, "systemctl restart reason-web reason-worker reason-scheduler reason-inference")
    print("✅ Services restarted.")


@task
def health_check(c):
    """
    Validates service responsiveness via systemd and HTTP probing with retry polling.
    """
    print("🩺 Validating system health with retry polling...")
    max_retries = 10
    retry_interval = 3

    for attempt in range(1, max_retries + 1):
        units_ok = True
        for unit in ["reason-web", "reason-worker", "reason-scheduler", "reason-inference"]:
            check = c.run(f"systemctl is-active --quiet {unit}", warn=True, hide=True)
            if check.failed:
                units_ok = False
                break

        # Probe HTTP endpoint
        curl_res = c.run(
            f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{GRANIAN_PORT}/demo/ "
            f"|| curl -k -s -o /dev/null -w '%{{http_code}}' https://127.0.0.1/demo/",
            warn=True,
            hide=True,
        )
        http_code = curl_res.stdout.strip()

        if units_ok and http_code in ["200", "302"]:
            print(f"🎉 Health check successful on attempt {attempt}! (HTTP {http_code})")
            return

        print(f"⏳ Attempt {attempt}/{max_retries}: Waiting for services to become healthy (HTTP {http_code or 'none'})...")
        time.sleep(retry_interval)

    raise Exit(f"🚨 Health check failed after {max_retries} attempts! Inspect journalctl -u reason-web.")


# ==============================================================================
# HIGH-LEVEL ORCHESTRATION PIPELINES
# ==============================================================================
@task
def deploy(c, branch="main", git_url=None, local_source=None, find_links=None, index_url=None, trusted_host=None, use_dumpdata=True):
    """
    Standard zero-downtime deployment pipeline:
    1. Preflight check
    2. Synchronize code
    3. Update dependencies, backup database & run migrations
    4. Restart systemd services
    5. Validate system health
    """
    ensure_sudo(c)
    print(f"🚀 Commencing deployment for {APP_NAME}...")
    preflight_check(c)
    update_code(c, git_url=git_url, branch=branch, local_source=local_source)
    build_and_migrate(
        c,
        find_links=find_links,
        index_url=index_url,
        trusted_host=trusted_host,
        use_dumpdata=use_dumpdata,
    )
    restart_services(c)
    health_check(c)
    print("🌟 Deployment completed successfully!")


@task
def setup_all(c, branch="main", git_url=None, local_source=None, find_links=None, index_url=None, trusted_host=None, remove_default_site=False, use_dumpdata=True, domain=None):
    """
    Full from-scratch server bootstrap:
    1. Preflight check
    2. Provision users, groups, directories, and caches
    3. Configure SSL certs and Nginx
    4. Install systemd process units
    5. Run initial deployment
    """
    ensure_sudo(c)
    print(f"🌟 Starting complete server bootstrap and deployment for {APP_NAME}...")
    preflight_check(c)
    provision_system(c)
    setup_nginx_and_ssl(c, domain=domain, remove_default_site=remove_default_site)
    setup_systemd(c)
    deploy(
        c,
        branch=branch,
        git_url=git_url,
        local_source=local_source,
        find_links=find_links,
        index_url=index_url,
        trusted_host=trusted_host,
        use_dumpdata=use_dumpdata,
    )
    print("🏁 Complete server bootstrap finished!")


if __name__ == "__main__":
    import sys
    task_name = sys.argv[1] if len(sys.argv) > 1 else "setup-all"
    host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("REASON_HOST", "localhost")
    try:
        from fabric import Connection
    except ImportError:
        print("❌ Fabric is required. Install with: pip install fabric")
        sys.exit(1)

    ssh_user = os.environ.get("REASON_SSH_USER")
    conn = Connection(host, user=ssh_user) if ssh_user else Connection(host)
    task_map = {
        "preflight-check": preflight_check,
        "preflight_check": preflight_check,
        "setup-all": setup_all,
        "setup_all": setup_all,
        "provision-system": provision_system,
        "provision_system": provision_system,
        "sync-env": sync_env,
        "sync_env": sync_env,
        "setup-nginx-and-ssl": setup_nginx_and_ssl,
        "setup_nginx_and_ssl": setup_nginx_and_ssl,
        "setup-systemd": setup_systemd,
        "setup_systemd": setup_systemd,
        "backup-database": backup_database,
        "backup_database": backup_database,
        "update-code": update_code,
        "update_code": update_code,
        "build-and-migrate": build_and_migrate,
        "build_and_migrate": build_and_migrate,
        "restart-services": restart_services,
        "restart_services": restart_services,
        "health-check": health_check,
        "health_check": health_check,
        "deploy": deploy,
    }
    func = task_map.get(task_name)
    if not func:
        print(f"Unknown task: '{task_name}'. Available tasks:\n  " + "\n  ".join(sorted(set(task_map.keys()))))
        sys.exit(1)
    func(conn)
