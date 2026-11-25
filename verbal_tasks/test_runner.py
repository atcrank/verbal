import logging
import shutil
import tempfile
from pathlib import Path

from django.db import connection
from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

logger = logging.getLogger(__name__)


class ForceTeardownTestRunner(DiscoverRunner):
    """
    Custom test runner that:
    1. Isolates MEDIA_ROOT to an ephemeral temporary directory so uploaded test
       fixtures never pollute the workspace's media/ directory.
    2. Disables live host orchestration/pings by default via explicit Django settings
       (rather than un-testable 'test' in sys.argv checks in production code), allowing
       targeted integration tests to opt in cleanly with @override_settings(...).
    3. Forces lingering Postgres connections to close before dropping the test database.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._temp_media_dir = tempfile.mkdtemp(prefix="verbal_test_media_")
        self._temp_backup_dir = tempfile.mkdtemp(prefix="verbal_test_backups_")
        self._ws43_settings_override = override_settings(
            MEDIA_ROOT=Path(self._temp_media_dir),
            BACKUP_DIR=Path(self._temp_backup_dir),
            ENABLE_HOSTING_BACKEND_SYNC=False,
            ENABLE_LIVE_BACKEND_PING=False,
            ENABLE_GRIPS_AUTO_SYNC=False,
        )
        self._ws43_settings_override.enable()

    def teardown_test_environment(self, **kwargs):
        if hasattr(self, "_ws43_settings_override"):
            self._ws43_settings_override.disable()
        for attr in ("_temp_media_dir", "_temp_backup_dir"):
            tmp_dir = getattr(self, attr, None)
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        super().teardown_test_environment(**kwargs)

    def teardown_databases(self, old_config, **kwargs):
        try:
            # Force close all other connections to the test database
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid();
                """)
        except Exception as e:
            logger.info(f"Warning: Failed to terminate DB connections: {e}")

        super().teardown_databases(old_config, **kwargs)
