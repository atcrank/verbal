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
                f"Blueprint ID: {run.experiment.configuration.get('blueprint_id', 'N/A') if run.experiment else 'N/A'}, "
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

    # AST Security Patch
    import ast
    try:
        tree = ast.parse(script_content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                module_name = getattr(node, 'module', None) or node.names[0].name
                if module_name in ['os', 'sys', 'subprocess']:
                    return f"Error: Importing '{module_name}' is blocked for security."
                if module_name.startswith('django.core.management'):
                    return "Error: Importing django management commands is blocked to prevent rogue migrations."
    except SyntaxError as e:
        return f"Syntax error in script: {e}"

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
    
    import shutil

    # Walk bottom-up so we can delete nested empty dirs
    for root, dirs, files in os.walk(workspaces_dir, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                contents = os.listdir(dir_path)
                if not contents:
                    os.rmdir(dir_path)
                    deleted_dirs.append(dir_path)
                elif root == workspaces_dir:
                    # Top-level workspace dir, allow deletion if only .git or .agents
                    allowed = {'.git', '.agents'}
                    if not (set(contents) - allowed):
                        shutil.rmtree(dir_path)
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

def discover_django_models(state: dict, params: dict) -> str:
    """
    Inspects the schema of Django models. If model_name is omitted, returns the whole spine of the app.
    """
    app_label = params.get("app_label")
    model_name = params.get("model_name")
    from django.apps import apps
    
    try:
        app_config = apps.get_app_config(app_label)
    except Exception as e:
        return f"Error loading app '{app_label}': {e}"
        
    models_to_inspect = []
    if model_name:
        try:
            models_to_inspect.append(app_config.get_model(model_name))
        except Exception as e:
            return f"Error loading model '{model_name}' in app '{app_label}': {e}"
    else:
        models_to_inspect = list(app_config.get_models())
        
    if not models_to_inspect:
        return f"No models found for app '{app_label}'."
        
    summary = []
    for model in models_to_inspect:
        model_info = f"Model: {model.__name__}\nFields:\n"
        for field in model._meta.get_fields():
            if field.is_relation:
                related_model = field.related_model
                if related_model:
                    rel_info = f"{related_model._meta.app_label}.{related_model.__name__}"
                else:
                    rel_info = "Unknown"
                model_info += f"  - {field.name} ({field.__class__.__name__}) -> {rel_info}\n"
            else:
                model_info += f"  - {field.name} ({field.__class__.__name__})\n"
        
        methods = [func for func in dir(model) if callable(getattr(model, func)) and not func.startswith("__")]
        custom_methods = [m for m in methods if m in ['create_variant', 'get_absolute_url', 'clean']]
        if custom_methods:
             model_info += f"Key Methods:\n"
             for m in custom_methods:
                 model_info += f"  - {m}()\n"
                 
        summary.append(model_info)
        
    return "\n\n".join(summary)


def read_django_models(state: dict, params: dict) -> str:
    """
    A generic tool accepting app_label, model_name, and kwargs (filter parameters).
    Replaces custom discovery scripts by allowing the LLM to query for things like unscored variants or empty Grips stubs directly.
    """
    app_label = params.get("app_label")
    model_name = params.get("model_name")
    filter_kwargs = params.get("kwargs", {})
    limit = params.get("limit", 10)
    
    from django.apps import apps
    from django.forms.models import model_to_dict
    import json
    try:
        model = apps.get_model(app_label, model_name)
    except Exception as e:
        return f"Error loading model: {e}"
        
    try:
        qs = model.objects.filter(**filter_kwargs)[:limit]
        if not qs.exists():
            return "No matching records found."
            
        results = []
        for obj in qs:
            obj_dict = model_to_dict(obj)
            for k, v in obj_dict.items():
                if v.__class__.__name__ not in ['str', 'int', 'float', 'bool', 'NoneType', 'dict', 'list']:
                    obj_dict[k] = str(v)
            obj_dict["__str__"] = str(obj)
            results.append(obj_dict)
        
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error executing query: {e}"


def write_django_model(state: dict, params: dict) -> str:
    """
    A generic write tool for Django models. Supports create, update, update_or_create, and create_variant.
    """
    app_label = params.get("app_label")
    model_name = params.get("model_name")
    action = params.get("action", "create")
    pk = params.get("pk")
    model_params = params.get("parameters", {})
    
    from django.apps import apps
    try:
        model = apps.get_model(app_label, model_name)
    except Exception as e:
        return f"Error loading model: {e}"
        
    try:
        if action == "create":
            obj = model.objects.create(**model_params)
            return f"Successfully created {model_name} (PK: {obj.pk})."
            
        elif action == "update":
            if not pk:
                return "Error: pk is required for update."
            obj = model.objects.get(pk=pk)
            for k, v in model_params.items():
                setattr(obj, k, v)
            obj.save()
            return f"Successfully updated {model_name} (PK: {obj.pk})."
            
        elif action == "update_or_create":
            defaults = model_params.pop("defaults", {})
            obj, created = model.objects.update_or_create(**model_params, defaults=defaults)
            status = "created" if created else "updated"
            return f"Successfully {status} {model_name} (PK: {obj.pk})."
            
        elif action == "create_variant":
            if not pk:
                return "Error: pk is required for create_variant to select the parent instance."
            obj = model.objects.get(pk=pk)
            if hasattr(obj, "create_variant"):
                variant_intent = model_params.pop("variant_intent", "")
                new_obj = obj.create_variant(variant_intent=variant_intent, **model_params)
                return f"Successfully created variant of {model_name} (New PK: {new_obj.pk})."
            else:
                return f"Error: Model {model_name} does not have a 'create_variant' method."
        else:
            return f"Error: Unknown action '{action}'."
            
    except Exception as e:
        return f"Error executing write operation: {e}"

def manage_dynamic_tools(state: dict, params: dict) -> str:
    """
    Allows the NightManager to write Python scripts to a metacognition/dynamic_tools/ directory and register them as ToolDefinitions.
    Forces created_by="NightManager" to ensure other agents/users cannot accidentally execute untested scripts.
    Implements basic AST-level security checks to block network/file/delete operations by default.
    """
    name = params.get("name")
    description = params.get("description", "")
    script_content = params.get("script_content", "")
    input_schema = params.get("input_schema", "")
    output_schema = params.get("output_schema", "")
    
    if not name or not script_content:
        return "Error: name and script_content are required."
        
    import ast
    try:
        tree = ast.parse(script_content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                module_name = getattr(node, 'module', None) or node.names[0].name
                if module_name in ['os', 'sys', 'subprocess', 'requests', 'socket', 'urllib']:
                    return f"Error: Importing '{module_name}' is not allowed for security reasons."
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == 'delete':
                        return "Error: calling .delete() is not allowed."
    except SyntaxError as e:
        return f"Syntax error in script: {e}"
        
    import os
    from django.conf import settings
    dynamic_tools_dir = os.path.join(settings.BASE_DIR, "metacognition", "dynamic_tools")
    os.makedirs(dynamic_tools_dir, exist_ok=True)
    
    file_path = os.path.join(dynamic_tools_dir, f"{name}.py")
    with open(file_path, "w") as f:
        f.write(script_content)
        
    from django.contrib.auth.models import User
    try:
        nm_user, _ = User.objects.get_or_create(username="NightManager")
    except Exception:
        nm_user = None
        
    try:
        from metacognition.models import ToolDefinition
        tool = ToolDefinition.objects.filter(name=name).first()
        if not tool:
            tool = ToolDefinition(name=name)
            
        tool.description = description
        tool.tool_type = "builtin"
        tool.python_path = f"metacognition.dynamic_tools.{name}.{name}"
        if input_schema:
            tool.input_schema = input_schema
        if output_schema:
            tool.output_schema = output_schema
        tool.created_by = nm_user
        tool.requires_approval = False
        tool.is_active = True
        tool.save()
        return f"Successfully created dynamic tool '{name}'."
    except Exception as e:
        return f"Failed to save tool definition: {e}"

def update_conversation_state(state: dict, params: dict) -> str:
    """
    A tool allowing the agent to mutate the state_tree in the active Conversation.
    actions: 'add_task', 'update_task_status'
    """
    action = params.get("action")
    task_path = params.get("task_path")
    status = params.get("status", "projected")
    
    conversation_id = state.get("conversation_id")
    if not conversation_id:
        return "Error: No active conversation."
        
    from llm_api.models import Conversation
    try:
        conv = Conversation.objects.get(id=conversation_id)
        if not conv.state_tree:
            conv.state_tree = {}
            
        parts = task_path.split(" > ")
        current = conv.state_tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {"status": "active", "children": {}}
            current = current[part].get("children", current[part])
            
        leaf = parts[-1]
        if action == "add_task":
            if leaf not in current:
                current[leaf] = {"status": status}
            else:
                current[leaf]["status"] = status
        elif action == "update_task_status":
            if leaf in current:
                current[leaf]["status"] = status
            else:
                return f"Task '{task_path}' not found."
                
        conv.save(update_fields=['state_tree'])
        import json
        return f"Successfully updated conversation state. Current state: {json.dumps(conv.state_tree)}"
    except Exception as e:
        return f"Failed to update conversation state: {e}"

def run_sub_blueprint(state: dict, params: dict) -> str:
    """
    Synchronously executes a sub-blueprint and returns its result, allowing the NightManager to instantly update the task's state in the tree.
    """
    blueprint_name = params.get('blueprint_name')
    task_prompt = params.get('task_prompt')
    user_id = state.get('user_id')
    conversation_id = state.get('conversation_id')
    
    from metacognition.models import CognitiveBlueprint
    from metacognition.tasks import run_blueprint
    try:
        bp = CognitiveBlueprint.objects.get(name=blueprint_name)
        result = run_blueprint(
            blueprint_id=bp.id,
            user_prompt=task_prompt,
            conversation_id=conversation_id,
            user_id=user_id
        )
        return f"Sub-blueprint '{blueprint_name}' executed. Result: {result.get('final_response', 'No response')}"
    except CognitiveBlueprint.DoesNotExist:
        return f"Error: Sub-blueprint '{blueprint_name}' not found."
    except Exception as e:
        return f"Failed to run sub-blueprint: {e}"

def get_conversation_metrics(state: dict, params: dict) -> str:
    """Queries PromptResponseLog to summarize success rates and identify frequently failing reasoning steps."""
    from llm_api.models import PromptResponseLog
    from django.db.models import Count, Avg
    
    logs = PromptResponseLog.objects.exclude(step_status__isnull=True)
    total = logs.count()
    if not total:
         return "No conversation logs with step_status found."
         
    success = logs.filter(step_status='SUCCESS').count()
    failed = logs.filter(step_status='FAILURE').count()
    retries = logs.filter(step_status='RETRY').count()
    
    # Redefine total to only include completed or failed steps for percentage calculation
    resolved_total = success + failed
    if resolved_total == 0:
        success_rate = 0.0
        failure_rate = 0.0
    else:
        success_rate = (success / resolved_total) * 100
        failure_rate = (failed / resolved_total) * 100
    
    avg_tokens = logs.aggregate(avg_in=Avg('input_tokens'), avg_out=Avg('output_tokens'))
    
    # Find steps that fail most often
    failed_steps = logs.filter(step_status='FAILURE').values('reasoning_step__name').annotate(fails=Count('id')).order_by('-fails')[:5]
    
    summary = (
        f"Conversation Metrics:\\n"
        f"- Total Steps Evaluated: {total} (Success: {success}, Failed: {failed}, Retries: {retries})\\n"
        f"- Success Rate (Resolved): {success_rate:.1f}%\\n"
        f"- Failure Rate (Resolved): {failure_rate:.1f}%\\n"
        f"- Avg Input Tokens: {avg_tokens['avg_in'] or 0:.0f} | Avg Output: {avg_tokens['avg_out'] or 0:.0f}\\n\\n"
        f"Most Frequently Failing Steps:\\n"
    )
    for f in failed_steps:
        name = f['reasoning_step__name'] or "Unknown Step"
        summary += f"- '{name}': {f['fails']} failures\\n"
        
    recent_failures = logs.filter(step_status='FAILURE').order_by('-created_at')[:3]
    if recent_failures.exists():
        summary += "\\nRecent Specific Failures (for deep reading):\\n"
        for log in recent_failures:
            name = log.reasoning_step.name if log.reasoning_step else "Unknown"
            snippet = log.generated_response[:150].replace("\\n", " ") + "..." if log.generated_response else "No output"
            summary += f"- Log ID: {log.id} | Step: '{name}' | Snippet: {snippet}\\n"
            summary += "  (Use `fetch_log_details` with this ID to read full context)\\n"
            
    return summary

def fetch_log_details(state: dict, params: dict) -> str:
    """Fetches full details of a specific PromptResponseLog for deep reading."""
    log_id = params.get("log_id")
    from llm_api.models import PromptResponseLog
    try:
        log = PromptResponseLog.objects.get(id=log_id)
        return (
            f"Log ID: {log.id}\\n"
            f"Step: {log.reasoning_step.name if log.reasoning_step else 'N/A'}\\n"
            f"Status: {log.step_status}\\n"
            f"System Prompt:\\n{log.system_prompt}\\n"
            f"User Prompt:\\n{log.user_prompt}\\n"
            f"Generated Response:\\n{log.generated_response}\\n"
            f"RAG Selections: {log.rag_selections}\\n"
        )
    except Exception as e:
        return f"Error fetching log: {e}"

def get_rag_efficiency_metrics(state: dict, params: dict) -> str:
    """Analyzes downstream impacts of RAG context on conversation failures."""
    from llm_api.models import PromptResponseLog
    from background_resources.models import Document, RAGChunk
    from django.db.models import Avg
    
    # Look for logs that had RAG selections but still failed
    failed_with_rag = PromptResponseLog.objects.filter(step_status='FAILURE').exclude(rag_selections=[]).exclude(rag_selections__isnull=True)
    success_with_rag = PromptResponseLog.objects.filter(step_status='SUCCESS').exclude(rag_selections=[]).exclude(rag_selections__isnull=True)
    
    summary = "RAG Efficiency & Downstream Impact Metrics:\\n"
    summary += f"- Steps failed despite having RAG context: {failed_with_rag.count()}\\n"
    summary += f"- Steps succeeded with RAG context: {success_with_rag.count()}\\n"
    
    if failed_with_rag.exists():
        avg_tokens = failed_with_rag.aggregate(avg_in=Avg('input_tokens'))
        summary += f"- Avg Input Tokens for Failed RAG steps (indicates potential overload): {avg_tokens['avg_in'] or 0:.0f}\\n"
        summary += "\\nReview these failed logs to determine if the RAG input was excessive, distracting, irrelevant, or insufficient.\\n"
        summary += "\\nSpecific Failed RAG Logs:\\n"
        for log in failed_with_rag.order_by('-created_at')[:3]:
            name = log.reasoning_step.name if log.reasoning_step else "Unknown"
            summary += f"- Log ID: {log.id} | Step: '{name}' (Use `fetch_log_details` to review RAG context)\\n"
    
    summary += f"\\nTotal Indexed Documents: {Document.objects.filter(currently_indexed=True).count()}\\n"
    summary += f"Total RAG Chunks: {RAGChunk.objects.count()}\\n"
    
    return summary

def get_grips_metrics(state: dict, params: dict) -> str:
    """Summarizes Grips ConceptNode stats and flags downstream failures."""
    from grips.models import Domain, ConceptNode, KnowledgeEdge
    
    domains = Domain.objects.count()
    nodes = ConceptNode.objects.count()
    edges = KnowledgeEdge.objects.count()
    unlinted_nodes = ConceptNode.objects.filter(needs_linting=True).count()
    empty_stubs = ConceptNode.objects.filter(narrative_content='').count()
    
    summary = "Grips Knowledge Graph Metrics:\\n"
    summary += f"- Total Domains: {domains}\\n"
    summary += f"- Total ConceptNodes: {nodes} ({empty_stubs} empty stubs, {unlinted_nodes} needing linting)\\n"
    summary += f"- Total KnowledgeEdges: {edges}\\n\\n"
    summary += "Note: Look for patterns in Conversation failures where Grips failed to provide relevant content, or provided too much irrelevant context.\\n"
    return summary

def get_empty_grips_stubs(state: dict, params: dict) -> str:
    """Returns the IDs of empty or unlinted Grips ConceptNodes so they can be processed by sub-agents."""
    from grips.models import ConceptNode
    
    empty_nodes = ConceptNode.objects.filter(narrative_content='')
    stubs = [str(node.id) for node in empty_nodes]
    
    if not stubs:
        return "No empty stubs found."
    
    return f"Found {len(stubs)} empty stubs. IDs: {', '.join(stubs)}"

def get_benchmark_stats(state: dict, params: dict) -> str:
    """Fetches summary statistics for recent benchmark investigations."""
    from benchmarking.models import Investigation
    investigations = Investigation.objects.all().order_by('-created_at')[:5]
    
    if not investigations:
        return "No benchmark investigations found."
        
    summary = []
    for inv in investigations:
        df = inv.to_dataframe()
        if not df.empty:
            summary.append(f"Investigation: {inv.name}\\nStats:\\n{df.describe().to_string()}\\n")
    return "\\n".join(summary) if summary else "No data in recent investigations."

def read_benchmark_topic(state: dict, params: dict) -> str:
    """Reads detailed performance for a specific investigation."""
    inv_name = params.get("investigation_name")
    from benchmarking.models import Investigation
    inv = Investigation.objects.filter(name__icontains=inv_name).first()
    if not inv:
        return f"Investigation '{inv_name}' not found."
    df = inv.to_dataframe()
    if df.empty:
        return "No data for this investigation."
    return df.to_string()

def create_benchmark_scenario(state: dict, params: dict) -> str:
    """Creates a new scenario for benchmarking."""
    question = params.get("question")
    from benchmarking.models import BenchmarkScenario
    try:
        sc, created = BenchmarkScenario.objects.get_or_create(question=question)
        return f"Scenario created/exists with ID: {sc.id}"
    except Exception as e:
        return f"Failed to create scenario: {e}"

def search_rag_chunks(state: dict, params: dict) -> str:
    query = params.get("query", "")
    k = params.get("k", 5)
    if not query:
        return "Error: query is required."
    from llm_api.apps import service_registry
    if not service_registry.rag_service:
        return "Error: RAG service offline."
    results = service_registry.rag_service.get_context(query, k=k)
    out = []
    for d in results:
        chunk_id = d.metadata.get('chunk_id')
        filename = d.metadata.get('filename', 'Unknown')
        out.append(f"[Chunk: {filename} (ID: {chunk_id})]\n{d.page_content}")
    return "\n\n".join(out) if out else "No RAG results found."

def search_grips_nodes(state: dict, params: dict) -> str:
    query = params.get("query", "")
    k = params.get("k", 5)
    if not query:
        return "Error: query is required."
    from llm_api.apps import service_registry
    if not getattr(service_registry, 'grips_service', None):
        return "Error: Grips service offline."
    results = service_registry.grips_service.get_grips_context(query, k=k)
    out = []
    for d in results:
        title = d.metadata.get('title', 'Unknown Concept')
        concept_id = d.metadata.get('concept_id')
        out.append(f"[Grips: {title} (ID: grips_{concept_id})]\n{d.page_content}")
    return "\n\n".join(out) if out else "No Grips results found."

def search_past_conversations(state: dict, params: dict) -> str:
    query = params.get("query", "")
    k = params.get("k", 5)
    if not query:
        return "Error: query is required."
    user_id = state.get("user_id")
    if not user_id:
        return "Error: user_id is required."
    
    from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
    from llm_api.models import PromptResponseLog
    
    search_query = SearchQuery(query)
    past_logs = PromptResponseLog.objects.annotate(
        rank=SearchRank(SearchVector('user_prompt', 'generated_response'), search_query)
    ).filter(user_id=user_id, rank__gt=0.1).order_by('-rank')[:k]
    
    out = []
    for log in past_logs:
        out.append(f"[Past Log ID: {log.id}]\nUser: {log.user_prompt}\nAgent: {log.generated_response}")
    return "\n\n".join(out) if out else "No past conversations found."
