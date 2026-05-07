import typing

from ninja import Router, Schema
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .tasks import run_blueprint

router = Router()


class BlueprintRunIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None

@router.post("/execute_blueprint/")
@ensure_csrf_cookie
def execute_blueprint(request, payload: BlueprintRunIn):
    """
    Executes a multi-step Cognitive Blueprint via the backend executor.
    """
    user_id = getattr(request.auth, 'id', None) if hasattr(request, 'auth') else None
    if not user_id and hasattr(request, 'user') and request.user.is_authenticated:
        user_id = request.user.id

    result = run_blueprint(
        blueprint_id=payload.blueprint_id,
        user_prompt=payload.user_prompt,
        conversation_id=payload.conversation_id,
        user_id=user_id
    )
    
    if "error" in result:
        status_code = result.get("status", 400)
        return JsonResponse({"error": result["error"]}, status=status_code)
        
    return JsonResponse(result)
