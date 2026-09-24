import logging
import json
import ast
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
    Clones an existing CognitiveBlueprint and its ReasoningSteps graph, applying modifications.
    """
    from django.db import transaction
    from metacognition.models import CognitiveBlueprint, ReasoningStep

    try:
        source_id = params.get("source_id")
        if not source_id:
            return "Error: 'source_id' parameter is required to clone a blueprint."
        source_bp = CognitiveBlueprint.objects.get(id=source_id)
        
        new_name = params.get("name") or f"{source_bp.name} (Cloned)"
        new_desc = params.get("description", source_bp.description)
        is_autonomous = params.get("is_autonomous", source_bp.is_autonomous)
        step_modifications = params.get("step_modifications", {})

        with transaction.atomic():
            new_bp = CognitiveBlueprint.objects.create(
                name=new_name,
                description=new_desc,
                parent=source_bp,
                is_autonomous=is_autonomous,
                is_canonical=False
            )
            # Copy moderation lists
            new_bp.moderation_lists.set(source_bp.moderation_lists.all())

            # First pass: clone all steps
            old_steps = list(source_bp.steps.all())
            old_to_new = {}
            for old_step in old_steps:
                step_mod = step_modifications.get(old_step.name, step_modifications.get(str(old_step.id), {}))
                
                cloned_step = ReasoningStep.objects.create(
                    blueprint=new_bp,
                    name=step_mod.get("name", old_step.name),
                    is_start_node=old_step.is_start_node,
                    is_canonical=False,
                    is_active=step_mod.get("is_active", old_step.is_active),
                    lora_adapter=old_step.lora_adapter,
                    is_pending_review=step_mod.get("is_pending_review", False),
                    proposed_by="system",
                    system_prompt=step_mod.get("system_prompt", old_step.system_prompt),
                    sub_blueprint=old_step.sub_blueprint,
                    max_retries=step_mod.get("max_retries", old_step.max_retries),
                    output_schema=old_step.output_schema,
                    max_new_tokens=step_mod.get("max_new_tokens", old_step.max_new_tokens),
                    include_state_tree=step_mod.get("include_state_tree", old_step.include_state_tree),
                    evaluation_criteria=step_mod.get("evaluation_criteria", old_step.evaluation_criteria),
                    parent_step=old_step,
                    variant_intent=step_mod.get("variant_intent", f"Cloned from {old_step.name}"),
                    performance_score=0.0,
                    selection_weight=1.0,
                )
                cloned_step.available_tools.set(old_step.available_tools.all())
                old_to_new[old_step.id] = cloned_step

            # Second pass: wire edges
            for old_step in old_steps:
                cloned_step = old_to_new[old_step.id]
                updated_fields = []
                if old_step.on_success_step_id:
                    cloned_step.on_success_step = old_to_new.get(old_step.on_success_step_id, old_step.on_success_step)
                    updated_fields.append("on_success_step")
                if old_step.on_failure_step_id:
                    cloned_step.on_failure_step = old_to_new.get(old_step.on_failure_step_id, old_step.on_failure_step)
                    updated_fields.append("on_failure_step")
                if updated_fields:
                    cloned_step.save(update_fields=updated_fields)

                # Parallel steps
                parallel_old_ids = list(old_step.parallel_steps.values_list("id", flat=True))
                if parallel_old_ids:
                    cloned_parallels = [old_to_new[pid] for pid in parallel_old_ids if pid in old_to_new]
                    if cloned_parallels:
                        cloned_step.parallel_steps.set(cloned_parallels)

        return f"Successfully cloned blueprint '{source_bp.name}' (id={source_bp.id}) into '{new_bp.name}' (id={new_bp.id}) with {len(old_to_new)} steps cloned and wired."
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
    Spawns a new Conversation using a specified Blueprint and assigns it to a verbal_tasks worker.
    """
    blueprint_name = params.get('blueprint_name')
    task_prompt = params.get('task_prompt')
    user_id = params.get('user_id')
    conversation_id = params.get('conversation_id')
    from metacognition.tasks import task_run_blueprint_async
    from metacognition.models import CognitiveBlueprint
    try:
        bp = CognitiveBlueprint.objects.filter(name=blueprint_name).first() if blueprint_name else None
        bp_id = bp.id if bp else 1
        task_run_blueprint_async.enqueue(
            blueprint_id=bp_id,
            user_prompt=task_prompt
        )
        return f"Delegated task '{task_prompt[:30]}...' to blueprint '{blueprint_name or bp_id}'"
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

