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
    fab -H localhost start-backend-containers
    fab -H localhost backup-database
    fab -H localhost createsuperuser
    fab -H localhost setup-nginx-and-ssl
    fab -H localhost restart-services
    fab -H localhost health-check
"""

import getpass
import io
import os
import shlex
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
    global BIN_DIR, MODELS_DIR, LLAMACPP_PORT
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
    BIN_DIR = os.environ.get("REASON_BIN_DIR", f"{BASE_DIR}/bin")
    MODELS_DIR = os.environ.get("REASON_MODELS_DIR", f"{BASE_DIR}/models")

    HF_CACHE_DIR = os.environ.get("REASON_HF_CACHE", "/var/lib/hf_home")
    DEPLOY_USER = os.environ.get("REASON_DEPLOY_USER", "verbal_deploy")
    DJANGO_USER = os.environ.get("REASON_APP_USER", "verbal_run")
    WEB_GROUP = os.environ.get("REASON_WEB_GROUP", "www-data")

    DOMAIN = os.environ.get("REASON_DOMAIN", "reason.andrew.com")
    GRANIAN_PORT = int(os.environ.get("REASON_GRANIAN_PORT", "8008"))
    INFERENCE_PORT = int(os.environ.get("REASON_INFERENCE_PORT", "8001"))
    LLAMACPP_PORT = int(os.environ.get("REASON_LLAMACPP_PORT", "8080"))
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


def ensure_deploy_ssh_key(c):
    """
    Ensures DEPLOY_USER (/home/verbal_deploy) has a dedicated SSH keypair.
    If none exists (neither id_ed25519 nor id_rsa), generates an ed25519 keypair
    with proper 0700/0600 permissions.
    Returns the public key string.
    """
    ensure_sudo(c)
    ssh_dir = f"/home/{DEPLOY_USER}/.ssh"
    key_file = f"{ssh_dir}/id_ed25519"
    pub_file = f"{key_file}.pub"
    rsa_pub = f"{ssh_dir}/id_rsa.pub"

    has_ed25519 = c.sudo(f"test -f {pub_file}", warn=True, hide=True).ok
    has_rsa = c.sudo(f"test -f {rsa_pub}", warn=True, hide=True).ok

    if not has_ed25519 and not has_rsa:
        print(f"🔑 Generating dedicated SSH deploy key for {DEPLOY_USER}...")
        c.sudo(f"mkdir -p {ssh_dir}")
        c.sudo(
            f"ssh-keygen -t ed25519 -N '' -C '{DEPLOY_USER}@reason-deploy' -f {key_file}",
            hide=True,
        )
        c.sudo(f"chown -R {DEPLOY_USER}:{DEPLOY_USER} {ssh_dir}")
        sudo_bash(c, f"chmod 0700 {ssh_dir} && chmod 0600 {key_file} && chmod 0644 {pub_file}")

    target_pub = pub_file if c.sudo(f"test -f {pub_file}", warn=True, hide=True).ok else rsa_pub
    pub_res = sudo_bash(c, f"cat {target_pub}", hide=True)
    return pub_res.stdout.strip() if pub_res.ok else ""


def print_deploy_key_guide_and_exit(c, target_git_url, error_message=""):
    """
    Displays an actionable banner showing the public key for DEPLOY_USER and
    step-by-step instructions for adding it as a Deploy Key, then exits cleanly.
    """
    pub_key = ensure_deploy_ssh_key(c)
    print("\n" + "=" * 80)
    print("🔑 [DEPLOY KEY REQUIRED] SSH Authentication to Git Repository Failed")
    print("=" * 80)
    print(f"The deployment user '{DEPLOY_USER}' could not authenticate to:")
    print(f"  {target_git_url}\n")
    if error_message:
        print(f"Git error details: {error_message.strip()}\n")
    print("To authorize this server, add the following public key as a Deploy Key (Read-Only):")
    print("-" * 80)
    print(f"{pub_key}")
    print("-" * 80)
    print("\nQuick setup steps:")
    print("  • Bitbucket: Repository Settings -> Access Keys -> Add key")
    print("  • GitHub:    Repository Settings -> Deploy Keys -> Add deploy key")
    print("  • GitLab:    Settings -> Repository -> Deploy keys -> Add key")
    print("\nOnce added, re-run:")
    print("  fab deploy  (or fab setup-all)")
    print("\n💡 Alternative (skip Git/deploy keys entirely):")
    print("  fab deploy --local-source=.  (syncs directly from your local directory)")
    print("=" * 80 + "\n")
    raise Exit(f"Git deploy key authorization required for {DEPLOY_USER} at {target_git_url}")


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
    bin_dir=BIN_DIR,
    models_dir=MODELS_DIR,
    hf_cache_dir=HF_CACHE_DIR,
    django_user=DJANGO_USER,
    web_group=WEB_GROUP,
    granian_port=GRANIAN_PORT,
    inference_port=INFERENCE_PORT,
    llamacpp_port=LLAMACPP_PORT,
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
        >>> "StartLimitIntervalSec=60s" in units["reason-inference.service"]
        True
        >>> "Environment=PYTHONUNBUFFERED=1" in units["reason-web.service"]
        True
    """
    env_app_file = f"{app_dir}/.env"
    env_app_secrets = f"{app_dir}/.env.secrets"
    env_app_prod = f"{app_dir}/.env.production"

    env_directives = f"""EnvironmentFile=-{env_app_file}
EnvironmentFile=-{env_app_secrets}
EnvironmentFile=-{env_app_prod}
Environment=PYTHONUNBUFFERED=1
Environment=REASON_LOG_DIR={log_dir}"""

    sandboxing = f"""NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths={shared_dir} {hf_cache_dir} {log_dir}"""

    master_service = f"""[Unit]
Description=Reason Cognitive Platform Master Service
Wants=reason-docker.service reason-web.service reason-worker.service reason-scheduler.service reason-inference.service
After=reason-docker.service

[Service]
Type=oneshot
ExecStart=/bin/true
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

    docker_unit = f"""[Unit]
Description=Reason Docker Infrastructure (PostgreSQL, Sandbox, Grobid)
After=network.target docker.service
Requires=docker.service
PartOf=reason.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={app_dir}
ExecStart=/usr/bin/docker compose up -d db sandbox
ExecStart=-/usr/bin/docker compose --profile academic up -d grobid
ExecStop=/usr/bin/docker compose stop

[Install]
WantedBy=multi-user.target reason.service
"""

    web_unit = f"""[Unit]
Description=Reason Web Server (Granian ASGI)
After=network.target docker.service reason-docker.service
Wants=network.target reason-docker.service
PartOf=reason.service
StartLimitIntervalSec=60s
StartLimitBurst=5

