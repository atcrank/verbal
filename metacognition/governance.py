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
    raw_level = getattr(settings, "VERBAL_LOCKDOWN_LEVEL", LockdownLevel.AIR_GAPPED)
    return str(raw_level).upper()


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