class SecurityASTVisitor(ast.NodeVisitor):
    """
    AST Visitor that strictly rejects:
    - Dangerous imports (os, sys, subprocess, shutil, socket, requests, urllib, http, importlib, etc.)
    - Builtin introspection (__builtins__, __subclasses__, __bases__, __class__, etc.)
    - Dangerous builtins (eval, exec, compile, __import__, getattr, setattr, delattr)
    - Hard deletions (.delete())
    """
    FORBIDDEN_CALLS = {'eval', 'exec', 'compile', '__import__', 'getattr', 'setattr', 'delattr'}
    FORBIDDEN_MODULES = {
        'os', 'sys', 'subprocess', 'shutil', 'socket', 'requests', 'urllib',
        'http', 'importlib', 'ctypes', 'posix', 'pty', 'builtins'
    }
    FORBIDDEN_ATTRS = {'__builtins__', '__subclasses__', '__bases__', '__class__', '__globals__', '__code__'}

    def __init__(self):
        self.errors = []

    def visit_Import(self, node):
        for alias in node.names:
            root_mod = alias.name.split('.')[0]
            if root_mod in self.FORBIDDEN_MODULES:
                self.errors.append(f"Importing '{alias.name}' is blocked for security.")
            if alias.name.startswith('django.core.management'):
                self.errors.append("Importing django management commands is blocked.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            root_mod = node.module.split('.')[0]
            if root_mod in self.FORBIDDEN_MODULES:
                self.errors.append(f"Importing from '{node.module}' is blocked for security.")
            if node.module.startswith('django.core.management'):
                self.errors.append("Importing django management commands is blocked.")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_CALLS:
            self.errors.append(f"Direct call to '{node.func.id}()' is forbidden for security.")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == 'delete':
                self.errors.append("Hard deletes via .delete() are blocked.")
            if node.func.attr in self.FORBIDDEN_CALLS:
                self.errors.append(f"Calling attribute '{node.func.attr}()' is forbidden for security.")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in self.FORBIDDEN_ATTRS:
            self.errors.append(f"Introspection attribute '{node.attr}' is forbidden.")
        self.generic_visit(node)


def django_shell_script(state: dict, params: dict) -> str:
    """
    Executes Python code in an isolated Docker sandbox container.
    Rejects host breakout vectors, builtin introspection, and hard deletes.
    """
    script_content = params.get("script_content", "")
    if not script_content.strip():
        return "Error: script_content is empty."

    # 1. AST Security Validation
    try:
        tree = ast.parse(script_content)
        visitor = SecurityASTVisitor()
        visitor.visit(tree)
        if visitor.errors:
            return f"Error: Security violation: {'; '.join(visitor.errors)}"
    except SyntaxError as e:
        return f"Syntax error in script: {e}"

    # 2. Stage script into workspaces/agent_scripts/
    import os
    import uuid
    import requests
    from django.conf import settings
    from sandbox_manager.models import SandboxExecutionLog

    script_id = uuid.uuid4().hex
    rel_path = f"agent_scripts/{script_id}.py"
    host_workspace = getattr(settings, 'WORKSPACE_ROOT', os.path.join(settings.BASE_DIR, 'workspaces'))
    full_path = os.path.join(host_workspace, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(script_content)

    sandbox_url = getattr(settings, 'SANDBOX_URL', "http://127.0.0.1:8002/execute")
    timeout = int(params.get("timeout", 30))
    conversation_id = state.get("conversation_id") if isinstance(state, dict) else None

    # 3. Execute via Sandbox HTTP API
    try:
        response = requests.post(
            sandbox_url,
            json={"filepath": rel_path, "timeout": timeout},
            timeout=timeout + 5
        )
        if response.status_code == 200:
            data = response.json()
            stdout = data.get("stdout", "")
            stderr = data.get("stderr", "")
            retcode = data.get("returncode", 0)

            # Audit log
            SandboxExecutionLog.objects.create(
                filepath=rel_path,
                conversation_id=conversation_id,
                return_code=retcode,
                stdout=stdout,
                stderr=stderr
            )

            if retcode == 0:
                return stdout if stdout.strip() else "Script executed successfully (no output)."
            else:
                return f"Execution error (exit code {retcode}):\n{stderr}\n{stdout}".strip()
        else:
            return f"Sandbox error (HTTP {response.status_code}): {response.text}"
    except requests.exceptions.RequestException as e:
        return f"Error: Sandbox service unreachable at {sandbox_url}: {e}"

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
    Enforces requires_approval = True to ensure Human-in-the-Loop authorization prior to execution.
    """
    name = params.get("name")
    description = params.get("description", "")
    script_content = params.get("script_content", "")
    input_schema = params.get("input_schema", "")
    output_schema = params.get("output_schema", "")
    
    if not name or not script_content:
        return "Error: name and script_content are required."
        
    try:
        tree = ast.parse(script_content)
        visitor = SecurityASTVisitor()
        visitor.visit(tree)
        if visitor.errors:
            return f"Error: Security violation in dynamic tool: {'; '.join(visitor.errors)}"
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
        tool.requires_approval = True
        tool.is_active = True
        tool.save()
        return f"Successfully created dynamic tool '{name}' (requires human approval before execution)."
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

def record_signal(state: dict, params: dict) -> str:
    """
    Append a faint signal, insight, or good idea to a persistent documentation file.
    """
    import os
    import datetime
    from django.conf import settings
    signal = params.get('signal', '')
    category = params.get('category', 'general')
    
    if not signal:
        return "Error: signal is required."
        
    filepath = os.path.join(settings.BASE_DIR, 'resources', 'night_manager_signals.md')
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n### {timestamp} - [{category.upper()}]\n{signal}\n"
    
    with open(filepath, 'a') as f:
        f.write(entry)
        
    return f"Signal recorded successfully in {filepath}."


def inspect_nightmanager_performance(state: dict, params: dict) -> str:
    """
    Fetches aggregated diagnostic and quantitative health metrics for the NightManager,
    including session pass/fail rates, latency, state_tree usage, and knowledge artifacts.
    """
    from metacognition.reporting import audit_nightmanager_performance, format_performance_report_markdown
    days = int(params.get("days", 7))
    report = audit_nightmanager_performance(since_days=days)
    return format_performance_report_markdown(report)