[Service]
Type=simple
User={django_user}
Group={web_group}
UMask=0002
WorkingDirectory={app_dir}
{env_directives}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=web
Environment=DJANGO_STATIC_ROOT={static_dir}
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/granian --interface asgi --host 127.0.0.1 --port {granian_port} verbal_config.asgi:application
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
LimitNOFILE=65535
{sandboxing}

[Install]
WantedBy=multi-user.target reason.service
"""

    worker_unit = f"""[Unit]
Description=Reason Background Task Worker
After=network.target docker.service reason-docker.service
Wants=network.target reason-docker.service
PartOf=reason.service
StartLimitIntervalSec=60s
StartLimitBurst=5

[Service]
Type=simple
User={django_user}
Group={web_group}
UMask=0002
WorkingDirectory={app_dir}
{env_directives}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=worker
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runtaskworker
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
{sandboxing}

[Install]
WantedBy=multi-user.target reason.service
"""

    scheduler_unit = f"""[Unit]
Description=Reason Task Scheduler (NightManager)
After=network.target docker.service reason-docker.service
Wants=network.target reason-docker.service
PartOf=reason.service
StartLimitIntervalSec=60s
StartLimitBurst=5

[Service]
Type=simple
User={django_user}
Group={web_group}
UMask=0002
WorkingDirectory={app_dir}
{env_directives}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=scheduler
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runtaskscheduler
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
{sandboxing}

[Install]
WantedBy=multi-user.target reason.service
"""

    inference_unit = f"""[Unit]
Description=Reason Local AI Model Inference Service
After=network.target docker.service reason-docker.service
Wants=network.target reason-docker.service
PartOf=reason.service
StartLimitIntervalSec=60s
StartLimitBurst=5

[Service]
Type=simple
User={django_user}
Group={web_group}
UMask=0002
WorkingDirectory={app_dir}
{env_directives}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=inference
Environment=PYTHONPATH={app_dir}
Environment=HF_HOME={hf_cache_dir}
ExecStart={venv_dir}/bin/python manage.py runserver --noreload 127.0.0.1:{inference_port}
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
{sandboxing}

[Install]
WantedBy=multi-user.target reason.service
"""

    llamacpp_unit = f"""[Unit]
Description=Reason llama.cpp CPU-First Local Inference Service
After=network.target
PartOf=reason.service
StartLimitIntervalSec=60s
StartLimitBurst=5

[Service]
Type=simple
User={django_user}
Group={web_group}
UMask=0002
WorkingDirectory={app_dir}
{env_directives}
Environment=VERBAL_ENV=prod
Environment=VERBAL_ROLE=inference
Environment=LLAMACPP_PORT={llamacpp_port}
ExecStart={bin_dir}/llama-server --host 127.0.0.1 --port {llamacpp_port}
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
{sandboxing}

