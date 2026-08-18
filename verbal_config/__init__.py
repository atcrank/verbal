import warnings
warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph.*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph.*")

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

__all__ = ('celery_app',)