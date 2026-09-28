"""
Tool Governance and Lockdown Middleware (WS17).

Injects the active system lockdown level into HTTP response headers:
    X-Verbal-Lockdown-Level: <LEVEL>
This applies across all standard views, API responses, and SSE streaming sessions.
"""

from django.conf import settings
from metacognition.governance import get_lockdown_level


class ToolGovernanceMiddleware:
    """
    WS17 Layered Tool Governance Middleware.
    Injects the active governance lockdown level header into all HTTP, API,
    and streaming responses.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Verbal-Lockdown-Level"] = get_lockdown_level()
        return response
