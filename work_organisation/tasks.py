from django.tasks import task
import logging
from .clustering import cluster_whiteboard_cards
from .events import publish_whiteboard_event

logger = logging.getLogger(__name__)


@task
def task_cluster_whiteboard_cards(session_id: int | str, user_id: int | None = None) -> dict:
    """
    Background Django task to cluster whiteboard brainstorming cards using structured LLM synthesis.
    Publishes completion event over PostgreSQL SSE if successful.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.filter(id=user_id).first() if user_id else None

    logger.info(f"🎨 Running background whiteboard clustering for Session {session_id} (user={user})")
    res = cluster_whiteboard_cards(session_id=session_id, user=user)

    # Publish Datastar notification event
    try:
        publish_whiteboard_event(session_id, "clustering_completed", res)
    except Exception as e:
        logger.debug(f"Could not publish whiteboard SSE completion event: {e}")

    return res
