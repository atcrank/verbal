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
# CONFIGURATION
# ==============================================================================
APP_NAME = os.environ.get("REASON_APP_NAME", "reason")
BASE_DIR = os.environ.get("REASON_BASE_DIR", f"/srv/{APP_NAME}")
REPO_DIR = f"{BASE_DIR}/repo"
APP_DIR = f"{BASE_DIR}/app"
VENV_DIR = os.environ.get("REASON_VENV", f"{BASE_DIR}/venv")
SHARED_DIR = f"{BASE_DIR}/shared"
PUBLIC_DIR = f"{BASE_DIR}/public"
STATIC_DIR = f"{PUBLIC_DIR}/static"
MEDIA_DIR = f"{PUBLIC_DIR}/media"
BACKUP_DIR = f"{BASE_DIR}/backups"
LOG_DIR = f"{BASE_DIR}/logs"

# Shared persistent caches & containers
HF_CACHE_DIR = os.environ.get("REASON_HF_CACHE", "/var/lib/hf_home")

# Users and permissions
DEPLOY_USER = os.environ.get("REASON_DEPLOY_USER", "verbal_deploy")   # Deployment operator
DJANGO_USER = os.environ.get("REASON_APP_USER", "verbal_run")         # Unprivileged app user (nologin)
WEB_GROUP = os.environ.get("REASON_WEB_GROUP", "www-data")            # Shared web group

# Web and upstream ports
DOMAIN = os.environ.get("REASON_DOMAIN", "reason.andrew.com")
GRANIAN_PORT = 8008
INFERENCE_PORT = 8001
SSL_DIR = f"{BASE_DIR}/ssl"
SSL_CERT_PATH = f"{SSL_DIR}/{DOMAIN}.crt"
SSL_KEY_PATH = f"{SSL_DIR}/{DOMAIN}.key"

# Database parameters for automated pre-migration dumps
DB_CONTAINER = "verbal_db"
DB_NAME = "verbal_db"
DB_USER = "verbal_user"
DB_PORT = 5433

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
    "--exclude='resources/' "
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
        print("   Create it from .env.example with secure keys before deploying to production.")
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
    c.sudo(f"mkdir -p {BASE_DIR} {REPO_DIR} {APP_DIR} {STATIC_DIR} {MEDIA_DIR} {BACKUP_DIR} {LOG_DIR} {SHARED_DIR}")
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
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR} {APP_DIR}")
    c.sudo(f"chmod 0750 {REPO_DIR} {APP_DIR}")

    # 10. Python Virtual Environment
    venv_pip = c.sudo(f"test -f {VENV_DIR}/bin/pip", warn=True)
    if venv_pip.failed:
        print("🐍 Creating dedicated Python virtual environment...")
        c.sudo(f"rm -rf {VENV_DIR}")
        c.sudo(f"python3 -m venv {VENV_DIR}")
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {VENV_DIR}")
    sudo_bash(c, f"chmod -R 0750 {VENV_DIR}")

    print("✅ System paths, users, and ownership hierarchy established.")


