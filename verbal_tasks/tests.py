from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.core.management import call_command
from django.tasks import task, TaskResultStatus
from django.tasks.exceptions import TaskResultDoesNotExist
from django.utils import timezone
from verbal_tasks.models import (
    TaskRecord,
    TaskRecordStatus,
    ScheduledTask,
    cron_matches,
    match_cron_field
)
from verbal_tasks.task_backend import DatabaseTaskBackend

# Import tasks from migrated apps to ensure clean definitions
from metacognition.tasks import task_run_blueprint_async, task_update_performance_scores
from grips.tasks import generate_concept_narrative, task_digest_corpus_level_1, task_lint_concept_node
from background_resources.tasks import task_process_documents, sweep_unprocessed_documents
from benchmarking.tasks import task_generate_benchmarks, sweep_benchmark_generation
from grobid_client.tasks import task_extract_grobid_metadata
from llm_api.tasks import download_model_cache


@task
def sample_task_add(a: int, b: int) -> int:
    return a + b


@task
def sample_task_fail(msg: str):
    raise ValueError(f"Intentionally failing: {msg}")


@task(priority=10)
def sample_high_priority_task() -> str:
    return "high_priority"


@task(priority=1)
def sample_low_priority_task() -> str:
    return "low_priority"


@task
def sample_task_with_llm(prompt: str) -> dict:
    import time
    from verbal_tasks.telemetry import record_task_generation
    time.sleep(0.01)
    record_task_generation(input_tokens=150, output_tokens=75, duration_ms=250.0, tokens_per_second=30.0)
    return {"status": "ok", "prompt": prompt}


class DjangoTasksFrameworkTests(TestCase):
    """
    Unit and integration tests for PostgreSQL-backed Django Tasks framework (WS11 Phase 1 & 2).
    """

    def setUp(self):
        TaskRecord.objects.all().delete()
        ScheduledTask.objects.all().delete()

    def test_immediate_backend_execution(self):
        """Verifies immediate backend executes synchronously and populates result."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "django.tasks.backends.immediate.ImmediateBackend",
                }
            }
        ):
            result = sample_task_add.enqueue(10, 20)
            self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
            self.assertEqual(result.return_value, 30)
            self.assertIsNotNone(result.enqueued_at)
            self.assertIsNotNone(result.finished_at)

    def test_database_task_backend_enqueue_and_get_result(self):
        """Verifies DatabaseTaskBackend writes a TaskRecord to PostgreSQL with READY status."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            result = sample_task_add.enqueue(5, 7)
            self.assertEqual(result.status, TaskResultStatus.READY)

            # Check DB record directly
            record = TaskRecord.objects.get(id=result.id)
            self.assertEqual(record.status, TaskRecordStatus.READY)
            self.assertEqual(record.args_json, [5, 7])
            self.assertEqual(record.queue_name, "default")

            # Check get_result on backend
            backend = DatabaseTaskBackend(alias="default", params={})
            fetched_result = backend.get_result(result.id)
            self.assertEqual(fetched_result.id, result.id)
            self.assertEqual(fetched_result.status, TaskResultStatus.READY)

    def test_runtaskworker_executes_task_successfully(self):
        """Verifies runtaskworker in burst mode processes READY tasks to SUCCESSFUL."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            result = sample_task_add.enqueue(40, 2)
            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.READY).count(), 1)

            # Run worker in burst mode
            call_command("runtaskworker", burst=True, queues=["default"])

            # Verify task completed
            record = TaskRecord.objects.get(id=result.id)
            self.assertEqual(record.status, TaskRecordStatus.SUCCESSFUL)
            self.assertEqual(record.return_value_json, 42)
            self.assertIsNotNone(record.started_at)
            self.assertIsNotNone(record.finished_at)
            self.assertTrue(record.worker_id.startswith("worker-"))

            # Verify TaskResult.refresh() reflects SUCCESSFUL
            result.refresh()
            self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
            self.assertEqual(result.return_value, 42)

    def test_runtaskworker_captures_task_failure(self):
        """Verifies runtaskworker catches exceptions, marks FAILED, and records traceback."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            result = sample_task_fail.enqueue("Database error")

            call_command("runtaskworker", burst=True, queues=["default"])

            record = TaskRecord.objects.get(id=result.id)
            self.assertEqual(record.status, TaskRecordStatus.FAILED)
            self.assertIn("ValueError: Intentionally failing: Database error", record.error_traceback)

            result.refresh()
            self.assertEqual(result.status, TaskResultStatus.FAILED)
            self.assertTrue(len(result.errors) > 0)

    def test_priority_ordering_in_worker(self):
        """Verifies higher priority tasks are dequeued first."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            low_result = sample_low_priority_task.enqueue()
            high_result = sample_high_priority_task.enqueue()

            first_ready = TaskRecord.objects.filter(status=TaskRecordStatus.READY).order_by('-priority', 'enqueued_at').first()
            self.assertEqual(first_ready.id, high_result.id)

    def test_get_nonexistent_result_raises_exception(self):
        """Verifies get_result raises TaskResultDoesNotExist when ID is invalid."""
        backend = DatabaseTaskBackend(alias="default", params={})
        with self.assertRaises(TaskResultDoesNotExist):
            backend.get_result("nonexistent-task-id")

    def test_migrated_app_tasks_can_be_enqueued_and_executed(self):
        """Verifies migrated app tasks can be enqueued and executed via DatabaseTaskBackend."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            sweep_doc_res = sweep_unprocessed_documents.enqueue()
            sweep_bench_res = sweep_benchmark_generation.enqueue()
            perf_res = task_update_performance_scores.enqueue()

            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.READY).count(), 3)

            call_command("runtaskworker", burst=True, queues=["default"])

            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.SUCCESSFUL).count(), 3)
            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.FAILED).count(), 0)


