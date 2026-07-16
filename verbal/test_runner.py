from django.test.runner import DiscoverRunner
from django.db import connection

class ForceTeardownTestRunner(DiscoverRunner):
    """
    A custom test runner that forces connections to close before destroying the test database.
    This resolves psycopg OperationalError: database "test_verbal_db" is being accessed by other users.
    """
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
            print(f"Warning: Failed to terminate DB connections: {e}")
            
        super().teardown_databases(old_config, **kwargs)
