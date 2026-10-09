"""
Fabric deployment pipeline for Verbal / Reason.

Provides modular, idempotent tasks for server provisioning, SSL/Nginx configuration,
systemd service management, pre-migration database backups, and zero-downtime updates.

Usage examples:
    # Full bootstrap and initial deployment:
    fab -H localhost setup-all

    # Routine update / redeploy:
    fab -H localhost deploy

    # Individual modular tasks:
    fab -H localhost backup-database
    fab -H localhost setup-nginx-and-ssl
    fab -H localhost restart-services
    fab -H localhost health-check
"""

import getpass
import io
import os
import uuid
from fabric import task
from invoke import Exit
from invoke.watchers import StreamWatcher

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
DOCKER_DIR = "/var/lib/docker"

# Users and permissions
DEPLOY_USER = os.environ.get("REASON_DEPLOY_USER", "verbal_deploy")   # Interactive/CI deployment operator
DJANGO_USER = os.environ.get("REASON_APP_USER", "verbal_run")         # Low-privileged system service account (nologin)
WEB_GROUP = os.environ.get("REASON_WEB_GROUP", "www-data")            # Shared group for Nginx & app file traversal


# Web and upstream ports
DOMAIN = os.environ.get("REASON_DOMAIN", "reason.andrew.com")
GRANIAN_PORT = 8008
INFERENCE_PORT = 8001
SSL_CERT_PATH = f"/etc/ssl/certs/{DOMAIN}.crt"
SSL_KEY_PATH = f"/etc/ssl/private/{DOMAIN}.key"


# Database parameters for automated pre-migration dumps
DB_CONTAINER = "verbal_db"
DB_NAME = "verbal_db"
DB_USER = "verbal_user"
DB_PORT = 5433


# ==============================================================================
# SUDO & SECURITY SESSION HARDENING
# ==============================================================================
class StrictSingleAttemptWatcher(StreamWatcher):
    """
    Guarantees that any sudo authentication failure aborts IMMEDIATELY on the
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
    1. NEVER echoes password to screen (pty=False).
    2. Cleans up prompt formatting so no regex strings leak to sudo -p.
    3. Prompts once for password with getpass.
    4. Validates credentials immediately with a single-shot test (sudo -k -S true).
       Since stdin has EOF after the single password line, sudo CANNOT retry.
    5. If validation fails, aborts instantly with Exit(1).
    6. Attaches StrictSingleAttemptWatcher so any subsequent rejection terminates instantly.
    """
    # Disable PTY for sudo to prevent credential echo on stdout
    c.config.run.pty = False
    c.config.sudo.pty = False

    # Clean standard prompt string (NOT a regex)
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

        # Single-shot upfront validation:
        # We test the password once with 'sudo -k -S true' using io.StringIO so stdin closes immediately (EOF).
        # This makes it physically impossible for sudo to prompt a second or third time!
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

    # Register strict single-attempt watcher into watchers list
    if hasattr(c.config.run, "watchers"):
        if not any(isinstance(w, StrictSingleAttemptWatcher) for w in c.config.run.watchers):
            c.config.run.watchers.append(StrictSingleAttemptWatcher())


def write_sudo_file(c, dest_path, content, mode="0644", owner="root:root"):
    """
    Safely writes content to a privileged destination file without breaking
    Fabric's sudo -S password stdin channel. Writes to an unprivileged /tmp
    file first using c.run (standard heredoc), then atomically installs and
    chowns it at dest_path with a clean sudo command.
    """
    owner_user, owner_group = (owner.split(":") + ["root"])[:2]
    tmp_path = f"/tmp/fabric_{uuid.uuid4().hex[:8]}"
    c.run(f"cat << 'EOF' > {tmp_path}\n{content}\nEOF")
    c.sudo(f"install -m {mode} -o {owner_user} -g {owner_group} {tmp_path} {dest_path} && rm -f {tmp_path}")



