import json
from django.apps import apps


# =============================================================================
# Tool Input Schemas
# Each schema tells the LLM exactly what parameters a tool expects.
# Without these, the compiler emits an empty {"type": "object", "properties": {}}
# and the LLM invents plausible but wrong parameters (Finding 2.1).
# =============================================================================

TOOL_SCHEMAS = {
    "list_available_tools": {
        "type": "object",
        "properties": {},
    },
    "create_tool": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Unique name for the new tool."},
            "description": {"type": "string", "description": "What the tool does."},
            "tool_type": {"type": "string", "enum": ["builtin", "api", "blueprint", "django_action"], "description": "The type of tool."},
            "python_path": {"type": "string", "description": "Dotted Python path to the callable (for builtin type)."},
            "input_schema": {"type": "string", "description": "JSON Schema string for the tool's input parameters."},
        },
        "required": ["name", "description", "tool_type"],
    },
    "list_blueprints": {
        "type": "object",
        "properties": {},
    },
    "create_blueprint": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name for the new blueprint."},
            "description": {"type": "string", "description": "Description of what the blueprint does."},
            "steps": {
                "type": "array",
                "description": "List of step definitions, each with 'name', 'system_prompt', and optional 'schema_name'.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "system_prompt": {"type": "string"},
                        "schema_name": {"type": "string"},
                    },
                    "required": ["name", "system_prompt"],
                },
            },
        },
        "required": ["name", "description"],
    },
    "review_benchmark_results": {
        "type": "object",
        "properties": {
            "experiment_id": {"type": "integer", "description": "Optional ID of a specific experiment. Omit to see the 5 most recent runs."},
        },
    },
    "get_conversation_metrics": {
        "type": "object",
        "properties": {},
    },
    "fetch_log_details": {
        "type": "object",
        "properties": {
            "log_id": {"type": "string", "description": "The UUID of the PromptResponseLog to fetch."},
        },
        "required": ["log_id"],
    },
    "get_rag_efficiency_metrics": {
        "type": "object",
        "properties": {},
    },
    "get_grips_metrics": {
        "type": "object",
        "properties": {},
    },
    "create_benchmark_scenario": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The benchmark question."},
            "ideal_answer": {"type": "string", "description": "The ground-truth answer for semantic comparison."},
            "expected_keywords": {"type": "string", "description": "Comma-separated keywords that should appear in retrieved context."},
            "group_name": {"type": "string", "description": "Name of the ScenarioGroup to add this scenario to."},
        },
        "required": ["question", "ideal_answer"],
    },
    "document_reader": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_document", "fetch_chunk", "fetch_whole_document"],
                "description": "The operation to perform.",
            },
            "query": {"type": "string", "description": "Search query (required when action is 'search_document')."},
            "target_id": {"type": "string", "description": "Chunk ID or document filename (required for fetch operations)."},
            "doc_range": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Range of surrounding chunks to fetch, e.g. [-2, 2].",
            },
        },
        "required": ["action"],
    },
    "delegate_task": {
        "type": "object",
        "properties": {
            "blueprint_name": {"type": "string", "description": "Name of the blueprint to delegate to."},
            "task_prompt": {"type": "string", "description": "The prompt/goal to pass to the delegated blueprint."},
        },
        "required": ["blueprint_name", "task_prompt"],
    },
    "run_benchmark": {
        "type": "object",
        "properties": {
            "scenario_group": {"type": "string", "description": "Name of the ScenarioGroup to benchmark."},
        },
        "required": ["scenario_group"],
    },
    "django_shell_script": {
        "type": "object",
        "properties": {
            "script_content": {"type": "string", "description": "Python code to execute in the Django environment. Do NOT use .delete() — use is_active=False instead."},
        },
        "required": ["script_content"],
    },
    "system_janitor": {
        "type": "object",
        "properties": {},
    },
    "database_backup": {
        "type": "object",
        "properties": {},
    },
    "read_django_models": {
        "type": "object",
        "properties": {
            "app_label": {"type": "string"},
            "model_name": {"type": "string"},
            "kwargs": {"type": "object"},
            "limit": {"type": "integer"},
        },
        "required": ["app_label", "model_name"],
    },
    "manage_dynamic_tools": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "script_content": {"type": "string"},
            "input_schema": {"type": "string"},
            "output_schema": {"type": "string"},
        },
        "required": ["name", "script_content"],
    },
    "update_conversation_state": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add_task", "update_task_status"]},
            "task_path": {"type": "string"},
            "status": {"type": "string"},
        },
        "required": ["action", "task_path"],
    },
    "run_sub_blueprint": {
        "type": "object",
        "properties": {
            "blueprint_name": {"type": "string"},
            "task_prompt": {"type": "string"},
        },
        "required": ["blueprint_name", "task_prompt"],
    },
    "TASK_COMPLETE": {
        "type": "object",
        "properties": {
            "final_answer": {"type": "string", "description": "Summary of what was accomplished. Signals the agent is done."},
        },
    },
    "discover_django_models": {
        "type": "object",
        "properties": {
            "app_label": {"type": "string", "description": "The Django app label (e.g., 'grips', 'background_resources')."},
            "model_name": {"type": "string", "description": "Optional model name. If omitted, returns the whole spine of the app."},
        },
        "required": ["app_label"],
    },
    "write_django_model": {
        "type": "object",
        "properties": {
            "app_label": {"type": "string"},
            "model_name": {"type": "string"},
            "action": {"type": "string", "enum": ["create", "update", "update_or_create", "create_variant"]},
            "pk": {"type": "integer", "description": "Primary key for updates or creating variants."},
            "parameters": {"type": "object", "description": "Dictionary of fields to set/update."},
        },
        "required": ["app_label", "model_name", "action", "parameters"],
    },
}


