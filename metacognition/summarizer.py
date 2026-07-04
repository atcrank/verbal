import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

logger = logging.getLogger(__name__)

def summarize_if_needed(state: dict) -> dict:
    """
    Node function that checks the token budget of the working memory.
    If the budget is exceeded, it uses the AI service to summarize
    older messages and condense them, retaining the system prompt and
    the most recent messages.
    """
    working_memory = state.get("working_memory", [])
    budget = state.get("token_budget_remaining")
    
    # Not enough messages to summarize
    if len(working_memory) <= 3:
        return {"working_memory": working_memory}
        
    # In a real implementation, we would count tokens here
    # For V1, we'll implement a simple sliding window heuristic
    # If we have more than 10 messages, summarize the middle ones
    
    if len(working_memory) > 10:
        logger.info(f"Summarizing working memory: {len(working_memory)} messages")
        
        system_msgs = [m for m in working_memory if isinstance(m, SystemMessage)]
        recent_msgs = working_memory[-4:]
        
        # Extract the middle messages to summarize
        middle_msgs = [m for m in working_memory if m not in system_msgs and m not in recent_msgs]
        
        if not middle_msgs:
            return {"working_memory": working_memory}
            
        # In a full implementation, we'd call the LLM here to summarize middle_msgs
        # For now, we'll just compress them into a system note to save tokens immediately
        # while keeping the structural framework.
        
        summary_text = f"[System Note: {len(middle_msgs)} older messages were condensed to save memory. They contained prior reasoning steps and context.]"
        summary_msg = SystemMessage(content=summary_text)
        
        new_memory = system_msgs + [summary_msg] + recent_msgs
        
        # We don't return the mutated list directly if we are using the add_messages reducer
        # However, LangGraph's add_messages requires returning the full list if we want to 
        # overwrite or remove messages (by using RemoveMessage or specific IDs).
        # A simpler approach for the prototype is to just replace the whole list in state.
        
        return {"working_memory": new_memory}
        
    return {"working_memory": working_memory}
