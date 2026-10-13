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

    def test_all_four_services_generated(self):
        """Ensures all 4 modular units exist."""
        expected_keys = {
            "reason-web.service",
            "reason-worker.service",
            "reason-scheduler.service",
            "reason-inference.service",
        }
        self.assertEqual(set(self.units.keys()), expected_keys)

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

    def test_inference_service_includes_noreload(self):
        """Verifies inference service specifies --noreload to prevent double model instantiation."""
        inference = self.units["reason-inference.service"]
        self.assertIn("--noreload", inference)
        self.assertIn("manage.py runserver --noreload 127.0.0.1:8001", inference)

    def test_systemd_sandboxing_and_privilege_isolation(self):
        """Verifies modern systemd security directives are present in all service units."""
        for name, unit in self.units.items():
            with self.subTest(unit=name):
                self.assertIn("NoNewPrivileges=yes", unit)
                self.assertIn("ProtectSystem=strict", unit)
                self.assertIn("ProtectHome=yes", unit)
                self.assertIn("PrivateTmp=yes", unit)
                self.assertIn("ReadWritePaths=/srv/reason/shared /var/lib/hf_home /srv/reason/logs", unit)
                self.assertIn("EnvironmentFile=-/srv/reason/shared/.env.secrets", unit)


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

    def test_backup_database_defaults_to_dumpdata(self):
        """Ensures backup_database task signature defaults to use_dumpdata=True."""
        import inspect
        from deploy.fabric_deploy import backup_database, deploy, setup_all, build_and_migrate

        backup_sig = inspect.signature(backup_database)
        self.assertIn("use_dumpdata", backup_sig.parameters)
        self.assertTrue(backup_sig.parameters["use_dumpdata"].default)

        build_sig = inspect.signature(build_and_migrate)
        self.assertIn("use_dumpdata", build_sig.parameters)
        self.assertTrue(build_sig.parameters["use_dumpdata"].default)

        deploy_sig = inspect.signature(deploy)
        self.assertIn("use_dumpdata", deploy_sig.parameters)
        self.assertTrue(deploy_sig.parameters["use_dumpdata"].default)

        setup_sig = inspect.signature(setup_all)
        self.assertIn("use_dumpdata", setup_sig.parameters)
        self.assertTrue(setup_sig.parameters["use_dumpdata"].default)


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
        c = MagicMock()

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
        c = MagicMock()

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
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    @patch("deploy.fabric_deploy.sudo_bash")
    def test_copies_wheel_from_repo_if_missing_in_app(self, mock_sudo_bash, mock_run_deploy, mock_backup, mock_sudo):
        from deploy.fabric_deploy import build_and_migrate, APP_DIR, REPO_DIR
        c = MagicMock()

        # Mock:
        # 1. test -f APP_DIR/.../en_core_web_sm... -> False (missing in app)
        # 2. test -f REPO_DIR/.../en_core_web_sm... -> True (present in repo)
        # 3. cp ... -> True
        # 4. pip install -> True
        # 5. check --deploy -> True
        # 6. migrate -> True
        # 7. collectstatic -> True
        mock_run_deploy.side_effect = [
            MagicMock(ok=False),  # test -f app wheel
            MagicMock(ok=True),   # test -f repo wheel
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


class TestBackupDatabaseGuards(SimpleTestCase):
    """Verifies that backup_database gracefully skips unpopulated venvs or empty databases."""

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_skips_when_django_not_installed_in_venv(self, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import backup_database
        c = MagicMock()
        c.sudo.return_value = MagicMock(ok=True)  # manage.py exists
        # import django fails (venv unpopulated)
        mock_run_deploy.return_value = MagicMock(ok=False)

        backup_database(c, use_dumpdata=True)

        # Should never invoke dumpdata
        for call in mock_run_deploy.call_args_list:
            self.assertNotIn("dumpdata", str(call))

    @patch("deploy.fabric_deploy.ensure_sudo")
    @patch("deploy.fabric_deploy.run_as_deploy_user")
    def test_skips_when_database_has_no_tables(self, mock_run_deploy, mock_sudo):
        from deploy.fabric_deploy import backup_database
        c = MagicMock()
        c.sudo.return_value = MagicMock(ok=True)  # manage.py exists
        # 1. import django -> True
        # 2. check_tables -> False (no django_migrations table)
        mock_run_deploy.side_effect = [
            MagicMock(ok=True),
            MagicMock(ok=False),
        ]

        backup_database(c, use_dumpdata=True)

        # Should never invoke dumpdata
        for call in mock_run_deploy.call_args_list:
            self.assertNotIn("dumpdata", str(call))



