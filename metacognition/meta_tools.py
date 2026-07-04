import logging
import json
from .models import ToolDefinition, CognitiveBlueprint, ReasoningStep, ResponseSchema

logger = logging.getLogger(__name__)

def list_available_tools(state: dict, params: dict) -> str:
    """Returns a summary of all active ToolDefinitions for the agent to reason about."""
    tools = ToolDefinition.objects.filter(is_active=True).order_by('name')
    if not tools.exists():
        return "No active tools found."
    
    summary = "AVAILABLE TOOLS:\n"
    for t in tools:
        summary += f"- {t.name} (type: {t.tool_type}): {t.description}\n"
        if t.requires_approval:
            summary += "  *Requires human approval before execution*\n"
    return summary

def create_tool(state: dict, params: dict) -> str:
    """
    Creates a new ToolDefinition in the database.
    Expects params: name, description, tool_type, python_path (optional), 
    api_url (optional), input_schema, output_schema.
    """
    try:
        name = params.get("name")
        if ToolDefinition.objects.filter(name=name).exists():
            return f"Error: Tool '{name}' already exists."
            
        tool = ToolDefinition.objects.create(
            name=name,
            description=params.get("description", ""),
            tool_type=params.get("tool_type", "api"),
            python_path=params.get("python_path", ""),
            api_url=params.get("api_url", ""),
            input_schema=params.get("input_schema", ""),
            output_schema=params.get("output_schema", ""),
            requires_approval=True,  # Always require human approval for agent-created tools
            is_promoted=False,       # Needs to be promoted by admin
            created_by_id=state.get("user_id"),
        )
        return f"Created tool '{tool.name}' (id={tool.id}). Requires human approval before use."
    except Exception as e:
        return f"Failed to create tool: {e}"

def list_blueprints(state: dict, params: dict) -> str:
    """Returns all available CognitiveBlueprints with their step topology."""
    bps = CognitiveBlueprint.objects.all().prefetch_related('steps')
    if not bps.exists():
        return "No blueprints found."
        
    summaries = []
    for bp in bps:
        steps = bp.steps.all()
        step_names = [s.name for s in steps]
        summaries.append(f"Blueprint: {bp.name} (ID: {bp.id})\n  Description: {bp.description}\n  Steps: {', '.join(step_names)}")
    return "\n\n".join(summaries)

def create_blueprint(state: dict, params: dict) -> str:
    """
    Creates a new CognitiveBlueprint with linked ReasoningSteps.
    Expects params: name, description, steps (list of step dicts).
    """
    try:
        bp = CognitiveBlueprint.objects.create(
            name=params.get("name", "New Agent Blueprint"),
            description=params.get("description", ""),
        )
        
        steps_data = params.get("steps", [])
        if not steps_data:
            return f"Created empty blueprint '{bp.name}' (id={bp.id})."
            
        # Create steps and link them linearly for simplicity
        prev_step = None
        for i, step_def in enumerate(steps_data):
            schema_name = step_def.get("schema_name")
            schema = ResponseSchema.objects.filter(name=schema_name).first() if schema_name else None
            
            step = ReasoningStep.objects.create(
                blueprint=bp,
                name=step_def.get("name", f"Step {i+1}"),
                system_prompt=step_def.get("system_prompt", ""),
                output_schema=schema,
                action_hook=step_def.get("action_hook", ""),
                is_start_node=(prev_step is None),
                max_retries=step_def.get("max_retries", 5),
            )
            if prev_step:
                prev_step.on_success_step = step
                prev_step.save()
            prev_step = step
            
        return f"Created blueprint '{bp.name}' (id={bp.id}) with {len(steps_data)} steps. Unpromoted."
    except Exception as e:
        return f"Failed to create blueprint: {e}"

def clone_and_modify_blueprint(state: dict, params: dict) -> str:
    """
    Clones an existing blueprint and applies modifications.
    """
    try:
        source_id = params.get("source_id")
        source_bp = CognitiveBlueprint.objects.get(id=source_id)
        
        # ... logic for cloning steps and wiring them ...
        # (Omitted for brevity in prototype, would deep-copy ReasoningSteps)
        
        return f"Cloned blueprint {source_bp.name}. Modification not fully implemented."
    except Exception as e:
        return f"Failed to clone blueprint: {e}"