[Install]
WantedBy=multi-user.target reason.service
"""

    return {
        "reason.service": master_service,
        "reason-docker.service": docker_unit,
        "reason-web.service": web_unit,
        "reason-worker.service": worker_unit,
        "reason-scheduler.service": scheduler_unit,
        "reason-inference.service": inference_unit,
        "reason-llamacpp.service": llamacpp_unit,
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

    env_shared = f"{SHARED_DIR}/.env"
    env_app = f"{APP_DIR}/.env"
    env_exists = c.sudo(f"test -f {env_shared} || test -f {env_app}", warn=True, hide=True).ok
    if not env_exists:
        print(f"⚠️ [WARNING] Production environment file not found at '{env_shared}' or '{env_app}'!")
        repo_root = Path(__file__).resolve().parent.parent
        if (repo_root / ".env").is_file():
            print("   💡 Tip: Run 'fab sync-env' to push your local .env to the server.")
        else:
            print("   Create .env from .env.example with secure keys, then run 'fab sync-env'.")
    else:
        # Check permissions on .env (must not be world-readable)
        target_env = env_shared if c.sudo(f"test -f {env_shared}", warn=True, hide=True).ok else env_app
        perm = sudo_bash(c, f"stat -c %a {target_env}", hide=True).stdout.strip()
        if perm in ["777", "666", "775", "664"]:
            print(f"🔒 Tightening permissions on {target_env} (was {perm})...")
            sudo_bash(c, f"chmod 0640 {target_env} && chown root:{WEB_GROUP} {target_env}")

    # Verify core system dependencies (python3, python3-venv, nginx)
    missing_packages = []

    py_check = c.run("python3 --version", warn=True, hide=True)
    if py_check.failed:
        missing_packages.append("python3")

    venv_check = c.run("python3 -m venv -h", warn=True, hide=True)
    if venv_check.failed:
        missing_packages.append("python3-venv")

    nginx_check = c.run("which nginx", warn=True, hide=True)
    if nginx_check.failed:
        missing_packages.append("nginx")

    if missing_packages:
        pkgs_str = " ".join(missing_packages)
        has_apt = c.run("command -v apt-get", warn=True, hide=True).ok
        if has_apt:
            print(f"📦 Target host missing required packages ({', '.join(missing_packages)}).")
            print(f"   Attempting auto-installation via apt-get...")
            install_res = c.sudo(f"apt-get update && apt-get install -y {pkgs_str}", warn=True)
            if install_res.failed:
                print(f"\n🚨 [PREFLIGHT ERROR] Could not automatically install: {pkgs_str}")
                print(f"   Please run manually on target host: sudo apt update && sudo apt install -y {pkgs_str}\n")
                raise Exit(f"Missing required packages: {pkgs_str}")
            print(f"✅ Successfully installed {pkgs_str}.")
        else:
            print(f"\n🚨 [PREFLIGHT ERROR] Missing required packages: {', '.join(missing_packages)}")
            print(f"   Please install them using your system package manager ({pkgs_str}).\n")
            raise Exit(f"Missing required packages: {pkgs_str}")

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

    # 2. Add deployer, daemon user, and connecting operator to web group for cooperative access
    c.sudo(f"usermod -aG {WEB_GROUP} {DEPLOY_USER}")
    c.sudo(f"usermod -aG {WEB_GROUP} {DJANGO_USER}")
    connecting_user = getattr(c, "user", None) or os.environ.get("USER", "")
    if connecting_user and connecting_user not in [DEPLOY_USER, DJANGO_USER, "root"]:
        c.sudo(f"usermod -aG {WEB_GROUP} {connecting_user}", warn=True)

    # Ensure DEPLOY_USER has an SSH keypair for Git repository access
    ensure_deploy_ssh_key(c)

    # 3. Create core directory structure
    c.sudo(f"mkdir -p {BASE_DIR} {REPO_DIR} {APP_DIR} {STATIC_DIR} {MEDIA_DIR} {BACKUP_DIR} {LOG_DIR} {SHARED_DIR} {BIN_DIR} {MODELS_DIR}")
    c.sudo(f"mkdir -p {SHARED_DIR}/workspaces {SHARED_DIR}/media {HF_CACHE_DIR}")

    # 4. Strict base directory lockdown (root owned)
    c.sudo(f"chown root:root {BASE_DIR}")
    c.sudo(f"chmod 0755 {BASE_DIR}")

    # 5. Persistent shared directories & HF Cache (SetGID 2775 + POSIX ACLs for vLLM container UID 2000)
    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {HF_CACHE_DIR}")
    sudo_bash(c, f"chmod -R 2775 {HF_CACHE_DIR}")
    # Apply default inheritance ACLs for UID 2000 (vLLM container) and WEB_GROUP if setfacl is installed
    sudo_bash(
        c,
        f"if command -v setfacl >/dev/null 2>&1; then "
        f"setfacl -R -m u:2000:rwx -m d:u:2000:rwx -m g:{WEB_GROUP}:rwx -m d:g:{WEB_GROUP}:rwx {HF_CACHE_DIR} 2>/dev/null || true; "
        f"fi",
        warn=True,
    )

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
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {BACKUP_DIR}")
    c.sudo(f"chmod 0750 {BACKUP_DIR}")
    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {LOG_DIR}")
    sudo_bash(c, f"chmod 2775 {LOG_DIR}")

    # Symlink /var/log/reason to LOG_DIR (/srv/reason/logs) for standard Linux convention
    c.sudo("mkdir -p /var/log")
    c.sudo(f"ln -sfn {LOG_DIR} /var/log/reason")

    # 9. Repo, App, Bin & Models directories
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {APP_DIR} {REPO_DIR} {BIN_DIR} {MODELS_DIR}")
    c.sudo(f"chmod 0750 {APP_DIR} {REPO_DIR}")
    sudo_bash(c, f"chmod 0755 {BIN_DIR} && chmod 0775 {MODELS_DIR}")

    # 10. Python Virtual Environment
    venv_pip = c.sudo(f"test -f {VENV_DIR}/bin/pip", warn=True)
    if venv_pip.failed:
        print("🐍 Creating dedicated Python virtual environment...")
        c.sudo(f"rm -rf {VENV_DIR}")
        c.sudo(f"python3 -m venv {VENV_DIR}")
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {VENV_DIR}")
    sudo_bash(c, f"chmod -R 0755 {VENV_DIR}")

    print("✅ System paths, users, and ownership hierarchy established.")


@task
def sync_env(c, local_env=None):
    """
    Safely pushes local application configuration (.env, .env.secrets) directly to the
    application directory at APP_DIR (/srv/reason/app/) with locked-down 0640 permissions.
    """
    ensure_sudo(c)
    repo_root = Path(__file__).resolve().parent.parent
    candidate = local_env or (repo_root / ".env") or (repo_root / ".env.deploy") or LOADED_ENV_FILE
    env_path = Path(candidate)
    if not env_path.is_file():
        raise Exit(f"Local environment file not found at: {env_path}")

    content = env_path.read_text(encoding="utf-8")
    c.sudo(f"mkdir -p {APP_DIR}")
    dest_path = f"{APP_DIR}/.env"
    print(f"📤 Uploading {env_path.name} to {dest_path} on remote server...")
    # Remove any existing symlink to ensure it is a clean regular file
    c.sudo(f"test -L {dest_path} && rm -f {dest_path} || true")
    write_sudo_file(c, dest_path, content, mode="0640", owner=f"{DEPLOY_USER}:{WEB_GROUP}")
    print(f"✅ Production environment configuration synchronized at {dest_path} (mode: 0640).")

    # If local .env.secrets exists, sync it too
    secrets_candidate = repo_root / ".env.secrets"
    if secrets_candidate.is_file():
        secrets_content = secrets_candidate.read_text(encoding="utf-8")
        secrets_dest = f"{APP_DIR}/.env.secrets"
        print(f"📤 Uploading .env.secrets to {secrets_dest}...")
        c.sudo(f"test -L {secrets_dest} && rm -f {secrets_dest} || true")
        write_sudo_file(c, secrets_dest, secrets_content, mode="0640", owner=f"{DJANGO_USER}:{WEB_GROUP}")
        print(f"✅ Production secrets synchronized at {secrets_dest} (mode: 0640).")


# ==============================================================================
# PHASE 2: SSL CERTIFICATES & NGINX
# ==============================================================================
@task
def setup_nginx_and_ssl(c, domain=None, source_ssl_dir=None, remove_default_site=True):
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

    # Remove default site to prevent "duplicate default server" port 80 collisions
    if remove_default_site and os.environ.get("REASON_REMOVE_DEFAULT_NGINX", "true").lower() not in ("false", "0"):
        print("🗑️ Removing default Nginx site configuration to prevent duplicate default server conflicts...")
        c.sudo("rm -f /etc/nginx/sites-enabled/default")
    else:
        print("ℹ️ Preserving /etc/nginx/sites-enabled/default.")

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
    Creates and enables modular systemd units for Reason/Verbal:
    1. reason.service           (Master application stack service for 'systemctl start reason')
    2. reason-docker.service    (Docker Compose infrastructure: DB, Sandbox, Grobid)
    3. reason-web.service       (Granian ASGI web server)
    4. reason-worker.service    (Background task worker)
    5. reason-scheduler.service (Periodic task scheduler / beat)
    6. reason-inference.service (Dedicated local AI inference endpoint)
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

    # Clean up legacy reason.target if it exists on host
    sudo_bash(c, "systemctl disable --now reason.target 2>/dev/null || true")
    c.sudo("rm -f /etc/systemd/system/reason.target")

    sudo_bash(c, "systemctl daemon-reload && systemctl enable reason.service reason-docker reason-web reason-worker reason-scheduler reason-inference")
    print("✅ Systemd services installed and registered for automatic boot startup.")



# ==============================================================================
# PHASE 4: PRE-MIGRATION DATABASE BACKUP
# ==============================================================================
@task
def backup_database(c):
    """
    Creates an automated pre-migration database snapshot prior to applying migrations,
    retaining the last 10 snapshots in /srv/reason/backups. Aborts deploy if backup fails.

    Uses Django's native ORM `dumpdata` with gzip compression:
    - Completely self-contained within Python/Django (no external pg_dump binary required).
    - Avoids dependency on custom/stripped container images or missing host client utilities.
    - Excludes transient ContentTypes and Permissions to prevent fixture collisions on restore.
    - Verified immediately for file integrity with `gzip -t`.
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

    # Prune older backups, keeping the most recent 10
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

        # Check if REPO_DIR exists and contains a valid git repository
        has_git_dir = c.sudo(f"test -d {REPO_DIR}/.git", warn=True, hide=True).ok
        is_git_repo = False
        if has_git_dir:
            is_git_repo = run_as_deploy_user(c, f"git -C {REPO_DIR} rev-parse --is-inside-work-tree", warn=True, hide=True).ok

        if not is_git_repo:
            if not target_git_url:
                raise Exit(f"No git repository found at {REPO_DIR} and no git_url / REASON_GIT_URL provided.")
            # If REPO_DIR exists (e.g. from previous aborted clone or non-git dir), wipe it for a clean clone
            if c.sudo(f"test -e {REPO_DIR}", warn=True).ok:
                print(f"🧹 Clearing non-git or uninitialized directory at {REPO_DIR} for clean clone...")
                c.sudo(f"rm -rf {REPO_DIR}")

            # Recreate empty REPO_DIR with deploy user ownership so git clone succeeds
            c.sudo(f"mkdir -p {REPO_DIR}")
            c.sudo(f"chown {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR}")
            c.sudo(f"chmod 0750 {REPO_DIR}")

            print(f"📥 Cloning repository from {target_git_url} as {DEPLOY_USER}...")
            clone_res = run_as_deploy_user(
                c,
                f"{git_ssh_cmd} git clone --branch {target_branch} {target_git_url} {REPO_DIR}",
                warn=True,
            )
            if not clone_res.ok:
                err_text = clone_res.stderr or clone_res.stdout or ""
                auth_keywords = ["permission denied", "publickey", "host key verification", "could not read from remote"]
                if any(x in err_text.lower() for x in auth_keywords):
                    print_deploy_key_guide_and_exit(c, target_git_url, error_message=err_text)
                else:
                    raise Exit(f"Git clone failed: {err_text.strip()}")
            c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR}")
            sudo_bash(c, f"chmod 0750 {REPO_DIR}")
        else:
            print(f"📥 Updating existing git repository at {REPO_DIR} as {DEPLOY_USER}...")
            if target_git_url:
                run_as_deploy_user(c, f"git -C {REPO_DIR} remote set-url origin {target_git_url}", warn=True)
            run_as_deploy_user(c, f"rm -f {REPO_DIR}/.git/index.lock", warn=True)
            fetch_res = run_as_deploy_user(
                c,
                f"{git_ssh_cmd} git -C {REPO_DIR} fetch --all --prune",
                warn=True,
            )
            if not fetch_res.ok:
                err_text = fetch_res.stderr or fetch_res.stdout or ""
                auth_keywords = ["permission denied", "publickey", "host key verification", "could not read from remote"]
                if any(x in err_text.lower() for x in auth_keywords):
                    fetch_url = target_git_url
                    if not fetch_url:
                        url_res = run_as_deploy_user(c, f"git -C {REPO_DIR} remote get-url origin", warn=True, hide=True)
                        fetch_url = url_res.stdout.strip() if url_res.ok else "origin"
                    print_deploy_key_guide_and_exit(c, fetch_url, error_message=err_text)
                else:
                    raise Exit(f"Git fetch failed: {err_text.strip()}")
            run_as_deploy_user(c, f"git -C {REPO_DIR} checkout -B {target_branch} origin/{target_branch}")
            run_as_deploy_user(c, f"git -C {REPO_DIR} reset --hard origin/{target_branch}")
            run_as_deploy_user(c, f"git -C {REPO_DIR} clean -fd")

        sudo_bash(c, f"rsync -ax --delete {RSYNC_EXCLUDES} {REPO_DIR}/ {APP_DIR}/")

    # Symlink shared workspaces and media
    c.sudo(f"ln -sfn {SHARED_DIR}/workspaces {APP_DIR}/workspaces")
    c.sudo(f"ln -sfn {SHARED_DIR}/media {APP_DIR}/media")

    # Clean up any legacy symlinks in APP_DIR/.env* to ensure regular local files are used
    for extra_env in [".env", ".env.secrets", ".env.production", ".env.deploy", ".env.local"]:
        app_extra = f"{APP_DIR}/{extra_env}"
        if c.sudo(f"test -L {app_extra}", warn=True, hide=True).ok:
            c.sudo(f"cp --remove-destination $(readlink -f {app_extra}) {app_extra} 2>/dev/null || rm -f {app_extra}")

    # Enforce Read-Only Code Boundaries
    print("🔒 Enforcing read-only code execution permissions...")
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {APP_DIR}")
    sudo_bash(c, f"find {APP_DIR} -type d -exec chmod 0750 {{}} +")
    sudo_bash(c, f"find {APP_DIR} -type f -exec chmod 0640 {{}} +")
    c.sudo(f"chmod +x {APP_DIR}/manage.py")

    print("✅ Application code updated and permission boundaries enforced.")


