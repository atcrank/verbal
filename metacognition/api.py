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
from .events import set_cancellation_flag, subscribe_blueprint_events, subscribe_blueprint_events_sync
from .datastar import DatastarSSE
from llm_api.models import PromptResponseLog, Conversation

try:
    import markdown
except ImportError:
    markdown = None

router = Router()


class BlueprintRunIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None
    parent_log_id: typing.Optional[str] = None
    run_id: typing.Optional[str] = None
    async_mode: bool = False

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
    Executes a multi-step Cognitive Blueprint via the backend executor.
    Supports async_mode=True delegating to dispatch_blueprint with task tracking.
    """
    if payload.async_mode:
        dispatch_in = BlueprintDispatchIn(
            blueprint_id=payload.blueprint_id,
            user_prompt=payload.user_prompt,
            conversation_id=payload.conversation_id
        )
        return dispatch_blueprint(request, dispatch_in)

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
    Asynchronously dispatches a Cognitive Blueprint execution to background task worker.
    Returns the task_id, run_id, and stream URL for Datastar SSE consumption.
    """
    user_id = getattr(request.auth, 'id', None) if hasattr(request, 'auth') else None
    if not user_id and hasattr(request, 'user') and request.user.is_authenticated:
        user_id = request.user.id

    run_id = str(uuid4())
    
    task_res = task_run_blueprint_async.enqueue(
        blueprint_id=payload.blueprint_id,
        user_prompt=payload.user_prompt,
        conversation_id=payload.conversation_id,
        user_id=user_id,
        run_id=run_id
    )

    return JsonResponse({
        "status": "dispatched",
        "task_id": str(task_res.id),
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
    task_resume_blueprint_async.enqueue(
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
def stream_blueprint(request, run_id: str, log_id: typing.Optional[str] = None):
    """
    Server-Sent Events (SSE) streaming endpoint using the Datastar protocol.
    Streams DOM fragment patches and reactive signal updates in real-time
    via synchronous PostgreSQL pub/sub events.
    """
    from .visualizer import render_blueprint_visualizer_html

    def event_generator():
        # Initial connection signal
        yield DatastarSSE.merge_signals({
            "isStreaming": True,
            "runId": run_id,
            "status": "running"
        })

        target_bp = None
        if log_id:
            try:
                init_log = PromptResponseLog.objects.filter(id=log_id).select_related('blueprint').first()
                if init_log and init_log.blueprint:
                    target_bp = init_log.blueprint
            except Exception:
                pass

        completed_step_names = []
        failed_step_names = []

        # If blueprint is known, emit initial visualizer with pending steps
        if target_bp:
            init_viz = render_blueprint_visualizer_html(target_bp, completed_steps=(), active_step=None)
            if init_viz:
                yield DatastarSSE.patch_elements(init_viz, selector="#blueprint-visualizer", mode="morph")

        # Fast path: check if the log already has a completed response
        if log_id:
            log = PromptResponseLog.objects.filter(id=log_id).first()
            if log and log.generated_response and not log.generated_response.startswith('<div id="blueprint-exec-'):
                html = markdown.markdown(log.generated_response, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if markdown else log.generated_response
                frag = f"""<div id="blueprint-final-response" class="response-content">
                    <div class="markdown-body">{html}</div>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#blueprint-final-response", mode="morph")
                if log.blueprint:
                    viz_html = render_blueprint_visualizer_html(log.blueprint)
                    if viz_html:
                        yield DatastarSSE.patch_elements(viz_html, selector="#blueprint-visualizer", mode="morph")
                if log.output_tokens:
                    out_frag = f'<span id="token-out-{log_id}" style="color: var(--text-main); font-weight: 600;">{log.output_tokens}</span>'
                    yield DatastarSSE.patch_elements(out_frag, selector=f"#token-out-{log_id}", mode="morph")
                if log.input_tokens:
                    in_frag = f'<span id="token-in-{log_id}" style="color: var(--text-main); font-weight: 600;">{log.input_tokens}</span>'
                    yield DatastarSSE.patch_elements(in_frag, selector=f"#token-in-{log_id}", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "completed"})
                return

        for event_data in subscribe_blueprint_events_sync(run_id):
            event_type = event_data.get("event")
            payload = event_data.get("data", {})

            if event_type == "step_started":
                step_name = payload.get("step_name", "")
                step_count = payload.get("step_count", 0)
                frag = f"""<div id="blueprint-status" class="agent-step active">
                    <span class="badge">Step {step_count}</span>
                    <strong>Executing: {step_name}</strong>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#blueprint-status", mode="morph")
                yield DatastarSSE.patch_signals({"currentStep": step_name, "stepCount": step_count})
                if target_bp:
                    viz_html = render_blueprint_visualizer_html(
                        target_bp,
                        completed_steps=completed_step_names,
                        active_step=step_name,
                        failed_steps=failed_step_names
                    )
                    if viz_html:
                        yield DatastarSSE.patch_elements(viz_html, selector="#blueprint-visualizer", mode="morph")

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
                yield DatastarSSE.patch_elements(card_html, selector="#tool-approval-container", mode="morph")
                yield DatastarSSE.patch_signals({"requiresApproval": True, "pendingTool": tool_name})

            elif event_type == "step_completed":
                step_name = payload.get("step_name", "")
                output = payload.get("output", "")
                route_to = payload.get("route_to", "")
                if route_to == "FAILURE":
                    failed_step_names.append(step_name)
                else:
                    completed_step_names.append(step_name)

                step_num = len(completed_step_names) + len(failed_step_names)
                icon = "❌" if route_to == "FAILURE" else "✅"
                out_html = markdown.markdown(output, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if markdown else output
                frag = f"""<div class="thinking-step-item">
                    <div class="thinking-step-header">
                        <span class="thinking-step-badge">{icon} Step {step_num}</span>
                        <strong>{step_name}</strong>
                    </div>
                    <div class="thinking-step-body">{out_html}</div>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#monologue-stream", mode="append")

                summary_frag = f"""<summary class="thinking-trace-summary" id="thinking-trace-summary">💭 Thinking Trace ({step_num} step{'s' if step_num != 1 else ''})</summary>"""
                yield DatastarSSE.patch_elements(summary_frag, selector="#thinking-trace-summary", mode="morph")

            elif event_type == "completed":
                final_resp = payload.get("final_response", "")
                html = markdown.markdown(final_resp, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if markdown else final_resp
                frag = f"""<div id="blueprint-final-response" class="response-content">
                    <div class="markdown-body">{html}</div>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#blueprint-final-response", mode="morph")
                status_frag = """<div id="blueprint-status" class="agent-step completed" style="display: none;"></div>"""
                yield DatastarSSE.patch_elements(status_frag, selector="#blueprint-status", mode="morph")
                if target_bp:
                    viz_html = render_blueprint_visualizer_html(
                        target_bp,
                        completed_steps=completed_step_names,
                        active_step=None,
                        failed_steps=failed_step_names
                    )
                    if viz_html:
                        yield DatastarSSE.patch_elements(viz_html, selector="#blueprint-visualizer", mode="morph")

                if log_id:
                    saved_log = PromptResponseLog.objects.filter(id=log_id).first()
                    if saved_log:
                        out_frag = f'<span id="token-out-{log_id}" style="color: var(--text-main); font-weight: 600;">{saved_log.output_tokens}</span>'
                        yield DatastarSSE.patch_elements(out_frag, selector=f"#token-out-{log_id}", mode="morph")
                        in_frag = f'<span id="token-in-{log_id}" style="color: var(--text-main); font-weight: 600;">{saved_log.input_tokens}</span>'
                        yield DatastarSSE.patch_elements(in_frag, selector=f"#token-in-{log_id}", mode="morph")

                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "completed"})
                break

            elif event_type == "cancelled":
                frag = """<div id="blueprint-status" class="agent-step cancelled">
                    <span class="badge badge-cancelled">Cancelled</span>
                    <strong>Execution halted by user.</strong>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#blueprint-status", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "cancelled"})
                break

            elif event_type == "error":
                err_msg = payload.get("error", "Unknown error")
                frag = f"""<div id="blueprint-status" class="agent-step error">
                    <span class="badge badge-error">Error</span>
                    <strong>{err_msg}</strong>
                </div>"""
                yield DatastarSSE.patch_elements(frag, selector="#blueprint-status", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "error"})
                break

    return StreamingHttpResponse(event_generator(), content_type="text/event-stream")

