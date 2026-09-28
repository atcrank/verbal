"""
Tool Governance and Lockdown Policy Engine (WS17).

Defines the 3-tier permission evaluation architecture:
- Tier 1: Host environment ceiling in settings.py (.env)
- Tier 2: ToolDefinition capability classifications and strict model write allowlists
- Tier 3: User and session clearance resolution
"""

from enum import StrEnum
import logging
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


class LockdownLevel(StrEnum):
    AIR_GAPPED = "AIR_GAPPED"
    RESTRICTED = "RESTRICTED"
    CONTROLLED = "CONTROLLED"
    DEVELOPMENT = "DEVELOPMENT"


class ToolCapability(StrEnum):
    READ_ONLY = "READ_ONLY"
    STATE_MUTATION = "STATE_MUTATION"
    CODE_EXECUTION = "CODE_EXECUTION"
    META_GOVERNANCE = "META_GOVERNANCE"


class ClearanceLevel(StrEnum):
    STANDARD = "STANDARD"
    TRUSTED = "TRUSTED"
    ADMIN = "ADMIN"


CLEARANCE_RANKS: dict[str, int] = {
    ClearanceLevel.STANDARD: 1,
    ClearanceLevel.TRUSTED: 2,
    ClearanceLevel.ADMIN: 3,
}

# Strict allowlist of models that generic model write tools (e.g. write_django_model)
# are permitted to create, update, or mutate at runtime.
BASE_ALLOWED_DOMAIN_MODELS: frozenset[str] = frozenset({
    "grips.conceptnode",
    "grips.knowledgeedge",
    "work_organisation.whiteboardcard",
    "work_organisation.whiteboardcluster",
    "notes.investigationnote",
})

# Models only mutable when ALLOW_AGENT_SELF_MODIFICATION is explicitly enabled (e.g. DEVELOPMENT mode)
DEV_SELF_MOD_MODELS: frozenset[str] = frozenset({
    "metacognition.cognitiveblueprint",
    "metacognition.reasoningstep",
})


def get_allowed_write_models() -> frozenset[str]:
    """
    Returns the dynamic set of allowed write models based on active governance settings.
    In DEVELOPMENT mode (or when ALLOW_AGENT_SELF_MODIFICATION is True), blueprint self-modification
    models are included for research. In all other modes, only base domain models are permitted.
    """
    allow_self_mod = getattr(settings, "ALLOW_AGENT_SELF_MODIFICATION", False)
    if allow_self_mod:
        return BASE_ALLOWED_DOMAIN_MODELS | DEV_SELF_MOD_MODELS
    return BASE_ALLOWED_DOMAIN_MODELS


def get_lockdown_level() -> str:
    """Returns the active system lockdown level from Django settings."""
    raw_level = getattr(settings, "VERBAL_LOCKDOWN_LEVEL", LockdownLevel.DEVELOPMENT)
    normalized = str(raw_level).upper().strip()
    if normalized in ("TEXT_ONLY", "TEXT-ONLY", "LOCKED"):
        return LockdownLevel.AIR_GAPPED
    return normalized