class ScheduledTaskSchedulerTests(TestCase):
    """
    Unit and integration tests for PostgreSQL-backed ScheduledTask and runtaskscheduler (WS11 Phase 3).
    """

    def setUp(self):
        TaskRecord.objects.all().delete()
        ScheduledTask.objects.all().delete()

    def test_cron_matches_evaluation(self):
        """Verifies 5-field cron parsing correctly matches target datetimes."""
        # 9:00 PM (21:00) every day: "0 21 * * *"
        dt_match = datetime(2026, 8, 31, 21, 0, tzinfo=dt_timezone.utc)
        dt_non_match = datetime(2026, 8, 31, 21, 1, tzinfo=dt_timezone.utc)
        self.assertTrue(cron_matches("0 21 * * *", dt_match))
        self.assertFalse(cron_matches("0 21 * * *", dt_non_match))

        # 8:30 PM (20:30) every day: "30 20 * * *"
        dt_scoring = datetime(2026, 8, 31, 20, 30, tzinfo=dt_timezone.utc)
        self.assertTrue(cron_matches("30 20 * * *", dt_scoring))
        self.assertFalse(cron_matches("30 20 * * *", dt_match))

        # Every 15 minutes: "*/15 * * * *"
        self.assertTrue(cron_matches("*/15 * * * *", datetime(2026, 8, 31, 12, 0, tzinfo=dt_timezone.utc)))
        self.assertTrue(cron_matches("*/15 * * * *", datetime(2026, 8, 31, 12, 15, tzinfo=dt_timezone.utc)))
        self.assertTrue(cron_matches("*/15 * * * *", datetime(2026, 8, 31, 12, 30, tzinfo=dt_timezone.utc)))
        self.assertFalse(cron_matches("*/15 * * * *", datetime(2026, 8, 31, 12, 10, tzinfo=dt_timezone.utc)))

    def test_scheduled_task_cron_is_due(self):
        """Verifies ScheduledTask.is_due evaluates correctly and prevents duplicate same-minute firing."""
        sched = ScheduledTask.objects.create(
            name="Test Daily 9PM Task",
            task_name="verbal_tasks.tests.sample_task_add",
            cron_expression="0 21 * * *",
            args_json=[1, 2]
        )

        dt_9pm = datetime(2026, 8, 31, 21, 0, tzinfo=dt_timezone.utc)
        dt_901pm = datetime(2026, 8, 31, 21, 1, tzinfo=dt_timezone.utc)

        # Is due at 9:00 PM when last_run_at is None
        self.assertTrue(sched.is_due(dt_9pm))
        self.assertFalse(sched.is_due(dt_901pm))

        # Once run at 9:00 PM, cannot run again at 9:00 PM
        sched.last_run_at = dt_9pm
        sched.save()
        self.assertFalse(sched.is_due(dt_9pm))

    def test_scheduled_task_interval_is_due(self):
        """Verifies ScheduledTask with interval_seconds calculates due state based on elapsed time."""
        sched = ScheduledTask.objects.create(
            name="Test Interval Task",
            task_name="verbal_tasks.tests.sample_task_add",
            interval_seconds=300, # 5 minutes
            args_json=[3, 4]
        )

        t0 = datetime(2026, 8, 31, 10, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(sched.is_due(t0))

        sched.last_run_at = t0
        sched.save()

        # 4 minutes later -> not due
        t1 = datetime(2026, 8, 31, 10, 4, tzinfo=dt_timezone.utc)
        self.assertFalse(sched.is_due(t1))

        # 5 minutes later -> due
        t2 = datetime(2026, 8, 31, 10, 5, tzinfo=dt_timezone.utc)
        self.assertTrue(sched.is_due(t2))

    def test_scheduled_task_trigger_enqueues_task(self):
        """Verifies ScheduledTask.trigger() creates a TaskRecord and updates schedule metrics."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            sched = ScheduledTask.objects.create(
                name="Test Trigger Task",
                task_name="verbal_tasks.tests.sample_task_add",
                interval_seconds=60,
                args_json=[10, 25]
            )

            record = sched.trigger()
            self.assertIsNotNone(record)
            self.assertEqual(record.status, TaskRecordStatus.READY)
            self.assertEqual(record.args_json, [10, 25])

            sched.refresh_from_db()
            self.assertEqual(sched.total_run_count, 1)
            self.assertIsNotNone(sched.last_run_at)
            self.assertEqual(sched.last_task_record, record)

    def test_runtaskscheduler_command_once(self):
        """Verifies runtaskscheduler --once evaluates due tasks and enqueues them."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            # Create a due interval task
            sched = ScheduledTask.objects.create(
                name="Test Scheduler Command Task",
                task_name="verbal_tasks.tests.sample_task_add",
                interval_seconds=10,
                args_json=[100, 200]
            )

            self.assertEqual(TaskRecord.objects.count(), 0)

            # Execute single tick of scheduler
            call_command("runtaskscheduler", once=True)

            self.assertEqual(TaskRecord.objects.count(), 1)
            record = TaskRecord.objects.first()
            self.assertEqual(record.args_json, [100, 200])

            # Now run task worker in burst mode to complete the task
            call_command("runtaskworker", burst=True, queues=["default"])
            record.refresh_from_db()
            self.assertEqual(record.status, TaskRecordStatus.SUCCESSFUL)
            self.assertEqual(record.return_value_json, 300)

    def test_seed_nightmanager_creates_scheduled_tasks(self):
        """Verifies seed_nightmanager populates NightManager and Performance Scoring ScheduledTasks."""
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition, bypass_canonical_lock
        from metacognition.seed import seed_nightmanager

        with bypass_canonical_lock():
            seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)

        scoring_task = ScheduledTask.objects.filter(name="Nightly Performance Scoring").first()
        self.assertIsNotNone(scoring_task)
        self.assertEqual(scoring_task.cron_expression, "30 20 * * *")
        self.assertEqual(scoring_task.task_name, "metacognition.tasks.task_update_performance_scores")

        maintenance_task = ScheduledTask.objects.filter(name="NightManager Daily Maintenance").first()
        self.assertIsNotNone(maintenance_task)
        self.assertEqual(maintenance_task.cron_expression, "0 21 * * *")
        self.assertEqual(maintenance_task.task_name, "metacognition.tasks.task_run_blueprint_async")
        self.assertIsNotNone(maintenance_task.kwargs_json.get("blueprint_id"))