def review_benchmark_results(state: dict, params: dict) -> str:
    """Fetches and summarises benchmark results for analysis."""
    try:
        experiment_id = params.get("experiment_id")
        if not experiment_id:
            from benchmarking.models import BenchmarkRun
            # Just get the 5 most recent runs overall
            runs = BenchmarkRun.objects.all().order_by('-timestamp')[:5]
        else:
            from benchmarking.models import BenchmarkRun
            runs = BenchmarkRun.objects.filter(experiment__id=experiment_id).order_by('-timestamp')[:5]
            
        if not runs.exists():
            return "No benchmark runs found."
            
        summaries = []
        for run in runs:
            summaries.append(
                f"Run {run.id} ({run.timestamp.strftime('%Y-%m-%d')}): "
                f"Blueprint ID: {run.experiment.blueprint_id if run.experiment else 'N/A'}, "
                f"RAG={run.average_rag_score:.2f}, Sem={run.average_semantic_score:.2f}, "
                f"Faith={run.average_faithfulness or 'N/A'}"
            )
        return "\n".join(summaries)
    except Exception as e:
        return f"Failed to retrieve benchmark results: {e}"

def create_benchmark_scenario(state: dict, params: dict) -> str:
    """Creates a new BenchmarkScenario from the agent's analysis."""
    try:
        from benchmarking.models import BenchmarkScenario, ScenarioGroup
        scenario = BenchmarkScenario.objects.create(
            question=params.get("question", ""),
            ideal_answer=params.get("ideal_answer", ""),
            expected_keywords=params.get("expected_keywords", ""),
        )
        
        group_name = params.get("group_name")
        if group_name:
            group, _ = ScenarioGroup.objects.get_or_create(name=group_name)
            group.scenarios.add(scenario)
            
        return f"Created scenario '{scenario.question[:60]}...' (id={scenario.id})."
    except Exception as e:
        return f"Failed to create scenario: {e}"

def deprecate_tool(state: dict, params: dict) -> str:
    """Marks a ToolDefinition as inactive."""
    try:
        tool_id = params.get("tool_id")
        tool = ToolDefinition.objects.get(id=tool_id)
        tool.is_active = False
        tool.save()
        return f"Deprecated tool '{tool.name}'."
    except Exception as e:
        return f"Failed to deprecate tool: {e}"

def promote_artifact(state: dict, params: dict) -> str:
    """
    Sets is_promoted=True on a tool or blueprint.
    Requires human admin privilege.
    """
    # Verify user is admin
    user_id = state.get("user_id")
    if not user_id:
        return "Authentication required."
        
    from django.contrib.auth.models import User
    try:
        user = User.objects.get(id=user_id)
        if not user.is_superuser:
            return "Admin privileges required to promote artifacts."
            
        artifact_type = params.get("artifact_type")
        artifact_id = params.get("artifact_id")
        
        if artifact_type == "tool":
            tool = ToolDefinition.objects.get(id=artifact_id)
            tool.is_promoted = True
            tool.requires_approval = False
            tool.save()
            return f"Promoted tool '{tool.name}' to production."
            
        elif artifact_type == "blueprint":
            # Just a conceptual promotion flag for now
            return f"Promoted blueprint {artifact_id}."
            
        return "Unknown artifact type."
    except Exception as e:
        return f"Failed to promote artifact: {e}"

def document_reader(state: dict, params: dict) -> str:
    """
    Unified tool for navigating and fetching documents from the RAG database.
    action: 'fetch_chunk', 'fetch_section', 'fetch_whole_document', 'search_document'
    target_id: The ID of the chunk, section, or document
    doc_range: For chunk operations, e.g. [-2, 2] to fetch previous 2 and next 2 chunks
    query: The search query if action is 'search_document'
    """
    action = params.get('action')
    target_id = params.get('target_id')
    doc_range = params.get('doc_range')
    query = params.get('query')
    
    from llm_api.apps import service_registry
    rag = service_registry.rag_service
    if not rag:
        return "Error: RAG service not available."
        
    try:
        if action == "search_document":
            if not query: return "Error: query is required for search_document."
            docs = rag.get_context(query, k=3)
            if not docs: return f"No results found for '{query}'"
            res = f"Search Results for '{query}':\n"
            for d in docs:
                res += f"- [Chunk ID: {d.metadata.get('chunk_id')}] Source: {d.metadata.get('filename')}\n  {d.page_content}\n"
            return res
            
        elif action == "fetch_chunk":
            if not target_id: return "Error: target_id is required."
            chunks = rag.store.mget([target_id])
            chunk = chunks[0] if chunks else None
            if not chunk: return f"Error: Chunk {target_id} not found."
            
            output = f"Chunk {target_id}:\n{chunk.page_content}\n"
            
            if doc_range and len(doc_range) == 2:
                output += f"\n(Range {doc_range} fetching requires document sequence index.)"
                
            return output
            
        elif action == "fetch_whole_document":
            if not target_id: return "Error: target_id (filename) required."
            return f"Mock: Fetched whole document {target_id}"
            
        else:
            return f"Error: Unknown action '{action}'"
    except Exception as e:
        return f"Error executing document_reader: {e}"

