import logging
import threading
from celery import shared_task
from django.core.cache import cache
import huggingface_hub.utils
import tqdm.auto
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

class CeleryTqdm(tqdm.auto.tqdm):
    """
    Custom tqdm class that reports progress to a Celery task state.
    Because huggingface_hub uses multiple threads/progress bars for downloading files,
    we aggregate the total bytes across all active bars.
    """
    
    # Global state for the current Celery task execution to aggregate across threads
    # Use a lock since snapshot_download runs multiple threads
    _lock = threading.Lock()
    _active_bars = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_id = cache.get('current_celery_task_id')
        
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
            # Push state to cache or celery (we will use cache for faster polling from HTMX)
            cache.set(f"model_download_progress_{self.task_id}", {
                "percentage": percentage,
                "downloaded": total_downloaded,
                "total": total_expected
            }, timeout=300)

@shared_task(bind=True)
def download_model_cache(self, hf_model_id):
    logger.info(f"Starting Celery background download for {hf_model_id}")
    task_id = self.request.id
    
    cache.set('current_celery_task_id', task_id, timeout=3600)
    cache.set(f"model_download_progress_{task_id}", {
        "percentage": 0,
        "downloaded": 0,
        "total": 0,
        "status": "Starting..."
    }, timeout=3600)
    
    try:
        snapshot_download(
            repo_id=hf_model_id,
            tqdm_class=CeleryTqdm
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
        cache.delete('current_celery_task_id')
        with CeleryTqdm._lock:
            if task_id in CeleryTqdm._active_bars:
                del CeleryTqdm._active_bars[task_id]
