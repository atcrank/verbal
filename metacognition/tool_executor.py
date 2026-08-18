import logging
import json
from django.db import transaction

logger = logging.getLogger(__name__)

def resolve_tool(tool_definition) -> callable:
    """
    Returns the callable associated with a ToolDefinition.
    """
    callable_func = tool_definition.get_callable()
    if not callable_func:
        raise ValueError(f"Could not resolve callable for tool {tool_definition.name}")
    return callable_func

def execute_tool(tool_def, state: dict, params: dict, dry_run: bool = False) -> str:
    """
    Executes a ToolDefinition. If dry_run is True, database mutations are rolled back.
    """
    try:
        if tool_def.tool_type == 'builtin':
            func = resolve_tool(tool_def)
            
            if dry_run:
                # Execute in an atomic transaction that is guaranteed to rollback
                try:
                    with transaction.atomic():
                        result = func(state, params)
                        transaction.set_rollback(True)  # Force rollback
                        return f"[DRY RUN SIMULATION]\n{result}"
                except Exception as e:
                    return f"[DRY RUN ERROR]\n{str(e)}"
            else:
                return func(state, params)
                
        elif tool_def.tool_type == 'api':
            # Simplified API execution simulation
            if dry_run:
                return f"[DRY RUN SIMULATION]\nWould make API call to: {tool_def.api_url} with params {json.dumps(params)}"
            else:
                import requests
                response = requests.post(tool_def.api_url, json=params, timeout=30)
                return response.text
                
        elif tool_def.tool_type == 'blueprint':
            if dry_run:
                 return f"[DRY RUN SIMULATION]\nWould execute sub-blueprint: {tool_def.sub_blueprint.name}"
            else:
                from metacognition.compiler import compile_graph_from_blueprint
                from metacognition.state import AgentState
                from langchain_core.messages import HumanMessage
                
                sub_graph = compile_graph_from_blueprint(tool_def.sub_blueprint)
                
                # Use the user's prompt or the tool's input parameter as the start message
                prompt_text = str(params)
                
                sub_state = AgentState(
                    working_memory=[HumanMessage(content=prompt_text)],
                    rag_context=state.get("rag_context", ""),
                    route_to=None,
                    conversation_id=f"{state.get('conversation_id', 'anon')}_sub_{tool_def.sub_blueprint.id}",
                    user_id=state.get("user_id"),
                    step_count=0,
                    max_steps=20,
                    retries_remaining={},
                    internal_monologue=[],
                    scratch={},
                    token_budget_remaining=state.get("token_budget_remaining")
                )
                
                config = {"configurable": {"thread_id": sub_state["conversation_id"]}}
                logger.info(f"Invoking sub-blueprint {tool_def.sub_blueprint.name}")
                
                result_state = sub_graph.invoke(sub_state, config)
                
                monologue = result_state.get("internal_monologue", [])
                if monologue:
                    return monologue[-1].get("output", monologue[-1].get("result", "No output."))
                return "Sub-blueprint executed but returned no output."
    except Exception as e:
        logger.error(f"Error executing tool {tool_def.name}: {e}")
        return f"Error executing tool {tool_def.name}: {str(e)}"
    
    return f"Unsupported tool type: {tool_def.tool_type}"