def get_remote_env(c, env_file=None):
    """
    Parses key-value pairs from remote environment files (.env, .env.secrets, etc.).
    Inspects both SHARED_DIR and APP_DIR to respect files located in either directory.
    """
    targets = [env_file] if env_file else [
        f"{SHARED_DIR}/.env",
        f"{SHARED_DIR}/.env.secrets",
        f"{APP_DIR}/.env",
        f"{APP_DIR}/.env.secrets",
        f"{APP_DIR}/.env.production",
    ]
    remote_env = {}
    for target in targets:
        if c.sudo(f"test -f {target}", warn=True, hide=True).ok:
            cat_res = c.sudo(f"cat {target}", warn=True, hide=True)
            if cat_res.ok and cat_res.stdout:
                for line in cat_res.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        remote_env[k.strip()] = v.strip().strip('"').strip("'")
    return remote_env


@task
def start_backend_containers(c):
    """
    Brings up core Docker services (PostgreSQL db, python sandbox) and optional services
    (Grobid academic PDF parser) before database migrations, and awaits database readiness.
    Idempotent: safely succeeds if backend containers are already running.
    """
    ensure_sudo(c)
    print("🐳 Starting Docker backend containers (db, sandbox)...")

    # 1. Bring up core containers (ensuring sandbox image is built before startup)
    sudo_bash(c, f"cd {APP_DIR} && docker compose build sandbox", warn=True)
    res = sudo_bash(c, f"cd {APP_DIR} && docker compose up -d db sandbox", warn=True)
    if not res.ok:
        # Check if database container is already running before failing
        db_running = sudo_bash(c, f"docker ps -q -f name={DB_CONTAINER} -f status=running", hide=True, warn=True)
        if not (db_running.ok and db_running.stdout.strip()):
            print(f"🚨 Failed to start backend containers:\n{res.stderr or res.stdout}")
            raise Exit("Failed to start Docker backend services.")
        else:
            print(f"ℹ️ Database container '{DB_CONTAINER}' is already running.")

    # 2. Check and start Grobid if enabled
    remote_env = get_remote_env(c)
    enable_grobid = (
        os.environ.get("ENABLE_GROBID")
        or remote_env.get("ENABLE_GROBID", "true")
    ).lower() not in ["0", "false", "off", "no"]

    if enable_grobid:
        print("Checking Grobid academic PDF container...")
        g_res = sudo_bash(c, f"cd {APP_DIR} && docker compose --profile academic up -d grobid", warn=True)
        if g_res.ok:
            print("✅ Grobid service started on port 8070.")
        else:
            print("⚠️ Grobid unavailable or blocked by security policies; continuing with basic services.")
    else:
        print("ℹ️ Grobid container disabled via ENABLE_GROBID=false; continuing with basic services.")

    # 3. Wait for PostgreSQL container to become ready (resolving credentials from remote_env)
    db_user = remote_env.get("DB_USER", DB_USER)
    db_name = remote_env.get("DB_NAME", DB_NAME)
    db_port = remote_env.get("DB_PORT", DB_PORT)

    print(f"⏳ Waiting for PostgreSQL container '{DB_CONTAINER}' to become healthy...")
    max_retries = 15
    retry_interval = 2
    is_ready = False
    for attempt in range(1, max_retries + 1):
        check = sudo_bash(
            c,
            f"docker exec {DB_CONTAINER} pg_isready -U {db_user} -d {db_name} 2>/dev/null || "
            f"pg_isready -h 127.0.0.1 -p {db_port} -U {db_user} 2>/dev/null",
            warn=True,
            hide=True,
        )
        if check.ok:
            is_ready = True
            print(f"✅ Database container '{DB_CONTAINER}' is healthy and accepting connections.")
            break
        time.sleep(retry_interval)

    if not is_ready:
        print(f"🚨 PostgreSQL container '{DB_CONTAINER}' did not report ready after {max_retries * retry_interval} seconds.")
        raise Exit(f"Database container {DB_CONTAINER} failed to become healthy.")



