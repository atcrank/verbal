import time
import signal
import traceback
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.module_loading import import_string
from django.db import transaction, models
from django.tasks.base import TaskContext, TaskResult, TaskResultStatus
from django.tasks.signals import task_started, task_finished
from verbal_tasks.models import TaskRecord, TaskRecordStatus
from verbal_tasks.task_backend import _normalize_json
from verbal_tasks.telemetry import TaskTelemetryContext

logger = logging.getLogger("verbal_tasks.worker")


class Command(BaseCommand):
    help = "Run the background task worker for PostgreSQL-backed Django Tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            '--queues',
            nargs='+',
            default=['default'],
            help="Queue name(s) to process (default: 'default')"
        )
        parser.add_argument(
            '--burst',
            action='store_true',
            help="Run in burst mode: process all ready tasks and exit when queue is empty."
        )
        parser.add_argument(
            '--interval',
            type=float,
            default=1.0,
            help="Sleep interval in seconds when no tasks are ready (default: 1.0)"
        )
        parser.add_argument(
            '--max-tasks',
            type=int,
            default=None,
            help="Maximum number of tasks to execute before stopping the worker."
        )

    def handle(self, *args, **options):
        queues = options['queues']
        burst = options['burst']
        interval = options['interval']
        max_tasks = options['max_tasks']

        worker_id = f"worker-{get_random_string(8)}"
        self.stdout.write(self.style.SUCCESS(f"🚀 Starting Django Tasks worker [{worker_id}] on queues: {', '.join(queues)}"))

        self.running = True

        def _handle_signal(signum, frame):
            self.stdout.write(self.style.WARNING(f"\nReceived signal {signum}. Shutting down worker gracefully..."))
            self.running = False

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        processed_count = 0

        while self.running:
            record_id = self._claim_next_task(queues, worker_id)

            if record_id:
                self._execute_claimed_task(record_id, worker_id)
                processed_count += 1
                if max_tasks and processed_count >= max_tasks:
                    self.stdout.write(self.style.SUCCESS(f"Reached max task limit ({max_tasks}). Exiting."))
                    break
            else:
                if burst:
                    self.stdout.write(self.style.SUCCESS(f"Burst complete: processed {processed_count} task(s)."))
                    break
                time.sleep(interval)

        self.stdout.write(self.style.SUCCESS(f"Worker [{worker_id}] stopped cleanly."))

    def _claim_next_task(self, queues, worker_id) -> str | None:
        """Atomically locks and claims the next ready task record."""
        now = timezone.now()
        with transaction.atomic():
            record = TaskRecord.objects.select_for_update(skip_locked=True).filter(
                status=TaskRecordStatus.READY,
                queue_name__in=queues,
            ).filter(
                models.Q(run_after__isnull=True) | models.Q(run_after__lte=now)
            ).order_by('-priority', 'enqueued_at').first()

            if record:
                record.status = TaskRecordStatus.RUNNING
                record.started_at = now
                record.last_attempted_at = now
                record.worker_id = worker_id
                record.attempts += 1
                record.save(update_fields=['status', 'started_at', 'last_attempted_at', 'worker_id', 'attempts'])
                return record.id
        return None

    def _execute_claimed_task(self, record_id: str, worker_id: str):
        """Executes the task outside the row-lock transaction so long jobs don't stall DB locks."""
        try:
            record = TaskRecord.objects.get(id=record_id)
        except TaskRecord.DoesNotExist:
            return

        self.stdout.write(f"⏳ [{worker_id}] Executing task: {record.task_path} (ID: {record.id})")

        with TaskTelemetryContext() as telemetry:
            try:
                task_target = import_string(record.task_path)
                task_func = getattr(task_target, 'func', task_target)
                takes_context = getattr(task_target, 'takes_context', False)

                if takes_context:
                    task_result = TaskResult(
                        task=task_target,
                        id=record.id,
                        status=TaskResultStatus.RUNNING,
                        enqueued_at=record.enqueued_at,
                        started_at=record.started_at,
                        last_attempted_at=record.last_attempted_at,
                        finished_at=None,
                        args=record.args_json,
                        kwargs=record.kwargs_json,
                        backend='default',
                        errors=[],
                        worker_ids=[worker_id],
                    )
                    context = TaskContext(task_result=task_result)
                    return_value = task_func(context, *record.args_json, **record.kwargs_json)
                else:
                    return_value = task_func(*record.args_json, **record.kwargs_json)

                # If the return_value is a dict and has custom metrics/tokens, blend them
                if isinstance(return_value, dict):
                    if "input_tokens" in return_value or "output_tokens" in return_value:
                        telemetry.record_generation(
                            input_tokens=return_value.get("input_tokens", 0),
                            output_tokens=return_value.get("output_tokens", 0),
                        )

                record.status = TaskRecordStatus.SUCCESSFUL
                record.finished_at = timezone.now()
                record.duration_ms = telemetry.duration_ms
                record.input_tokens = telemetry.input_tokens
                record.output_tokens = telemetry.output_tokens
                record.total_tokens = telemetry.total_tokens
                record.metrics_json = telemetry.to_dict()
                record.return_value_json = _normalize_json(return_value)
                record.save(update_fields=[
                    'status', 'finished_at', 'duration_ms',
                    'input_tokens', 'output_tokens', 'total_tokens',
                    'metrics_json', 'return_value_json'
                ])

                tokens_str = f" | {telemetry.total_tokens:,} tokens" if telemetry.total_tokens else ""
                self.stdout.write(self.style.SUCCESS(
                    f"✅ [{worker_id}] Task {record.short_task_name} completed in {record.duration_formatted}{tokens_str}"
                ))

            except Exception as e:
                tb_str = traceback.format_exc()
                logger.error(f"Task {record.task_path} (ID: {record.id}) failed: {e}\n{tb_str}")

                record.status = TaskRecordStatus.FAILED
                record.finished_at = timezone.now()
                record.duration_ms = telemetry.duration_ms
                record.input_tokens = telemetry.input_tokens
                record.output_tokens = telemetry.output_tokens
                record.total_tokens = telemetry.total_tokens
                record.metrics_json = telemetry.to_dict()
                record.error_traceback = tb_str
                record.save(update_fields=[
                    'status', 'finished_at', 'duration_ms',
                    'input_tokens', 'output_tokens', 'total_tokens',
                    'metrics_json', 'error_traceback'
                ])

                self.stdout.write(self.style.ERROR(
                    f"❌ [{worker_id}] Task {record.short_task_name} failed in {record.duration_formatted}: {e}"
                ))