def seed_tools(ToolDefinition):
    ACTION_REGISTRY = {
        "handle_research": "metacognition.actions.handle_research",
        "handle_execution_plan": "metacognition.actions.handle_execution_plan",
        "handle_difficult_prompt": "metacognition.actions.handle_difficult_prompt",
        "handle_result_critique": "metacognition.actions.handle_result_critique",
        "python_sandbox": "metacognition.actions.python_sandbox",
    }

    for name, python_path in ACTION_REGISTRY.items():
        schema = ""
        if name == "python_sandbox":
            schema = '{"type": "object", "properties": {"code": {"type": "string", "description": "Python code string to execute"}}, "required": ["code"]}'
            
        ToolDefinition.objects.update_or_create(
            name=name,
            defaults={
                'description': f"Action hook: {name}",
                'tool_type': 'builtin',
                'python_path': python_path,
                'is_active': True,
                'is_promoted': True,
                'input_schema': schema,
            }
        )

    meta_tools = [
        ("list_available_tools", "Returns a summary of all active ToolDefinitions.", "builtin", "metacognition.meta_tools.list_available_tools"),
        ("create_tool", "Creates a new ToolDefinition in the database.", "builtin", "metacognition.meta_tools.create_tool"),
        ("list_blueprints", "Returns all available CognitiveBlueprints with their step topology.", "builtin", "metacognition.meta_tools.list_blueprints"),
        ("create_blueprint", "Creates a new CognitiveBlueprint with linked ReasoningSteps.", "builtin", "metacognition.meta_tools.create_blueprint"),
        ("get_benchmark_stats", "Fetches summary statistics for recent benchmark investigations using df.describe().", "builtin", "metacognition.meta_tools.get_benchmark_stats"),
        ("read_benchmark_topic", "Reads detailed performance for a specific investigation.", "builtin", "metacognition.meta_tools.read_benchmark_topic"),
        ("review_benchmark_results", "Fetches and summarises benchmark results for analysis.", "builtin", "metacognition.meta_tools.review_benchmark_results"),
        ("get_conversation_metrics", "Queries PromptResponseLog to summarize success rates and identify failing reasoning steps.", "builtin", "metacognition.meta_tools.get_conversation_metrics"),
        ("fetch_log_details", "Fetches full details of a specific PromptResponseLog for deep reading.", "builtin", "metacognition.meta_tools.fetch_log_details"),
        ("get_rag_efficiency_metrics", "Analyzes downstream impacts of RAG context on conversation failures.", "builtin", "metacognition.meta_tools.get_rag_efficiency_metrics"),
        ("get_grips_metrics", "Summarizes Grips ConceptNode stats and flags downstream failures.", "builtin", "metacognition.meta_tools.get_grips_metrics"),
        ("create_benchmark_scenario", "Creates a new BenchmarkScenario from the agent's analysis.", "builtin", "metacognition.meta_tools.create_benchmark_scenario"),
        ("document_reader", "Unified tool for navigating and fetching documents from the RAG database. You MUST specify the 'action' parameter.", "builtin", "metacognition.meta_tools.document_reader"),
        ("delegate_task", "Delegates a sub-task to another blueprint via Celery.", "builtin", "metacognition.meta_tools.delegate_task"),
        ("run_benchmark", "Triggers a benchmarking test for a group of scenarios.", "builtin", "metacognition.meta_tools.run_benchmark"),
        ("django_shell_script", "Executes raw Python code in the host Django environment. Pass code via 'script_content' parameter.", "builtin", "metacognition.meta_tools.django_shell_script"),
        ("system_janitor", "Deletes empty workspace directories.", "builtin", "metacognition.meta_tools.system_janitor"),
        ("database_backup", "Takes a JSON backup of the Django DB.", "builtin", "metacognition.meta_tools.database_backup"),
        ("read_django_models", "Queries the database.", "builtin", "metacognition.meta_tools.read_django_models"),
        ("manage_dynamic_tools", "Creates Python scripts securely.", "builtin", "metacognition.meta_tools.manage_dynamic_tools"),
        ("update_conversation_state", "Mutates the state_tree in the active Conversation.", "builtin", "metacognition.meta_tools.update_conversation_state"),
        ("run_sub_blueprint", "Executes a sub-blueprint synchronously.", "builtin", "metacognition.meta_tools.run_sub_blueprint"),
        ("discover_django_models", "Inspects the schema of Django models to discover fields and relationships.", "builtin", "metacognition.meta_tools.discover_django_models"),
        ("write_django_model", "Safely creates, updates, or variants a Django model instance.", "builtin", "metacognition.meta_tools.write_django_model"),
        # Finding 2.2: TASK_COMPLETE must be a real registered tool with python_path
        ("TASK_COMPLETE", "Signals that the agent has finished all planned work. Use this when done.", "builtin", "metacognition.meta_tools.TASK_COMPLETE"),
    ]

    for name, desc, ttype, path in meta_tools:
        schema = TOOL_SCHEMAS.get(name)
        ToolDefinition.objects.update_or_create(
            name=name,
            defaults={
                'description': desc,
                'tool_type': ttype,
                'python_path': path,
                'input_schema': json.dumps(schema) if schema else '',
                'is_active': True,
                'is_promoted': True,
            }
        )

def seed_architect(CognitiveBlueprint, ReasoningStep):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="The Architect",
        defaults={'description': "Meta-agent blueprint capable of self-composing tools and blueprints."}
    )

    ReasoningStep.objects.filter(blueprint=bp).delete()

    step1 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Understand Request",
        system_prompt="You are The Architect. Analyze the user's request and determine if you need to create a new cognitive blueprint, a new tool, or review benchmark results. Output your strategic analysis.",
        is_start_node=True,
    )

    step2 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Research Existing Tools",
        system_prompt="List all available tools and blueprints to see what you can reuse.",
    )

    step3 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Compose Solution",
        system_prompt="Based on your research, compose the solution. If a new blueprint is needed, format your output to trigger 'create_blueprint'. If a new tool is needed, use 'create_tool'.",
    )

    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.save()

