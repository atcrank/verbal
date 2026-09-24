import time
import json
from django.http import JsonResponse, StreamingHttpResponse, HttpResponseForbidden, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from .broadcast_service import BroadcastService


def current_broadcast(request):
    """JSON endpoint returning active broadcast message."""
    data = BroadcastService.get_current_broadcast()
    return JsonResponse(data or {"active": False})


def stream_broadcasts(request):
    """
    Server-Sent Events (SSE) stream endpoint for live announcements.
    Pushes messages with SSE auto-reconnect headers (retry: 3000).
    Limits synchronous stream duration (max 10s) to prevent WSGI worker pool starvation.
    """
    max_duration = min(max(int(request.GET.get("timeout", 5)), 0), 10)

    def event_stream():
        # Instruct SSE client to reconnect after 3 seconds upon stream close
        yield "retry: 3000\n\n"

        last_id = None
        # Send initial state immediately
        initial = BroadcastService.get_current_broadcast()
        if initial:
            last_id = initial.get("id")
            yield f"data: {json.dumps(initial)}\n\n"
        else:
            yield f"data: {json.dumps({'active': False})}\n\n"

        # Short stream loop (client EventSource auto-reconnects cleanly)
        start_time = time.time()
        while time.time() - start_time < max_duration:
            time.sleep(1)
            current = BroadcastService.get_current_broadcast()
            current_id = current.get("id") if current else None

            if current_id != last_id:
                last_id = current_id
                payload = current if current else {"active": False}
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                # Keep-alive comment
                yield ": keepalive\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response



@login_required
def send_broadcast_api(request):
    """API endpoint to publish a broadcast message."""
    if not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden("Staff permission required to send broadcasts.")

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = request.POST

        message = data.get("message", "").strip()
        level = data.get("level", "info")
        redirect_url = data.get("redirect_url")
        duration = int(data.get("duration", 300))

        if not message:
            return JsonResponse({"error": "Message text is required"}, status=400)

        result = BroadcastService.set_broadcast(
            message=message,
            level=level,
            redirect_url=redirect_url,
            duration_seconds=duration,
        )
        return JsonResponse({"success": True, "broadcast": result})

    return JsonResponse({"error": "POST required"}, status=405)


@login_required
def clear_broadcast_api(request):
    """API endpoint to dismiss/clear active broadcast."""
    if not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden("Staff permission required.")
    BroadcastService.clear_broadcast()
    return JsonResponse({"success": True})