def build_pip_install_cmd(
    find_links=None,
    index_url=None,
    trusted_host=None,
    pytorch_index_url=None,
    remote_env=None,
    no_cache_dir=True,
    app_dir=APP_DIR,
    venv_dir=VENV_DIR,
):
    """
    Constructs a clean, transparent pip install command using pure Python string functions,
    resolving canonical index flags from CLI arguments, local environment, and remote .env.
    """
    remote = remote_env or {}
    fl = (find_links or os.environ.get("PIP_FIND_LINKS") or remote.get("PIP_FIND_LINKS", "")).strip()
    iu = (index_url or os.environ.get("PIP_INDEX_URL") or remote.get("PIP_INDEX_URL", "")).strip()
    th = (trusted_host or os.environ.get("PIP_TRUSTED_HOST") or remote.get("PIP_TRUSTED_HOST", "")).strip()
    pt = (pytorch_index_url or os.environ.get("PYTORCH_INDEX_URL") or remote.get("PYTORCH_INDEX_URL", "")).strip()

    flags = [f"--find-links {app_dir}/resources/wheels"]
    if fl:
        flags.append(f"--find-links {fl}")
    if iu:
        flags.append(f"--index-url {iu}")
    if th:
        flags.append(f"--trusted-host {th}")
    if pt and pt.lower() not in ["none", "off", "false", "disabled", ""]:
        flags.append(f"--extra-index-url {pt}")
    if no_cache_dir:
        flags.append("--no-cache-dir")

    flags_str = " ".join(flags)
    return f"{venv_dir}/bin/pip install {flags_str} -r {app_dir}/requirements.txt"


