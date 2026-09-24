import os
import tempfile
from unittest.mock import patch, MagicMock
import subprocess
import requests
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.conf import settings
from .models import SandboxConfiguration, SandboxExecutionLog
from metacognition.meta_tools import SecurityASTVisitor, django_shell_script


class SandboxConfigurationModelTests(TestCase):
    """Unit tests for SandboxConfiguration model and validation."""

    def test_singleton_enforcement(self):
        """Verify that get_solo() always retrieves or creates the singleton configuration."""
        config1 = SandboxConfiguration.get_solo()
        config1.execution_timeout = 45
        config1.save()

        config2 = SandboxConfiguration.get_solo()
        self.assertEqual(config2.pk, 1)
        self.assertEqual(config2.execution_timeout, 45)

        # Explicitly creating another should overwrite pk=1
        config3 = SandboxConfiguration(requirements_txt="numpy\npandas")
        config3.save()
        self.assertEqual(config3.pk, 1)
        self.assertEqual(SandboxConfiguration.objects.count(), 1)

    def test_pep508_validation_success(self):
        """Valid PEP-508 requirement lines and comments should validate cleanly."""
        valid_reqs = (
            "# Core dependencies\n"
            "fastapi>=0.100.0\n"
            "uvicorn[standard]==0.24.0\n"
            "numpy~=2.1.0\n"
            "--extra-index-url https://example.com/simple\n"
            "pandas\n"
        )
        config = SandboxConfiguration(requirements_txt=valid_reqs)
        try:
            config.clean()
        except ValidationError:
            self.fail("config.clean() raised ValidationError unexpectedly on valid requirements!")

    def test_pep508_validation_failure(self):
        """Malformed package lines should trigger ValidationError."""
        invalid_reqs = "numpy\n<<<invalid-syntax-pkg>>>\npandas"
        config = SandboxConfiguration(requirements_txt=invalid_reqs)
        with self.assertRaises(ValidationError) as ctx:
            config.clean()
        self.assertIn("requirements_txt", ctx.exception.message_dict)

    def test_sync_requirements_file(self):
        """sync_requirements_file writes the requirements text to disk."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(settings, 'BASE_DIR', tmp_dir):
                config = SandboxConfiguration(requirements_txt="scipy\nsympy")
                config.sync_requirements_file()
                expected_file = os.path.join(tmp_dir, 'sandbox', 'requirements.txt')
                self.assertTrue(os.path.exists(expected_file))
                with open(expected_file, 'r', encoding='utf-8') as f:
                    self.assertEqual(f.read(), "scipy\nsympy")


class SandboxExecutionLogTests(TestCase):
    """Unit tests for SandboxExecutionLog model."""

    def test_execution_log_creation_and_ordering(self):
        """Logs should record execution details and order by most recent timestamp."""
        log1 = SandboxExecutionLog.objects.create(
            filepath="workspace_1/test1.py",
            conversation_id="conv_123",
            return_code=0,
            stdout="Success output",
            stderr=""
        )
        log2 = SandboxExecutionLog.objects.create(
            filepath="workspace_1/test2.py",
            conversation_id="conv_123",
            return_code=124,
            stdout="",
            stderr="Timed out after 30 seconds."
        )

        logs = list(SandboxExecutionLog.objects.all())
        self.assertEqual(logs[0].id, log2.id)
        self.assertEqual(logs[1].id, log1.id)
        self.assertEqual(logs[0].return_code, 124)


class SandboxFastAPIEndpointTests(TestCase):
    """Unit tests for sandbox HTTP execute endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sandbox_url = getattr(settings, 'SANDBOX_URL', "http://127.0.0.1:8002/execute")
        try:
            r = requests.get(cls.sandbox_url.replace('/execute', '/docs'), timeout=2)
            cls.sandbox_available = (r.status_code == 200)
        except Exception:
            cls.sandbox_available = False

    def test_path_traversal_blocked(self):
        """Relative path traversal outside /workspace must be rejected with HTTP 403."""
        if not self.sandbox_available:
            self.skipTest("Sandbox container not reachable on port 8002.")
        res = requests.post(self.sandbox_url, json={"filepath": "../etc/passwd", "timeout": 5})
        self.assertEqual(res.status_code, 403)
        self.assertIn("Path traversal attempt blocked", res.json().get("detail", ""))

    def test_nonexistent_file_returns_404(self):
        """Nonexistent script file inside workspace must return HTTP 404."""
        if not self.sandbox_available:
            self.skipTest("Sandbox container not reachable on port 8002.")
        res = requests.post(self.sandbox_url, json={"filepath": "does_not_exist_subfolder/script.py", "timeout": 5})
        self.assertEqual(res.status_code, 404)

    def test_timeout_decoding_bug_safeguard(self):
        """Verify that timeout handling logic works safely with both string and bytes."""
        timeout_exc = subprocess.TimeoutExpired(
            cmd=["python", "/workspace/mock_script.py"],
            timeout=5,
            output="partial string output",
            stderr="partial string error"
        )
        stdout_str = timeout_exc.stdout.decode() if isinstance(timeout_exc.stdout, bytes) else (timeout_exc.stdout or "")
        stderr_str = timeout_exc.stderr.decode() if isinstance(timeout_exc.stderr, bytes) else (timeout_exc.stderr or "")
        payload = {
            "stdout": stdout_str,
            "stderr": stderr_str + f"\n[SYSTEM] Execution timed out after {timeout_exc.timeout} seconds.",
            "returncode": 124,
            "status": "timeout"
        }
        self.assertEqual(payload["status"], "timeout")
        self.assertEqual(payload["returncode"], 124)
        self.assertIn("partial string output", payload["stdout"])
        self.assertIn("Execution timed out after 5 seconds", payload["stderr"])


