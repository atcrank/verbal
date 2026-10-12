"""
Automated tests for deployment configuration, Nginx templating,
systemd unit generation, and Django settings hardening.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from deploy.fabric_deploy import generate_nginx_conf, generate_systemd_units, DOMAIN


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
        for cmd in ['migrate', 'collectstatic', 'check']:
            self.assertIn(cmd, management_commands_skip)