def seed_grips_stub_filler(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="Grips Stub Filler",
        defaults={'description': "Reads a ConceptNode ID, researches context, and writes its narrative.", 'is_autonomous': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    step1 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Fill Stub",
        is_start_node=True,
        system_prompt="You are a research assistant filling in empty Grips ConceptNodes. The user prompt provides the ID. Query the database, research the topic if necessary, and use django_shell_script to save the narrative. Then output TASK_COMPLETE.",
        max_retries=3,
        max_new_tokens=800,
    )
    step1.on_success_step = None
    step1.on_failure_step = step1
    step1.save()
    for t in ["read_django_models", "django_shell_script", "document_reader", "TASK_COMPLETE"]:
        tool, _ = ToolDefinition.objects.get_or_create(name=t)
        step1.available_tools.add(tool)

def seed_variant_scorer(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="Variant Scorer",
        defaults={'description': "Computes EWMA for ReasoningSteps and updates performance_score.", 'is_autonomous': True}
    )
def seed_nm_housekeeping(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Housekeeping",
        defaults={'description': "NightManager sub-blueprint for system cleanup.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Document Ingestions",
        is_start_node=True,
        is_canonical=True,
        system_prompt=(
            "Goal: Complete outstanding document ingestions from the background_resources app.\n"
            "Action: Use the `django_shell_script` tool to run the following exact script:\n"
            "```python\nfrom background_resources.tasks import sweep_unprocessed_documents\nprint(sweep_unprocessed_documents())\n```\n"
            "Self-Evaluation: Ensure the script executed without crashing."
        ),
        evaluation_criteria="Did the tool execute without crashing?",
        max_retries=2,
        max_new_tokens=400,
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Grips Digestion and Linting",
        is_canonical=True,
        system_prompt=(
            "Goal: Run the grips app 3-levels of digestion and linting.\n"
            "Action: Use the `django_shell_script` tool to run the following exact script:\n"
            "```python\nfrom grips.tasks import sweep_unlinted_concepts, sweep_dirty_edges\nprint(sweep_unlinted_concepts())\nprint(sweep_dirty_edges())\n```\n"
            "Self-Evaluation: Ensure digestion completed without fatal errors."
        ),
        evaluation_criteria="Did digestion and linting run without crashing?",
        max_retries=2,
        max_new_tokens=400,
    )

    step3 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Database Backup",
        is_canonical=True,
        system_prompt=(
            "Goal: Backup the database.\n"
            "Action: Use the `database_backup` tool.\n"
            "Self-Evaluation: Ensure the backup tool returns success."
        ),
        evaluation_criteria="Did the backup tool return a success message?",
        max_retries=2,
        max_new_tokens=400,
    )

    step4 = ReasoningStep.objects.create(
        blueprint=bp,
        name="System Janitor",
        is_canonical=True,
        system_prompt=(
            "Goal: Delete unused workspaces.\n"
            "Action: Use the `system_janitor` tool.\n"
            "Self-Evaluation: Ensure the tool successfully deleted old temp files and workspaces."
        ),
        evaluation_criteria="Did system_janitor successfully complete?",
        max_retries=2,
        max_new_tokens=400,
    )

    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    
    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()
    
    step4.on_success_step = None
    step4.on_failure_step = None
    step4.save()
    
    tool_django, _ = ToolDefinition.objects.get_or_create(name="django_shell_script")
    tool_db, _ = ToolDefinition.objects.get_or_create(name="database_backup")
    tool_janitor, _ = ToolDefinition.objects.get_or_create(name="system_janitor")
    
    for s in [step1, step2, step3, step4]:
        s.save()
        
    step1.available_tools.add(tool_django)
    step2.available_tools.add(tool_django)
    step3.available_tools.add(tool_db)
    step4.available_tools.add(tool_janitor)

def seed_nm_deep_system_evaluation(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Deep_system_evaluation",
        defaults={'description': "Phase 1: Evaluates conversations, blueprint reasoning step variants, benchmark trends, RAG input efficiency, and Grips recall/precision.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()

    # --- Domain 1: Conversations & Blueprints ---
    step_conv_fetch = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Conversation Metrics", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Fetch recent conversation metrics.\nAction: Use `get_conversation_metrics`.",
        evaluation_criteria="Did the tool execute and return metrics?", max_retries=3, max_new_tokens=400
    )
    step_conv_analyze = ReasoningStep.objects.create(
        blueprint=bp, name="Analyze Conversation Metrics", is_canonical=True,
        system_prompt="Goal: Analyze the fetched conversation metrics to identify weak/strong reasoning step variants.\nAction: Write a brief analysis. You may use `fetch_log_details` to read specific logs if needed.",
        evaluation_criteria="Did the LLM write an analysis of conversation metrics?", max_retries=3, max_new_tokens=800
    )
    step_conv_record = ReasoningStep.objects.create(
        blueprint=bp, name="Record Conversation Findings", is_canonical=True,
        system_prompt="Goal: Queue identified modification tasks based on conversation analysis.\nAction: Use `update_conversation_state` (action: 'add_task', task_path: 'NM_Ideas_for_System_Modifications > [Descriptive Task Name]').\nNote: Do NOT output TASK_COMPLETE. Transition to the next phase will happen automatically.",
        evaluation_criteria="Did the LLM queue modification tasks or confirm nothing to queue?", max_retries=3, max_new_tokens=400
    )

    # --- Domain 2: Benchmarks ---
    step_bench_fetch = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Benchmark Metrics", is_canonical=True,
        system_prompt="Goal: Fetch recent benchmark metrics.\nAction: Use `get_benchmark_stats`.",
        evaluation_criteria="Did the tool execute and return metrics?", max_retries=3, max_new_tokens=400
    )
    step_bench_analyze = ReasoningStep.objects.create(
        blueprint=bp, name="Analyze Benchmark Metrics", is_canonical=True,
        system_prompt="Goal: Analyze the fetched benchmark metrics for failure patterns.\nAction: Write a brief analysis. No tools needed.",
        evaluation_criteria="Did the LLM write an analysis of benchmark metrics?", max_retries=3, max_new_tokens=800
    )
    step_bench_record = ReasoningStep.objects.create(
        blueprint=bp, name="Record Benchmark Findings", is_canonical=True,
        system_prompt="Goal: Queue identified modification tasks based on benchmark analysis.\nAction: Use `update_conversation_state` (action: 'add_task', task_path: 'NM_Ideas_for_System_Modifications > [Descriptive Task Name]').\nNote: Do NOT output TASK_COMPLETE. Transition to the next phase will happen automatically.",
        evaluation_criteria="Did the LLM queue modification tasks or confirm nothing to queue?", max_retries=3, max_new_tokens=400
    )

    # --- Domain 3: RAG Efficiency ---
    step_rag_fetch = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch RAG Metrics", is_canonical=True,
        system_prompt="Goal: Fetch RAG efficiency metrics.\nAction: Use `get_rag_efficiency_metrics`.",
        evaluation_criteria="Did the tool execute and return metrics?", max_retries=3, max_new_tokens=400
    )
    step_rag_analyze = ReasoningStep.objects.create(
        blueprint=bp, name="Analyze RAG Metrics", is_canonical=True,
        system_prompt="Goal: Analyze RAG efficiency. Consider if RAG input is excessive, distracting, irrelevant, or insufficient downstream.\nAction: Write a brief analysis. You may use `fetch_log_details` to read specific logs if needed.",
        evaluation_criteria="Did the LLM write an analysis of RAG efficiency?", max_retries=3, max_new_tokens=800
    )
    step_rag_record = ReasoningStep.objects.create(
        blueprint=bp, name="Record RAG Findings", is_canonical=True,
        system_prompt="Goal: Queue identified modification tasks based on RAG analysis.\nAction: Use `update_conversation_state` (action: 'add_task', task_path: 'NM_Ideas_for_System_Modifications > [Descriptive Task Name]').\nNote: Do NOT output TASK_COMPLETE. Transition to the next phase will happen automatically.",
        evaluation_criteria="Did the LLM queue modification tasks or confirm nothing to queue?", max_retries=3, max_new_tokens=400
    )

    # --- Domain 4: Grips ---
    step_grips_fetch = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Grips Metrics", is_canonical=True,
        system_prompt="Goal: Fetch Grips knowledge graph metrics.\nAction: Use `get_grips_metrics`.",
        evaluation_criteria="Did the tool execute and return metrics?", max_retries=3, max_new_tokens=400
    )
    step_grips_analyze = ReasoningStep.objects.create(
        blueprint=bp, name="Analyze Grips Metrics", is_canonical=True,
        system_prompt="Goal: Analyze Grips metrics. Consider if Grips failed to provide relevant content or provided too much irrelevant context.\nAction: Write a brief analysis. No tools needed.",
        evaluation_criteria="Did the LLM write an analysis of Grips metrics?", max_retries=3, max_new_tokens=800
    )
    step_grips_record = ReasoningStep.objects.create(
        blueprint=bp, name="Record Grips Findings", is_canonical=True,
        system_prompt="Goal: Queue identified modification tasks based on Grips analysis.\nAction: Use `update_conversation_state` (action: 'add_task', task_path: 'NM_Ideas_for_System_Modifications > [Descriptive Task Name]').\nNote: Output TASK_COMPLETE when done.",
        evaluation_criteria="Did the LLM queue modification tasks or confirm nothing to queue?", max_retries=3, max_new_tokens=400
    )

    # Chain them sequentially
    steps = [
        step_conv_fetch, step_conv_analyze, step_conv_record,
        step_bench_fetch, step_bench_analyze, step_bench_record,
        step_rag_fetch, step_rag_analyze, step_rag_record,
        step_grips_fetch, step_grips_analyze, step_grips_record
    ]
    for i in range(len(steps) - 1):
        steps[i].on_success_step = steps[i+1]
        steps[i].on_failure_step = steps[i+1]
        steps[i].save()

    steps[-1].on_success_step = None
    steps[-1].on_failure_step = None
    steps[-1].save()

    tool_conv, _ = ToolDefinition.objects.get_or_create(name="get_conversation_metrics")
    tool_bench, _ = ToolDefinition.objects.get_or_create(name="get_benchmark_stats")
    tool_rag, _ = ToolDefinition.objects.get_or_create(name="get_rag_efficiency_metrics")
    tool_grips, _ = ToolDefinition.objects.get_or_create(name="get_grips_metrics")
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    tool_fetch_log, _ = ToolDefinition.objects.get_or_create(name="fetch_log_details")
    
    step_conv_fetch.available_tools.add(tool_conv)
    step_conv_analyze.available_tools.add(tool_fetch_log)
    step_conv_record.available_tools.add(tool_update)
    
    step_bench_fetch.available_tools.add(tool_bench)
    step_bench_analyze.available_tools.clear()
    step_bench_record.available_tools.add(tool_update)
    
    step_rag_fetch.available_tools.add(tool_rag)
    step_rag_analyze.available_tools.add(tool_fetch_log)
    step_rag_record.available_tools.add(tool_update)
    
    step_grips_fetch.available_tools.add(tool_grips)
    step_grips_analyze.available_tools.clear()
    step_grips_record.available_tools.add(tool_update, tool_complete)