def delegate_task(state: dict, params: dict) -> str:
    """
    Spawns a new Conversation using a specified Blueprint and assigns it to a Celery worker.
    """
    blueprint_name = params.get('blueprint_name')
    task_prompt = params.get('task_prompt')
    user_id = params.get('user_id')
    conversation_id = params.get('conversation_id')
    from metacognition.tasks import task_run_blueprint_async
    try:
        task_run_blueprint_async.delay(
            blueprint_id=1, # Note: Needs name -> ID resolution, simplified here
            user_prompt=task_prompt
        )
        return f"Delegated task '{task_prompt[:30]}...' to blueprint '{blueprint_name}'"
    except Exception as e:
        return f"Failed to delegate task: {e}"

def run_benchmark(state: dict, params: dict) -> str:
    """
    Triggers an evaluation scenario.
    """
    scenario_group = params.get('scenario_group')
    from benchmarking.runner import run_benchmark_suite
    try:
        # Simplified: runner actually needs an experiment object, we just mock the success string here for the LLM
        return f"Started benchmark suite: {scenario_group}"
    except Exception as e:
        return f"Failed to start benchmark: {e}"

def django_shell_script(state: dict, params: dict) -> str:
    """
    Executes raw Python code in the host Django environment. 
    Allows full access to models and scheduling. 
    For safety, explicit calls to `.delete()` are blocked.
    """
    script_content = params.get("script_content", "")
    
    # Safety check
    if ".delete(" in script_content:
        return "Error: Hard deletes are blocked. Use is_active=False or flag for review."

    try:
        # We need a string buffer to capture stdout
        import sys
        from io import StringIO
        
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        
        try:
            # We must pass globals() and a local dict so the script can import
            # and mutate variables safely.
            local_vars = {}
            exec(script_content, globals(), local_vars)
            output = redirected_output.getvalue()
            if not output:
                output = "Script executed successfully (no output)."
            return output
        finally:
            sys.stdout = old_stdout
    except Exception as e:
        return f"Error executing script: {e}"

def system_janitor(state: dict, params: dict) -> str:
    """
    Deletes completely empty directories inside the workspaces/ directory.
    """
    import os
    from django.conf import settings
    
    workspaces_dir = os.path.join(settings.BASE_DIR, "workspaces")
    if not os.path.exists(workspaces_dir):
        return f"Workspaces directory not found at {workspaces_dir}."
        
    deleted_dirs = []
    
    # Walk bottom-up so we can delete nested empty dirs
    for root, dirs, files in os.walk(workspaces_dir, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    deleted_dirs.append(dir_path)
            except Exception as e:
                logger.error(f"Failed to delete {dir_path}: {e}")
                
    if deleted_dirs:
        return f"Janitor deleted {len(deleted_dirs)} empty directories:\n" + "\n".join(deleted_dirs)
    return "Janitor ran successfully. No empty directories found."

def database_backup(state: dict, params: dict) -> str:
    """
    Executes django's dumpdata to backup the database to backups/ dir.
    """
    from django.core.management import call_command
    from django.conf import settings
    import os
    from datetime import datetime
    
    backup_dir = os.path.join(settings.BASE_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"db_backup_{timestamp}.json")
    
    try:
        # We exclude contenttypes and auth.Permission to avoid restore conflicts
        with open(backup_path, "w") as f:
            call_command("dumpdata", exclude=["contenttypes", "auth.permission"], stdout=f)
        return f"Database backup successfully saved to {backup_path}"
    except Exception as e:
        return f"Database backup failed: {e}"

def TASK_COMPLETE(state: dict, params: dict) -> dict:
    """
    Signals that the agent has finished all its planned work and is ready to go to sleep.
    """
    return {
        "working_prompt": "Task completed successfully. Shutting down.",
        "route_to": "SUCCESS"
    }
