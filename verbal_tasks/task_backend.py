import logging
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.module_loading import import_string
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import TaskResult, TaskResultStatus, TaskError
from django.tasks.exceptions import TaskResultDoesNotExist
from django.tasks.signals import task_enqueued
from django.core.serializers.json import DjangoJSONEncoder
import json

logger = logging.getLogger(__name__)


def _normalize_json(value):
    """Serialize and deserialize to ensure JSON-compatible primitives."""
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


class DatabaseTaskBackend(BaseTaskBackend):
    """
    PostgreSQL-backed task backend for Django Tasks.
    Stores enqueued tasks and results in the verbal.TaskRecord table.
    """
    supports_async_task = False
    supports_defer = True
    supports_get_result = True
    supports_priority = True

    def enqueue(self, task, args, kwargs):
        self.validate_task(task)

        from verbal_tasks.models import TaskRecord, TaskRecordStatus

        result_id = get_random_string(32)
        task_path = f"{task.func.__module__}.{task.func.__qualname__}"
        now = timezone.now()

        record = TaskRecord.objects.create(
            id=result_id,
            task_path=task_path,
            queue_name=task.queue_name,
            priority=task.priority,
            args_json=_normalize_json(args),
            kwargs_json=_normalize_json(kwargs),
            status=TaskRecordStatus.READY,
            enqueued_at=now,
            run_after=task.run_after,
        )

        task_result = TaskResult(
            task=task,
            id=result_id,
            status=TaskResultStatus.READY,
            enqueued_at=now,
            started_at=None,
            last_attempted_at=None,
            finished_at=None,
            args=args,
            kwargs=kwargs,
            backend=self.alias,
            errors=[],
            worker_ids=[],
        )

        task_enqueued.send(sender=type(self), task_result=task_result)
        return task_result

    def get_result(self, result_id: str) -> TaskResult:
        from verbal_tasks.models import TaskRecord

        try:
            record = TaskRecord.objects.get(id=result_id)
        except TaskRecord.DoesNotExist:
            raise TaskResultDoesNotExist(f"Task result with ID '{result_id}' does not exist.")

        try:
            task_func = import_string(record.task_path)
            task_obj = getattr(task_func, 'task', task_func)
        except Exception as e:
            logger.warning(f"Could not import task function '{record.task_path}': {e}")
            task_obj = None

        errors = []
        if record.error_traceback:
            errors.append(TaskError(
                exception_class_path="TaskExecutionError",
                traceback=record.error_traceback
            ))

        status_mapping = {
            "READY": TaskResultStatus.READY,
            "RUNNING": TaskResultStatus.RUNNING,
            "SUCCESSFUL": TaskResultStatus.SUCCESSFUL,
            "FAILED": TaskResultStatus.FAILED,
        }
        mapped_status = status_mapping.get(record.status, TaskResultStatus.READY)

        task_result = TaskResult(
            task=task_obj,
            id=record.id,
            status=mapped_status,
            enqueued_at=record.enqueued_at,
            started_at=record.started_at,
            last_attempted_at=record.last_attempted_at,
            finished_at=record.finished_at,
            args=record.args_json,
            kwargs=record.kwargs_json,
            backend=self.alias,
            errors=errors,
            worker_ids=[record.worker_id] if record.worker_id else [],
        )

        if record.return_value_json is not None:
            object.__setattr__(task_result, "_return_value", record.return_value_json)

        return task_result