def seed_nm_optimize_reasoning(CognitiveBlueprint, ReasoningStep, ToolDefinition, ResponseSchema=None):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Optimize_Reasoning",
        defaults={'description': "Sub-blueprint to fetch, contemplate, and save ReasoningStep variants.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    schema_variant = None
    if ResponseSchema:
        schema_variant, _ = ResponseSchema.objects.get_or_create(
            name="PromptVariant_Schema",
            defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'PromptVariant'}
        )

    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Next Task & Context", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Read the `state_tree` for the next pending reasoning optimization task and query the database for the current step's context.\nAction: Use `update_conversation_state` (if necessary) or a database tool to fetch context.",
        evaluation_criteria="Did the agent successfully fetch the next task?", max_retries=3, max_new_tokens=600
    )
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Contemplate", is_canonical=True,
        system_prompt="Goal: Analyze failure modes and draft a better prompt for the targeted ReasoningStep.\nAction: Write a detailed analysis and draft.",
        evaluation_criteria="Did the LLM write an analysis and draft?", max_retries=3, max_new_tokens=800
    )
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Act (Save Variant)", is_canonical=True, output_schema=schema_variant,
        system_prompt="Goal: Save the proposed variant and mark the task as resolved in the `state_tree`.\nAction: Generate the PromptVariant schema.",
        evaluation_criteria="Did the LLM output the PromptVariant?", max_retries=3, max_new_tokens=800
    )
    step4 = ReasoningStep.objects.create(
        blueprint=bp, name="Check Queue & Loop", is_canonical=True,
        system_prompt="Goal: Determine if there are more reasoning optimization tasks in the queue.\nAction: Output analysis of the remaining queue.",
        evaluation_criteria="Check the conversation state tree. Are there any pending reasoning tasks? If yes, fail (FAILURE) to force a loop. If all reasoning tasks are completed, pass (SUCCESS).",
        max_retries=3, max_new_tokens=600
    )
    
    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()
    step4.on_success_step = None
    step4.on_failure_step = step1
    step4.save()
    
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    step3.action_hook = 'create_prompt_variant'
    step3.save()
    step3.available_tools.clear()
    step1.available_tools.clear()
    step2.available_tools.clear()
    step4.available_tools.clear()

