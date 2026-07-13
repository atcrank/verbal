"""
Fine-tuning data export utilities.

Exports training data from BenchmarkScenarios and successful PromptResponseLogs
in formats suitable for local LoRA fine-tuning (ShareGPT/Axolotl) and
OpenAI fine-tuning API (JSONL messages format).
"""

import json
import logging
from typing import Literal

logger = logging.getLogger(__name__)


def export_scenarios(
    scenario_group_id: int,
    format: Literal["sharegpt", "openai"] = "sharegpt",
    system_prompt: str = "You are a domain expert in causal modeling and quantitative analysis.",
) -> str:
    """
    Exports BenchmarkScenarios from a ScenarioGroup as JSONL training data.

    Args:
        scenario_group_id: ID of the ScenarioGroup to export.
        format: Output format — 'sharegpt' for Unsloth/Axolotl, 'openai' for OpenAI API.
        system_prompt: System prompt to include in each training example.

    Returns:
        JSONL string with one training example per line.
    """
    from benchmarking.models import ScenarioGroup

    group = ScenarioGroup.objects.get(id=scenario_group_id)
    scenarios = group.scenarios.all()

    if not scenarios.exists():
        raise ValueError(f"ScenarioGroup '{group.name}' has no scenarios to export.")

    lines = []
    for scenario in scenarios:
        if not scenario.question or not scenario.ideal_answer:
            continue

        if format == "sharegpt":
            entry = {
                "conversations": [
                    {"from": "system", "value": system_prompt},
                    {"from": "human", "value": scenario.question},
                    {"from": "gpt", "value": scenario.ideal_answer},
                ]
            }
        elif format == "openai":
            entry = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": scenario.question},
                    {"role": "assistant", "content": scenario.ideal_answer},
                ]
            }
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'sharegpt' or 'openai'.")

        lines.append(json.dumps(entry, ensure_ascii=False))

    logger.info(f"Exported {len(lines)} training examples from ScenarioGroup '{group.name}' ({format} format)")
    return "\n".join(lines)


def export_successful_trajectories(
    format: Literal["sharegpt", "openai"] = "sharegpt",
    require_thumbs_up: bool = False,
    max_examples: int = 1000,
) -> str:
    """
    Exports successful PromptResponseLog entries as training data.

    Mines the most valuable fine-tuning signal: actual successful trajectories
    where the model got it right (optionally confirmed by user feedback).

    Args:
        format: Output format — 'sharegpt' or 'openai'.
        require_thumbs_up: If True, only export logs with THUMB_UP feedback.
        max_examples: Maximum number of examples to export.

    Returns:
        JSONL string with one training example per line.
    """
    from llm_api.models import PromptResponseLog

    queryset = PromptResponseLog.objects.filter(
        step_status='SUCCESS',
    ).exclude(
        user_prompt__isnull=True,
    ).exclude(
        user_prompt='',
    ).exclude(
        generated_response='',
    )

    if require_thumbs_up:
        queryset = queryset.filter(user_feedback=1)

    logs = queryset.order_by('-created_at')[:max_examples]

    lines = []
    for log in logs:
        system = log.system_prompt or "You are a helpful assistant."

        if format == "sharegpt":
            entry = {
                "conversations": [
                    {"from": "system", "value": system},
                    {"from": "human", "value": log.user_prompt},
                    {"from": "gpt", "value": log.generated_response},
                ]
            }
        elif format == "openai":
            entry = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": log.user_prompt},
                    {"role": "assistant", "content": log.generated_response},
                ]
            }
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'sharegpt' or 'openai'.")

        lines.append(json.dumps(entry, ensure_ascii=False))

    logger.info(f"Exported {len(lines)} successful trajectories ({format} format, thumbs_up_only={require_thumbs_up})")
    return "\n".join(lines)


def export_conversation_trajectories(
    conversation_id: str,
    format: Literal["sharegpt", "openai"] = "sharegpt",
    system_prompt: str | None = None,
) -> str:
    """
    Exports a full conversation as a multi-turn training example.

    Reconstructs the conversation from PromptResponseLogs and exports it
    as a single multi-turn example, suitable for training models on
    extended reasoning and follow-up capability.

    Args:
        conversation_id: UUID of the Conversation to export.
        format: Output format — 'sharegpt' or 'openai'.
        system_prompt: Override system prompt. If None, uses the first log's.

    Returns:
        JSONL string with one multi-turn training example.
    """
    from llm_api.models import Conversation

    conversation = Conversation.objects.get(id=conversation_id)
    # Use max_logs=0 to get the full untruncated conversation
    messages = conversation.as_messages(max_logs=0)

    if not messages:
        raise ValueError(f"Conversation {conversation_id} has no messages.")

    if system_prompt:
        # Replace or inject system prompt
        if messages[0].get("role") == "system":
            messages[0]["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})

    if format == "sharegpt":
        role_map = {"system": "system", "user": "human", "assistant": "gpt"}
        entry = {
            "conversations": [
                {"from": role_map.get(m["role"], m["role"]), "value": m["content"]}
                for m in messages
            ]
        }
    elif format == "openai":
        entry = {"messages": messages}
    else:
        raise ValueError(f"Unsupported format: {format}. Use 'sharegpt' or 'openai'.")

    logger.info(f"Exported conversation {conversation_id} as multi-turn training example ({len(messages)} turns)")
    return json.dumps(entry, ensure_ascii=False)
