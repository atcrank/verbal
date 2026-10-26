"""
Fabric entrypoint for Verbal / Reason.
Exposes modular deployment tasks defined in deploy/fabric_deploy.py.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))
import fabric_deploy
from fabric_deploy import *  # noqa: F401, F403


if __name__ == "__main__":
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
        "preflight-check": fabric_deploy.preflight_check,
        "preflight_check": fabric_deploy.preflight_check,
        "setup-all": fabric_deploy.setup_all,
        "setup_all": fabric_deploy.setup_all,
        "provision-system": fabric_deploy.provision_system,
        "provision_system": fabric_deploy.provision_system,
        "sync-env": fabric_deploy.sync_env,
        "sync_env": fabric_deploy.sync_env,
        "setup-nginx-and-ssl": fabric_deploy.setup_nginx_and_ssl,
        "setup_nginx_and_ssl": fabric_deploy.setup_nginx_and_ssl,
        "setup-systemd": fabric_deploy.setup_systemd,
        "setup_systemd": fabric_deploy.setup_systemd,
        "backup-database": fabric_deploy.backup_database,
        "backup_database": fabric_deploy.backup_database,
        "update-code": fabric_deploy.update_code,
        "update_code": fabric_deploy.update_code,
        "build-and-migrate": fabric_deploy.build_and_migrate,
        "build_and_migrate": fabric_deploy.build_and_migrate,
        "restart-services": fabric_deploy.restart_services,
        "restart_services": fabric_deploy.restart_services,
        "health-check": fabric_deploy.health_check,
        "health_check": fabric_deploy.health_check,
        "build-llamacpp": fabric_deploy.build_llamacpp,
        "build_llamacpp": fabric_deploy.build_llamacpp,
        "deploy": fabric_deploy.deploy,
    }
    func = task_map.get(task_name)
    if not func:
        print(f"Unknown task: '{task_name}'. Available tasks:\n  " + "\n  ".join(sorted(set(task_map.keys()))))
        sys.exit(1)
    func(conn)
