import time
import signal
import sys
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from verbal_tasks.models import ScheduledTask

logger = logging.getLogger("verbal_tasks.scheduler")


class Command(BaseCommand):
    help = "Run the periodic task scheduler (replaces Celery Beat)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=15,
            help="Polling interval in seconds between schedule checks (default: 15)."
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help="Run a single schedule evaluation check and exit immediately."
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.should_stop = False

    def handle(self, *args, **options):
        interval = options['interval']
        run_once = options['once']

        # Setup graceful signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self.stdout.write(self.style.SUCCESS(
            f"⏰ Starting Django Tasks Scheduler (interval: {interval}s, once: {run_once})..."
        ))

        while not self.should_stop:
            triggered_count = self._evaluate_and_trigger_schedules()
            if run_once:
                self.stdout.write(self.style.SUCCESS(
                    f"Scheduler tick complete: {triggered_count} task(s) triggered."
                ))
                break

            # Sleep in 1-second slices so signals can interrupt promptly
            for _ in range(interval):
                if self.should_stop:
                    break
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS("Scheduler stopped cleanly."))

    def _handle_signal(self, signum, frame):
        self.stdout.write("\nReceived shutdown signal. Stopping scheduler...")
        self.should_stop = True

    def _evaluate_and_trigger_schedules(self) -> int:
        """Evaluates active ScheduledTasks and triggers any that are due."""
        triggered = 0
        now = timezone.now()

        # Find active tasks
        active_ids = list(ScheduledTask.objects.filter(is_active=True).values_list('id', flat=True))

        for task_id in active_ids:
            if self.should_stop:
                break

            with transaction.atomic():
                # Lock row to prevent concurrent scheduler workers from double-triggering
                task = ScheduledTask.objects.select_for_update(skip_locked=True).filter(
                    id=task_id,
                    is_active=True
                ).first()

                if not task:
                    continue

                if task.is_due(now):
                    try:
                        record = task.trigger()
                        triggered += 1
                        self.stdout.write(self.style.SUCCESS(
                            f"🚀 Triggered scheduled task '{task.name}' (Record: {record.id if record else 'N/A'})"
                        ))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(
                            f"❌ Error triggering scheduled task '{task.name}': {e}"
                        ))
                        logger.exception(f"Error triggering scheduled task '{task.name}'")

        return triggered