def get_user_clearance(user: Optional[User]) -> str:
    """
    Resolves the operational clearance level for a given Django User.
    - Superusers and staff get ADMIN clearance.
    - Members of 'Trusted Operators' group or users with is_trusted flag get TRUSTED clearance.
    - All other authenticated or anonymous users get STANDARD clearance.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return ClearanceLevel.STANDARD

    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return ClearanceLevel.ADMIN

    if getattr(user, "is_trusted", False):
        return ClearanceLevel.TRUSTED

    if hasattr(user, "groups") and user.groups.filter(name="Trusted Operators").exists():
        return ClearanceLevel.TRUSTED

    return ClearanceLevel.STANDARD


def is_tool_permitted(tool, user: Optional[User] = None) -> tuple[bool, str]:
    """
    Evaluates whether a ToolDefinition is permitted to be exposed or executed
    based on the 3-tier governance policy:
    1. Tier 1 Environment Clamps (settings ceiling)
    2. Tier 2 Tool Active State
    3. Tier 3 User Clearance Level

    Returns:
        tuple[bool, str]: (is_allowed, reason_message)
    """
    lockdown_level = get_lockdown_level()
    tool_name = getattr(tool, "name", "unknown_tool")
    capability = getattr(tool, "capability_category", ToolCapability.READ_ONLY)
    required_clearance = getattr(tool, "required_clearance", ClearanceLevel.STANDARD)
    is_active = getattr(tool, "is_active", True)

    # 1. Tier 1 Environment Clamps (Immutable ceiling)
    if capability == ToolCapability.CODE_EXECUTION:
        allow_code_exec = getattr(settings, "ALLOW_MODEL_CODE_EXECUTION", False)
        if not allow_code_exec:
            return (
                False,
                f"Tool '{tool_name}' requires code execution, which is disabled by system policy ({lockdown_level}).",
            )

    if capability == ToolCapability.META_GOVERNANCE:
        allow_self_mod = getattr(settings, "ALLOW_AGENT_SELF_MODIFICATION", False)
        if not allow_self_mod:
            return (
                False,
                f"Tool '{tool_name}' is a meta-tool, which is blocked in {lockdown_level} mode.",
            )

    # 2. Tier 2 Active State
    if not is_active:
        return (False, f"Tool '{tool_name}' is currently inactive.")

    # 3. Tier 3 User Clearance
    user_clearance = get_user_clearance(user)
    user_rank = CLEARANCE_RANKS.get(user_clearance, 1)
    tool_rank = CLEARANCE_RANKS.get(required_clearance, 1)

    if user_rank < tool_rank:
        return (
            False,
            f"User clearance '{user_clearance}' is insufficient for tool '{tool_name}' (requires '{required_clearance}').",
        )

    return (True, "Permitted")


def check_model_write_allowed(app_label: str, model_name: str) -> tuple[bool, str]:
    """
    Validates whether a target Django model is permitted to be written or modified
    by runtime LLM tools.
    """
    canonical_name = f"{app_label.lower().strip()}.{model_name.lower().strip()}"
    allowed_models = get_allowed_write_models()
    if canonical_name in allowed_models:
        return (True, f"Model '{canonical_name}' is permitted for runtime mutation.")

    lockdown_level = get_lockdown_level()
    return (
        False,
        f"Security violation: Model '{canonical_name}' is not in the permitted domain write allowlist under {lockdown_level} policy.",
    )


def get_governance_summary(user: Optional[User] = None) -> dict:
    """
    Returns a unified dictionary representing active system lockdown status,
    environment ceiling clamps, and effective user clearance for UI/templates.
    """
    level = get_lockdown_level()
    user_clearance = get_user_clearance(user)
    allow_code = getattr(settings, "ALLOW_MODEL_CODE_EXECUTION", False)
    allow_net = getattr(settings, "ALLOW_TOOL_NETWORK_ACCESS", False)
    allow_self_mod = getattr(settings, "ALLOW_AGENT_SELF_MODIFICATION", False)

    raw_setting = getattr(settings, "VERBAL_LOCKDOWN_LEVEL", LockdownLevel.DEVELOPMENT)
    raw_upper = str(raw_setting).upper().strip()

    if level == LockdownLevel.AIR_GAPPED:
        if raw_upper in ("TEXT_ONLY", "TEXT-ONLY"):
            badge_label = "TEXT-ONLY"
        elif raw_upper == "LOCKED":
            badge_label = "LOCKED"
        else:
            badge_label = "AIR-GAPPED"
        badge_icon = "🛡️"
        color_class = "gov-badge-airgapped"
        bg_style = "background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35);"
    elif level == LockdownLevel.RESTRICTED:
        badge_label = "RESTRICTED"
        badge_icon = "🔒"
        color_class = "gov-badge-restricted"
        bg_style = "background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35);"
    elif level == LockdownLevel.CONTROLLED:
        badge_label = "CONTROLLED"
        badge_icon = "⚡"
        color_class = "gov-badge-controlled"
        bg_style = "background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.35);"
    else:  # DEVELOPMENT / PERMISSIVE
        badge_label = "DEVELOPMENT"
        badge_icon = "🧪"
        color_class = "gov-badge-dev"
        bg_style = "background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35);"

    return {
        "lockdown_level": level,
        "badge_label": badge_label,
        "badge_icon": badge_icon,
        "color_class": color_class,
        "bg_style": bg_style,
        "code_execution_allowed": allow_code,
        "code_exec_display": "ALLOWED" if allow_code else "BLOCKED (Ceiling)",
        "network_allowed": allow_net,
        "network_display": "ALLOWED" if allow_net else "BLOCKED (Ceiling)",
        "self_mod_allowed": allow_self_mod,
        "self_mod_display": "ACTIVE" if allow_self_mod else "DISABLED (Immutable)",
        "user_clearance": user_clearance,
    }


def evaluate_blueprint_governance(blueprint, user: Optional[User] = None) -> dict:
    """
    Evaluates whether a CognitiveBlueprint is compatible with the active lockdown
    policy and the user's operational clearance.

    Returns:
        dict:
            - status: "READY" | "DEGRADED" | "LOCKED"
            - is_locked: bool
            - is_degraded: bool
            - badge_text: str (e.g. "🔒 Locked", "⚠️ Degraded", "✅ Ready")
            - tooltip: str explaining why the blueprint is locked, degraded, or compatible
            - blocked_tools: list[str] of tool names that are prohibited
            - permitted_tools: list[str] of tool names that are permitted
    """
    lockdown_level = get_lockdown_level()
    steps = list(blueprint.steps.filter(is_active=True))
    if not steps:
        steps = list(blueprint.steps.all())

    all_tools = []
    blocked_tools = []
    permitted_tools = []
    has_dead_step = False
    dead_step_reasons = []

    for step in steps:
        step_tools = list(step.available_tools.all())
        all_tools.extend(step_tools)
        substantive = [
            t for t in step_tools
            if t.name not in ("TASK_COMPLETE", "PAUSE_FOR_INPUT", "NO_TOOL")
        ]
        step_blocked = []
        step_permitted = []
        for t in step_tools:
            allowed, reason = is_tool_permitted(t, user)
            if allowed:
                permitted_tools.append(t)
                step_permitted.append(t)
            else:
                blocked_tools.append((t, reason))
                step_blocked.append((t, reason))

        # Check if substantive tools for this step are all blocked
        if substantive:
            substantive_permitted = [t for t in substantive if is_tool_permitted(t, user)[0]]
            if not substantive_permitted:
                has_dead_step = True
                reasons = [r for t, r in step_blocked if t in substantive]
                dead_step_reasons.append(f"Step '{step.name}': {'; '.join(reasons)}")

        # Check sub-blueprint recursively if attached
        if getattr(step, "sub_blueprint_id", None) and step.sub_blueprint:
            sub_eval = evaluate_blueprint_governance(step.sub_blueprint, user)
            if sub_eval["is_locked"]:
                has_dead_step = True
                dead_step_reasons.append(f"Sub-blueprint '{step.sub_blueprint.name}': {sub_eval['tooltip']}")

    if has_dead_step or (all_tools and len(permitted_tools) == 0):
        status = "LOCKED"
        is_locked = True
        is_degraded = False
        badge_text = "🔒 Locked"
        if any("code execution" in r.lower() for r in dead_step_reasons) or any(
            t.capability_category == ToolCapability.CODE_EXECUTION for t, _ in blocked_tools
        ):
            tooltip = f"Locked: Requires Code Execution clearance which is blocked by system policy ({lockdown_level})."
        elif dead_step_reasons:
            tooltip = f"Locked: {dead_step_reasons[0]}"
        else:
            tooltip = f"Locked: Required capabilities are blocked under {lockdown_level} policy."
    elif blocked_tools:
        status = "DEGRADED"
        is_locked = False
        is_degraded = True
        badge_text = "⚠️ Degraded"
        blocked_names = ", ".join(sorted(set(t.name for t, _ in blocked_tools)))
        tooltip = f"Degraded: Tool(s) [{blocked_names}] unavailable under {lockdown_level}; falling back to available tools."
    else:
        status = "READY"
        is_locked = False
        is_degraded = False
        badge_text = "✅ Ready"
        if all_tools:
            tooltip = f"Compatible: All required tools are active under {lockdown_level}."
        else:
            tooltip = f"Compatible: Pure reasoning blueprint (active under {lockdown_level})."

    return {
        "status": status,
        "is_locked": is_locked,
        "is_degraded": is_degraded,
        "badge_text": badge_text,
        "tooltip": tooltip,
        "blocked_tools": [t.name for t, _ in blocked_tools],
        "permitted_tools": [t.name for t in permitted_tools],
    }