# ==============================================================================
# PHASE 4B: BARE-METAL LLAMA.CPP COMPILATION & INSTALLATION
# ==============================================================================
@task
def build_llamacpp(c, source_bundle=None, threads=None):
    """
    Compiles llama.cpp (llama-server) from a local source bundle in resources/sources/
    and installs the optimized native binary into /srv/reason/bin/llama-server.
    
    Verifies build prerequisites (gcc/g++, make, cmake) with clear remediation guidance,
    compiles with native CPU vectorization (AVX2/AVX-512), and tests the resulting binary.
    """
    ensure_sudo(c)
    print("🔨 [llama.cpp] Commencing bare-metal build for llama-server...")

    # 1. Ensure target directories exist
    build_dir = f"{BASE_DIR}/llama.cpp"
    c.sudo(f"mkdir -p {BIN_DIR} {MODELS_DIR} {build_dir}")
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {BIN_DIR} {MODELS_DIR} {build_dir}")

    # 2. Check for build prerequisites on target host
    missing_prereqs = []

    compiler_check = c.run("which gcc || which g++", warn=True, hide=True)
    if compiler_check.failed:
        missing_prereqs.append("gcc/g++ (C++17 capable compiler from build-essential)")

    make_check = c.run("which make", warn=True, hide=True)
    if make_check.failed:
        missing_prereqs.append("make (build automation tool)")

    cmake_check = c.run("which cmake", warn=True, hide=True)
    has_cmake = cmake_check.ok
    if not has_cmake:
        venv_cmake = c.sudo(f"test -f {VENV_DIR}/bin/cmake", warn=True, hide=True).ok
        if not venv_cmake:
            missing_prereqs.append("cmake (version >= 3.14)")

    if missing_prereqs:
        print("\n" + "=" * 80)
        print("🚨 [PREFLIGHT ERROR] Missing build prerequisites on target host:")
        print("=" * 80)
        for p in missing_prereqs:
            print(f"   ❌ Missing: {p}")
        print("\n🛠️  Remediation Instructions:")
        print("1. Standard Ubuntu / Debian system package manager (recommended, no PPAs needed):")
        print("   sudo apt update && sudo apt install -y build-essential cmake")
        print("\n2. If CMake is missing or restricted in apt, install CMake via PyPI / Artifactory:")
        print(f"   {VENV_DIR}/bin/pip install cmake")
        print("   (or 'pip install cmake' in your Python environment)")
        print("\n3. If running on RedHat / Fedora / CentOS:")
        print("   sudo dnf install -y gcc-c++ make cmake")
        print("=" * 80 + "\n")
        raise Exit(f"Missing build prerequisites: {', '.join(missing_prereqs)}")

    # 3. Locate source bundle in local repository
    repo_root = Path(__file__).resolve().parent.parent
    candidate_dirs = [
        repo_root / "resources" / "sources",
        repo_root / "resource" / "sources",
        repo_root / "resources",
        repo_root / "resource",
        Path.cwd() / "resources" / "sources",
        Path.cwd() / "resource" / "sources",
    ]

    archive_path = None
    if source_bundle:
        sb_p = Path(source_bundle)
        if sb_p.is_file() or sb_p.is_dir():
            archive_path = sb_p.resolve()
        else:
            raise Exit(f"Specified source bundle not found: {source_bundle}")
    else:
        for cdir in candidate_dirs:
            if not cdir.is_dir():
                continue
            for pat in [
                "llama*.tar.gz", "llama*.tgz", "llama*.tar.bz2", "llama*.tar.xz",
                "llama*.zip", "*llama.cpp*.tar.gz", "*llama.cpp*.zip", "*llama.cpp*.tgz"
            ]:
                matches = list(cdir.glob(pat))
                if matches:
                    archive_path = matches[0].resolve()
                    break
            if archive_path:
                break
            llama_dirs = [d for d in cdir.iterdir() if d.is_dir() and "llama" in d.name.lower()]
            if llama_dirs and (llama_dirs[0] / "CMakeLists.txt").is_file():
                archive_path = llama_dirs[0].resolve()
                break

    remote_src_exists = c.sudo(f"test -f {build_dir}/CMakeLists.txt", warn=True, hide=True).ok

    if not archive_path and not remote_src_exists:
        print("\n" + "=" * 80)
        print("📢 [ACTION REQUIRED] No llama.cpp source bundle found in local repository!")
        print("=" * 80)
        print("Please download a release source bundle of llama.cpp (e.g. from GitHub releases):")
        print("   https://github.com/ggerganov/llama.cpp/releases")
        print("and place the .tar.gz, .tgz, .zip, or extracted folder into either:")
        print(f"   {repo_root}/resource/sources/")
        print(f"   or: {repo_root}/resources/sources/")
        print("   (e.g. resource/sources/llama-b4000.tar.gz)")
        print("\nAlternatively, specify the bundle path explicitly via CLI:")
        print("   fab build-llamacpp --source-bundle=/path/to/llama.cpp.tar.gz")
        print("=" * 80 + "\n")
        raise Exit("Missing llama.cpp source bundle in resource/sources/ or resources/sources/.")

    # 4. Upload & Extract source bundle if local archive is present
    if archive_path:
        print(f"📦 Uploading source bundle ({archive_path.name}) to target host...")
        remote_tmp = f"/tmp/{archive_path.name}"
        c.put(str(archive_path), remote_tmp)

        c.sudo(f"rm -rf {build_dir}")
        c.sudo(f"mkdir -p {build_dir}")

        name_lower = archive_path.name.lower()
        if name_lower.endswith((".tar.gz", ".tgz")):
            print("📂 Extracting tar.gz archive...")
            sudo_bash(c, f"tar -xzf {remote_tmp} --strip-components=1 -C {build_dir} 2>/dev/null || tar -xzf {remote_tmp} -C {build_dir}")
        elif name_lower.endswith(".tar.bz2"):
            print("📂 Extracting tar.bz2 archive...")
            sudo_bash(c, f"tar -xjf {remote_tmp} --strip-components=1 -C {build_dir} 2>/dev/null || tar -xjf {remote_tmp} -C {build_dir}")
        elif name_lower.endswith(".tar.xz"):
            print("📂 Extracting tar.xz archive...")
            sudo_bash(c, f"tar -xJf {remote_tmp} --strip-components=1 -C {build_dir} 2>/dev/null || tar -xJf {remote_tmp} -C {build_dir}")
        elif name_lower.endswith(".zip"):
            print("📂 Extracting zip archive...")
            sudo_bash(c, f"unzip -q -o {remote_tmp} -d {build_dir}")
            sudo_bash(c, f"if [ ! -f {build_dir}/CMakeLists.txt ]; then mv {build_dir}/*/* {build_dir}/ 2>/dev/null || true; fi")

        c.run(f"rm -f {remote_tmp}", warn=True)
        c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {build_dir}")
        sudo_bash(c, f"chmod -R 0755 {build_dir}")

    # 5. Compile in-place with native CPU vectorization
    nproc = threads or c.run("nproc", hide=True).stdout.strip() or "4"
    print(f"⚙️ Compiling llama-server with Release optimizations (-DGGML_CUDA=OFF -DGGML_NATIVE=ON, {nproc} threads)...")

    cmake_bin = "cmake"
    if c.run("which cmake", warn=True, hide=True).failed:
        cmake_bin = f"{VENV_DIR}/bin/cmake"

    cmake_configure_cmd = f"{cmake_bin} -B build -DGGML_CUDA=OFF -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release"
    print(f"   [configure] {cmake_configure_cmd}")
    conf_res = run_as_deploy_user(c, cmake_configure_cmd, cwd=build_dir, warn=True)
    if conf_res.failed:
        print("\n" + "=" * 80)
        print("🚨 [BUILD ERROR] CMake configuration failed for llama.cpp!")
        print("=" * 80)
        print(f"Command: {cmake_configure_cmd}")
        print(f"Directory: {build_dir}")
        print(f"Error Details:\n{conf_res.stderr or conf_res.stdout}")
        print("\nCommon Causes & Remediation:")
        print(" 1. Missing C/C++ compiler or standard headers:")
        print("    Run: sudo apt update && sudo apt install -y build-essential")
        print(" 2. Outdated CMake version:")
        print("    Run: sudo apt install -y cmake   (or: pip install cmake)")
        print(" 3. Corrupted source bundle extraction:")
        print(f"    Check that {build_dir}/CMakeLists.txt is valid.")
        print("=" * 80 + "\n")
        raise Exit("CMake configuration failed for llama.cpp.")

    cmake_build_cmd = f"{cmake_bin} --build build --config Release -j {nproc} --target llama-server"
    print(f"   [build] {cmake_build_cmd}")
    build_res = run_as_deploy_user(c, cmake_build_cmd, cwd=build_dir, warn=True)
    if build_res.failed:
        print("\n" + "=" * 80)
        print("🚨 [BUILD ERROR] Compilation failed for llama-server!")
        print("=" * 80)
        print(f"Command: {cmake_build_cmd}")
        print(f"Error Details:\n{build_res.stderr or build_res.stdout}")
        print("\nCommon Causes & Remediation:")
        print(" 1. Out-of-memory (OOM) error during parallel compilation:")
        print("    If compilation was killed or crashed, re-run with fewer threads:")
        print("    fab build-llamacpp --threads=2")
        print(" 2. Missing standard C++ libraries or headers:")
        print("    Run: sudo apt install -y g++ libstdc++-12-dev")
        print("=" * 80 + "\n")
        raise Exit("Compilation failed for llama-server.")

    # 6. Locate and install binary
    built_binary = f"{build_dir}/build/bin/llama-server"
    if not c.sudo(f"test -f {built_binary}", warn=True, hide=True).ok:
        alt_check = sudo_bash(c, f"find {build_dir}/build -name llama-server -type f | head -n 1", hide=True)
        if alt_check.ok and alt_check.stdout.strip():
            built_binary = alt_check.stdout.strip()
        else:
            raise Exit(f"Compiled binary 'llama-server' not found under {build_dir}/build.")

    dest_binary = f"{BIN_DIR}/llama-server"
    print(f"🚀 Installing native binary to {dest_binary}...")
    c.sudo(f"install -m 0755 -o {DEPLOY_USER} -g {WEB_GROUP} {built_binary} {dest_binary}")

    sudo_bash(
        c,
        f"find $(dirname {built_binary}) -name '*.so*' -type f -exec cp -d {{}} {BIN_DIR}/ \\; 2>/dev/null || true",
        warn=True,
    )
    sudo_bash(c, f"chmod 0755 {BIN_DIR}/*.so* 2>/dev/null || true")

    # 7. Test and verify execution
    print("🔍 Testing installed llama-server binary...")
    test_exec = run_as_deploy_user(c, f"{dest_binary} --help | head -n 10", warn=True, hide=True)
    if test_exec.failed:
        print(f"⚠️ Binary test returned non-zero. Check CPU compatibility:\n{test_exec.stderr or test_exec.stdout}")
    else:
        print("✅ llama-server binary verified and ready for execution.")

    print(f"\n🎉 Successfully compiled and installed llama-server at {dest_binary}!")
    print(f"   Models directory: {MODELS_DIR}/ (place .gguf files here)\n")