def seed_nm_refine_rag_grips(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Refine_RAG_Grips",
        defaults={'description': "Sub-blueprint to analyze and refine RAG/Grips nodes.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Next Task & Context", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Read the `state_tree` for the next RAG/Grips task and fetch relevant nodes.\nAction: Fetch context from RAG/Grips.",
        evaluation_criteria="Did the agent successfully fetch the task and context?", max_retries=3, max_new_tokens=600
    )
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Contemplate", is_canonical=True,
        system_prompt="Goal: Analyze gaps in knowledge retrieval and plan expansions or modifications.\nAction: Write a detailed analysis.",
        evaluation_criteria="Did the LLM write an analysis?", max_retries=3, max_new_tokens=800
    )
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Act (Save Modifications)", is_canonical=True,
        system_prompt="Goal: Update Grips/ReadingStrategies and mark task as resolved.\nAction: Apply modifications.",
        evaluation_criteria="Did the LLM execute the modifications?", max_retries=3, max_new_tokens=800
    )
    step4 = ReasoningStep.objects.create(
        blueprint=bp, name="Check Queue & Loop", is_canonical=True,
        system_prompt="Goal: Determine if there are more RAG/Grips tasks.\nAction: Output analysis of the remaining queue.",
        evaluation_criteria="Check the conversation state tree. Are there any pending RAG/Grips tasks? If yes, fail (FAILURE) to force a loop. If all are completed, pass (SUCCESS).",
        max_retries=3, max_new_tokens=600
    )
    
    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()
    step4.on_success_step = None
    step4.on_failure_step = step1
    step4.save()
    
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    tool_discover, _ = ToolDefinition.objects.get_or_create(name="discover_django_models")
    tool_read, _ = ToolDefinition.objects.get_or_create(name="read_django_models")
    tool_write, _ = ToolDefinition.objects.get_or_create(name="write_django_model")
    
    step3.available_tools.add(tool_update, tool_complete, tool_discover, tool_write)
    step1.available_tools.add(tool_discover, tool_read)
    step2.available_tools.clear()
    step4.available_tools.clear()

def seed_nm_formulate_benchmarks(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Formulate_Benchmarks",
        defaults={'description': "Sub-blueprint to formulate and create Benchmarks.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Fetch Next Task & Context", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Read benchmarking tasks from the queue.\nAction: Fetch context from relevant Documents.",
        evaluation_criteria="Did the agent successfully fetch the task?", max_retries=3, max_new_tokens=600
    )
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Contemplate", is_canonical=True,
        system_prompt="Goal: Plan the scenarios needed to test recent system modifications.\nAction: Write a detailed plan analyzing the source Document and break it down into thematic aspects (e.g., 'real-world practicalities', 'command and legal issues', 'human psychology') to generate a comprehensive, multi-faceted benchmark suite.",
        evaluation_criteria="Did the LLM write a benchmark plan?", max_retries=3, max_new_tokens=800
    )
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Act (Create Benchmark)", is_canonical=True,
        system_prompt="Goal: Create the benchmark group/scenario and mark task as resolved.\nAction: Use create_benchmark_scenario and write_django_model.",
        evaluation_criteria="Did the LLM save the benchmark?", max_retries=3, max_new_tokens=800
    )
    step4 = ReasoningStep.objects.create(
        blueprint=bp, name="Check Queue & Loop", is_canonical=True,
        system_prompt="Goal: Determine if there are more benchmark tasks.\nAction: Output analysis of the remaining queue.",
        evaluation_criteria="Check the conversation state tree. Are there any pending benchmark tasks? If yes, fail (FAILURE) to force a loop. If all are completed, pass (SUCCESS).",
        max_retries=3, max_new_tokens=600
    )
    
    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()
    step4.on_success_step = None
    step4.on_failure_step = step1
    step4.save()
    
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    tool_create_scenario, _ = ToolDefinition.objects.get_or_create(name="create_benchmark_scenario")
    tool_write, _ = ToolDefinition.objects.get_or_create(name="write_django_model")
    tool_read, _ = ToolDefinition.objects.get_or_create(name="document_reader")
    
    step3.available_tools.add(tool_update, tool_complete, tool_create_scenario, tool_write)
    step1.available_tools.add(tool_read)
    step2.available_tools.clear()
    step4.available_tools.clear()

def seed_nm_system_modifications(CognitiveBlueprint, ReasoningStep, ToolDefinition, ResponseSchema=None):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Ideas_for_System_Modifications",
        defaults={'description': "Phase 2: Routes to specific sub-blueprints to formulate system modifications.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()

    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Identify Target Modification Tasks", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Review Phase 1 evaluation findings and identify specific target modification tasks.\nAction: Queue projected modification tasks in the active conversation state_tree.",
        evaluation_criteria="Did the LLM queue modification tasks in conversation state?", max_retries=5, max_new_tokens=1200
    )
    
    bp_opt = CognitiveBlueprint.objects.get(name="NM_Optimize_Reasoning")
    bp_rag = CognitiveBlueprint.objects.get(name="NM_Refine_RAG_Grips")
    bp_bench = CognitiveBlueprint.objects.get(name="NM_Formulate_Benchmarks")

    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Optimize Reasoning", is_canonical=True, sub_blueprint=bp_opt,
        system_prompt="Execute NM_Optimize_Reasoning.", evaluation_criteria="Did it complete?", max_retries=1
    )
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Refine RAG/Grips", is_canonical=True, sub_blueprint=bp_rag,
        system_prompt="Execute NM_Refine_RAG_Grips.", evaluation_criteria="Did it complete?", max_retries=1
    )
    step4 = ReasoningStep.objects.create(
        blueprint=bp, name="Formulate Benchmarks", is_canonical=True, sub_blueprint=bp_bench,
        system_prompt="Execute NM_Formulate_Benchmarks.", evaluation_criteria="Did it complete?", max_retries=1
    )

    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()
    step4.on_success_step = None
    step4.on_failure_step = None
    step4.save()
    
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    step1.available_tools.add(tool_update, tool_complete)
    step2.available_tools.clear()
    step3.available_tools.clear()
    step4.available_tools.clear()

