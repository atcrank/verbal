import logging
import threading
from django.tasks import task, TaskContext
from django.core.cache import cache
import huggingface_hub.utils
import tqdm.auto
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)


class TaskProgressTqdm(tqdm.auto.tqdm):
    """
    Custom tqdm class that reports progress to a task state.
    Because huggingface_hub uses multiple threads/progress bars for downloading files,
    we aggregate the total bytes across all active bars.
    """

    # Global state for the current task execution to aggregate across threads
    _lock = threading.Lock()
    _active_bars = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_id = cache.get('current_download_task_id')

        with self._lock:
            if self.task_id not in self.__class__._active_bars:
                self.__class__._active_bars[self.task_id] = []
            self.__class__._active_bars[self.task_id].append(self)

    def update(self, n=1):
        super().update(n)
        self._report_progress()

    def close(self):
        super().close()
        # Clean up this bar
        with self._lock:
            if self.task_id in self.__class__._active_bars:
                if self in self.__class__._active_bars[self.task_id]:
                    self.__class__._active_bars[self.task_id].remove(self)
        self._report_progress()

    def _report_progress(self):
        if not self.task_id:
            return

        with self._lock:
            bars = self.__class__._active_bars.get(self.task_id, [])
            total_expected = sum(b.total for b in bars if hasattr(b, 'total') and b.total)
            total_downloaded = sum(b.n for b in bars if hasattr(b, 'n') and b.n)

        if total_expected > 0:
            percentage = min(100, int((total_downloaded / total_expected) * 100))
            cache.set(f"model_download_progress_{self.task_id}", {
                "percentage": percentage,
                "downloaded": total_downloaded,
                "total": total_expected
            }, timeout=300)


@task(takes_context=True)
def download_model_cache(context: TaskContext, hf_model_id: str):
    task_id = context.task_result.id
    logger.info(f"Starting background download for {hf_model_id} (Task ID: {task_id})")

    cache.set('current_download_task_id', task_id, timeout=3600)
    cache.set(f"model_download_progress_{task_id}", {
        "percentage": 0,
        "downloaded": 0,
        "total": 0,
        "status": "Starting..."
    }, timeout=3600)

    try:
        snapshot_download(
            repo_id=hf_model_id,
            tqdm_class=TaskProgressTqdm
        )
        cache.set(f"model_download_progress_{task_id}", {
            "percentage": 100,
            "status": "Complete"
        }, timeout=3600)
        return {"status": "Complete", "model_id": hf_model_id}
    except Exception as e:
        logger.error(f"Failed to download model {hf_model_id}: {e}")
        cache.set(f"model_download_progress_{task_id}", {
            "percentage": 0,
            "status": f"Failed: {str(e)}"
        }, timeout=3600)
        raise e
    finally:
        # Cleanup
        cache.delete('current_download_task_id')
        with TaskProgressTqdm._lock:
            if task_id in TaskProgressTqdm._active_bars:
                del TaskProgressTqdm._active_bars[task_id]


@task
def task_generate_response(
    messages: list,
    max_new_tokens: int,
    user_id: int | None,
    run_id: str,
    log_id: str,
    parent_log_id: str | None = None,
    conversation_id: str | None = None,
    rag_selections: list | None = None
):
    """
    Background worker task to generate an LLM response asynchronously,
    updating PromptResponseLog and publishing completion events over PostgreSQL.
    """
    from django.contrib.auth import get_user_model
    from llm_api.apps import service_registry
    from llm_api.models import PromptResponseLog
    from verbal_tasks.postgres_events import publish_pg_event

    user = None
    if user_id:
        user = get_user_model().objects.filter(id=user_id).first()

    log = PromptResponseLog.objects.filter(id=log_id).first()

    try:
        [raw_response] = service_registry.ai_service.generate_response2(
            messages=messages,
            max_new_tokens=max_new_tokens,
            log_kwargs={"skip_log": True},
            user=user
        )
        cleaned_response = service_registry.ai_service.clean_response(raw_response)
        output_tokens = service_registry.ai_service.count_conversation_tokens(
            [{"role": "assistant", "content": cleaned_response}]
        )

        if log:
            log.generated_response = cleaned_response
            log.output_tokens = output_tokens
            log.save(update_fields=["generated_response", "output_tokens"])

        publish_pg_event(f"verbal_events_{run_id}", "completed", {
            "final_response": cleaned_response,
            "log_id": str(log_id),
            "run_id": run_id
        })

        return {
            "status": "completed",
            "log_id": str(log_id),
            "run_id": run_id,
            "output_tokens": output_tokens
        }

    except Exception as e:
        logger.error(f"Error in task_generate_response for run {run_id}: {e}")
        err_msg = f"Generation failed: {str(e)}"
        if log:
            log.generated_response = err_msg
            log.save(update_fields=["generated_response"])

        publish_pg_event(f"verbal_events_{run_id}", "error", {
            "error": err_msg,
            "log_id": str(log_id),
            "run_id": run_id
        })
        raise