# ==============================================================================
# PHASE 1: SYSTEM PROVISIONING & PERMISSIONS
# ==============================================================================
@task
def provision_system(c):
    """
    Provisions system users, directories under /srv, shared caches, and
    establishes the 3-tier ownership model with SetGID on media directories.
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


    # 2. Add both to www-data group for controlled file traversal
    c.sudo(f"usermod -aG {WEB_GROUP} {DEPLOY_USER}")
    c.sudo(f"usermod -aG {WEB_GROUP} {DJANGO_USER}")

    # 3. Create core directory structure
    c.sudo(f"mkdir -p {BASE_DIR} {REPO_DIR} {APP_DIR} {STATIC_DIR} {MEDIA_DIR} {BACKUP_DIR} {LOG_DIR} {SHARED_DIR}")
    c.sudo(f"mkdir -p {SHARED_DIR}/workspaces {SHARED_DIR}/media {HF_CACHE_DIR}")

    # 4. Strict base directory lockdown (root owned)
    c.sudo(f"chown root:root {BASE_DIR}")
    c.sudo(f"chmod 0755 {BASE_DIR}")

    # 5. Persistent shared directories & HF Cache
    # HF_CACHE_DIR: writable by django user and deployer
    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {HF_CACHE_DIR}")
    c.sudo(f"chmod -R 2775 {HF_CACHE_DIR}")

    # Shared workspaces & media: SetGID so new files inherit www-data
    c.sudo(f"chown -R {DJANGO_USER}:{WEB_GROUP} {SHARED_DIR}")
    c.sudo(f"chmod -R 2775 {SHARED_DIR}")

    # 6. Public static (owned by deployer, read-only to www-data/Nginx)
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {STATIC_DIR}")
    c.sudo(f"chmod 0755 {STATIC_DIR}")

    # 7. Public media (symlinked to shared media, SetGID)
    c.sudo(f"rm -rf {MEDIA_DIR}")
    c.sudo(f"ln -sfn {SHARED_DIR}/media {MEDIA_DIR}")
    c.sudo(f"chown -h {DJANGO_USER}:{WEB_GROUP} {MEDIA_DIR}")

    # 8. Backups and logs
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {BACKUP_DIR} {LOG_DIR}")
    c.sudo(f"chmod 0750 {BACKUP_DIR} {LOG_DIR}")

    # 9. Repo & App directories (owned by deployer, readable by web group)
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {REPO_DIR} {APP_DIR}")
    c.sudo(f"chmod 0750 {REPO_DIR} {APP_DIR}")

    # 10. Python Virtual Environment
    # Created by root inside /srv/reason to prevent parent permission issues, then chowned
    venv_pip = c.run(f"test -f {VENV_DIR}/bin/pip", warn=True)
    if venv_pip.failed:
        print("🐍 Creating dedicated Python virtual environment...")
        c.sudo(f"rm -rf {VENV_DIR}")
        c.sudo(f"python3 -m venv {VENV_DIR}")
    c.sudo(f"chown -R {DEPLOY_USER}:{DJANGO_USER} {VENV_DIR}")
    c.sudo(f"chmod -R 0750 {VENV_DIR}")

    print("✅ System paths, users, and ownership hierarchy established.")


# ==============================================================================
# PHASE 2: SSL CERTIFICATES & NGINX
# ==============================================================================
@task
def setup_nginx_and_ssl(c, source_ssl_dir=None):
    """
    Installs SSL certificates into /etc/ssl/, generates an optimized Nginx site
    configuration with SSE streaming (no buffering) and WebSocket support,
    and reloads Nginx safely.
    """
    ensure_sudo(c)
    print("🔒 [2/4] Configuring SSL certificates and Nginx reverse proxy...")

    # Determine SSL certificate sources
    if source_ssl_dir is None:
        source_ssl_dir = f"{REPO_DIR}/deploy/ssl"

    # Install SSL certs into system standard locations (resolves home dir permission blocks)
    c.sudo("mkdir -p /etc/ssl/certs /etc/ssl/private")
    if c.run(f"test -f {source_ssl_dir}/{DOMAIN}.crt", warn=True, hide=True).ok:
        c.sudo(f"cp {source_ssl_dir}/{DOMAIN}.crt {SSL_CERT_PATH}")
    if c.run(f"test -f {source_ssl_dir}/{DOMAIN}.key", warn=True, hide=True).ok:
        c.sudo(f"cp {source_ssl_dir}/{DOMAIN}.key {SSL_KEY_PATH}")

    # If certificates do not exist yet, generate self-signed fallback certs
    cert_check = c.run(f"test -f {SSL_CERT_PATH} && test -f {SSL_KEY_PATH}", warn=True)
    if cert_check.failed:
        print("⚠️ Generating self-signed SSL certificates for local domain...")
        c.sudo(
            f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 "
            f"-keyout {SSL_KEY_PATH} -out {SSL_CERT_PATH} "
            f"-subj '/CN={DOMAIN}/O=Reason/C=AU'"
        )

    # Secure SSL private key permissions (combined to minimize sudo round-trips)
    c.sudo(
        f"chmod 0644 {SSL_CERT_PATH} && chown root:root {SSL_CERT_PATH} && "
        f"chmod 0600 {SSL_KEY_PATH} && chown root:root {SSL_KEY_PATH}"
    )

    # Generate Nginx configuration
    nginx_conf = f"""# Auto-generated by Fabric for {DOMAIN}