def seed_nm_self_improvement(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NM_Self-improvement",
        defaults={'description': "Phase 3: Meta-self reflection on NightManager execution patterns across model rotations.", 'is_autonomous': True, 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()

    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Meta-Performance Contemplation", is_start_node=True, is_canonical=True,
        system_prompt="Goal: Review overall NightManager execution performance across model runs.\nAction: Write a detailed summary.",
        evaluation_criteria="Did the LLM write a summary?", max_retries=3, max_new_tokens=800
    )
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Blueprint Evolution Contemplation", is_canonical=True,
        system_prompt="Goal: Propose an updated NightManager Blueprint with any amended ReasoningSteps.\nAction: Write a proposed blueprint specification.",
        evaluation_criteria="Did the LLM write a proposal?", max_retries=3, max_new_tokens=800
    )
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Act (Construct Blueprints)", is_canonical=True,
        system_prompt="Goal: Autonomously construct the new cognitive blueprints or tools.\nAction: Call `create_blueprint` or `create_tool` to instantiate your new designs.",
        evaluation_criteria="Did the LLM construct the new blueprints or tools?", max_retries=3, max_new_tokens=800
    )

    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()
    step3.on_success_step = None
    step3.on_failure_step = None
    step3.save()
    
    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    tool_create_bp, _ = ToolDefinition.objects.get_or_create(name="create_blueprint")
    tool_create_tool, _ = ToolDefinition.objects.get_or_create(name="create_tool")
    step3.available_tools.add(tool_update, tool_complete, tool_create_bp, tool_create_tool)
    step1.available_tools.clear()
    step2.available_tools.clear()

def seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition):
    seed_nm_housekeeping(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_nm_deep_system_evaluation(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_nm_optimize_reasoning(CognitiveBlueprint, ReasoningStep, ToolDefinition, ResponseSchema)
    seed_nm_refine_rag_grips(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_nm_formulate_benchmarks(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_nm_system_modifications(CognitiveBlueprint, ReasoningStep, ToolDefinition, ResponseSchema)
    seed_nm_self_improvement(CognitiveBlueprint, ReasoningStep, ToolDefinition)

    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NightManager",
        defaults={
            'description': "Master orchestration blueprint for autonomous nightly system review, maintenance, and self-evolution.",
            'is_autonomous': True,
            'is_canonical': True
        }
    )

    ReasoningStep.objects.filter(blueprint=bp).delete()

    bp_housekeeping = CognitiveBlueprint.objects.get(name="NM_Housekeeping")
    bp_eval = CognitiveBlueprint.objects.get(name="NM_Deep_system_evaluation")
    bp_mods = CognitiveBlueprint.objects.get(name="NM_Ideas_for_System_Modifications")
    bp_self = CognitiveBlueprint.objects.get(name="NM_Self-improvement")

    step1 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Step 1: Housekeeping",
        is_start_node=True,
        is_canonical=True,
        sub_blueprint=bp_housekeeping,
        system_prompt=(
            "The work in this moment is to execute Phase 0 Housekeeping to contribute to the overall goal of NightManager system maintenance."
        ),
        evaluation_criteria="Did the housekeeping sub-blueprint complete?",
        max_retries=1,
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Step 2: Deep System Evaluation",
        is_canonical=True,
        sub_blueprint=bp_eval,
        system_prompt=(
            "The work in this moment is to execute Phase 1 Deep System Evaluation to contribute to the overall goal of NightManager system maintenance."
        ),
        evaluation_criteria="Did deep system evaluation complete?",
        max_retries=1,
    )

    step3 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Step 3: Ideas for System Modifications",
        is_canonical=True,
        sub_blueprint=bp_mods,
        system_prompt=(
            "The work in this moment is to execute Phase 2 Ideas for System Modifications to contribute to the overall goal of NightManager system maintenance."
        ),
        evaluation_criteria="Did system modifications review complete?",
        max_retries=1,
    )

    step4 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Step 4: Self-Improvement",
        is_canonical=True,
        sub_blueprint=bp_self,
        system_prompt=(
            "The work in this moment is to execute Phase 3 Self-Improvement to contribute to the overall goal of NightManager system maintenance."
        ),
        evaluation_criteria="Did self-improvement complete?",
        max_retries=1,
    )

    step1.on_success_step = step2
    step1.on_failure_step = step2
    step1.save()
    
    step2.on_success_step = step3
    step2.on_failure_step = step3
    step2.save()

    step3.on_success_step = step4
    step3.on_failure_step = step4
    step3.save()

    step4.on_success_step = None
    step4.on_failure_step = None
    step4.save()

    tool_update, _ = ToolDefinition.objects.get_or_create(name="update_conversation_state")
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    for s in [step1, step2, step3, step4]:
        s.available_tools.add(tool_update, tool_complete)

    try:
        from django_celery_beat.models import PeriodicTask, CrontabSchedule
        import json
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='3',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*'
        )
        PeriodicTask.objects.update_or_create(
            name='NightManager Daily Maintenance',
            defaults={
                'crontab': schedule,
                'task': 'metacognition.tasks.task_run_blueprint_async',
                'kwargs': json.dumps({'blueprint_id': bp.id, 'user_prompt': 'Perform nightly maintenance.'}),
            }
        )
        PeriodicTask.objects.update_or_create(
            name='Nightly Performance Scoring',
            defaults={
                'crontab': schedule,
                'task': 'metacognition.tasks.task_update_performance_scores',
            }
        )
    except ImportError:
        pass

