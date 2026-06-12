import typing
from django.db.models.signals import post_migrate
from django.dispatch import receiver


def parse_schema_docstring(docstring):
    """Parses custom metadata tags out of Python docstrings."""
    if not docstring:
        return {}
    
    parsed = {
        "description": [], "step_prompt": [], "evaluation_prompt": [],
        "prior_nodes": [], "following_nodes": [], "failure_nodes": []
    }
    
    current_key = "description"
    
    for line in docstring.strip().split('\n'):
        line_str = line.strip()
        lower_line = line_str.lower()
        
        if lower_line.startswith("step prompt:"):
            current_key = "step_prompt"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("evaluation prompt:"):
            current_key = "evaluation_prompt"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("prior nodes:"):
            current_key = "prior_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("following nodes:"):
            current_key = "following_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        elif lower_line.startswith("failure nodes:"):
            current_key = "failure_nodes"
            parsed[current_key].append(line_str.split(":", 1)[1].strip())
        else:
            parsed[current_key].append(line_str)
            
    return {k: " ".join(v).strip() for k, v in parsed.items()}


@receiver(post_migrate)
def seed_database(sender, **kwargs):
    """
    Introspects the application's schemas and action hooks to ensure the database
    is populated with corresponding ResponseSchemas, Blueprints, and Linked ReasoningSteps.
    """
    # Only run this once for the metacognition app itself
    if sender.name != 'metacognition':
        return

    from .models import ResponseSchema, CognitiveBlueprint, ReasoningStep, OUTPUT_TYPES
    from .actions import ACTION_REGISTRY

    # 2. Clever Introspection: Map Actions back to Schemas
    # We inspect the type hint of the 'llm_output' parameter in each action hook
    # e.g., handle_active_reading(state: dict, llm_output: ActiveReadingEvaluation) -> dict
    schema_to_action = {}
    for action_name, action_func in ACTION_REGISTRY.items():
        hints = typing.get_type_hints(action_func)
        if 'llm_output' in hints:
            param_type = hints['llm_output']
            if hasattr(param_type, '__name__'):
                schema_to_action[param_type.__name__] = action_name

    step_data_map = {}
    created_steps = {}

    # 1. Parse Docstrings and Create ResponseSchemas
    for name, cls in OUTPUT_TYPES.items():
        doc_data = parse_schema_docstring(getattr(cls, '__doc__', None))
        desc = doc_data.get('description') or f"Auto-generated schema for {name}"
        
        schema_obj, _ = ResponseSchema.objects.get_or_create(
            name=name,
            defaults={
                'description': desc[:255],
                'schema_type': 'pydantic',
                'pydantic_model_name': name,
            }
        )
        step_data_map[name] = {
            'schema_obj': schema_obj,
            'action_hook': schema_to_action.get(name, ""),
            'doc_data': doc_data
        }

    # 2. First Pass: Create isolated Blueprints and Steps
    for name, data in step_data_map.items():
        doc_data = data['doc_data']
        sys_prompt = doc_data.get('step_prompt') or f"You are a specialized agent. Your task is to: Execute the {name} reasoning step."
        eval_criteria = doc_data.get('evaluation_prompt') or f"Successfully generated a valid {name} data structure."
        
        bp, _ = CognitiveBlueprint.objects.get_or_create(
            name=f"Workflow: {name}",
            defaults={'description': f"Auto-generated blueprint starting with {name}."}
        )
        
        step_obj, _ = ReasoningStep.objects.get_or_create(
            blueprint=bp,
            name=f"Template: {name}",
            defaults={
                'system_prompt': sys_prompt,
                'output_schema': data['schema_obj'],
                'action_hook': data['action_hook'],
                'is_start_node': True,
                'max_retries': 10,
                'evaluation_criteria': eval_criteria,
            }
        )
        
        # Safely bump max_retries for existing steps without overwriting user prompt edits
        if step_obj.max_retries < 10:
            step_obj.max_retries = 10
            step_obj.save()
            
        created_steps[name] = step_obj

    # 3. Second Pass: Link nodes based on 'Following Nodes' and consolidate Blueprints
    for name, step_obj in created_steps.items():
        following = step_data_map[name]['doc_data'].get('following_nodes', '')
        
        if following and following in created_steps and following.lower() != "none":
            next_step = created_steps[following]
            
            # Draw the edge
            step_obj.on_success_step = next_step
            step_obj.save()
            
            # Consolidate: Move the next step into this step's blueprint
            old_bp = next_step.blueprint
            next_step.blueprint = step_obj.blueprint
            next_step.is_start_node = False
            next_step.save()
            
            # Rename the newly merged blueprint to reflect the pipeline
            bp = step_obj.blueprint
            if bp.name.startswith("Pipeline: "):
                bp.name = f"{bp.name} -> {following}"
            else:
                bp.name = f"Pipeline: {name} -> {following}"
            bp.description = "Auto-generated pipeline sequence."
            bp.save()
            
            # Clean up the orphaned blueprint
            if old_bp.steps.count() == 0:
                old_bp.delete()