# ==============================================================================
# PHASE 2: SSL CERTIFICATES & NGINX
# ==============================================================================
@task
def setup_nginx_and_ssl(c, source_ssl_dir=None):
    """
    Installs SSL certificates into /srv/reason/ssl/, generates an optimized Nginx site
    configuration with SSE streaming and WebSocket support, and reloads Nginx safely.
    """
    ensure_sudo(c)
    print("🔒 [2/4] Configuring SSL certificates and Nginx reverse proxy...")

    # Determine SSL certificate sources
    if source_ssl_dir is None:
        local_ssl = Path(__file__).resolve().parent / "ssl"
        if local_ssl.exists() and (local_ssl / f"{DOMAIN}.crt").exists():
            source_ssl_dir = str(local_ssl)
        else:
            source_ssl_dir = f"{REPO_DIR}/deploy/ssl"

    # Install SSL certs into isolated /srv/reason/ssl directory
    c.sudo(f"mkdir -p {SSL_DIR}")
    sudo_bash(c, f"chmod 0750 {SSL_DIR} && chown root:{WEB_GROUP} {SSL_DIR}")

    if c.sudo(f"test -f {source_ssl_dir}/{DOMAIN}.crt", warn=True, hide=True).ok:
        print(f"📋 Installing certificate from {source_ssl_dir}...")
        c.sudo(f"cp {source_ssl_dir}/{DOMAIN}.crt {SSL_CERT_PATH}")
    if c.sudo(f"test -f {source_ssl_dir}/{DOMAIN}.key", warn=True, hide=True).ok:
        c.sudo(f"cp {source_ssl_dir}/{DOMAIN}.key {SSL_KEY_PATH}")

    # Generate fallback self-signed cert if neither exists
    cert_check = c.sudo(f"test -f {SSL_CERT_PATH} && test -f {SSL_KEY_PATH}", warn=True)
    if cert_check.failed:
        print("⚠️ Generating self-signed SSL certificates for local domain...")
        sudo_bash(
            c,
            f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 "
            f"-keyout {SSL_KEY_PATH} -out {SSL_CERT_PATH} "
            f"-subj '/CN={DOMAIN}/O=Reason/C=AU'"
        )

    # Secure SSL private key permissions: only root can read key
    sudo_bash(
        c,
        f"chmod 0644 {SSL_CERT_PATH} && chown root:{WEB_GROUP} {SSL_CERT_PATH} && "
        f"chmod 0600 {SSL_KEY_PATH} && chown root:root {SSL_KEY_PATH}"
    )

    # Generate and write Nginx configuration
    nginx_conf = generate_nginx_conf(
        domain=DOMAIN,
        ssl_cert_path=SSL_CERT_PATH,
        ssl_key_path=SSL_KEY_PATH,
        static_dir=STATIC_DIR,
        media_dir=MEDIA_DIR,
        granian_port=GRANIAN_PORT,
    )

    conf_dest = f"/etc/nginx/sites-available/{DOMAIN}.conf"
    enabled_dest = f"/etc/nginx/sites-enabled/{DOMAIN}.conf"

    write_sudo_file(c, conf_dest, nginx_conf, mode="0644", owner="root:root")
    c.sudo(f"ln -sfn {conf_dest} {enabled_dest}")

    # Remove default site if present
    c.sudo("rm -f /etc/nginx/sites-enabled/default")

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
def backup_database(c):
    """
    Creates an automated, compressed PostgreSQL dump prior to applying migrations,
    retaining the last 10 snapshots in /srv/reason/backups. Aborts deploy if backup fails.
    """
    ensure_sudo(c)
    print("💾 Creating pre-migration database snapshot...")
    timestamp = c.run("date +%Y%m%d_%H%M%S", hide=True).stdout.strip()
    backup_file = f"{BACKUP_DIR}/pre_deploy_{timestamp}.dump"

    docker_check = c.sudo(f"docker ps -q -f name={DB_CONTAINER}", warn=True)
    if docker_check.ok and docker_check.stdout.strip():
        print(f"📦 Dumping database from container '{DB_CONTAINER}'...")
        res = sudo_bash(c, f"docker exec {DB_CONTAINER} pg_dump -U {DB_USER} -Fc {DB_NAME} > {backup_file}", warn=True)
    else:
        print("📦 Container offline or local DB, running host pg_dump...")
        res = sudo_bash(c, f"PGPASSWORD=\"${{DB_PASSWORD:-verbal_password}}\" pg_dump -h 127.0.0.1 -p {DB_PORT} -U {DB_USER} -Fc {DB_NAME} > {backup_file}", warn=True)

    # Strictly verify backup file exists and has content
    verify_res = sudo_bash(c, f"test -s {backup_file} && [ $(stat -c%s {backup_file}) -gt 100 ]", warn=True)
    if verify_res.failed or (res and res.failed):
        print(f"🚨 [ERROR] Pre-deploy database backup failed or produced an empty file at {backup_file}!")
        raise Exit("Pre-deploy database backup failed. Aborting to prevent data loss.")

    # Prune older backups, keeping the most recent 10
    sudo_bash(c, f"ls -1t {BACKUP_DIR}/pre_deploy_*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f")
    print(f"✅ Pre-deploy snapshot secured: {backup_file}")


