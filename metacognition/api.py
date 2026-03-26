import typing
import json
import outlines

from ninja import Router, Schema
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import CognitiveBlueprint
from llm_api.models import Conversation
from llm_api.apps import service_registry
from llm_api.api import OUTPUT_TYPES  # Import your hardcoded types registry!

router = Router()


class BlueprintRunIn(Schema):
    blueprint_id: int
    user_prompt: str
    conversation_id: typing.Optional[str] = None

def check_for_banned_concepts(text: str, blueprint: CognitiveBlueprint, nlp_service) -> set:
    """
    Checks text against a hardcoded base list of banned terms and any custom
    ModerationLists attached to the blueprint.
    Returns a set of violating terms (empty set if no violations).
    """
    # 1. Built-in absolute bans (lemmas)
    banned_terms = {"racist", "sexist", "pornography", "slur"}
    
    # 2. Add user-defined bans from the blueprint's moderation lists
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
    """Resolves a ResponseSchema model instance into an outlines-compatible schema object."""
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
    """Validates the generated string using Pydantic or json, and pretty-prints it."""
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

@router.post("/execute_blueprint/")
@ensure_csrf_cookie
def execute_blueprint(request, payload: BlueprintRunIn):
    """
    Executes a multi-step Cognitive Blueprint.
    """
    try:
        blueprint = CognitiveBlueprint.objects.get(id=payload.blueprint_id)
    except CognitiveBlueprint.DoesNotExist:
        return JsonResponse({"error": "Blueprint not found."}, status=404)

    current_step = blueprint.steps.filter(is_start_node=True).first()
    if not current_step:
        return JsonResponse({"error": "This blueprint has no starting node."}, status=400)

    # 0. Handle Conversation Tracking
    user_id = getattr(request.auth, 'id', None) if hasattr(request, 'auth') else None
    if not user_id and hasattr(request, 'user') and request.user.is_authenticated:
        user_id = request.user.id

    if payload.conversation_id:
        try:
            conversation = Conversation.objects.get(id=payload.conversation_id)
            if not conversation.blueprint:
                conversation.blueprint = blueprint
                conversation.save()
        except Conversation.DoesNotExist:
            return JsonResponse({"error": "Conversation not found."}, status=404)
    else:
        conversation = Conversation.objects.create(
            user_id=user_id,
            title=payload.user_prompt.split(".")[0][:50] + "...",
            blueprint=blueprint
        )

    log_kwargs = {"conversation_id": str(conversation.id), "user_id": user_id}

    # 1. Fetch RAG Context
    rag_service = service_registry.rag_service
    ai_service = service_registry.ai_service

    rag_docs = rag_service.get_context(payload.user_prompt)
    rag_text = "\n\n".join(
        [f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs])

    # 2. Initialize the "Working Memory"
    internal_monologue = []

    # Log the initial RAG extraction to the conversation under the first step's context
    log_kwargs["rag_selections"] = rag_text

    # This context grows with every step, so Step 3 can read the output of Step 1 and 2
    accumulated_context = f"ORIGINAL USER REQUEST:\n{payload.user_prompt}\n\n"
    accumulated_context += f"RETRIEVED KNOWLEDGE BASE:\n{rag_text}\n"

    # 3. Traverse the State Machine
    step_count = 0
    max_steps = 10  # Safety switch to prevent infinite loops

    while current_step and step_count < max_steps:
        print(f"🧠 Executing Blueprint Step: {current_step.name}")

        messages = [
            {"role": "system", "content": current_step.system_prompt},
            {"role": "user", "content": accumulated_context}
        ]

        # Execute LLM Call
        schema_obj = get_schema_object(current_step.output_schema)

        if schema_obj:
            # Delegate to ai_service to handle the generation AND the pervasive logging
            raw_output = ai_service.generate_outline(
                messages=messages,
                response_schema=schema_obj,
                max_new_tokens=1024,
                log_kwargs=log_kwargs
            )
            if isinstance(raw_output, list):
                raw_output = raw_output[0]
            
            cleaned_response = parse_structured_response(raw_output, current_step.output_schema)
        else:
            [raw_response] = ai_service.generate_response(messages, max_new_tokens=800, log_kwargs=log_kwargs)
            cleaned_response = ai_service.clean_response(raw_response)

        # Clear RAG selections from kwargs after first use so we don't duplicate it in DB logs
        log_kwargs["rag_selections"] = ""

        # Check for Banned Concepts
        violation = check_for_banned_concepts(cleaned_response, blueprint, service_registry.nlp_service)
        if violation:
            print(f"Violation detected in step {current_step.name}: {violation}")
            # Route to failure step if defined, or halt
            cleaned_response = f"[CONTENT BLOCKED: Detected restricted concepts: {', '.join(violation)}]"
            internal_monologue.append({"step_name": current_step.name, "output": cleaned_response, "failed": True})

            if current_step.on_failure_step:
                current_step = current_step.on_failure_step
                step_count += 1
                continue
            else:
                break
       
        # Log the thought process
        internal_monologue.append({
            "step_name": current_step.name,
            "output": cleaned_response
        })

        # Append this step's output to the working memory for the NEXT step to read
        accumulated_context += f"\n\n--- OUTPUT FROM PREVIOUS STEP ({current_step.name}) ---\n{cleaned_response}\n"

        # For now, we assume success and take the happy path.
        # (Evaluation logic can be added here later).
        current_step = current_step.on_success_step
        step_count += 1

    return JsonResponse({
        "blueprint_name": blueprint.name,
        "conversation_id": str(conversation.id),
        "final_response": internal_monologue[-1]["output"] if internal_monologue else "No output.",
        "internal_monologue": internal_monologue
    })
