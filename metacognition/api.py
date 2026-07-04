import logging
logger = logging.getLogger(__name__)

import os
import subprocess
import typing

from ninja import Router, Schema
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .tasks import run_blueprint
from llm_api.models import PromptResponseLog, Conversation

router = Router()


class BlueprintRunIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None
    parent_log_id: typing.Optional[str] = None

@router.post("/execute_blueprint/")
@ensure_csrf_cookie
def execute_blueprint(request, payload: BlueprintRunIn):
    """
    Executes a multi-step Cognitive Blueprint via the backend executor.
    """
    user_id = getattr(request.auth, 'id', None) if hasattr(request, 'auth') else None
    if not user_id and hasattr(request, 'user') and request.user.is_authenticated:
        user_id = request.user.id

    # Rewind the physical Git workspace if branching from an earlier state
    if payload.parent_log_id and payload.conversation_id:
        try:
            parent_log = PromptResponseLog.objects.get(id=payload.parent_log_id)
            if parent_log.git_commit_hash:
                conv = Conversation.objects.get(id=payload.conversation_id)
                workspace_dir = conv.get_workspace_dir()
                
                if os.path.exists(workspace_dir):
                    # Use -f (force) to ensure any uncommitted artifacts from the 
                    # abandoned timeline (like __pycache__ or sandbox outputs) are overwritten
                    subprocess.run(["git", "checkout", "-f", parent_log.git_commit_hash], cwd=workspace_dir, check=True, capture_output=True)
                    logger.info(f'Rewound workspace {workspace_dir} to commit {parent_log.git_commit_hash[:7]}')
        except (PromptResponseLog.DoesNotExist, Conversation.DoesNotExist):
            pass
        except subprocess.CalledProcessError as e:
            logger.info(f'Failed to rewind workspace: {e.stderr}')

    result = run_blueprint(
        blueprint_id=payload.blueprint_id,
        user_prompt=payload.user_prompt,
        conversation_id=payload.conversation_id,
        user_id=user_id,
        parent_log_id=payload.parent_log_id  # Note: tasks.py must be updated to accept and use this!
    )
    
    if "error" in result:
        status_code = result.get("status", 400)
        return JsonResponse({"error": result["error"]}, status=status_code)
        
    return JsonResponse(result)
