from django.apps import apps

def seed_tools(ToolDefinition):
    ACTION_REGISTRY = {
        "handle_research": "metacognition.actions.handle_research",
        "handle_execution_plan": "metacognition.actions.handle_execution_plan",
        "handle_difficult_prompt": "metacognition.actions.handle_difficult_prompt",
        "handle_result_critique": "metacognition.actions.handle_result_critique",
        "python_sandbox": "metacognition.actions.python_sandbox",
    }
    
    for name, python_path in ACTION_REGISTRY.items():
        ToolDefinition.objects.get_or_create(
            name=name,
            defaults={
                'description': f"Action hook: {name}",
                'tool_type': 'builtin',
                'python_path': python_path,
                'is_active': True,
                'is_promoted': True,
            }
        )
        
    meta_tools = [
        ("list_available_tools", "Returns a summary of all active ToolDefinitions.", "builtin", "metacognition.meta_tools.list_available_tools"),
        ("create_tool", "Creates a new ToolDefinition in the database.", "builtin", "metacognition.meta_tools.create_tool"),
        ("list_blueprints", "Returns all available CognitiveBlueprints with their step topology.", "builtin", "metacognition.meta_tools.list_blueprints"),
        ("create_blueprint", "Creates a new CognitiveBlueprint with linked ReasoningSteps.", "builtin", "metacognition.meta_tools.create_blueprint"),
        ("review_benchmark_results", "Fetches and summarises benchmark results for analysis.", "builtin", "metacognition.meta_tools.review_benchmark_results"),
        ("create_benchmark_scenario", "Creates a new BenchmarkScenario from the agent's analysis.", "builtin", "metacognition.meta_tools.create_benchmark_scenario"),
        ("document_reader", "Unified tool for navigating and fetching documents from the RAG database.", "builtin", "metacognition.meta_tools.document_reader"),
        ("delegate_task", "Delegates a sub-task to another blueprint via Celery.", "builtin", "metacognition.meta_tools.delegate_task"),
        ("run_benchmark", "Triggers a benchmarking test for a group of scenarios.", "builtin", "metacognition.meta_tools.run_benchmark"),
        ("django_shell_script", "Executes raw Python code in the host Django environment.", "builtin", "metacognition.meta_tools.django_shell_script"),
        ("system_janitor", "Deletes empty workspace directories.", "builtin", "metacognition.meta_tools.system_janitor"),
        ("database_backup", "Takes a JSON backup of the Django DB.", "builtin", "metacognition.meta_tools.database_backup"),
    ]

    for name, desc, ttype, path in meta_tools:
        ToolDefinition.objects.get_or_create(
            name=name,
            defaults={
                'description': desc,
                'tool_type': ttype,
                'python_path': path,
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

def seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.update_or_create(
        name="NightManager",
        defaults={'description': "Autonomous system administrator that manages server maintenance, RAG benchmark tracking, and active reading."}
    )

    ReasoningStep.objects.filter(blueprint=bp).delete()

    admin_step = ReasoningStep.objects.create(
        blueprint=bp,
        name="Server Admin",
        is_start_node=True,
        system_prompt=(
            "You are the NightManager, an autonomous administrator for the verbal project. "
            "You are a proactive agent, eager to find opportunities to improve the system. "
            "You are awoken on a periodic schedule, and each wake-up starts a fresh Conversation which serves as your scratchpad. "
            "First, you MUST run the database_backup tool. "
            "Then, you must explicitly review the following areas of the system state:\n"
            "1. Benchmark Results: Analyze recent benchmarks to see how blueprints are performing.\n"
            "2. Conversations: Review your own past NightManager logs and recent user interactions and outcomes.\n"
            "3. Knowledge Base: Review background_resources (RAG chunks) and grips content. Evaluate their ability to return high-value context.\n"
            "4. Periodic Tasks: Confirm which periodic tasks should have happened and verify their execution.\n"
            "The server runs all night, so you should keep it busy with useful work. Normal proactive activities include: "
            "creating new or varied Blueprints and ReasoningStep content, configuring Benchmark Investigations on new Blueprints, "
            "designing new ReadingStrategies, and all sorts of creative investigating and play. "
            "If you find issues, report bugs or request codebase changes to expose more meaningful content. "
            "Execute any necessary maintenance tasks using your provided tools. You can use django_shell_script for full CRUD access. "
            "IMPORTANT: Do not use hard deletes (.delete()); use is_active=False or queue for review. "
            "Stop looping only when you have completed all planned tasks and reviews using the TASK_COMPLETE tool."
        ),
        max_retries=5
    )
    
    # Assign tools to the NightManager
    tools = [
        "run_benchmark",
        "delegate_task",
        "document_reader",
        "django_shell_script",
        "system_janitor",
        "database_backup",
        "handle_execution_plan",
        "TASK_COMPLETE"
    ]
    for tool_name in tools:
        tool, _ = ToolDefinition.objects.get_or_create(name=tool_name)
        admin_step.available_tools.add(tool)

    admin_step.on_failure_step = admin_step
    admin_step.save()
    
    # Programmatically add a PeriodicTask in Celery Beat using the run_blueprint_async signature
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
    except ImportError:
        pass

def seed_grill_me(CognitiveBlueprint, ReasoningStep):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Grill me!",
        defaults={'description': "By asking pointed and insightful questions of the user, elicit all the details of the problem"}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    grill_step = ReasoningStep.objects.create(
        blueprint=bp,
        name="Grill User",
        system_prompt="Consider the user's input in this conversation until now and ask the best new question that will elicit the most clarifying additional information. Consider the domain of the problem and aspects like processes, data, people, and organisations involved, adversarial thinking about risks of failure, cause and effect dependencies in the domain.  This question asking process will continue until the user says: \"That's enough\"",
        is_start_node=True,
        evaluation_criteria="Has the user indicated they want to stop, e.g. \"That's enough\" or an equivalent stopping phrase?  If yes, output an evaluation passing."
    )
    
    # Establish a self-loop so LangGraph pauses for user input on FAILURE
    grill_step.on_failure_step = grill_step
    grill_step.save()

def seed_escalation_of_effort(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Pipeline: Escalation of Effort",
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
    if python_sandbox_tool:
        step1.available_tools.add(python_sandbox_tool)

    step2 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Execution",
        system_prompt="Review the sandbox execution output from your previous introspection step. Now, write the final Python script to solve the user's original request.",
    )
    if python_sandbox_tool:
        step2.available_tools.add(python_sandbox_tool)

    step3 = ReasoningStep.objects.create(
        blueprint=bp,
        name="Validation and Formulation",
        system_prompt="Review the sandbox execution output from your final script. Formulate a final, natural language response answering the user's original prompt. Do NOT just dump the script again. Summarize the results (e.g. Expected Utility, Final Table, etc) clearly.",
    )

    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.save()

def seed_computational_logic(CognitiveBlueprint, ReasoningStep, ToolDefinition):
    # For backwards compatibility with other tests.
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Pipeline: Computational Logic",
        defaults={'description': "Solve a strategic decision-making problem using Python code."}
    )
    ReasoningStep.objects.filter(blueprint=bp).delete()
    step = ReasoningStep.objects.create(
        blueprint=bp,
        name="Solve Strategic Scenario",
        system_prompt="You are a decision analysis expert. Write a Python script to solve the user's strategic scenario.",
        is_start_node=True,
    )
    python_sandbox_tool = ToolDefinition.objects.filter(name="python_sandbox").first()
    if python_sandbox_tool:
        step.available_tools.add(python_sandbox_tool)

def seed_all():
    ToolDefinition = apps.get_model('metacognition', 'ToolDefinition')
    CognitiveBlueprint = apps.get_model('metacognition', 'CognitiveBlueprint')
    ReasoningStep = apps.get_model('metacognition', 'ReasoningStep')
    ResponseSchema = apps.get_model('metacognition', 'ResponseSchema')

    seed_tools(ToolDefinition)
    seed_architect(CognitiveBlueprint, ReasoningStep)
    seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)
    seed_grill_me(CognitiveBlueprint, ReasoningStep)
    seed_escalation_of_effort(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_computational_logic(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    seed_research_evaluation(CognitiveBlueprint, ReasoningStep, ResponseSchema)
    seed_strategic_plan(CognitiveBlueprint, ReasoningStep, ResponseSchema)
    seed_task_decomposer(CognitiveBlueprint, ReasoningStep, ResponseSchema)

def seed_research_evaluation(CognitiveBlueprint, ReasoningStep, ResponseSchema):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Pipeline: ResearchEvaluation",
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
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Research",
        system_prompt="You are a meticulous researcher. Analyze the current working context. If you lack information, formulate queries for our RAG (document) and Grips (concept) databases. CRITICAL: Do NOT guess API syntaxes or mathematical formulas. If the user asks for a specific library (e.g., pycid, numpy) or complex model, you MUST search the databases for documentation first. If the current information is SUFFICIENT to answer the user's overarching goal, output SUFFICIENT.", is_start_node=True, output_schema=schema_research
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Plan",
        system_prompt="Review the user request and gathered research. Formulate a highly specific, step-by-step strategy for how to solve the problem using code execution. Do not write the code yet; just write the logical plan.", output_schema=schema_plan
    )
    
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Execute",
        system_prompt="CRITICAL INSTRUCTIONS: 1. The sandbox can only execute files that you have already written using WRITE_FILE. 2. Scripts must use simple print() statements to output results. Avoid multi-line strings or escaped newlines (\\n). 3. Do NOT repeat actions that have already succeeded. 4. If the results of a previous EXECUTE_SCRIPT give you the answer to the user's request, your NEXT queue MUST contain ONLY the `TASK_COMPLETE` tool with the final answer. 5. If a script fails due to memory limits, reduce array sizes or iterations, or avoid heavy libraries like numpy for simple tasks. You are a code execution agent. Your goal is to use a sequence of tools to accomplish the user's request. Review the user's goal and the results of previous tool executions. Formulate a plan by creating a queue of tool actions. CRITICAL: If the user's request has been fully satisfied by the execution results, your response MUST be a single `TASK_COMPLETE` action to terminate the loop.", max_retries=10, output_schema=schema_execute
    )
    
    step4 = ReasoningStep.objects.create(
        blueprint=bp, name="Critique",
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
        name="Pipeline: StrategicPlan",
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

def seed_task_decomposer(CognitiveBlueprint, ReasoningStep, ResponseSchema):
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="Pipeline: Task Decomposer",
        defaults={'description': "Iteratively process nested tasks using a JSON queue."}
    )
    
    schema_queue, _ = ResponseSchema.objects.get_or_create(
        name="TaskQueue_Schema", defaults={'schema_type': 'pydantic', 'pydantic_model_name': 'TaskQueue'}
    )
    
    ReasoningStep.objects.filter(blueprint=bp).delete()
    
    step1 = ReasoningStep.objects.create(
        blueprint=bp, name="Task Breakdown",
        system_prompt="Break the user's complex request into a strict programmatic JSON queue of sub-tasks. If a sub-task is massive, you may flag it for nested blueprint delegation.", is_start_node=True, output_schema=schema_queue
    )
    
    step2 = ReasoningStep.objects.create(
        blueprint=bp, name="Iterative Processor",
        system_prompt="Look at the scratch.task_queue. Pop the first incomplete task. Complete it thoroughly using your reasoning. If there are more tasks, loop back.",
    )
    
    step3 = ReasoningStep.objects.create(
        blueprint=bp, name="Final Compiler",
        system_prompt="All tasks are complete. Summarize the total work done.",
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