@task
def build_and_migrate(c, find_links=None, index_url=None, trusted_host=None, pytorch_index_url=None):
    """
    Installs Python dependencies, verifies deployment health, executes
    Django migrations, and collects static assets into STATIC_DIR.
    Supports Artifactory wheel links (--find-links), PyTorch extra indices, and custom mirrors.
    """
    ensure_sudo(c)
    print("⚡ Running Python dependency updates and Django maintenance...")

    # Ensure vendored spaCy model wheel (en_core_web_sm) is available and valid for pip
    spacy_wheel_name = "en_core_web_sm-3.8.0-py3-none-any.whl"
    spacy_wheel_app = f"{APP_DIR}/resources/wheels/{spacy_wheel_name}"
    spacy_wheel_repo = f"{REPO_DIR}/resources/wheels/{spacy_wheel_name}"
    env_file = f"{APP_DIR}/.env"

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

    # Read any persistent remote environment overrides if available
    remote_env = get_remote_env(c, env_file=env_file)

    # Build pip installation command purely in Python for complete transparency
    pip_install_cmd = build_pip_install_cmd(
        find_links=find_links,
        index_url=index_url,
        trusted_host=trusted_host,
        pytorch_index_url=pytorch_index_url,
        remote_env=remote_env,
    )
    print(f"\n📦 [pip install] Executing command as {DEPLOY_USER}:")
    print(f"   {pip_install_cmd}\n")
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
    backup_database(c)

    # Run migrations as deployer
    print("🗄️ Applying Django database migrations...")
    run_as_deploy_user(c, f"{VENV_DIR}/bin/python {APP_DIR}/manage.py migrate --noinput", cwd=APP_DIR)

    # Verify or guide superuser setup
    check_or_create_superuser(c, remote_env=remote_env)

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

    # Ensure virtual environment packages and binaries are readable and executable by daemon user
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {VENV_DIR}")
    sudo_bash(c, f"chmod -R 0755 {VENV_DIR}")

    print("✅ Build, migrations, and static asset collection completed.")


def check_or_create_superuser(c, remote_env=None):
    """
    Verifies if a Django superuser exists in the database.
    If none exists:
      - Automatically creates one if DJANGO_SUPERUSER_USERNAME & DJANGO_SUPERUSER_PASSWORD are set.
      - Otherwise prints an actionable guidance banner instructing the operator how to create one.
    """
    remote = remote_env if remote_env is not None else get_remote_env(c)

    # Check if a superuser already exists
    check_cmd = (
        f"VERBAL_ROLE=web {VENV_DIR}/bin/python {APP_DIR}/manage.py shell -c "
        f"\"from django.contrib.auth import get_user_model; "
        f"print('EXISTS' if get_user_model().objects.filter(is_superuser=True).exists() else 'MISSING')\""
    )
    check_res = run_as_deploy_user(c, check_cmd, cwd=APP_DIR, warn=True, hide=True)
    if check_res.ok and "EXISTS" in (check_res.stdout or ""):
        print("👤 Verified existing Django superuser account.")
        return

    # Check for credentials in env or remote_env
    su_user = (os.environ.get("DJANGO_SUPERUSER_USERNAME") or remote.get("DJANGO_SUPERUSER_USERNAME", "")).strip()
    su_pass = (os.environ.get("DJANGO_SUPERUSER_PASSWORD") or remote.get("DJANGO_SUPERUSER_PASSWORD", "")).strip()
    su_email = (os.environ.get("DJANGO_SUPERUSER_EMAIL") or remote.get("DJANGO_SUPERUSER_EMAIL", "")).strip()

    if su_user and su_pass:
        print(f"👤 Creating initial Django superuser '{su_user}' from environment credentials...")
        email_flag = f"--email {shlex.quote(su_email)}" if su_email else ""
        create_cmd = (
            f"DJANGO_SUPERUSER_PASSWORD={shlex.quote(su_pass)} "
            f"{VENV_DIR}/bin/python {APP_DIR}/manage.py createsuperuser "
            f"--noinput --username {shlex.quote(su_user)} {email_flag}"
        )
        create_res = run_as_deploy_user(c, create_cmd, cwd=APP_DIR, warn=True)
        if create_res.ok:
            print(f"✅ Initial Django superuser '{su_user}' created successfully.")
            return
        elif "already" in (create_res.stderr or create_res.stdout or "").lower():
            print(f"👤 Django superuser '{su_user}' already exists.")
            return
        else:
            print(f"⚠️ Failed to auto-create superuser '{su_user}': {create_res.stderr or create_res.stdout}")

    # No credentials provided and no superuser exists: display guidance banner
    print("\n" + "=" * 70)
    print("📢 [ACTION REQUIRED] No Django superuser found in database!")
    print("   To log in to Django Admin and configure AI models, create an")
    print("   administrative account using one of the following methods:")
    print("")
    print("   1. From your local deployment workstation:")
    print("      fab -H <host> createsuperuser")
    print("")
    print("   2. Or directly on the server:")
    print(f"      sudo -u {DEPLOY_USER} {VENV_DIR}/bin/python {APP_DIR}/manage.py createsuperuser")
    print("=" * 70 + "\n")


