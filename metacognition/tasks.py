import logging
logger = logging.getLogger(__name__)

import json
import typing
from celery import shared_task
from .models import CognitiveBlueprint, OUTPUT_TYPES
from llm_api.models import Conversation, PromptResponseLog
from llm_api.apps import service_registry

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from .compiler import compile_graph_from_blueprint

# --- UTILITIES ---

def check_for_banned_concepts(text: str, blueprint: CognitiveBlueprint, nlp_service) -> set:
    banned_terms = {"racist", "sexist", "pornography", "slur"}
    for mod_list in blueprint.moderation_lists.all():
        for term in mod_list.concepts.split(","):
            clean_term = term.strip().lower()
            if clean_term:
                banned_terms.add(clean_term)
                
    if not banned_terms:
        return set()

    response_lemmas = set([t.lower() for t in nlp_service.get_lemmatized_tokens(text)])
    return response_lemmas.intersection(banned_terms)

def get_schema_object(schema_def):
    if not schema_def:
        return None
        
    if schema_def.schema_type == 'json' and schema_def.json_schema:
        try:
            schema_dict = json.loads(schema_def.json_schema)
            from outlines.types import JsonSchema
            return JsonSchema(schema_dict)
        except json.JSONDecodeError:
            logger.info(f"Warning: Invalid JSON Schema in schema '{schema_def.name}'")
    elif schema_def.schema_type == 'pydantic' and schema_def.pydantic_model_name:
        schema_obj = OUTPUT_TYPES.get(schema_def.pydantic_model_name)
        if not schema_obj:
            logger.info(f"Warning: Pydantic model '{schema_def.pydantic_model_name}' not found in OUTPUT_TYPES.")
        return schema_obj
        
    return None

def parse_structured_response(raw_output, schema_def) -> str:
    if not isinstance(raw_output, str):
        if hasattr(raw_output, 'model_dump_json'):
            return raw_output.model_dump_json(indent=2)
        return str(raw_output)

    try:
        if schema_def.schema_type == 'pydantic' and schema_def.pydantic_model_name:
            pydantic_model = OUTPUT_TYPES.get(schema_def.pydantic_model_name)
            if pydantic_model:
                parsed_obj = pydantic_model.model_validate_json(raw_output)
                return parsed_obj.model_dump_json(indent=2)
        elif schema_def.schema_type == 'json':
            parsed_dict = json.loads(raw_output)
            return json.dumps(parsed_dict, indent=2)
    except Exception as e:
        logger.info(f"Warning: Failed to parse structured output according to schema '{schema_def.name}': {e}")
        
    return raw_output

# --- CORE EXECUTION ---

@shared_task
def task_run_blueprint_async(blueprint_id: int, user_prompt: str, conversation_id: typing.Optional[str] = None, user_id: typing.Optional[int] = None, max_steps: int = 100):
    """Asynchronous wrapper for running a blueprint via Celery."""
    return run_blueprint(blueprint_id, user_prompt, conversation_id, user_id, max_steps=max_steps)

def run_blueprint(blueprint_id: int, 
                  user_prompt: str, 
                  conversation_id: typing.Optional[str] = None, 
                  user_id: typing.Optional[int] = None,
                  parent_log_id: typing.Optional[str] = None,
                  max_steps: int = 100):
    try:
        blueprint = CognitiveBlueprint.objects.get(id=blueprint_id)
    except CognitiveBlueprint.DoesNotExist:
        return {"error": "Blueprint not found.", "status": 404}

    # 0. Handle Conversation Tracking
    if not user_id:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        night_manager, _ = User.objects.get_or_create(username="NightManager")
        user_id = night_manager.id

    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return {"error": "Conversation not found.", "status": 404}
    else:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        night_manager_user = User.objects.filter(username="NightManager").first()
        
        if night_manager_user and user_id == night_manager_user.id:
            title = f"NightManager: {blueprint.name}"
            conversation, _ = Conversation.objects.get_or_create(
                user_id=user_id,
                title=title
            )
        else:
            conversation = Conversation.objects.create(
                user_id=user_id,
                title=user_prompt.split(".")[0][:50] + "..."
            )

    # 2. Setup initial state
    from .state import AgentState
    
    # Check Langgraph compile setup
    graph = compile_graph_from_blueprint(blueprint)

    initial_state = AgentState(
        working_memory=[HumanMessage(content=user_prompt)],
        rag_context="",
        route_to=None,
        conversation_id=str(conversation.id),
        user_id=user_id,
        step_count=0,
        max_steps=max_steps,
        retries_remaining={},
        internal_monologue=[],
        scratch={
            "current_chunk_index": 0,
        },
        token_budget_remaining=None,
    )
    
    # Key the checkpoint thread_id by conversation + blueprint name
    # to prevent sub-blueprints from colliding with parent checkpoints.
    thread_id = f"{conversation.id}_{blueprint.name}"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": max_steps}
    
    # 3. Invoke LangGraph
    logger.info(f"Starting LangGraph execution for blueprint {blueprint.name} (thread_id={thread_id})")
    try:
        current_state = graph.get_state(config)
        if current_state and current_state.next:
            # We are resuming an interrupted graph (e.g. user-input-required).
            # Update the working memory with the new user prompt.
            logger.info(f"Resuming interrupted graph for {blueprint.name}, next={current_state.next}")
            graph.update_state(config, {"working_memory": [HumanMessage(content=user_prompt)]})
            result_state = graph.invoke(None, config)
        else:
            # First time running or completed prior run.
            # Clear any stale checkpoints for this thread_id so the graph
            # starts fresh rather than immediately returning the old final state.
            if current_state and current_state.values:
                from .models import AgentCheckpoint
                stale_count, _ = AgentCheckpoint.objects.filter(thread_id=thread_id).delete()
                if stale_count:
                    logger.info(f"Cleared {stale_count} stale checkpoint(s) for thread {thread_id}")
            
            result_state = graph.invoke(initial_state, config)

    except Exception as e:
        import traceback
        logger.error(f"LangGraph execution failed: {traceback.format_exc()}")
        return {"error": f"Execution failed: {str(e)}", "status": 500}

    monologue = result_state.get("internal_monologue", [])
    
    return {
        "blueprint_name": blueprint.name,
        "conversation_id": str(conversation.id),
        "final_response": monologue[-1].get("output", monologue[-1].get("result", "No output.")) if monologue else "No output.",
        "internal_monologue": monologue,
        "working_memory": [
            {"role": getattr(m, "type", "unknown"), "content": getattr(m, "content", "")} 
            for m in result_state.get("working_memory", [])
        ]
    }

@shared_task
def task_update_performance_scores():
    """
    Periodic task to compute EWMA for ReasoningSteps based on their recent PromptResponseLogs.
    Updates the performance_score field.
    """
    from .models import ReasoningStep
    from llm_api.models import PromptResponseLog
    
    alpha = 0.3
    steps = ReasoningStep.objects.all()
    
    for step in steps:
        logs = PromptResponseLog.objects.filter(reasoning_step=step).order_by('created_at')
        if not logs.exists():
            continue
        
        score = step.performance_score
        for log in logs:
            if log.step_status == PromptResponseLog.StepStatus.SUCCESS:
                val = 1.0
            elif log.step_status in [PromptResponseLog.StepStatus.FAILURE, PromptResponseLog.StepStatus.RETRY]:
                val = 0.0
            else:
                continue
            score = (alpha * val) + ((1 - alpha) * score)
            
        step.performance_score = score
        step.save()
    logger.info("Updated performance_scores for all ReasoningSteps.")