def seed_grill_me(CognitiveBlueprint, ReasoningStep):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="Grill me!",
        defaults={'description': "By asking pointed and insightful questions of the user, elicit all the details of the problem", 'is_canonical': True}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    grill_step = ReasoningStep.objects.create(
        blueprint=bp,
        name="Grill User",
        system_prompt="Consider the user's input in this conversation until now and ask the best new question that will elicit the most clarifying additional information. Consider the domain of the problem and aspects like processes, data, people, and organisations involved, adversarial thinking about risks of failure, cause and effect dependencies in the domain.  This question asking process will continue until the user says: \"That's enough\"",
        is_start_node=True,
        is_canonical=True,
        evaluation_criteria="Has the user indicated they want to stop, e.g. \"That's enough\" or an equivalent stopping phrase?  If yes, output an evaluation passing."
    )
    
    # Establish a self-loop so LangGraph pauses for user input on FAILURE
    grill_step.on_failure_step = grill_step
    grill_step.save()

def seed_computational_logic(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="Computational Logic",
        defaults={'description': "Solve a strategic decision-making problem using Python code.", 'is_canonical': True}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    step = ReasoningStep.objects.create(
        blueprint=bp,
        name="Solve Strategic Scenario",
        system_prompt="You are a decision analysis expert. Write a Python script to solve the user's strategic scenario.",
        is_start_node=True,
    )
    python_sandbox_tool = ToolDefinition.objects.filter(name="python_sandbox").first()
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    step.system_prompt += "\nOnce you have successfully executed the required action, you MUST output the `TASK_COMPLETE` tool to finish."
    step.save()
    if python_sandbox_tool:
        step.available_tools.add(python_sandbox_tool)
    step.available_tools.add(tool_complete)

def seed_escalation_of_effort(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Escalation of Effort",
        defaults={'description': "Research APIs dynamically, solve the problem with execution, and summarize the result."}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Introspection and Planning",
        system_prompt="You are a senior software engineer. The user has provided a complex request. Write a Python script to explore the environment (e.g., using `dir()`, `help()`, or reading docstrings of relevant modules) to understand how to solve the problem. Your script will be executed in the sandbox.",
        is_start_node=True,
    )
    python_sandbox_tool = ToolDefinition.objects.filter(name="python_sandbox").first()
    tool_complete, _ = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE")
    step1.system_prompt += "\nOnce you have successfully executed the required action, you MUST output the `TASK_COMPLETE` tool to finish."
    step1.save()
    if python_sandbox_tool:
        step1.available_tools.add(python_sandbox_tool)
    step1.available_tools.add(tool_complete)

    step2 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Execution",
        system_prompt="Review the sandbox execution output from your previous introspection step. Now, write the final Python script to solve the user's original request.\nOnce you have successfully executed the required action, you MUST output the `TASK_COMPLETE` tool to finish.",
    )
    if python_sandbox_tool:
        step2.available_tools.add(python_sandbox_tool)
    step2.available_tools.add(tool_complete)

    step3 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Validation and Formulation",
        system_prompt="Review the sandbox execution output from your final script. Formulate a final, natural language response answering the user's original prompt. Do NOT just dump the script again. Summarize the results (e.g. Expected Utility, Final Table, etc) clearly.",
    )

    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.save()

def seed_all():
    from .models import bypass_canonical_lock
    ToolDefinition = apps.get_model('metacognition', 'ToolDefinition')
    CognitiveBlueprint = apps.get_model('metacognition', 'CognitiveBlueprint')
    ReasoningStep = apps.get_model('metacognition', 'ReasoningStep')
    ResponseSchema = apps.get_model('metacognition', 'ResponseSchema')

    with bypass_canonical_lock():
        seed_tools(ToolDefinition)
        seed_architect(CognitiveBlueprint, ReasoningStep)
        seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)
        seed_grill_me(CognitiveBlueprint, ReasoningStep)
        seed_escalation_of_effort(CognitiveBlueprint, ReasoningStep, ToolDefinition)
        seed_computational_logic(CognitiveBlueprint, ReasoningStep, ToolDefinition)
        seed_research_evaluation(CognitiveBlueprint, ReasoningStep, ResponseSchema)
        seed_strategic_plan(CognitiveBlueprint, ReasoningStep, ResponseSchema)
        seed_task_decomposer(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)
        seed_propose_blueprint(CognitiveBlueprint, ReasoningStep, ToolDefinition)

