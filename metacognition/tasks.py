import json
import typing
from celery import shared_task
from .models import CognitiveBlueprint, OUTPUT_TYPES
from llm_api.models import Conversation, PromptResponseLog
from llm_api.apps import service_registry
from .actions import ACTION_REGISTRY


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
            print(f"Warning: Invalid JSON Schema in schema '{schema_def.name}'")
    elif schema_def.schema_type == 'pydantic' and schema_def.pydantic_model_name:
        schema_obj = OUTPUT_TYPES.get(schema_def.pydantic_model_name)
        if not schema_obj:
            print(f"Warning: Pydantic model '{schema_def.pydantic_model_name}' not found in OUTPUT_TYPES.")
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
        print(f"Warning: Failed to parse structured output according to schema '{schema_def.name}': {e}")
        
    return raw_output

# --- CORE EXECUTION ---

@shared_task
def task_run_blueprint_async(blueprint_id: int, user_prompt: str, conversation_id: typing.Optional[str] = None, user_id: typing.Optional[int] = None):
    """Asynchronous wrapper for running a blueprint via Celery."""
    return run_blueprint(blueprint_id, user_prompt, conversation_id, user_id)

def run_blueprint(blueprint_id: int, 
                  user_prompt: str, 
                  conversation_id: typing.Optional[str] = None, 
                  user_id: typing.Optional[int] = None,
                  parent_log_id: typing.Optional[str] = None):
    try:
        blueprint = CognitiveBlueprint.objects.get(id=blueprint_id)
    except CognitiveBlueprint.DoesNotExist:
        return {"error": "Blueprint not found.", "status": 404}

    current_step = blueprint.steps.filter(is_start_node=True).first()
    if not current_step:
        return {"error": "This blueprint has no starting node.", "status": 400}

    # 0. Handle Conversation Tracking
    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            return {"error": "Conversation not found.", "status": 404}
    else:
        conversation = Conversation.objects.create(
            user_id=user_id,
            title=user_prompt.split(".")[0][:50] + "..."
        )

    # Determine initial parent_log for the tree
    parent_log = None
    if parent_log_id:
        parent_log = PromptResponseLog.objects.filter(id=parent_log_id).first()
    elif conversation_id:
        parent_log = PromptResponseLog.objects.filter(conversation_id=conversation_id).order_by('-created_at').first()
        
    # Skip automatic logging so we can manually build the timeline tree
    log_kwargs = {"skip_log": True}

    # 1. Fetch RAG Context
    rag_service = service_registry.rag_service
    ai_service = service_registry.ai_service

    rag_docs = rag_service.get_context(user_prompt)
    rag_text = "\n\n".join(
        [f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs])

    primary_meta = rag_docs[0].metadata if rag_docs else {}

    # 2. Initialize the "Working Memory" State
    accumulated_context = f"ORIGINAL USER REQUEST:\n{user_prompt}\n\n"
    accumulated_context += f"RETRIEVED KNOWLEDGE BASE:\n{rag_text}\n"

    state = {
        "working_prompt": accumulated_context,
        "retries_remaining": {},
        "route_to": None,
        "primary_rag_doc_meta": primary_meta,
        "current_chunk_index": primary_meta.get("chunk_index", 0),
        "user_id": user_id,
        "conversation_id": str(conversation.id)
    }

    internal_monologue = []

    # 3. Traverse the State Machine
    step_count = 0
    max_steps = 20  # Overall safety switch

    while current_step and step_count < max_steps:
        print(f"🧠 Executing Blueprint Step: {current_step.name}")

        # Handle Loop/Retry Management
        if current_step.id not in state["retries_remaining"]:
            state["retries_remaining"][current_step.id] = current_step.max_retries
        else:
            state["retries_remaining"][current_step.id] -= 1
            if state["retries_remaining"][current_step.id] <= 0:
                print(f"Max retries exhausted for step '{current_step.name}'. Exiting loop.")
                internal_monologue.append({"step_name": current_step.name, "output": "[ABORTED: Max retries reached.]", "failed": True})
                break

        messages = [
            {"role": "system", "content": current_step.system_prompt},
            {"role": "user", "content": state["working_prompt"]}
        ]

        # Execute LLM Call
        schema_obj = get_schema_object(current_step.output_schema)
        raw_output = None
        generation_failed = False

        if schema_obj:
            raw_output = ai_service.generate_outline(messages=messages, response_schema=schema_obj, max_new_tokens=1024, log_kwargs=log_kwargs)
            if isinstance(raw_output, list):
                raw_output = raw_output[0]
                
            if isinstance(raw_output, dict) and "error" in raw_output:
                generation_failed = True
                cleaned_response = f"[GENERATION FAILED: {raw_output.get('error', 'Error')} - {raw_output.get('details', '')}]"
            else:
                cleaned_response = parse_structured_response(raw_output, current_step.output_schema)
        else:
            [raw_response] = ai_service.generate_response2(messages, max_new_tokens=800, log_kwargs=log_kwargs)
            cleaned_response = ai_service.clean_response(raw_response)
            raw_output = cleaned_response
        print("raw_output:", raw_output)

        # Save this cognitive step to the database to ensure contemplation steps are persisted
        new_log = PromptResponseLog.objects.create(
            conversation_id=conversation.id,
            user_id=user_id,
            system_prompt=current_step.system_prompt,
            user_prompt=state["working_prompt"],
            generated_response=cleaned_response,
            rag_selections=rag_text if step_count == 0 else "",  # Only attach RAG to first step
            parent_log=parent_log,
            blueprint_id=blueprint.id
        )
        # Update parent_log so the NEXT step in the loop becomes a child of THIS step
        parent_log = new_log

        if generation_failed:
            print(f"Generation failure detected in step {current_step.name}")
            internal_monologue.append({"step_name": current_step.name, "output": cleaned_response, "failed": True})
            state["route_to"] = "FAILURE"
        else:
            # Check for Banned Concepts
            violation = check_for_banned_concepts(cleaned_response, blueprint, service_registry.nlp_service)
            if violation:
                print(f"Violation detected in step {current_step.name}: {violation}")
                cleaned_response = f"[CONTENT BLOCKED: Detected restricted concepts: {', '.join(violation)}]"
                internal_monologue.append({"step_name": current_step.name, "output": cleaned_response, "failed": True})
                state["route_to"] = "FAILURE"
            else:
                internal_monologue.append({"step_name": current_step.name, "output": cleaned_response})
                
                state["route_to"] = "SUCCESS"  # Default assumption
                
                # Trigger Action Hook if defined
                if current_step.action_hook and current_step.action_hook in ACTION_REGISTRY:
                    action_func = ACTION_REGISTRY[current_step.action_hook]
                    try:
                        state = action_func(state, raw_output)
                    except Exception as e:
                        print(f"Action hook {current_step.action_hook} failed: {e}")
                        state["route_to"] = "FAILURE"

        # Routing Logic based on state mutation
        if state.get("route_to") == "FAILURE":
            current_step = current_step.on_failure_step
        elif state.get("route_to") == "SELF":
            pass  # current_step remains the same, initiating the retry logic at the top of the while loop
        elif state.get("route_to") == "USER_INPUT_REQUIRED":
            # Halts the loop and returns the clarification question
            clarification = getattr(raw_output, 'clarification_question', 'Please clarify your request.')
            internal_monologue.append({"step_name": current_step.name, "output": clarification, "paused": True})
            break
        else:
            # Accumulate successful output into Working Memory and move forward
            state["working_prompt"] += f"\n\n--- OUTPUT FROM PREVIOUS STEP ({current_step.name}) ---\n{cleaned_response}\n"
            current_step = current_step.on_success_step

        step_count += 1

    return {
        "blueprint_name": blueprint.name,
        "conversation_id": str(conversation.id),
        "final_response": internal_monologue[-1]["output"] if internal_monologue else "No output.",
        "internal_monologue": internal_monologue
    }
