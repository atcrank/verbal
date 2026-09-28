"""
Tool Governance Context Processors (WS17).

Exposes active governance state, ceiling clamps, badge styles, and effective
clearance to all Django templates.
"""

from metacognition.governance import get_governance_summary


def governance_context(request):
    """
    Exposes active tool governance state, lockdown level, and user clearance
    to all rendered templates as 'governance'.
    """
    user = getattr(request, "user", None)
    return {
        "governance": get_governance_summary(user),
    }