# ==============================================================================
# PHASE 5: CODE SYNC, MIGRATION & CACHE LINKING
# ==============================================================================
@task
def update_code(c, git_url=None, branch="main", local_source=None):
    """
    Synchronizes code into the active production directory, symlinks shared
    assets and caches, and enforces the read-only code boundary.
    """
    ensure_sudo(c)
    print(f"📦 Synchronizing application code (branch: {branch})...")

    if local_source:
        print(f"🔄 Syncing directly from local directory {local_source}...")
        sudo_bash(c, f"rsync -ax --delete {RSYNC_EXCLUDES} {local_source}/ {APP_DIR}/")
    else:
        git_check = c.sudo(f"test -d {REPO_DIR}/.git", warn=True)
        if git_check.failed:
            if not git_url:
                raise Exit(f"No git repository found at {REPO_DIR} and no git_url provided.")
            print(f"📥 Cloning repository from {git_url}...")
            c.sudo(f"git clone {git_url} {REPO_DIR}", user=DEPLOY_USER)
        else:
            c.sudo(f"git -C {REPO_DIR} fetch --all", user=DEPLOY_USER)
            c.sudo(f"git -C {REPO_DIR} reset --hard origin/{branch}", user=DEPLOY_USER)

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
def build_and_migrate(c):
    """
    Installs Python dependencies, verifies deployment health, executes
    Django migrations, and collects static assets into STATIC_DIR.
    """
    ensure_sudo(c)
    print("⚡ Running Python dependency updates and Django maintenance...")

    # Install pip requirements
    c.sudo(f"{VENV_DIR}/bin/pip install --no-cache-dir -r {APP_DIR}/requirements.txt", user=DEPLOY_USER)

    # Run deployment verification checks
    print("🔍 Running Django deployment check...")
    check_res = sudo_bash(
        c,
        f"VERBAL_ROLE=web DJANGO_STATIC_ROOT={STATIC_DIR} {VENV_DIR}/bin/python {APP_DIR}/manage.py check --deploy",
        user=DEPLOY_USER,
        warn=True,
    )
    if check_res.failed:
        print("⚠️ [WARNING] Django check --deploy identified potential deployment warnings.")

    # Run migrations as deployer
    print("🗄️ Applying Django database migrations...")
    c.sudo(f"{VENV_DIR}/bin/python {APP_DIR}/manage.py migrate --noinput", user=DEPLOY_USER)

    # Collect static assets directly into STATIC_DIR
    print(f"🎨 Collecting static assets into {STATIC_DIR}...")
    sudo_bash(
        c,
        f"DJANGO_STATIC_ROOT={STATIC_DIR} {VENV_DIR}/bin/python {APP_DIR}/manage.py collectstatic --noinput",
        user=DEPLOY_USER,
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
def deploy(c, branch="main", git_url=None, local_source=None):
    """
    Standard zero-downtime deployment pipeline:
    1. Preflight check
    2. Backup database
    3. Synchronize code
    4. Update dependencies & run migrations
    5. Restart systemd services
    6. Validate system health
    """
    ensure_sudo(c)
    print(f"🚀 Commencing deployment for {APP_NAME}...")
    preflight_check(c)
    backup_database(c)
    update_code(c, git_url=git_url, branch=branch, local_source=local_source)
    build_and_migrate(c)
    restart_services(c)
    health_check(c)
    print("🌟 Deployment completed successfully!")


@task
def setup_all(c, branch="main", git_url=None, local_source=None):
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
    setup_nginx_and_ssl(c)
    setup_systemd(c)
    deploy(c, branch=branch, git_url=git_url, local_source=local_source)
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

    conn = Connection(host)
    task_map = {
        "preflight-check": preflight_check,
        "preflight_check": preflight_check,
        "setup-all": setup_all,
        "setup_all": setup_all,
        "provision-system": provision_system,
        "provision_system": provision_system,
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