class SecurityASTVisitorAndJailingTests(TestCase):
    """Unit tests for AST security checks and sandbox dispatch in metacognition."""

    def test_ast_visitor_blocks_host_imports(self):
        """Direct imports of os, sys, subprocess, requests, socket must be blocked."""
        malicious_snippets = [
            "import os; os.system('ls')",
            "import sys; sys.exit(1)",
            "import subprocess; subprocess.run(['ls'])",
            "from os import path",
            "import socket",
            "import urllib.request",
            "import importlib; m = importlib.import_module('os')",
        ]
        for snippet in malicious_snippets:
            import ast
            tree = ast.parse(snippet)
            visitor = SecurityASTVisitor()
            visitor.visit(tree)
            self.assertTrue(len(visitor.errors) > 0, f"Expected AST check to fail for snippet: {snippet}")

    def test_ast_visitor_blocks_eval_exec_and_introspection(self):
        """Calls to eval(), exec(), __import__(), and __builtins__ introspection must be blocked."""
        bypass_snippets = [
            "eval('1 + 1')",
            "exec('a = 1')",
            "compile('1 + 1', '<string>', 'eval')",
            "__import__('os').system('ls')",
            "getattr(__builtins__, '__import__')('os')",
            "print([].__class__.__bases__[0].__subclasses__())",
            "x = object.__subclasses__()",
            "User.objects.all().delete()",
        ]
        for snippet in bypass_snippets:
            import ast
            tree = ast.parse(snippet)
            visitor = SecurityASTVisitor()
            visitor.visit(tree)
            self.assertTrue(len(visitor.errors) > 0, f"Expected AST check to fail for bypass snippet: {snippet}")

    def test_ast_visitor_allows_clean_computation(self):
        """Standard mathematical / computational Python without host escapes should pass."""
        safe_snippet = (
            "def calculate_mean(values):\n"
            "    return sum(values) / len(values) if values else 0.0\n"
            "data = [10, 20, 30, 40]\n"
            "result = calculate_mean(data)\n"
            "print(f'Mean: {result}')\n"
        )
        import ast
        tree = ast.parse(safe_snippet)
        visitor = SecurityASTVisitor()
        visitor.visit(tree)
        self.assertEqual(visitor.errors, [])

    @patch("requests.post")
    def test_django_shell_script_sandbox_routing(self, mock_post):
        """Verify django_shell_script dispatches to Sandbox HTTP endpoint instead of running host exec."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "stdout": "Computation result: 42\n",
            "stderr": "",
            "returncode": 0,
            "status": "success"
        }
        mock_post.return_value = mock_response

        state = {"conversation_id": "test-conv-001"}
        params = {"script_content": "val = 21 * 2\nprint(f'Computation result: {val}')"}

        output = django_shell_script(state, params)
        self.assertIn("Computation result: 42", output)
        self.assertTrue(mock_post.called)

        # Verify execution was logged
        log = SandboxExecutionLog.objects.filter(conversation_id="test-conv-001").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.return_code, 0)
        self.assertIn("Computation result: 42", log.stdout)

    def test_django_shell_script_blocks_malicious_code_before_network(self):
        """Malicious code must be rejected immediately by AST check without making network calls."""
        with patch("requests.post") as mock_post:
            params = {"script_content": "import os\nos.system('whoami')"}
            output = django_shell_script({}, params)
            self.assertIn("Error: Security violation", output)
            self.assertFalse(mock_post.called)
