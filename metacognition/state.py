import operator
from typing import TypedDict, Annotated, Optional, List, Dict, Any
from langchain_core.messages import BaseMessage

# Reducer functions
def add_messages(left: list[BaseMessage], right: list[BaseMessage]) -> list[BaseMessage]:
    """Reducer for working memory: appends new messages."""
    if right and getattr(right[0], 'content', '') == '__OVERWRITE_WORKING_MEMORY__':
        return right[1:]
    return left + right

def update_monologue(left: list[Dict], right: list[Dict]) -> list[Dict]:
    """Reducer for internal monologue: appends new monologue dicts."""
    return left + right

def override_last(left: Any, right: Any) -> Any:
    """Reducer that simply takes the latest value. If concurrent, takes an arbitrary one."""
    return right

def update_dict(left: dict, right: dict) -> dict:
    """Reducer that merges dictionaries."""
    if not isinstance(left, dict):
        left = {}
    if not isinstance(right, dict):
        right = {}
    new_dict = dict(left)
    new_dict.update(right)
    return new_dict

class AgentState(TypedDict):
    """The shared state for all nodes in a Verbal agent graph."""
    
    # Message-based working memory (replaces unbounded string concatenation)
    working_memory: Annotated[list[BaseMessage], add_messages]
    
    # RAG context fetched at graph entry (constant for the duration of the graph, or updated via scratch)
    rag_context: str
    
    # Routing signal set by action hooks  
    route_to: Annotated[Optional[str], override_last]  # "SUCCESS", "FAILURE", "SELF", "USER_INPUT_REQUIRED"
    resume_to: Annotated[Optional[str], override_last] # Target node after interrupt resumption
    
    # Execution metadata
    conversation_id: str
    user_id: Optional[int]
    step_count: Annotated[int, override_last]
    max_steps: int
    retries_remaining: Annotated[Dict[str, int], update_dict] # Track retries per step_name
    
    # Monologue accumulator (for UI display)
    internal_monologue: Annotated[list[Dict[str, Any]], update_monologue]
    
    # Structured data from action hooks (replaces state["primary_rag_doc_meta"] etc.)
    scratch: Annotated[Dict[str, Any], update_dict]
    
    # Token budget tracking
    token_budget_remaining: Annotated[Optional[int], override_last]