class TaskTelemetryAndMetricsTests(TestCase):
    """
    Unit and integration tests for TaskTelemetryContext and LLM token tracking (WS11 Phase 4).
    """

    def setUp(self):
        TaskRecord.objects.all().delete()

    def test_telemetry_context_standalone(self):
        """Verifies TaskTelemetryContext records durations, tokens, and extra metrics."""
        from verbal_tasks.telemetry import TaskTelemetryContext
        import time

        with TaskTelemetryContext() as telemetry:
            time.sleep(0.01)
            telemetry.record_generation(input_tokens=100, output_tokens=50, duration_ms=120.0, tokens_per_second=25.0)
            telemetry.record_metric("custom_key", "custom_val")

        self.assertGreater(telemetry.duration_ms, 5.0)
        self.assertEqual(telemetry.input_tokens, 100)
        self.assertEqual(telemetry.output_tokens, 50)
        self.assertEqual(telemetry.total_tokens, 150)
        self.assertEqual(telemetry.llm_calls_count, 1)

        d = telemetry.to_dict()
        self.assertEqual(d["total_tokens"], 150)
        self.assertEqual(d["extra"]["custom_key"], "custom_val")
        self.assertEqual(d["extra"]["last_tps"], 25.0)

    def test_worker_execution_records_telemetry_and_tokens(self):
        """Verifies worker execution populates duration_ms, input_tokens, output_tokens, total_tokens on TaskRecord."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            res = sample_task_with_llm.enqueue("What is the capital of France?")
            call_command("runtaskworker", burst=True, queues=["default"])

            record = TaskRecord.objects.get(id=res.id)
            self.assertEqual(record.status, TaskRecordStatus.SUCCESSFUL)
            self.assertEqual(record.input_tokens, 150)
            self.assertEqual(record.output_tokens, 75)
            self.assertEqual(record.total_tokens, 225)
            self.assertIsNotNone(record.duration_ms)
            self.assertGreaterEqual(record.duration_ms, 0)
            self.assertIn("150", record.tokens_formatted)
            self.assertIn("75", record.tokens_formatted)
            self.assertEqual(record.metrics_json.get("llm_calls_count"), 1)


class TaskAdminAndDashboardTests(TestCase):
    """
    Tests for TaskRecordAdmin, ScheduledTaskAdmin, Dashboard view, and statistics API.
    """

    def setUp(self):
        TaskRecord.objects.all().delete()
        ScheduledTask.objects.all().delete()
        from django.contrib.auth.models import User
        self.admin_user = User.objects.create_superuser(
            username="testadmin",
            email="admin@example.com",
            password="password123"
        )
        self.client.force_login(self.admin_user)

    def test_task_record_admin_methods(self):
        """Verifies admin formatting methods and badges."""
        from verbal_tasks.admin import TaskRecordAdmin
        from django.contrib.admin.sites import AdminSite

        site = AdminSite()
        admin_obj = TaskRecordAdmin(TaskRecord, site)

        rec = TaskRecord.objects.create(
            id="test123456",
            task_path="grips.tasks.generate_concept_narrative",
            queue_name="default",
            priority=5,
            status=TaskRecordStatus.SUCCESSFUL,
            duration_ms=1450.0,
            input_tokens=500,
            output_tokens=250,
            total_tokens=750,
            args_json=[42],
            kwargs_json={"debug": True},
            return_value_json={"success": True},
            error_traceback="",
        )

        badge_html = str(admin_obj.status_badge(rec))
        self.assertIn("SUCCESSFUL", badge_html)
        self.assertIn("#16a34a", badge_html)

        dur_html = str(admin_obj.duration_display(rec))
        self.assertIn("1.45s", dur_html)

        tokens_html = str(admin_obj.tokens_display(rec))
        self.assertIn("750", tokens_html)
        self.assertIn("500", tokens_html)
        self.assertIn("250", tokens_html)

        args_html = str(admin_obj.formatted_args(rec))
        self.assertIn("42", args_html)

        kwargs_html = str(admin_obj.formatted_kwargs(rec))
        self.assertIn("debug", kwargs_html)

    def test_task_record_admin_retry_action(self):
        """Verifies admin retry action re-enqueues selected task records."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            from verbal_tasks.admin import TaskRecordAdmin
            from django.contrib.admin.sites import AdminSite
            from django.test.client import RequestFactory

            site = AdminSite()
            admin_obj = TaskRecordAdmin(TaskRecord, site)

            rec = TaskRecord.objects.create(
                id="failed_task_1",
                task_path="verbal_tasks.tests.sample_task_add",
                args_json=[3, 4],
                status=TaskRecordStatus.FAILED,
            )

            req = RequestFactory().post("/admin/verbal_tasks/taskrecord/")
            req.user = self.admin_user

            from django.contrib.messages.storage.fallback import FallbackStorage
            setattr(req, 'session', {})
            setattr(req, '_messages', FallbackStorage(req))

            admin_obj.retry_selected_tasks(req, TaskRecord.objects.filter(id=rec.id))
            # A new READY record should have been enqueued
            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.READY).count(), 1)

    def test_scheduled_task_admin_trigger_action(self):
        """Verifies ScheduledTaskAdmin trigger action fires the schedule."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            from verbal_tasks.admin import ScheduledTaskAdmin
            from django.contrib.admin.sites import AdminSite
            from django.test.client import RequestFactory

            site = AdminSite()
            admin_obj = ScheduledTaskAdmin(ScheduledTask, site)

            sched = ScheduledTask.objects.create(
                name="Action Trigger Test",
                task_name="verbal_tasks.tests.sample_task_add",
                interval_seconds=120,
                args_json=[5, 9],
            )

            req = RequestFactory().post("/admin/verbal_tasks/scheduledtask/")
            req.user = self.admin_user
            from django.contrib.messages.storage.fallback import FallbackStorage
            setattr(req, 'session', {})
            setattr(req, '_messages', FallbackStorage(req))

            admin_obj.trigger_now_action(req, ScheduledTask.objects.filter(id=sched.id))
            sched.refresh_from_db()
            self.assertEqual(sched.total_run_count, 1)
            self.assertEqual(TaskRecord.objects.filter(status=TaskRecordStatus.READY).count(), 1)

    def test_dashboard_views_and_stats_api(self):
        """Verifies the dashboard template view and stats JSON polling API."""
        TaskRecord.objects.create(
            id="dash_rec_1",
            task_path="grips.tasks.generate_concept_narrative",
            status=TaskRecordStatus.SUCCESSFUL,
            duration_ms=2500.0,
            input_tokens=1000,
            output_tokens=400,
            total_tokens=1400,
        )
        TaskRecord.objects.create(
            id="dash_rec_2",
            task_path="grips.tasks.task_lint_concept_node",
            status=TaskRecordStatus.FAILED,
            error_traceback="ValueError: test error",
        )

        # 1. HTML Dashboard View
        resp = self.client.get("/admin/verbal_tasks/taskrecord/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Task Queue & Worker Monitoring")

        # 2. JSON Stats API
        resp_api = self.client.get("/admin/verbal_tasks/taskrecord/dashboard/api/stats/")
        self.assertEqual(resp_api.status_code, 200)
        data = resp_api.json()

        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["successful_count"], 1)
        self.assertEqual(data["summary"]["failed_count"], 1)
        self.assertEqual(data["summary"]["total_tokens_24h"], 1400)
        self.assertEqual(data["summary"]["total_in_24h"], 1000)
        self.assertEqual(data["summary"]["total_out_24h"], 400)

        self.assertIn("token_breakdown", data)
        self.assertTrue(len(data["token_breakdown"]) > 0)
        self.assertEqual(data["token_breakdown"][0]["tokens"], 1400)

        self.assertIn("recent_tasks", data)
        self.assertEqual(len(data["recent_tasks"]), 2)

    def test_dashboard_retry_ajax_endpoint(self):
        """Verifies AJAX task retry endpoint on the dashboard."""
        with override_settings(
            TASKS={
                "default": {
                    "BACKEND": "verbal_tasks.task_backend.DatabaseTaskBackend",
                    "QUEUES": ["default"],
                }
            }
        ):
            failed_rec = TaskRecord.objects.create(
                id="failed_rec_99",
                task_path="verbal_tasks.tests.sample_task_add",
                args_json=[20, 30],
                status=TaskRecordStatus.FAILED,
            )

            url = f"/admin/verbal_tasks/taskrecord/dashboard/api/retry/{failed_rec.id}/"
            resp = self.client.post(url)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))
            self.assertIsNotNone(data.get("new_task_id"))

            # Check new task in database
            new_record = TaskRecord.objects.get(id=data["new_task_id"])
            self.assertEqual(new_record.status, TaskRecordStatus.READY)
            self.assertEqual(new_record.args_json, [20, 30])


class PostgresEventsTests(TestCase):
    """
    Unit tests for zero-Redis PostgreSQL LISTEN/NOTIFY pub/sub bridge and runtime flags.
    """

    def test_sanitize_channel_name(self):
        from verbal_tasks.postgres_events import sanitize_channel_name
        self.assertEqual(sanitize_channel_name("verbal:events:123"), "verbal_events_123")
        self.assertEqual(sanitize_channel_name("valid_channel_name"), "valid_channel_name")
        self.assertEqual(sanitize_channel_name("123_invalid_start"), "ch_123_invalid_start")

    def test_runtime_flag_lifecycle(self):
        from verbal_tasks.postgres_events import (
            set_runtime_flag,
            is_runtime_flag_set,
            get_runtime_flag,
            clear_runtime_flag
        )
        test_key = "test_cancel_token_42"
        self.assertFalse(is_runtime_flag_set(test_key))
        self.assertIsNone(get_runtime_flag(test_key))

        set_runtime_flag(test_key, "active_flag", ttl=10)
        self.assertTrue(is_runtime_flag_set(test_key))
        self.assertEqual(get_runtime_flag(test_key), "active_flag")

        clear_runtime_flag(test_key)
        self.assertFalse(is_runtime_flag_set(test_key))

    def test_publish_and_subscribe_sync(self):
        from verbal_tasks.postgres_events import (
            publish_pg_event,
            subscribe_pg_events_sync,
            _in_memory_subscribers,
            _in_memory_lock
        )
        channel = "test_sync_pubsub_channel"
        # Test publish returns True
        res = publish_pg_event(channel, "test_event", {"message": "hello world"})
        self.assertTrue(res)

        # Test sync subscriber receives event from in-memory queue
        queue = []
        with _in_memory_lock:
            _in_memory_subscribers.setdefault(channel, []).append(queue)
        
        publish_pg_event(channel, "completed", {"result": 100})
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["event"], "completed")
        self.assertEqual(queue[0]["data"]["result"], 100)

        with _in_memory_lock:
            _in_memory_subscribers[channel].remove(queue)

    async def test_publish_and_subscribe_async(self):
        from verbal_tasks.postgres_events import (
            publish_pg_event,
            subscribe_pg_events_async
        )
        import asyncio

        channel = "test_async_channel"
        received = []

        async def listen():
            async for ev in subscribe_pg_events_async(channel):
                received.append(ev)
                if ev.get("event") == "completed":
                    break

        listen_task = asyncio.create_task(listen())
        await asyncio.sleep(0.05)

        publish_pg_event(channel, "step_started", {"step": 1})
        publish_pg_event(channel, "completed", {"status": "done"})

        await asyncio.wait_for(listen_task, timeout=2.0)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0]["event"], "step_started")
        self.assertEqual(received[1]["event"], "completed")

