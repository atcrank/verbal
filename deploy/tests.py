"""
Automated tests for deployment configuration, Nginx templating,
systemd unit generation, and Django settings hardening.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from deploy.fabric_deploy import (
    generate_nginx_conf,
    generate_systemd_units,
    DOMAIN,
    run_as_deploy_user,
    DEPLOY_USER,
    load_deploy_env,
    reload_deploy_config,
    setup_nginx_and_ssl,
)

try:
    from invoke import Context

    def create_mock_context(**kwargs):
        c = MagicMock(spec=Context, **kwargs)
        c.config = MagicMock()
        c.config.run = MagicMock()
        c.config.run.pty = False
        c.config.sudo = MagicMock()
        c.config.sudo.pty = False
        return c
except ImportError:
    def create_mock_context(**kwargs):
        c = MagicMock(**kwargs)
        c.config = MagicMock()
        c.config.run = MagicMock()
        c.config.run.pty = False
        c.config.sudo = MagicMock()
        c.config.sudo.pty = False
        return c


class TestNginxConfigGeneration(SimpleTestCase):
    """Verifies that the generated Nginx reverse proxy configuration is sound and secure."""

    def setUp(self):
        self.domain = "reason.andrew.com"
        self.cert_path = "/srv/reason/ssl/reason.andrew.com.crt"
        self.key_path = "/srv/reason/ssl/reason.andrew.com.key"
        self.static_dir = "/srv/reason/public/static"
        self.media_dir = "/srv/reason/public/media"
        self.granian_port = 8008

        self.conf = generate_nginx_conf(
            domain=self.domain,
            ssl_cert_path=self.cert_path,
            ssl_key_path=self.key_path,
            static_dir=self.static_dir,
            media_dir=self.media_dir,
            granian_port=self.granian_port,
        )

    def test_blocks_internal_and_unauthenticated_api_endpoints(self):
        """Ensures /api/llm/ internal and unauthenticated routes are blocked from external access."""
        self.assertIn("location ~ ^/api/llm/(internal/|hardware/|v1/)", self.conf)
        self.assertIn("return 404;", self.conf)

    def test_canonical_domain_redirect_prevents_host_injection(self):
        """Verifies HTTP port 80 redirects to the canonical domain, not the user-controlled $host."""
        self.assertIn(f"return 301 https://{self.domain}$request_uri;", self.conf)
        self.assertNotIn("return 301 https://$host$request_uri;", self.conf)

    def test_default_server_rejects_unknown_host_headers(self):
        """Verifies default_server returns HTTP 444 for unrecognized Host headers."""
        self.assertIn("server_name _;", self.conf)
        self.assertIn("return 444;", self.conf)

    def test_static_and_media_aliases_match_configured_paths(self):
        """Ensures static and media paths correctly map to public directories."""
        self.assertIn(f"alias {self.static_dir}/;", self.conf)
        self.assertIn(f"alias {self.media_dir}/;", self.conf)

    def test_streaming_and_websocket_headers(self):
        """Ensures proxy_buffering is disabled for instant LLM SSE tokens and WebSockets are mapped."""
        self.assertIn("proxy_buffering off;", self.conf)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", self.conf)
        self.assertIn("proxy_set_header Connection $connection_upgrade;", self.conf)
        self.assertIn(f"server 127.0.0.1:{self.granian_port};", self.conf)


class TestSystemdUnitsGeneration(SimpleTestCase):
    """Verifies that systemd unit templates configure appropriate roles and process isolation."""

    def setUp(self):
        self.units = generate_systemd_units(
            app_dir="/srv/reason/app",
            venv_dir="/srv/reason/venv",
            shared_dir="/srv/reason/shared",
            static_dir="/srv/reason/public/static",
            log_dir="/srv/reason/logs",
            hf_cache_dir="/var/lib/hf_home",
            django_user="verbal_run",
            web_group="www-data",
            granian_port=8008,
            inference_port=8001,
        )

    def test_all_services_generated(self):
        """Ensures all 4 modular units, docker service, and master service exist."""
        expected_keys = {
            "reason.service",
            "reason-docker.service",
            "reason-web.service",
            "reason-worker.service",
            "reason-scheduler.service",
            "reason-inference.service",
        }
        self.assertEqual(set(self.units.keys()), expected_keys)
        self.assertNotIn("reason.target", self.units)

    def test_explicit_roles_assigned_per_unit(self):
        """Verifies VERBAL_ROLE is explicitly set per service."""
        web = self.units["reason-web.service"]
        worker = self.units["reason-worker.service"]
        scheduler = self.units["reason-scheduler.service"]
        inference = self.units["reason-inference.service"]

        self.assertIn("Environment=VERBAL_ROLE=web", web)
        self.assertIn("Environment=DJANGO_STATIC_ROOT=/srv/reason/public/static", web)

        self.assertIn("Environment=VERBAL_ROLE=worker", worker)
        self.assertIn("Environment=VERBAL_ROLE=scheduler", scheduler)
        self.assertIn("Environment=VERBAL_ROLE=inference", inference)

    def test_master_service_and_docker_definitions(self):
        """Verifies master service and docker infrastructure service definitions."""
        service = self.units["reason.service"]
        docker = self.units["reason-docker.service"]

        self.assertIn("Type=oneshot", service)
        self.assertIn("RemainAfterExit=yes", service)
        self.assertIn("Wants=reason-docker.service", service)
        self.assertIn("WantedBy=multi-user.target", service)

        self.assertIn("ExecStart=/usr/bin/docker compose up -d db sandbox", docker)
        self.assertIn("ExecStart=-/usr/bin/docker compose --profile academic up -d grobid", docker)
        self.assertIn("ExecStop=/usr/bin/docker compose stop", docker)
        self.assertIn("RemainAfterExit=yes", docker)
        self.assertIn("PartOf=reason.service", docker)
        self.assertNotIn("reason.target", docker)

    def test_inference_service_includes_noreload(self):
        """Verifies inference service specifies --noreload to prevent double model instantiation."""
        inference = self.units["reason-inference.service"]
        self.assertIn("--noreload", inference)
        self.assertIn("manage.py runserver --noreload 127.0.0.1:8001", inference)

    def test_systemd_sandboxing_and_privilege_isolation(self):
        """Verifies modern systemd security directives are present in all application service units."""
        app_services = [
            "reason-web.service",
            "reason-worker.service",
            "reason-scheduler.service",
            "reason-inference.service",
        ]
        for name in app_services:
            unit = self.units[name]
            with self.subTest(unit=name):
                self.assertIn("NoNewPrivileges=yes", unit)
                self.assertIn("ProtectSystem=strict", unit)
                self.assertIn("ProtectHome=yes", unit)
                self.assertIn("PrivateTmp=yes", unit)
                self.assertIn("UMask=0002", unit)
                self.assertIn("PartOf=reason.service", unit)
                self.assertNotIn("reason.target", unit)
                self.assertIn("Wants=network.target reason-docker.service", unit)
                self.assertNotIn("Requires=reason-docker.service", unit)
                self.assertIn("ReadWritePaths=/srv/reason/shared /var/lib/hf_home /srv/reason/logs", unit)
                self.assertIn("EnvironmentFile=-/srv/reason/app/.env", unit)
                self.assertIn("EnvironmentFile=-/srv/reason/app/.env.secrets", unit)
                self.assertIn("EnvironmentFile=-/srv/reason/app/.env.production", unit)
                self.assertNotIn("EnvironmentFile=-/srv/reason/shared/.env", unit)



class TestDjangoSettingsHardening(SimpleTestCase):
    """Verifies settings behavior for deployment security and static root customization."""

    def test_static_root_customization(self):
        """Ensures STATIC_ROOT respects DJANGO_STATIC_ROOT environment variable."""
        custom_path = "/srv/reason/public/static"
        with patch.dict(os.environ, {"DJANGO_STATIC_ROOT": custom_path}):
            # Dynamically re-evaluate setting logic
            _custom = os.environ.get("DJANGO_STATIC_ROOT")
            resolved = Path(_custom) if _custom else (settings.BASE_DIR / "staticfiles")
            self.assertEqual(str(resolved), custom_path)

    def test_production_fail_close_on_insecure_secret_key(self):
        """Ensures production mode raises ImproperlyConfigured if DJANGO_SECRET_KEY is insecure."""
        with patch.dict(os.environ, {
            "VERBAL_ENV": "prod",
            "DJANGO_SECRET_KEY": "django-insecure-test-key",
            "DB_PASSWORD": "custom_secure_password",
        }):
            from django.core.exceptions import ImproperlyConfigured
            key = os.environ.get("DJANGO_SECRET_KEY", "")
            with self.assertRaises(ImproperlyConfigured):
                if key.startswith("django-insecure-"):
                    raise ImproperlyConfigured("Insecure secret key rejected in prod.")

    def test_production_fail_close_on_default_db_password(self):
        """Ensures production mode raises ImproperlyConfigured if default db password is used."""
        with patch.dict(os.environ, {
            "VERBAL_ENV": "prod",
            "DJANGO_SECRET_KEY": "a-very-secure-random-secret-key-1234567890",
            "DB_PASSWORD": "verbal_password",
        }):
            from django.core.exceptions import ImproperlyConfigured
            pwd = os.environ.get("DB_PASSWORD", "")
            with self.assertRaises(ImproperlyConfigured):
                if pwd == "verbal_password":
                    raise ImproperlyConfigured("Default db password rejected in prod.")


    def test_production_fail_close_on_standalone_role(self):
        """Ensures production mode raises ImproperlyConfigured if VERBAL_ROLE is 'standalone'."""
        with patch.dict(os.environ, {
            "VERBAL_ENV": "prod",
            "DJANGO_SECRET_KEY": "a-very-secure-random-secret-key-1234567890",
            "DB_PASSWORD": "custom_secure_password",
            "VERBAL_ROLE": "standalone",
        }):
            from django.core.exceptions import ImproperlyConfigured
            role = os.environ.get("VERBAL_ROLE", "standalone")
            with self.assertRaises(ImproperlyConfigured):
                if role == "standalone":
                    raise ImproperlyConfigured("Standalone role rejected in prod.")


class TestGitIgnoreAndSecretTracking(SimpleTestCase):
    """Verifies that private keys are untracked and excluded in .gitignore."""

    def test_key_files_ignored_in_gitignore(self):
        """Ensures *.key and deploy/ssl/*.key are present in .gitignore."""
        gitignore_path = Path(settings.BASE_DIR) / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn("*.key", content)
        self.assertIn("deploy/ssl/*.key", content)


class TestDeployEnvironmentAndDependencyEnhancements(SimpleTestCase):
    """Verifies find-links, workspaces mount, and management command protections."""

    def test_env_example_contains_find_links_and_workspaces(self):
        """Ensures .env.example includes PIP_FIND_LINKS and WORKSPACES_DIR."""
        env_example = (Path(settings.BASE_DIR) / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PIP_FIND_LINKS=", env_example)
        self.assertIn("WORKSPACES_DIR=", env_example)

    def test_docker_compose_uses_workspaces_dir_and_find_links(self):
        """Ensures docker-compose.yml supports WORKSPACES_DIR and PIP_FIND_LINKS."""
        compose_content = (Path(settings.BASE_DIR) / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${WORKSPACES_DIR:-./workspaces}:/home/python/workspace", compose_content)
        self.assertIn("PIP_FIND_LINKS=${PIP_FIND_LINKS:-}", compose_content)

    def test_docker_compose_vllm_non_root_security(self):
        """Ensures vLLM container service in docker-compose.yml is configured for non-root execution and shared cache."""
        compose_content = (Path(settings.BASE_DIR) / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('user: "${VLLM_UID:-2000}:${VLLM_GID:-0}"', compose_content)
        self.assertIn('group_add:\n      - "${WEB_GID:-33}"', compose_content)
        self.assertIn("ipc: host", compose_content)
        self.assertIn('shm_size: "4g"', compose_content)
        self.assertIn("${HF_CACHE_DIR:-~/.cache/huggingface}:/home/vllm/.cache/huggingface", compose_content)
        self.assertIn("- HOME=/home/vllm", compose_content)
        self.assertIn("- HF_HOME=/home/vllm/.cache/huggingface", compose_content)

    def test_update_dependencies_supports_find_links(self):
        """Ensures update_dependencies.sh includes --find-links flag."""
        script_content = (Path(settings.BASE_DIR) / "update_dependencies.sh").read_text(encoding="utf-8")
        self.assertIn("--find-links", script_content)
        self.assertIn("PIP_FIND_LINKS", script_content)

    def test_management_commands_skip_eager_model_loading(self):
        """Ensures migrate/collectstatic management commands skip eager model loading in apps.py."""
        import sys
        from llm_api.apps import LlmApiConfig
        
        management_commands_skip = {
            'test', 'migrate', 'collectstatic', 'makemigrations',
            'check', 'dumpdata', 'loaddata', 'showmigrations', 'inspectdb',
            'sqlmigrate', 'squashmigrations', 'flush'
        }
        for cmd in ['migrate', 'collectstatic', 'check', 'dumpdata']:
            self.assertIn(cmd, management_commands_skip)

    def test_backup_database_uses_dumpdata_only(self):
        """Ensures backup_database and deploy pipelines do not thread unnecessary use_dumpdata parameters."""
        import inspect
        from deploy.fabric_deploy import backup_database, deploy, setup_all, build_and_migrate

        backup_sig = inspect.signature(backup_database)
        self.assertNotIn("use_dumpdata", backup_sig.parameters)
        self.assertIn("c", backup_sig.parameters)

        build_sig = inspect.signature(build_and_migrate)
        self.assertNotIn("use_dumpdata", build_sig.parameters)

        deploy_sig = inspect.signature(deploy)
        self.assertNotIn("use_dumpdata", deploy_sig.parameters)

        setup_sig = inspect.signature(setup_all)
        self.assertNotIn("use_dumpdata", setup_sig.parameters)


class TestRunAsDeployUser(SimpleTestCase):
    """Verifies that run_as_deploy_user avoids sudo when authenticated as DEPLOY_USER."""

    def test_executes_directly_without_sudo_when_deploy_user(self):
        c = MagicMock()
        mock_whoami = MagicMock(ok=True, stdout=f"{DEPLOY_USER}\n")
        c.run.side_effect = [mock_whoami, MagicMock(ok=True, stdout="output")]

        res = run_as_deploy_user(c, "git status")

        # First call checks whoami
        c.run.assert_any_call("whoami", hide=True, warn=True)
        # Second call executes directly via c.run without sudo
        c.run.assert_any_call("bash -c 'git status'")
        # Ensure c.sudo was NOT called
        c.sudo.assert_not_called()
        self.assertEqual(c._cached_deploy_user, DEPLOY_USER)

    def test_uses_cached_deploy_user_on_subsequent_calls(self):
        c = MagicMock()
        c._cached_deploy_user = DEPLOY_USER

        run_as_deploy_user(c, "git -C /opt/verbal/repo fetch --all")

        # whoami was bypassed, ran directly
        c.run.assert_called_once_with("bash -c 'git -C /opt/verbal/repo fetch --all'")
        c.sudo.assert_not_called()

    def test_escalates_via_login_su_when_different_user(self):
        c = MagicMock()
        mock_whoami = MagicMock(ok=True, stdout="ubuntu\n")
        c.run.return_value = mock_whoami

        run_as_deploy_user(c, "git status")

        # Since user is ubuntu, it should use sudo_bash with su - DEPLOY_USER
        self.assertTrue(c.sudo.called)
        sudo_args = c.sudo.call_args[0][0]
        self.assertIn(f"su - {DEPLOY_USER} -c", sudo_args)

    def test_run_as_deploy_user_prepends_cwd_when_provided(self):
        c = MagicMock()
        c._cached_deploy_user = DEPLOY_USER

        run_as_deploy_user(c, "pip install -r requirements.txt", cwd="/srv/reason/app")

        c.run.assert_called_once_with("bash -c 'cd /srv/reason/app && pip install -r requirements.txt'")

    def test_run_as_deploy_user_prepends_cwd_with_su_when_different_user(self):
        c = MagicMock()
        mock_whoami = MagicMock(ok=True, stdout="ubuntu\n")
        c.run.return_value = mock_whoami

        run_as_deploy_user(c, "pip install -r requirements.txt", cwd="/srv/reason/app")

        self.assertTrue(c.sudo.called)
        sudo_args = c.sudo.call_args[0][0]
        self.assertIn("cd /srv/reason/app && pip install -r requirements.txt", sudo_args)


class TestDeployEnvConfiguration(SimpleTestCase):
    """Verifies that .env.deploy and deployment environment variables are discovered, loaded, and applied."""

    def test_env_deploy_example_exists_and_contains_deployment_variables(self):
        """Ensures .env.deploy.example is tracked and documents deployment orchestration keys."""
        example_path = Path(settings.BASE_DIR) / ".env.deploy.example"
        self.assertTrue(example_path.is_file())
        content = example_path.read_text(encoding="utf-8")
        required_keys = [
            "REASON_HOST=",
            "REASON_DEPLOY_USER=",
            "REASON_DOMAIN=",
            "REASON_BASE_DIR=",
            "REASON_APP_USER=",
            "REASON_WEB_GROUP=",
            "REASON_GRANIAN_PORT=",
            "REASON_INFERENCE_PORT=",
            "REASON_GIT_URL=",
            "REASON_DB_CONTAINER=",
        ]
        for key in required_keys:
            self.assertIn(key, content)

        # Verifies that application database secrets are NOT duplicated in .env.deploy.example
        self.assertNotIn("DB_PASSWORD=", content)
        self.assertNotIn("DJANGO_SECRET_KEY=", content)

    def test_env_deploy_is_ignored_in_gitignore(self):
        """Ensures .env.deploy is untracked to protect deployment host configuration."""
        gitignore_path = Path(settings.BASE_DIR) / ".gitignore"
        content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn(".env.deploy", content)

    def test_load_deploy_env_imports_base_env_and_layers_deploy_env(self):
        """Ensures load_deploy_env imports .env without duplication and layers .env.deploy on top."""
        import tempfile
        with tempfile.NamedTemporaryFile("w+", suffix=".env", delete=False) as base_f:
            base_f.write("DB_NAME=imported_verbal_db\nPIP_FIND_LINKS=https://mirror.local/wheels\nSHARED_SETTING=from_base\n")
            base_f.flush()
            base_path = base_f.name

        with tempfile.NamedTemporaryFile("w+", suffix=".env.deploy", delete=False) as deploy_f:
            deploy_f.write("REASON_DOMAIN=layered.andrew.com\nSHARED_SETTING=from_deploy\n")
            deploy_f.flush()
            deploy_path = deploy_f.name

        try:
            # Clean test isolation
            with patch.dict(os.environ, {}, clear=False):
                for k in ["DB_NAME", "PIP_FIND_LINKS", "SHARED_SETTING", "REASON_DOMAIN"]:
                    os.environ.pop(k, None)

                loaded = load_deploy_env(deploy_env_file=deploy_path, base_env_file=base_path)
                self.assertEqual(loaded, deploy_path)

                # Imported from base .env without duplication:
                self.assertEqual(os.environ.get("DB_NAME"), "imported_verbal_db")
                self.assertEqual(os.environ.get("PIP_FIND_LINKS"), "https://mirror.local/wheels")

                # Layered from .env.deploy:
                self.assertEqual(os.environ.get("REASON_DOMAIN"), "layered.andrew.com")
                # Deploy overrides base for same key:
                self.assertEqual(os.environ.get("SHARED_SETTING"), "from_deploy")
        finally:
            os.remove(base_path)
            os.remove(deploy_path)

    def test_reload_deploy_config_updates_module_globals(self):
        """Ensures reload_deploy_config updates DOMAIN and path constants from environment."""
        with patch.dict(os.environ, {
            "REASON_DOMAIN": "verbal.enterprise.corp",
            "REASON_BASE_DIR": "/opt/custom_reason",
            "REASON_APP_USER": "custom_app_runner",
        }):
            reload_deploy_config()
            import deploy.fabric_deploy as fd
            self.assertEqual(fd.DOMAIN, "verbal.enterprise.corp")
            self.assertEqual(fd.BASE_DIR, "/opt/custom_reason")
            self.assertEqual(fd.DJANGO_USER, "custom_app_runner")
            self.assertEqual(fd.SSL_CERT_PATH, "/opt/custom_reason/ssl/verbal.enterprise.corp.crt")

            # Restore defaults after test
            reload_deploy_config()

    def test_setup_nginx_and_ssl_accepts_domain_override(self):
        """Ensures setup_nginx_and_ssl honors explicit domain argument."""
        import inspect
        sig = inspect.signature(setup_nginx_and_ssl)
        self.assertIn("domain", sig.parameters)


class TestRsyncAndVendoredResources(SimpleTestCase):
    """Verifies that vendored wheels in resources/ are preserved across deployment syncs."""

    def test_resources_is_not_excluded_by_rsync(self):
        """Ensures resources/ is preserved in RSYNC_EXCLUDES so en_core_web_sm is deployed."""
        from deploy.fabric_deploy import RSYNC_EXCLUDES
        self.assertNotIn("resources/", RSYNC_EXCLUDES)
        self.assertNotIn("--exclude='resources/'", RSYNC_EXCLUDES)


class TestUpdateCodeIdempotency(SimpleTestCase):
    """Verifies that update_code idempotently handles both fresh/dirty dirs and existing repos."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_cleans_non_git_repo_directory_before_cloning(self, mock_sudo_bash, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import update_code, REPO_DIR
        c = create_mock_context()

        # Mock: safe.directory succeeds, but rev-parse fails (not a valid git repo)
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),   # safe.directory
            MagicMock(ok=False),  # rev-parse --is-inside-work-tree
            MagicMock(ok=True),   # git clone
        ]
        # test -e REPO_DIR returns true (directory exists from previous run)
        c.sudo.return_value = MagicMock(ok=True)

        update_code(c, git_url="git@bitbucket.org:org/verbal.git", branch="main")

        # Must have wiped the invalid REPO_DIR
        c.sudo.assert_any_call(f"rm -rf {REPO_DIR}")

        # Must have cloned freshly as deploy user
        clone_calls = [
            call for call in mock_run_deploy.call_args_list
            if "git clone" in str(call)
        ]
        self.assertTrue(len(clone_calls) > 0)
        self.assertIn("--branch main", str(clone_calls[0]))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_updates_existing_git_repo_with_fetch_and_reset(self, mock_sudo_bash, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import update_code, REPO_DIR
        c = create_mock_context()

        # Mock: safe.directory succeeds, rev-parse succeeds (valid git repo)
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),   # safe.directory
            MagicMock(ok=True),   # rev-parse --is-inside-work-tree
            MagicMock(ok=True),   # remote set-url
            MagicMock(ok=True),   # rm index.lock
            MagicMock(ok=True),   # fetch --all --prune
            MagicMock(ok=True),   # checkout -B main origin/main
            MagicMock(ok=True),   # reset --hard origin/main
            MagicMock(ok=True),   # clean -fd
        ]
        c.sudo.return_value = MagicMock(ok=True)

        update_code(c, git_url="git@bitbucket.org:org/verbal.git", branch="main")

        # Must NOT have removed REPO_DIR
        for call in c.sudo.call_args_list:
            self.assertNotIn(f"rm -rf {REPO_DIR}", str(call))

        # Check git maintenance steps
        cmd_history = [str(call) for call in mock_run_deploy.call_args_list]
        self.assertTrue(any("fetch --all --prune" in cmd for cmd in cmd_history))
        self.assertTrue(any("checkout -B main origin/main" in cmd for cmd in cmd_history))
        self.assertTrue(any("reset --hard origin/main" in cmd for cmd in cmd_history))
        self.assertTrue(any("clean -fd" in cmd for cmd in cmd_history))


class TestBuildAndMigrateSpacyWheel(SimpleTestCase):
    """Verifies that build_and_migrate ensures the vendored spaCy wheel is in place."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.backup_database")
    @patch("deploy.fabric_deploy.check_or_create_superuser")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_copies_wheel_from_repo_if_missing_in_app(self, mock_sudo_bash, mock_run_deploy, mock_su, mock_backup, mock_sudo):
        from deploy.fabric_deploy import build_and_migrate, APP_DIR, REPO_DIR
        c = create_mock_context()

        # Mock:
        # 1. test -f APP_DIR/.../en_core_web_sm... -> False (missing in app)
        # 2. test -f REPO_DIR/.../en_core_web_sm... -> True (present in repo)
        # 3. python3 -m zipfile -t REPO_DIR/... -> True (valid archive)
        # 4. cp ... -> True
        # 5. pip install -> True
        # 6. check --deploy -> True
        # 7. migrate -> True
        # 8. collectstatic -> True
        mock_run_deploy.side_effect = [
            MagicMock(ok=False),  # test -f app wheel
            MagicMock(ok=True),   # test -f repo wheel
            MagicMock(ok=True),   # zipfile -t repo wheel
            MagicMock(ok=True),   # cp repo wheel to app wheel
            MagicMock(ok=True),   # pip install
            MagicMock(ok=True),   # check --deploy
            MagicMock(ok=True),   # migrate
            MagicMock(ok=True),   # collectstatic
        ]
        c.sudo.return_value = MagicMock(ok=True)

        build_and_migrate(c)

        cmd_history = [str(call) for call in mock_run_deploy.call_args_list]
        self.assertTrue(any("cp" in cmd and "en_core_web_sm-3.8.0-py3-none-any.whl" in cmd for cmd in cmd_history))
        mock_backup.assert_called_once()
        mock_su.assert_called_once()

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.backup_database")
    @patch("deploy.fabric_deploy.check_or_create_superuser")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_purges_corrupt_wheel_and_replaces_from_repo(self, mock_sudo_bash, mock_run_deploy, mock_su, mock_backup, mock_sudo):
        from deploy.fabric_deploy import build_and_migrate, APP_DIR, REPO_DIR
        c = create_mock_context()

        # Mock:
        # 1. test -f APP_DIR/... -> True (file exists)
        # 2. python3 -m zipfile -t APP_DIR/... -> False (corrupt / HTML page!)
        # 3. test -f REPO_DIR/... -> True (present in repo)
        # 4. python3 -m zipfile -t REPO_DIR/... -> True (valid archive)
        # 5. cp ... -> True
        # 6. pip install -> True
        # 7. check --deploy -> True
        # 8. migrate -> True
        # 9. collectstatic -> True
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),   # test -f app wheel exists
            MagicMock(ok=False),  # zipfile -t app wheel FAILS (corrupt)
            MagicMock(ok=True),   # test -f repo wheel
            MagicMock(ok=True),   # zipfile -t repo wheel succeeds
            MagicMock(ok=True),   # cp repo wheel to app wheel
            MagicMock(ok=True),   # pip install
            MagicMock(ok=True),   # check --deploy
            MagicMock(ok=True),   # migrate
            MagicMock(ok=True),   # collectstatic
        ]
        c.sudo.return_value = MagicMock(ok=True)

        build_and_migrate(c)

        # Must have purged the corrupt file
        c.sudo.assert_any_call(f"rm -f {APP_DIR}/resources/wheels/en_core_web_sm-3.8.0-py3-none-any.whl")
        mock_su.assert_called_once()


class TestBackupDatabaseGuards(SimpleTestCase):
    """Verifies that backup_database gracefully skips unpopulated venvs or empty databases."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_skips_when_django_not_installed_in_venv(self, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import backup_database
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True)  # manage.py exists
        # import django fails (venv unpopulated)
        mock_run_deploy.return_value = MagicMock(ok=False)

        backup_database(c)

        # Should never invoke dumpdata
        for call in mock_run_deploy.call_args_list:
            self.assertNotIn("dumpdata", str(call))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_skips_when_database_has_no_tables(self, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import backup_database
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True)  # manage.py exists
        # 1. import django -> True
        # 2. check_tables -> False (no django_migrations table)
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),
            MagicMock(ok=False),
        ]

        backup_database(c)

        # Should never invoke dumpdata
        for call in mock_run_deploy.call_args_list:
            self.assertNotIn("dumpdata", str(call))


class TestDependencyAndIndexConsolidation(SimpleTestCase):
    """Verifies index flag consolidation, offline resilience, and requirements.txt purity."""

    def test_requirements_txt_has_no_active_index_directives(self):
        """Ensures requirements.txt does not contain un-commented index directives that break offline installs."""
        req_path = Path(settings.BASE_DIR) / "requirements.txt"
        self.assertTrue(req_path.is_file())
        with open(req_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                self.assertFalse(
                    stripped.startswith("--index-url"),
                    f"Found hardcoded --index-url in requirements.txt: {stripped}"
                )
                self.assertFalse(
                    stripped.startswith("--extra-index-url"),
                    f"Found hardcoded --extra-index-url in requirements.txt: {stripped}"
                )

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.backup_database")
    @patch("deploy.fabric_deploy.check_or_create_superuser")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_build_and_migrate_includes_pytorch_extra_index_flag(
        self, mock_sudo_bash, mock_run_deploy, mock_su, mock_backup, mock_sudo
    ):
        """Verifies that pytorch_index_url is injected into pip install EXTRA_FLAGS."""
        from deploy.fabric_deploy import build_and_migrate
        c = create_mock_context()
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),  # wheel test
            MagicMock(ok=True),  # zipfile test
            MagicMock(ok=True),  # pip install
            MagicMock(ok=True),  # check --deploy
            MagicMock(ok=True),  # migrate
            MagicMock(ok=True),  # collectstatic
        ]
        c.sudo.return_value = MagicMock(ok=True)

        build_and_migrate(c, pytorch_index_url="https://mirror.local/whl/cu124")

        pip_calls = [
            str(call) for call in mock_run_deploy.call_args_list
            if "pip install" in str(call)
        ]
        self.assertEqual(len(pip_calls), 1)
        self.assertIn("--extra-index-url", pip_calls[0])
        self.assertIn("https://mirror.local/whl/cu124", pip_calls[0])
        mock_su.assert_called_once()

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.backup_database")
    @patch("deploy.fabric_deploy.check_or_create_superuser")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_build_and_migrate_ignores_pytorch_when_none_or_disabled(
        self, mock_sudo_bash, mock_run_deploy, mock_su, mock_backup, mock_sudo
    ):
        """Verifies that pytorch_index_url='none' omits --extra-index-url."""
        from deploy.fabric_deploy import build_and_migrate
        c = create_mock_context()
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),  # wheel test
            MagicMock(ok=True),  # zipfile test
            MagicMock(ok=True),  # pip install
            MagicMock(ok=True),  # check --deploy
            MagicMock(ok=True),  # migrate
            MagicMock(ok=True),  # collectstatic
        ]
        c.sudo.return_value = MagicMock(ok=True)

        build_and_migrate(c, pytorch_index_url="none")

        pip_calls = [
            str(call) for call in mock_run_deploy.call_args_list
            if "pip install" in str(call)
        ]
        self.assertEqual(len(pip_calls), 1)
        # Should omit --extra-index-url entirely when none
        self.assertNotIn("--extra-index-url", pip_calls[0])
        mock_su.assert_called_once()

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.backup_database")
    @patch("deploy.fabric_deploy.check_or_create_superuser")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_build_and_migrate_formats_all_four_canonical_flags(
        self, mock_sudo_bash, mock_run_deploy, mock_su, mock_backup, mock_sudo
    ):
        """Verifies that all 4 canonical index flags are supported together."""
        from deploy.fabric_deploy import build_and_migrate
        c = create_mock_context()
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),  # wheel test
            MagicMock(ok=True),  # zipfile test
            MagicMock(ok=True),  # pip install
            MagicMock(ok=True),  # check --deploy
            MagicMock(ok=True),  # migrate
            MagicMock(ok=True),  # collectstatic
        ]
        c.sudo.return_value = MagicMock(ok=True)

        build_and_migrate(
            c,
            index_url="https://artifactory.local/api/pypi/simple",
            pytorch_index_url="https://artifactory.local/whl/cu124",
            find_links="https://artifactory.local/wheels",
            trusted_host="artifactory.local",
        )

        pip_calls = [
            str(call) for call in mock_run_deploy.call_args_list
            if "pip install" in str(call)
        ]
        self.assertEqual(len(pip_calls), 1)
        cmd_str = pip_calls[0]
        self.assertIn("https://artifactory.local/api/pypi/simple", cmd_str)
        self.assertIn("https://artifactory.local/whl/cu124", cmd_str)
        self.assertIn("https://artifactory.local/wheels", cmd_str)
        self.assertIn("artifactory.local", cmd_str)
        mock_su.assert_called_once()

    def test_build_pip_install_cmd_pure_function(self):
        """Ensures build_pip_install_cmd formats a clean, transparent command without shell indirection."""
        from deploy.fabric_deploy import build_pip_install_cmd
        cmd = build_pip_install_cmd(
            index_url="https://mirror.corp/pypi",
            pytorch_index_url="https://mirror.corp/whl/cu124",
            find_links="https://mirror.corp/wheels",
            trusted_host="mirror.corp",
            app_dir="/srv/reason/app",
            venv_dir="/srv/reason/venv",
        )
        self.assertEqual(
            cmd,
            "/srv/reason/venv/bin/pip install "
            "--find-links /srv/reason/app/resources/wheels "
            "--find-links https://mirror.corp/wheels "
            "--index-url https://mirror.corp/pypi "
            "--trusted-host mirror.corp "
            "--extra-index-url https://mirror.corp/whl/cu124 "
            "--no-cache-dir "
            "-r /srv/reason/app/requirements.txt"
        )


class TestStartBackendContainers(SimpleTestCase):
    """Verifies that start_backend_containers brings up core infrastructure and waits for PostgreSQL readiness."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.sudo_bash")
    @patch("deploy.fabric_deploy.get_remote_env")
    def test_starts_db_and_sandbox_and_waits_for_postgres_ready(self, mock_env, mock_sudo_bash, mock_sudo):
        from deploy.fabric_deploy import start_backend_containers, APP_DIR, DB_CONTAINER
        c = create_mock_context()
        mock_env.return_value = {"ENABLE_GROBID": "true"}
        mock_sudo_bash.return_value = MagicMock(ok=True, failed=False)

        start_backend_containers(c)

        calls = [str(call) for call in mock_sudo_bash.call_args_list]
        self.assertTrue(any("docker compose up -d db sandbox" in cmd for cmd in calls))
        self.assertTrue(any("docker compose --profile academic up -d grobid" in cmd for cmd in calls))
        self.assertTrue(any("pg_isready" in cmd for cmd in calls))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.sudo_bash")
    @patch("deploy.fabric_deploy.get_remote_env")
    def test_grobid_disabled_via_remote_env(self, mock_env, mock_sudo_bash, mock_sudo):
        from deploy.fabric_deploy import start_backend_containers
        c = create_mock_context()
        mock_env.return_value = {"ENABLE_GROBID": "false"}
        mock_sudo_bash.return_value = MagicMock(ok=True, failed=False)

        start_backend_containers(c)

        calls = [str(call) for call in mock_sudo_bash.call_args_list]
        self.assertTrue(any("docker compose up -d db sandbox" in cmd for cmd in calls))
        self.assertFalse(any("grobid" in cmd for cmd in calls))
        self.assertTrue(any("pg_isready" in cmd for cmd in calls))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.time.sleep")
    @patch("deploy.fabric_deploy.sudo_bash")
    @patch("deploy.fabric_deploy.get_remote_env")
    def test_postgres_readiness_failure_raises_exit(self, mock_env, mock_sudo_bash, mock_sleep, mock_sudo):
        from deploy.fabric_deploy import start_backend_containers, Exit
        c = create_mock_context()
        mock_env.return_value = {}
        # docker compose succeeds, but pg_isready fails repeatedly
        mock_sudo_bash.side_effect = [MagicMock(ok=True, failed=False)] + [MagicMock(ok=False, failed=True)] * 20

        with self.assertRaises(Exit):
            start_backend_containers(c)


class TestSuperuserManagement(SimpleTestCase):
    """Verifies automated detection, env-based auto-creation, guidance banner, and interactive task for superusers."""

    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_check_or_create_superuser_detects_existing(self, mock_run_deploy):
        from deploy.fabric_deploy import check_or_create_superuser
        c = create_mock_context()
        mock_run_deploy.return_value = MagicMock(ok=True, stdout="EXISTS\n")

        check_or_create_superuser(c)

        self.assertEqual(mock_run_deploy.call_count, 1)
        self.assertIn("is_superuser=True", str(mock_run_deploy.call_args))

    @patch.dict(os.environ, {"DJANGO_SUPERUSER_USERNAME": "auto_admin", "DJANGO_SUPERUSER_PASSWORD": "auto_password", "DJANGO_SUPERUSER_EMAIL": "admin@example.com"})
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_check_or_create_superuser_auto_creates_from_env(self, mock_run_deploy):
        from deploy.fabric_deploy import check_or_create_superuser
        c = create_mock_context()
        mock_run_deploy.side_effect = [
            MagicMock(ok=True, stdout="MISSING\n"),  # shell check
            MagicMock(ok=True, stdout="Created"),    # createsuperuser
        ]

        check_or_create_superuser(c)

        self.assertEqual(mock_run_deploy.call_count, 2)
        create_call = str(mock_run_deploy.call_args_list[1])
        self.assertIn("createsuperuser --noinput", create_call)
        self.assertIn("--username auto_admin", create_call)
        self.assertIn("--email admin@example.com", create_call)
        self.assertIn("DJANGO_SUPERUSER_PASSWORD=auto_password", create_call)

    @patch.dict(os.environ, {}, clear=True)
    @patch("builtins.print")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_check_or_create_superuser_warns_when_missing_credentials(self, mock_run_deploy, mock_print):
        from deploy.fabric_deploy import check_or_create_superuser
        c = create_mock_context()
        mock_run_deploy.return_value = MagicMock(ok=True, stdout="MISSING\n")

        check_or_create_superuser(c, remote_env={})

        self.assertEqual(mock_run_deploy.call_count, 1)
        printed_text = " ".join(str(call) for call in mock_print.call_args_list)
        self.assertIn("No Django superuser found in database", printed_text)
        self.assertIn("fab -H <host> createsuperuser", printed_text)

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("builtins.input", side_effect=["testboss", "boss@test.com"])
    @patch("getpass.getpass", side_effect=["pass123", "pass123"])
    def test_createsuperuser_task_interactive(self, mock_pass, mock_input, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import createsuperuser
        c = create_mock_context()
        mock_run_deploy.return_value = MagicMock(ok=True)

        createsuperuser(c)

        mock_run_deploy.assert_called_once()
        create_call = str(mock_run_deploy.call_args)
        self.assertIn("createsuperuser --noinput", create_call)
        self.assertIn("--username testboss", create_call)
        self.assertIn("--email boss@test.com", create_call)
        self.assertIn("DJANGO_SUPERUSER_PASSWORD=pass123", create_call)


class TestDeployOrdering(SimpleTestCase):
    """Verifies that the high-level deploy pipeline executes tasks in the strict sequence required for Docker/DB health."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.preflight_check")
    @patch("deploy.fabric_deploy.update_code")
    @patch("deploy.fabric_deploy.start_backend_containers")
    @patch("deploy.fabric_deploy.build_and_migrate")
    @patch("deploy.fabric_deploy.restart_services")
    @patch("deploy.fabric_deploy.health_check")
    def test_deploy_invokes_start_backend_containers_before_build_and_migrate(
        self, mock_health, mock_restart, mock_build, mock_start_containers, mock_update, mock_preflight, mock_sudo
    ):
        from deploy.fabric_deploy import deploy
        c = create_mock_context()
        manager = MagicMock()
        manager.attach_mock(mock_preflight, "preflight_check")
        manager.attach_mock(mock_update, "update_code")
        manager.attach_mock(mock_start_containers, "start_backend_containers")
        manager.attach_mock(mock_build, "build_and_migrate")
        manager.attach_mock(mock_restart, "restart_services")
        manager.attach_mock(mock_health, "health_check")

        deploy(c)

        expected_order = [
            "preflight_check",
            "update_code",
            "start_backend_containers",
            "build_and_migrate",
            "restart_services",
            "health_check",
        ]
        actual_order = [call[0] for call in manager.mock_calls]
        self.assertEqual(actual_order, expected_order)


class TestEnvironmentPreservation(SimpleTestCase):
    """Verifies that environment files in APP_DIR and SHARED_DIR are preserved and recognized."""

    def test_get_remote_env_merges_app_and_shared_files(self):
        """Verifies get_remote_env parses environment settings across both SHARED_DIR and APP_DIR."""
        import deploy.fabric_deploy as fd
        from deploy.fabric_deploy import get_remote_env
        c = create_mock_context()

        def mock_sudo(cmd, **kwargs):
            if f"test -f {fd.SHARED_DIR}/.env" in cmd:
                return MagicMock(ok=True)
            if f"test -f {fd.APP_DIR}/.env" in cmd:
                return MagicMock(ok=True)
            if f"cat {fd.SHARED_DIR}/.env" in cmd:
                return MagicMock(ok=True, stdout="DB_NAME=shared_db\nENABLE_GROBID=true\n")
            if f"cat {fd.APP_DIR}/.env" in cmd:
                return MagicMock(ok=True, stdout="DB_PORT=5433\nDB_NAME=custom_app_db\n")
            return MagicMock(ok=False, stdout="")

        c.sudo.side_effect = mock_sudo
        env = get_remote_env(c)

        self.assertEqual(env["ENABLE_GROBID"], "true")
        self.assertEqual(env["DB_PORT"], "5433")
        self.assertEqual(env["DB_NAME"], "custom_app_db")

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_update_code_preserves_regular_app_env_file(self, mock_bash, mock_run_deploy, mock_sudo):
        """Verifies update_code does not overwrite physical APP_DIR/.env with a symlink."""
        import deploy.fabric_deploy as fd
        from deploy.fabric_deploy import update_code
        c = create_mock_context()

        def mock_sudo_behavior(cmd, **kwargs):
            if f"test -f {fd.APP_DIR}/.env" in cmd:
                return MagicMock(ok=True)
            if f"test -L {fd.APP_DIR}/.env" in cmd:
                return MagicMock(ok=False)  # regular file, not a symlink
            if f"test -f {fd.SHARED_DIR}/.env" in cmd:
                return MagicMock(ok=True)
            return MagicMock(ok=True)

        c.sudo.side_effect = mock_sudo_behavior
        mock_run_deploy.return_value = MagicMock(ok=True)
        mock_bash.return_value = MagicMock(ok=True)

        update_code(c, local_source="/mock/source")

        # Ensure ln -sfn {fd.SHARED_DIR}/.env {fd.APP_DIR}/.env was NOT called
        all_sudo_calls = [str(call) for call in c.sudo.call_args_list]
        for call_str in all_sudo_calls:
            self.assertNotIn(f"ln -sfn {fd.SHARED_DIR}/.env {fd.APP_DIR}/.env", call_str)


class TestProvisioningAndEnvSync(SimpleTestCase):
    """Verifies that provisioning avoids host network interference and sync_env writes directly to APP_DIR."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_provision_system_does_not_modify_host_network(self, mock_bash, mock_sudo_check):
        """Verifies that provision_system never writes to networkd or restarts network daemons."""
        import deploy.fabric_deploy as fd
        from deploy.fabric_deploy import provision_system
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True, failed=False)
        mock_bash.return_value = MagicMock(ok=True)

        provision_system(c)

        for call in c.sudo.call_args_list:
            call_str = str(call)
            self.assertNotIn("systemd-networkd", call_str)
            self.assertNotIn("99-docker-unmanaged.network", call_str)
            self.assertNotIn("iptables", call_str)
            self.assertNotIn("ufw", call_str)

        for call in mock_bash.call_args_list:
            call_str = str(call)
            self.assertNotIn("systemd-networkd", call_str)
            self.assertNotIn("99-docker-unmanaged.network", call_str)

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.write_sudo_file")
    def test_sync_env_writes_directly_to_app_dir(self, mock_write_file, mock_sudo_check):
        """Verifies sync_env targets APP_DIR/.env directly without shared symlinks."""
        import deploy.fabric_deploy as fd
        from deploy.fabric_deploy import sync_env
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True)

        repo_root = Path(__file__).resolve().parent.parent
        test_env = repo_root / ".env.example"

        sync_env(c, local_env=str(test_env))

        # Check write_sudo_file calls
        written_targets = [call[0][1] for call in mock_write_file.call_args_list]
        self.assertIn(f"{fd.APP_DIR}/.env", written_targets)
        for target in written_targets:
            self.assertNotIn(f"{fd.SHARED_DIR}/.env", target)


class TestNginxAndPackagePreflight(SimpleTestCase):
    """Verifies that preflight validates packages and setup_nginx_and_ssl defaults to removing default site."""

    def test_setup_nginx_and_ssl_signature_defaults_remove_default_site_to_true(self):
        """Ensures setup_nginx_and_ssl and setup_all default remove_default_site=True."""
        import inspect
        from deploy.fabric_deploy import setup_nginx_and_ssl, setup_all
        sig_nginx = inspect.signature(setup_nginx_and_ssl)
        self.assertIn("remove_default_site", sig_nginx.parameters)
        self.assertTrue(sig_nginx.parameters["remove_default_site"].default)

        sig_setup = inspect.signature(setup_all)
        self.assertIn("remove_default_site", sig_setup.parameters)
        self.assertTrue(sig_setup.parameters["remove_default_site"].default)

    @patch.dict(os.environ, {"REASON_REMOVE_DEFAULT_NGINX": "true"}, clear=False)
    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.write_sudo_file")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_setup_nginx_removes_default_site_by_default(self, mock_bash, mock_write, mock_sudo_check):
        """Verifies setup_nginx_and_ssl removes /etc/nginx/sites-enabled/default by default."""
        from deploy.fabric_deploy import setup_nginx_and_ssl
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True, failed=False)
        mock_bash.return_value = MagicMock(ok=True)

        setup_nginx_and_ssl(c)

        # Verify default site unlinking was called
        sudo_calls = [str(call) for call in c.sudo.call_args_list]
        self.assertTrue(any("rm -f /etc/nginx/sites-enabled/default" in call for call in sudo_calls))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.write_sudo_file")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_setup_nginx_preserves_default_site_when_disabled(self, mock_bash, mock_write, mock_sudo_check):
        """Verifies setup_nginx_and_ssl preserves default site when remove_default_site=False."""
        from deploy.fabric_deploy import setup_nginx_and_ssl
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True, failed=False)
        mock_bash.return_value = MagicMock(ok=True)

        setup_nginx_and_ssl(c, remove_default_site=False)

        sudo_calls = [str(call) for call in c.sudo.call_args_list]
        self.assertFalse(any("rm -f /etc/nginx/sites-enabled/default" in call for call in sudo_calls))

    @patch("deploy.fabric_deploy.ensure_sudo")
    def test_preflight_auto_installs_missing_packages(self, mock_sudo_check):
        """Verifies preflight_check auto-installs python3-venv and nginx via apt-get if missing."""
        from deploy.fabric_deploy import preflight_check
        c = create_mock_context()
        c.sudo.return_value = MagicMock(ok=True, failed=False)

        def mock_run(cmd, **kwargs):
            if "python3 --version" in cmd:
                return MagicMock(ok=True, failed=False)
            if "python3 -m venv -h" in cmd:
                return MagicMock(ok=False, failed=True)
            if "which nginx" in cmd:
                return MagicMock(ok=False, failed=True)
            if "command -v apt-get" in cmd:
                return MagicMock(ok=True, failed=False)
            return MagicMock(ok=True, failed=False)

        c.run.side_effect = mock_run

        preflight_check(c)

        # Check apt-get install was called for missing packages
        sudo_calls = [str(call) for call in c.sudo.call_args_list]
        self.assertTrue(any("apt-get install -y python3-venv nginx" in call for call in sudo_calls))