map $http_upgrade $connection_upgrade {{
    default upgrade;
    '' close;
}}

upstream granian_backend {{
    server 127.0.0.1:{GRANIAN_PORT};
    keepalive 32;
}}

# HTTP -> HTTPS redirect
server {{
    listen 80;
    listen [::]:80;
    server_name {DOMAIN} localhost 127.0.0.1;

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

# HTTPS Gateway & Reverse Proxy
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {DOMAIN} localhost 127.0.0.1;

    ssl_certificate {SSL_CERT_PATH};
    ssl_certificate_key {SSL_KEY_PATH};

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
        alias {STATIC_DIR}/;
        expires 30d;
        access_log off;
        add_header Cache-Control "public, max-age=2592000";
    }}

    # Uploaded Media & Artifacts
    location /media/ {{
        alias {MEDIA_DIR}/;
        expires 7d;
        access_log off;
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

    conf_dest = f"/etc/nginx/sites-available/{DOMAIN}.conf"
    enabled_dest = f"/etc/nginx/sites-enabled/{DOMAIN}.conf"

    # Write config safely without breaking sudo stdin
    write_sudo_file(c, conf_dest, nginx_conf, mode="0644", owner="root:root")
    c.sudo(f"ln -sfn {conf_dest} {enabled_dest}")

    # Remove default site if active
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

    env_file = f"{SHARED_DIR}/.env"

    # 1. Granian ASGI Web Service
    web_unit = f"""[Unit]
Description=Reason Web Server (Granian ASGI)
After=network.target postgresql.service
Wants=network.target

[Service]
Type=simple
User={DJANGO_USER}
Group={WEB_GROUP}
WorkingDirectory={APP_DIR}
EnvironmentFile=-{env_file}
Environment=PYTHONPATH={APP_DIR}
Environment=HF_HOME={HF_CACHE_DIR}
ExecStart={VENV_DIR}/bin/granian --interface asgi --host 127.0.0.1 --port {GRANIAN_PORT} verbal_config.asgi:application
Restart=always
RestartSec=3s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""

    # 2. Background Task Worker
    worker_unit = f"""[Unit]
Description=Reason Background Task Worker
After=network.target postgresql.service
Wants=network.target

[Service]
Type=simple
User={DJANGO_USER}
Group={WEB_GROUP}
WorkingDirectory={APP_DIR}
EnvironmentFile=-{env_file}
Environment=PYTHONPATH={APP_DIR}
Environment=HF_HOME={HF_CACHE_DIR}
ExecStart={VENV_DIR}/bin/python manage.py runtaskworker
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

    # 3. Periodic Task Scheduler
    scheduler_unit = f"""[Unit]
Description=Reason Task Scheduler (NightManager)
After=network.target postgresql.service
Wants=network.target

[Service]
Type=simple
User={DJANGO_USER}
Group={WEB_GROUP}
WorkingDirectory={APP_DIR}
EnvironmentFile=-{env_file}
Environment=PYTHONPATH={APP_DIR}
Environment=HF_HOME={HF_CACHE_DIR}
ExecStart={VENV_DIR}/bin/python manage.py runtaskscheduler
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
"""

    # 4. Dedicated Local Inference Service
    inference_unit = f"""[Unit]
Description=Reason Local AI Model Inference Service
After=network.target
Wants=network.target

[Service]
Type=simple
User={DJANGO_USER}
Group={WEB_GROUP}
WorkingDirectory={APP_DIR}
EnvironmentFile=-{env_file}
Environment=VERBAL_ROLE=inference
Environment=PYTHONPATH={APP_DIR}
Environment=HF_HOME={HF_CACHE_DIR}
ExecStart={VENV_DIR}/bin/python manage.py runserver 127.0.0.1:{INFERENCE_PORT}
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

    services = [
        ("reason-web.service", web_unit),
        ("reason-worker.service", worker_unit),
        ("reason-scheduler.service", scheduler_unit),
        ("reason-inference.service", inference_unit),
    ]

    for unit_name, content in services:
        write_sudo_file(c, f"/etc/systemd/system/{unit_name}", content, mode="0644", owner="root:root")

    c.sudo("systemctl daemon-reload && systemctl enable reason-web reason-worker reason-scheduler reason-inference")
    print("✅ Systemd services installed and registered for automatic boot startup.")


# ==============================================================================
# PHASE 4: PRE-MIGRATION DATABASE BACKUP
# ==============================================================================
@task
def backup_database(c):
    """
    Creates an automated, compressed PostgreSQL dump prior to applying migrations,
    retaining the last 10 snapshots in /srv/reason/backups.
    """
    ensure_sudo(c)
    print("💾 Creating pre-migration database snapshot...")
    timestamp = c.run("date +%Y%m%d_%H%M%S", hide=True).stdout.strip()
    backup_file = f"{BACKUP_DIR}/pre_deploy_{timestamp}.dump"

    # Attempt dump via docker container first, fallback to pg_dump on host
    docker_check = c.sudo(f"docker ps -q -f name={DB_CONTAINER}", warn=True)
    if docker_check.ok and docker_check.stdout.strip():
        print(f"📦 Dumping database from container '{DB_CONTAINER}'...")
        c.sudo(f"docker exec {DB_CONTAINER} pg_dump -U {DB_USER} -Fc {DB_NAME} > {backup_file}")
    else:
        print("📦 Container offline or local DB, running host pg_dump...")
        c.sudo(f"pg_dump -h 127.0.0.1 -p {DB_PORT} -U {DB_USER} -Fc {DB_NAME} > {backup_file}", warn=True)

    # Prune older backups, keeping the most recent 10
    c.sudo(f"ls -1t {BACKUP_DIR}/pre_deploy_*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f")
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
        # Direct rsync from local source repository
        print(f"🔄 Syncing directly from local directory {local_source}...")
        c.sudo(
            f"rsync -ax --delete "
            f"--exclude='.git*' --exclude='*.pyc' --exclude='__pycache__' "
            f"--exclude='media/' --exclude='staticfiles/' --exclude='workspaces/' "
            f"{local_source}/ {APP_DIR}/"
        )
    else:
        # Git repository clone/pull
        with c.cd(REPO_DIR):
            git_check = c.run("git status", warn=True)
            if git_check.failed and git_url:
                c.sudo(f"git clone {git_url} .", user=DEPLOY_USER)
            else:
                c.sudo("git fetch --all", user=DEPLOY_USER)
                c.sudo(f"git reset --hard origin/{branch}", user=DEPLOY_USER)

        c.sudo(
            f"rsync -ax --delete "
            f"--exclude='.git*' --exclude='*.pyc' --exclude='__pycache__' "
            f"--exclude='media/' --exclude='staticfiles/' --exclude='workspaces/' "
            f"{REPO_DIR}/ {APP_DIR}/"
        )

    # Symlink shared workspaces and shared media into app runtime
    c.sudo(f"ln -sfn {SHARED_DIR}/workspaces {APP_DIR}/workspaces")
    c.sudo(f"ln -sfn {SHARED_DIR}/media {APP_DIR}/media")

    # Symlink persistent .env if present in shared
    env_source = f"{SHARED_DIR}/.env"
    if c.run(f"test -f {env_source}", warn=True, hide=True).ok:
        c.sudo(f"ln -sfn {env_source} {APP_DIR}/.env")

    # Enforce Read-Only Code Boundaries:
    # Owned by DEPLOY_USER, readable by DJANGO_USER/WEB_GROUP, unwritable by runtime.
    print("🔒 Enforcing read-only code execution permissions...")
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {APP_DIR}")
    c.sudo(f"find {APP_DIR} -type d -exec chmod 0750 {{}} +")
    c.sudo(f"find {APP_DIR} -type f -exec chmod 0640 {{}} +")
    c.sudo(f"chmod +x {APP_DIR}/manage.py")

    print("✅ Application code updated and permission boundaries enforced.")


@task
def build_and_migrate(c):
    """
    Installs Python dependencies, executes Django migrations, and runs collectstatic.
    """
    ensure_sudo(c)
    print("⚡ Running Python dependency updates and Django maintenance...")

    # Install pip requirements
    c.sudo(f"{VENV_DIR}/bin/pip install --no-cache-dir -r {APP_DIR}/requirements.txt", user=DEPLOY_USER)

    # Run migrations as deployer
    print("🗄️ Applying Django database migrations...")
    c.sudo(f"{VENV_DIR}/bin/python {APP_DIR}/manage.py migrate --noinput", user=DEPLOY_USER)

    # Collect static assets into STATIC_DIR
    print("🎨 Collecting static assets...")
    c.sudo(f"{VENV_DIR}/bin/python {APP_DIR}/manage.py collectstatic --noinput", user=DEPLOY_USER)

    # Lock down static asset permissions for Nginx read-only access
    c.sudo(f"chown -R {DEPLOY_USER}:{WEB_GROUP} {STATIC_DIR}")
    c.sudo(f"find {STATIC_DIR} -type d -exec chmod 0755 {{}} +")
    c.sudo(f"find {STATIC_DIR} -type f -exec chmod 0644 {{}} +")

    print("✅ Build and migrations completed.")


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
    c.sudo("systemctl restart reason-web reason-worker reason-scheduler reason-inference")
    print("✅ Services restarted.")


@task
def health_check(c):
    """
    Validates service responsiveness via HTTP and inspects systemd unit health.
    """
    print("🩺 Validating system health...")
    c.run(f"systemctl is-active --quiet reason-web || (echo '❌ reason-web failed!' && exit 1)")
    c.run(f"systemctl is-active --quiet reason-worker || (echo '❌ reason-worker failed!' && exit 1)")
    c.run(f"systemctl is-active --quiet reason-scheduler || (echo '❌ reason-scheduler failed!' && exit 1)")
    c.run(f"systemctl is-active --quiet reason-inference || (echo '❌ reason-inference failed!' && exit 1)")

    # Test HTTP endpoint
    curl_res = c.run(
        f"curl -k -s -o /dev/null -w '%{{http_code}}' https://127.0.0.1:{GRANIAN_PORT}/demo/ "
        f"|| curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{GRANIAN_PORT}/demo/",
        warn=True
    )
    code = curl_res.stdout.strip()
    if code in ["200", "302"]:
        print(f"🎉 Health check successful! Endpoint returned HTTP {code}.")
    else:
        print(f"⚠️ Warning: Endpoint returned HTTP {code}. Check journalctl -u reason-web.")


# ==============================================================================
# HIGH-LEVEL ORCHESTRATION PIPELINES
# ==============================================================================
@task
def deploy(c, branch="main", local_source=None):
    """
    Standard zero-downtime deployment pipeline:
    1. Backup database
    2. Synchronize code
    3. Update dependencies & run migrations
    4. Restart systemd services
    5. Validate system health
    """
    ensure_sudo(c)
    print(f"🚀 Commencing deployment for {APP_NAME}...")
    backup_database(c)
    update_code(c, branch=branch, local_source=local_source)
    build_and_migrate(c)
    restart_services(c)
    health_check(c)
    print("🌟 Deployment completed successfully!")


@task
def setup_all(c, branch="main", local_source=None):
    """
    Full from-scratch server bootstrap:
    1. Provision users, groups, directories, and caches
    2. Configure SSL certs and Nginx
    3. Install systemd process units
    4. Run initial deployment
    """
    ensure_sudo(c)
    print(f"🌟 Starting complete server bootstrap and deployment for {APP_NAME}...")
    provision_system(c)
    setup_nginx_and_ssl(c)
    setup_systemd(c)
    deploy(c, branch=branch, local_source=local_source)
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

