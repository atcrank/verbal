from django.db import models
from django.utils import timezone
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def match_cron_field(field_pattern: str, val: int, min_val: int, max_val: int, is_dow: bool = False) -> bool:
    """Matches an integer value against a standard cron field expression."""
    for part in field_pattern.split(','):
        part = part.strip()
        if not part:
            continue
        if part == '*':
            return True
        if '/' in part:
            range_part, step_str = part.split('/', 1)
            step = int(step_str)
            if range_part == '*' or range_part == '':
                start, end = min_val, max_val
            elif '-' in range_part:
                s_str, e_str = range_part.split('-', 1)
                start, end = int(s_str), int(e_str)
            else:
                start, end = int(range_part), max_val
            if start <= val <= end and (val - start) % step == 0:
                return True
        elif '-' in part:
            s_str, e_str = part.split('-', 1)
            if int(s_str) <= val <= int(e_str):
                return True
        else:
            target = int(part)
            if is_dow:
                if (target in (0, 7) and val in (0, 7)) or target == val:
                    return True
            elif target == val:
                return True
    return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """
    Evaluates whether a datetime matches a standard 5-field cron expression
    (minute hour day_of_month month day_of_week).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression '{cron_expr}'. Must have exactly 5 fields.")

    min_pat, hour_pat, dom_pat, month_pat, dow_pat = parts

    # Standard cron day-of-week: 0=Sun, 1=Mon, ..., 6=Sat, 7=Sun
    # Python dt.weekday(): 0=Mon, ..., 6=Sun -> (dt.weekday() + 1) % 7
    cron_dow = (dt.weekday() + 1) % 7

    if not match_cron_field(min_pat, dt.minute, 0, 59):
        return False
    if not match_cron_field(hour_pat, dt.hour, 0, 23):
        return False
    if not match_cron_field(dom_pat, dt.day, 1, 31):
        return False
    if not match_cron_field(month_pat, dt.month, 1, 12):
        return False
    if not match_cron_field(dow_pat, cron_dow, 0, 7, is_dow=True):
        return False

    return True


class TaskRecordStatus(models.TextChoices):
    READY = 'READY', 'Ready'
    RUNNING = 'RUNNING', 'Running'
    SUCCESSFUL = 'SUCCESSFUL', 'Successful'
    FAILED = 'FAILED', 'Failed'


class TaskRecord(models.Model):
    """
    Database record representing an enqueued, executing, or completed background task.
    Serves as the PostgreSQL-backed storage for django.tasks.
    """
    id = models.CharField(max_length=64, primary_key=True)
    task_path = models.CharField(max_length=255, help_text="Dotted python import path to the task function")
    queue_name = models.CharField(max_length=64, default="default", db_index=True)
    priority = models.IntegerField(default=0, db_index=True)
    args_json = models.JSONField(default=list, blank=True)
    kwargs_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=TaskRecordStatus.choices,
        default=TaskRecordStatus.READY,
        db_index=True
    )
    enqueued_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField(null=True, blank=True, db_index=True)
    return_value_json = models.JSONField(null=True, blank=True)
    error_traceback = models.TextField(null=True, blank=True)
    worker_id = models.CharField(max_length=128, null=True, blank=True)
    duration_ms = models.FloatField(null=True, blank=True, help_text="Execution duration in milliseconds")
    input_tokens = models.PositiveIntegerField(default=0, help_text="Prompt/input tokens consumed by LLM calls")
    output_tokens = models.PositiveIntegerField(default=0, help_text="Generated/output tokens from LLM calls")
    total_tokens = models.PositiveIntegerField(default=0, db_index=True, help_text="Total LLM tokens consumed")
    metrics_json = models.JSONField(default=dict, blank=True, help_text="Structured execution and telemetry metrics")

    class Meta:
        verbose_name = "Task Record"
        verbose_name_plural = "Task Records"
        ordering = ['-priority', 'enqueued_at']
        indexes = [
            models.Index(fields=['status', 'queue_name', 'priority', 'enqueued_at']),
            models.Index(fields=['status', 'run_after']),
            models.Index(fields=['status', 'enqueued_at']),
        ]

    def __str__(self):
        return f"TaskRecord({self.id}, task={self.short_task_name}, status={self.status})"

    @property
    def short_task_name(self) -> str:
        """Returns a shortened representation of the task path (e.g. 'grips.tasks.generate_concept_narrative')."""
        if not self.task_path:
            return "Unknown"
        parts = self.task_path.split('.')
        if len(parts) >= 3:
            return f"{parts[-3]}.{parts[-2]}.{parts[-1]}"
        return self.task_path

    @property
    def duration_formatted(self) -> str:
        """Returns human-readable duration."""
        if self.duration_ms is not None:
            ms = self.duration_ms
            if ms < 1000:
                return f"{int(ms)}ms"
            elif ms < 60000:
                return f"{ms / 1000:.2f}s"
            else:
                mins = int(ms // 60000)
                secs = int((ms % 60000) // 1000)
                return f"{mins}m {secs}s"
        elif self.started_at and self.finished_at:
            delta = (self.finished_at - self.started_at).total_seconds()
            if delta < 1:
                return f"{int(delta * 1000)}ms"
            elif delta < 60:
                return f"{delta:.2f}s"
            else:
                mins = int(delta // 60)
                secs = int(delta % 60)
                return f"{mins}m {secs}s"
        return "-"

    @property
    def queue_latency_ms(self) -> float | None:
        """Calculates latency spent waiting in queue before execution started."""
        if self.enqueued_at and self.started_at:
            return max(0.0, (self.started_at - self.enqueued_at).total_seconds() * 1000.0)
        return None

    @property
    def queue_latency_formatted(self) -> str:
        """Human-readable queue latency."""
        latency = self.queue_latency_ms
        if latency is None:
            return "-"
        if latency < 1000:
            return f"{int(latency)}ms"
        elif latency < 60000:
            return f"{latency / 1000:.2f}s"
        return f"{int(latency // 60000)}m {int((latency % 60000) // 1000)}s"

    @property
    def tokens_formatted(self) -> str:
        """Returns formatted token count breakdown."""
        if not self.total_tokens:
            return "0"
        return f"{self.total_tokens:,} (In: {self.input_tokens:,}, Out: {self.output_tokens:,})"


class ScheduledTask(models.Model):
    """
    Periodic task schedule stored in PostgreSQL.
    Replaces Celery Beat and django_celery_beat.
    """
    name = models.CharField(max_length=255, unique=True, help_text="Unique label for this scheduled task")
    task_name = models.CharField(
        max_length=255,
        help_text="Dotted import path to the @task function (e.g. 'metacognition.tasks.task_run_blueprint_async')"
    )
    cron_expression = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Standard 5-field cron expression (e.g. '0 21 * * *' for 9 PM daily)"
    )
    interval_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text="Interval in seconds between task executions (alternative to cron)"
    )
    args_json = models.JSONField(default=list, blank=True, help_text="JSON list of positional arguments")
    kwargs_json = models.JSONField(default=dict, blank=True, help_text="JSON dict of keyword arguments")
    queue_name = models.CharField(max_length=64, default="default")
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    total_run_count = models.PositiveIntegerField(default=0)
    last_task_record = models.ForeignKey(
        TaskRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scheduled_triggers"
    )

    class Meta:
        verbose_name = "Scheduled Task"
        verbose_name_plural = "Scheduled Tasks"
        ordering = ['name']

    def __str__(self):
        schedule_info = self.cron_expression or f"every {self.interval_seconds}s"
        return f"{self.name} ({self.task_name} @ {schedule_info})"

    def is_due(self, current_time: datetime | None = None) -> bool:
        """Determines whether the task is due for execution at current_time."""
        if not self.is_active:
            return False

        dt = current_time or timezone.now()

        if self.cron_expression:
            try:
                if not cron_matches(self.cron_expression, dt):
                    return False
            except ValueError as e:
                logger.error(f"Invalid cron expression on ScheduledTask '{self.name}': {e}")
                return False

            # Prevent executing multiple times within the same minute
            if self.last_run_at:
                # Compare in local/same timezone
                last = self.last_run_at
                if (last.year == dt.year and last.month == dt.month and
                        last.day == dt.day and last.hour == dt.hour and
                        last.minute == dt.minute):
                    return False
            return True

        if self.interval_seconds:
            if not self.last_run_at:
                return True
            elapsed = (dt - self.last_run_at).total_seconds()
            return elapsed >= self.interval_seconds

        return False

    def trigger(self) -> TaskRecord | None:
        """Enqueues the task via django.tasks and records execution metadata."""
        from django.utils.module_loading import import_string
        task_func = import_string(self.task_name)

        args = self.args_json or []
        kwargs = self.kwargs_json or {}

        task_result = task_func.enqueue(*args, **kwargs)
        record = TaskRecord.objects.filter(id=task_result.id).first()

        self.last_run_at = timezone.now()
        self.total_run_count += 1
        if record:
            self.last_task_record = record
        self.save(update_fields=['last_run_at', 'total_run_count', 'last_task_record'])

        logger.info(f"Triggered ScheduledTask '{self.name}' (Task ID: {task_result.id})")
        return record
