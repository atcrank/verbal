import logging
logger = logging.getLogger(__name__)

import os
import subprocess
import typing

from ninja import Router, Schema
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from uuid import uuid4
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .tasks import run_blueprint, task_run_blueprint_async, task_resume_blueprint_async
from .events import set_cancellation_flag, subscribe_blueprint_events
from .datastar import DatastarSSE
from llm_api.models import PromptResponseLog, Conversation

router = Router()


class BlueprintRunIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None
    parent_log_id: typing.Optional[str] = None
    run_id: typing.Optional[str] = None

class BlueprintDispatchIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None

class CancelBlueprintIn(Schema):
    run_id: str

class ApproveToolIn(Schema):
    run_id: str
    thread_id: str
    tool_name: str
    blueprint_id: int
    user_prompt: typing.Optional[str] = None


@router.post("/execute_blueprint/")
@ensure_csrf_cookie
def execute_blueprint(request, payload: BlueprintRunIn):
    """
    Synchronously executes a multi-step Cognitive Blueprint via the backend executor.
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
        parent_log_id=payload.parent_log_id,
        run_id=payload.run_id
    )
    
    if "error" in result:
        status_code = result.get("status", 400)
        return JsonResponse({"error": result["error"]}, status=status_code)
        
    return JsonResponse(result)


@router.post("/dispatch_blueprint/")
@ensure_csrf_cookie
def dispatch_blueprint(request, payload: BlueprintDispatchIn):
    """
    Asynchronously dispatches a Cognitive Blueprint execution to Celery.
    Returns the run_id and stream URL for Datastar SSE consumption.
    """
    user_id = getattr(request.auth, 'id', None) if hasattr(request, 'auth') else None
    if not user_id and hasattr(request, 'user') and request.user.is_authenticated:
        user_id = request.user.id

    run_id = str(uuid4())
    
    task_run_blueprint_async.delay(
        blueprint_id=payload.blueprint_id,
        user_prompt=payload.user_prompt,
        conversation_id=payload.conversation_id,
        user_id=user_id,
        run_id=run_id
    )

    return JsonResponse({
        "status": "dispatched",
        "run_id": run_id,
        "conversation_id": payload.conversation_id,
        "stream_url": f"/api/meta/stream_blueprint/?run_id={run_id}"
    })


@router.post("/cancel_blueprint/")
@ensure_csrf_cookie
def cancel_blueprint(request, payload: CancelBlueprintIn):
    """
    Cancels an active blueprint run by setting the cancellation flag in Redis.
    """
    set_cancellation_flag(payload.run_id)
    return JsonResponse({
        "status": "cancellation_requested",
        "run_id": payload.run_id
    })


@router.post("/approve_tool/")
@ensure_csrf_cookie
def approve_tool(request, payload: ApproveToolIn):
    """
    Authorizes a pending tool execution and resumes the suspended LangGraph checkpoint.
    """
    task_resume_blueprint_async.delay(
        blueprint_id=payload.blueprint_id,
        thread_id=payload.thread_id,
        run_id=payload.run_id,
        approved_tool=payload.tool_name,
        user_prompt=payload.user_prompt
    )
    return JsonResponse({
        "status": "resumed",
        "run_id": payload.run_id,
        "approved_tool": payload.tool_name
    })


@router.get("/stream_blueprint/")
def stream_blueprint(request, run_id: str):
    """
    Server-Sent Events (SSE) streaming endpoint using the Datastar protocol.
    Streams DOM fragment patches and reactive signal updates in real-time.
    """
    async def event_generator():
        # Initial connection signal
        yield DatastarSSE.merge_signals({
            "isStreaming": True,
            "runId": run_id,
            "status": "running"
        })

        async for event_data in subscribe_blueprint_events(run_id):
            event_type = event_data.get("event")
            payload = event_data.get("data", {})

            if event_type == "step_started":
                step_name = payload.get("step_name", "")
                step_count = payload.get("step_count", 0)
                frag = f"""<div id="blueprint-status" class="agent-step active">
                    <span class="badge">Step {step_count}</span>
                    <strong>Executing: {step_name}</strong>
                </div>"""
                yield DatastarSSE.merge_fragments(frag, selector="#blueprint-status", merge_mode="morph")
                yield DatastarSSE.merge_signals({"currentStep": step_name, "stepCount": step_count})

            elif event_type == "approval_required":
                tool_name = payload.get("tool_name", "")
                tool_args = payload.get("tool_args", {})
                tool_desc = payload.get("tool_description", "")
                thread_id = payload.get("thread_id", "")
                step_id = payload.get("step_id", 0)
                card_html = f"""<div id="tool-approval-card" class="approval-card pending">
                    <h4>⚠️ Approval Required for Tool: <code>{tool_name}</code></h4>
                    <p>{tool_desc}</p>
                    <pre><code>{json.dumps(tool_args, indent=2)}</code></pre>
                    <div class="actions">
                        <button data-on-click="@post('/api/meta/approve_tool/', {{run_id: '{run_id}', thread_id: '{thread_id}', tool_name: '{tool_name}', blueprint_id: {step_id}}})" class="btn-approve">Approve & Continue</button>
                        <button data-on-click="@post('/api/meta/cancel_blueprint/', {{run_id: '{run_id}'}})" class="btn-cancel">Cancel Run</button>
                    </div>
                </div>"""
                yield DatastarSSE.merge_fragments(card_html, selector="#tool-approval-container", merge_mode="morph")
                yield DatastarSSE.merge_signals({"requiresApproval": True, "pendingTool": tool_name})

            elif event_type == "step_completed":
                step_name = payload.get("step_name", "")
                output = payload.get("output", "")
                frag = f"""<div class="monologue-item">
                    <h5>✅ {step_name}</h5>
                    <p>{output}</p>
                </div>"""
                yield DatastarSSE.merge_fragments(frag, selector="#monologue-stream", merge_mode="append")

            elif event_type == "completed":
                final_resp = payload.get("final_response", "")
                frag = f"""<div id="blueprint-final-response" class="response-content">
                    <div class="markdown-body">{final_resp}</div>
                </div>"""
                yield DatastarSSE.merge_fragments(frag, selector="#blueprint-final-response", merge_mode="morph")
                yield DatastarSSE.merge_signals({"isStreaming": False, "status": "completed"})
                break

            elif event_type == "cancelled":
                frag = """<div id="blueprint-status" class="agent-step cancelled">
                    <span class="badge badge-cancelled">Cancelled</span>
                    <strong>Execution halted by user.</strong>
                </div>"""
                yield DatastarSSE.merge_fragments(frag, selector="#blueprint-status", merge_mode="morph")
                yield DatastarSSE.merge_signals({"isStreaming": False, "status": "cancelled"})
                break

            elif event_type == "error":
                err_msg = payload.get("error", "Unknown error")
                frag = f"""<div id="blueprint-status" class="agent-step error">
                    <span class="badge badge-error">Error</span>
                    <strong>{err_msg}</strong>
                </div>"""
                yield DatastarSSE.merge_fragments(frag, selector="#blueprint-status", merge_mode="morph")
                yield DatastarSSE.merge_signals({"isStreaming": False, "status": "error"})
                break

    return StreamingHttpResponse(event_generator(), content_type="text/event-stream")

