import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

logger = logging.getLogger(__name__)

def summarize_if_needed(state: dict) -> dict:
    """
    Truncates working_memory to fit within token budget.
    Keeps: system message (first) + last N messages that fit.
    """
    budget = state.get("token_budget_remaining", 8000)
    if budget >= 500:
        return {}  # No action needed
    
    working_memory = list(state.get("working_memory", []))
    if len(working_memory) <= 2:
        return {}  # Nothing to truncate
    
    # Keep system message + at least the last user message
    system_msg = working_memory[0] if working_memory else None
    remaining = working_memory[1:]
    
    # Estimate tokens per message, keep from the end
    kept = []
    token_estimate = 0
    for msg in reversed(remaining):
        msg_tokens = int(len(str(getattr(msg, 'content', '')).split()) * 1.3)
        if token_estimate + msg_tokens > budget * 0.7:  # Leave 30% headroom
            break
        kept.insert(0, msg)
        token_estimate += msg_tokens
    
    new_memory = ([system_msg] if system_msg else []) + kept
    return {"working_memory": new_memory}