def seed_research_evaluation(CognitiveBlueprint, ReasoningStep, ResponseSchema):
    bp_eval, _ = CognitiveBlueprint.objects.get_or_create(
        name="ResearchEvaluation",
        defaults={'description': "Full end-to-end pipeline for doctests."}
    )
    
    schema_research, _ = ResponseSchema.objects.get_or_create(
        name="ResearchEvaluation_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'ResearchEvaluation'}
    )
    schema_plan, _ = ResponseSchema.objects.get_or_create(
        name="StrategicPlan_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'StrategicPlan'}
    )
    schema_execute, _ = ResponseSchema.objects.get_or_create(
        name="ExecutionPlan_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'ExecutionPlan'}
    )
    schema_critique, _ = ResponseSchema.objects.get_or_create(
        name="ResultCritique_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'ResultCritique'}
    )
    
    ReasoningStep.objects.filter(blueprint=bp_eval).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp_eval, name="Research",
        system_prompt="You are a meticulous researcher. Analyze the current working context. If you lack information, formulate queries for our RAG (document) and Grips (concept) databases. CRITICAL: Do NOT guess API syntaxes or mathematical formulas. If the user asks for a specific library (e.g., pycid, numpy) or complex model, you MUST search the databases for documentation first. If the current information is SUFFICIENT to answer the user's overarching goal, output SUFFICIENT.", is_start_node=True, output_schema=schema_research
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp_eval, name="Plan",
        system_prompt="Review the user request and gathered research. Formulate a highly specific, step-by-step strategy for how to solve the problem using code execution. Do not write the code yet; just write the logical plan.", output_schema=schema_plan
    )
    
    step3 = ReasoningStep.objects.create(
        blueprint=bp_eval, name="Execute",
        system_prompt="CRITICAL INSTRUCTIONS: 1. The sandbox can only execute files that you have already written using WRITE_FILE. 2. Scripts must use simple print() statements to output results. Avoid multi-line strings or escaped newlines (\\n). 3. Do NOT repeat actions that have already succeeded. 4. If the results of a previous EXECUTE_SCRIPT give you the answer to the user's request, your NEXT queue MUST contain ONLY the `TASK_COMPLETE` tool with the final answer. 5. If a script fails due to memory limits, reduce array sizes or iterations, or avoid heavy libraries like numpy for simple tasks. You are a code execution agent. Your goal is to use a sequence of tools to accomplish the user's request. Review the user's goal and the results of previous tool executions. Formulate a plan by creating a queue of tool actions. CRITICAL: If the user's request has been fully satisfied by the execution results, your response MUST be a single `TASK_COMPLETE` action to terminate the loop.", max_retries=10, output_schema=schema_execute
    )
    
    step4 = ReasoningStep.objects.create(
        blueprint=bp_eval, name="Critique",
        system_prompt="You are a senior data scientist. Review the final results provided by the execution agent. Does the data logically align with the theoretical expectations? Are the statistics robust? If the output is flawed, naive, or missing critical precision, REJECT it and provide feedback. If it is solid, ACCEPT it.", output_schema=schema_critique
    )
    
    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.save()
    step3.on_success_step = step4
    step3.save()
    step4.on_failure_step = step2
    step4.save()

def seed_strategic_plan(CognitiveBlueprint, ReasoningStep, ResponseSchema):
    bp_strat, _ = CognitiveBlueprint.objects.get_or_create(
        name="StrategicPlan",
        defaults={'description': "Strategic Planning isolated pipeline"}
    )
    
    schema_plan, _ = ResponseSchema.objects.get_or_create(
        name="StrategicPlan_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'StrategicPlan'}
    )
    
    ReasoningStep.objects.filter(blueprint=bp_strat).delete()
    ReasoningStep.objects.create(
        blueprint=bp_strat,
        name="Plan Strategy",
        system_prompt="You are planning a strategy.",
        is_start_node=True,
        output_schema=schema_plan
    )

def seed_propose_blueprint(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Propose Blueprint",
        defaults={'description': "Dynamically designs and saves a new CognitiveBlueprint to the database."}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    create_tool = ToolDefinition.objects.filter(name="create_blueprint").first()
    
    step = ReasoningStep.objects.create(
        blueprint=bp, name="Blueprint Architect",
        system_prompt="You are a metacognitive architect. Your goal is to design a new agentic blueprint for a novel task. Use the 'create_blueprint' tool to save it. Return the name and ID of the created blueprint.",
        is_start_node=True,
    )
    if create_tool:
        step.available_tools.add(create_tool)

def seed_task_decomposer(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Task Decomposer",
        defaults={'description': "Iteratively process nested tasks using a JSON queue."}
    )
    
    schema_queue, _ = ResponseSchema.objects.get_or_create(
        name="TaskQueue_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'TaskQueue'}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Task Breakdown",
        system_prompt="Break the user's complex request into a strict programmatic JSON queue of sub-tasks. Each task must be delegated to a specific blueprint. Available blueprints:\n- 'Escalation of Effort': General purpose python/django worker\n- 'Computational Logic': Data processing worker\n- 'Propose Blueprint': If no existing blueprint fits, use this to design a new one.",
        is_start_node=True, output_schema=schema_queue
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Iterative Processor",
        system_prompt="Check the 'queue' in your Scratchpad Variables. Pop the first pending task. Use the 'run_sub_blueprint' tool to execute it by passing 'blueprint_name' and 'task_prompt'. You MUST update the queue state (by deleting the task you just ran) using 'update_conversation_state' or by just describing the remaining queue. If no tasks remain, output the 'TASK_COMPLETE' tool.",
    )
    
    run_tool = ToolDefinition.objects.filter(name="run_sub_blueprint").first()
    update_tool = ToolDefinition.objects.filter(name="update_conversation_state").first()
    complete_tool = ToolDefinition.objects.filter(name="TASK_COMPLETE").first()
    
    if run_tool:
        step2.available_tools.add(run_tool)
    if complete_tool:
        step2.available_tools.add(complete_tool)
    
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Final Compiler",
        system_prompt="All tasks are complete. Summarize the total work done based on the execution history.",
    )
    
    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.on_failure_step = step2 # Loop back
    step2.save()
    
def seed_lint_grips_edge(CognitiveBlueprint, ReasoningStep, ResponseSchema):
    from metacognition.models import ToolDefinition
    import json
    
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="LintGripsEdge",
        defaults={'description': "Metacognitive blueprint that rewrites KnowledgeEdge justifications to be human-readable, removing placeholder terms."}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    schema_dict = {
        "type": "object",
        "properties": {
            "edge_id": {"type": "integer", "description": "The ID of the edge being linted, exactly as provided in the prompt."},
            "improved_justification": {"type": "string", "description": "The rewritten, human-readable justification."}
        },
        "required": ["edge_id", "improved_justification"]
    }
    
    lint_tool, _ = ToolDefinition.objects.update_or_create(
        name="update_edge_justification",
        defaults={
            'description': "Saves the rewritten justification to the database.",
            'tool_type': 'builtin',
            'python_path': 'metacognition.actions.handle_edge_lint_tool',
            'input_schema': json.dumps(schema_dict)
        }
    )
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, 
        name="Rewrite Justification",
        system_prompt="You are a meticulous editor. The user will provide a relationship justification containing placeholder terms like 'Concept A' or 'Concept B'. Rewrite it so that it NEVER uses placeholder terms. Instead, use the actual titles of the concepts. Ensure the justification sounds natural, professional, and clear for a human reader. Once done, you MUST use the `update_edge_justification` tool to save your work.", 
        is_start_node=True, 
    )
    step1.available_tools.add(lint_tool)
    step1.save()