@task
def createsuperuser(c, username=None, email=None):
    """
    Guides the deployer through creating an administrative superuser account
    on the remote server, prompting securely for credentials if not supplied.
    """
    ensure_sudo(c)
    print("👤 Setting up Django superuser...")

    target_user = username or os.environ.get("DJANGO_SUPERUSER_USERNAME")
    if not target_user:
        target_user = input("Superuser username [admin]: ").strip() or "admin"

    target_email = email if email is not None else os.environ.get("DJANGO_SUPERUSER_EMAIL")
    if target_email is None:
        target_email = input("Superuser email address (optional): ").strip()

    target_password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
    if not target_password:
        while True:
            p1 = getpass.getpass(f"Password for '{target_user}': ")
            if not p1:
                print("❌ Password cannot be empty.")
                continue
            p2 = getpass.getpass("Password (again): ")
            if p1 != p2:
                print("❌ Passwords do not match. Please try again.")
                continue
            target_password = p1
            break

    email_opt = f"--email {shlex.quote(target_email)}" if target_email else ""
    escaped_pw = shlex.quote(target_password)
    escaped_user = shlex.quote(target_user)

    cmd = (
        f"DJANGO_SUPERUSER_PASSWORD={escaped_pw} "
        f"{VENV_DIR}/bin/python {APP_DIR}/manage.py createsuperuser "
        f"--noinput --username {escaped_user} {email_opt}"
    )
    res = run_as_deploy_user(c, cmd, cwd=APP_DIR, warn=True)
    if res.ok:
        print(f"✅ Superuser '{target_user}' created successfully.")
    elif "already" in (res.stderr or res.stdout or "").lower():
        print(f"ℹ️ Superuser '{target_user}' already exists in database.")
    else:
        print(f"❌ Failed to create superuser:\n{res.stderr or res.stdout}")
        raise Exit(f"Failed to create superuser '{target_user}'.")


# ==============================================================================
# PHASE 6: PROCESS RESTART & HEALTH VALIDATION
# ==============================================================================
@task
def restart_services(c):
    """
    Gracefully restarts the supervised systemd services.
    """
    ensure_sudo(c)
    print("🔄 Restarting Reason systemd services...")
    sudo_bash(c, "systemctl restart reason-docker.service", warn=True)
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

    raise Exit(f"🚨 Health check failed after {max_retries} attempts! Inspect logs via 'fab logs -s reason-web' or 'journalctl -u reason-web'.")


@task
def logs(c, service="all", lines=50, follow=False, errors_only=False):
    """
    Displays or follows aggregated or service-specific logs from systemd journald and /srv/reason/logs.

    Arguments:
        service: Service alias or name: 'all', 'web', 'worker', 'scheduler', 'inference', 'llamacpp',
                 or full systemd unit name like 'reason-inference'. Default: 'all'.
        lines: Number of recent lines to display. Default: 50.
        follow: If True, stream logs live (-f). Default: False.
        errors_only: If True, filter for errors only (-p err..alert). Default: False.

    Examples:
        fab logs
        fab logs -s inference
        fab logs -s web -f
        fab logs -s worker -l 100 --errors-only
    """
    ensure_sudo(c)

    service_alias_map = {
        "all": [
            "reason-web.service",
            "reason-inference.service",
            "reason-worker.service",
            "reason-scheduler.service",
            "reason-llamacpp.service",
        ],
        "web": ["reason-web.service"],
        "inference": ["reason-inference.service"],
        "worker": ["reason-worker.service"],
        "scheduler": ["reason-scheduler.service"],
        "llamacpp": ["reason-llamacpp.service"],
        "docker": ["reason-docker.service"],
    }

    key = str(service).strip().lower()
    if key in service_alias_map:
        target_units = service_alias_map[key]
    elif key.startswith("reason-"):
        unit_name = key if key.endswith(".service") else f"{key}.service"
        target_units = [unit_name]
    else:
        target_units = [f"reason-{key}.service"]

    units_flag = " ".join([f"-u {u}" for u in target_units])
    lines_val = int(lines)
    n_flag = f"-n {lines_val}"
    f_flag = "-f" if follow else "--no-pager"
    p_flag = "-p err..alert" if errors_only else ""

    display_targets = ", ".join(target_units)
    print(f"📋 [Logs] Displaying logs for: {display_targets} (lines: {lines_val}, follow: {follow})")
    print(f"💡 Tip: File logs are also available at {LOG_DIR}/ and /var/log/reason/\n")

    cmd_parts = ["journalctl", units_flag, n_flag]
    if p_flag:
        cmd_parts.append(p_flag)
    cmd_parts.append(f_flag)
    journal_cmd = " ".join(cmd_parts)
    c.sudo(journal_cmd, pty=True)


# ==============================================================================
# HIGH-LEVEL ORCHESTRATION PIPELINES
# ==============================================================================
@task
def deploy(c, branch="main", git_url=None, local_source=None, find_links=None, index_url=None, trusted_host=None, pytorch_index_url=None):
    """
    Standard zero-downtime deployment pipeline:
    1. Preflight check
    2. Synchronize code
    3. Start backend infrastructure containers (db, sandbox) and await database health
    4. Update dependencies, backup database & run migrations
    5. Restart systemd services
    6. Validate system health
    """
    ensure_sudo(c)
    print(f"🚀 Commencing deployment for {APP_NAME}...")
    preflight_check(c)
    update_code(c, git_url=git_url, branch=branch, local_source=local_source)
    start_backend_containers(c)
    build_and_migrate(
        c,
        find_links=find_links,
        index_url=index_url,
        trusted_host=trusted_host,
        pytorch_index_url=pytorch_index_url,
    )
    restart_services(c)
    health_check(c)
    print("🌟 Deployment completed successfully!")


@task
def setup_all(c, branch="main", git_url=None, local_source=None, find_links=None, index_url=None, trusted_host=None, pytorch_index_url=None, remove_default_site=True, domain=None):
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
        pytorch_index_url=pytorch_index_url,
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
        "start-backend-containers": start_backend_containers,
        "start_backend_containers": start_backend_containers,
        "update-code": update_code,
        "update_code": update_code,
        "build-and-migrate": build_and_migrate,
        "build_and_migrate": build_and_migrate,
        "createsuperuser": createsuperuser,
        "create-superuser": createsuperuser,
        "create_superuser": createsuperuser,
        "restart-services": restart_services,
        "restart_services": restart_services,
        "health-check": health_check,
        "health_check": health_check,
        "build-llamacpp": build_llamacpp,
        "build_llamacpp": build_llamacpp,
        "logs": logs,
        "deploy": deploy,
    }
    func = task_map.get(task_name)
    if not func:
        print(f"Unknown task: '{task_name}'. Available tasks:\n  " + "\n  ".join(sorted(set(task_map.keys()))))
        sys.exit(1)
    func(conn)
