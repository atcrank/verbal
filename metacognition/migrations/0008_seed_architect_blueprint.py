from django.db import migrations

def seed_architect(apps, schema_editor):
    ToolDefinition = apps.get_model('metacognition', 'ToolDefinition')
    CognitiveBlueprint = apps.get_model('metacognition', 'CognitiveBlueprint')
    ReasoningStep = apps.get_model('metacognition', 'ReasoningStep')

    # Seed Meta-Tools
    meta_tools = [
        ("list_available_tools", "Returns a summary of all active ToolDefinitions.", "builtin", "metacognition.meta_tools.list_available_tools"),
        ("create_tool", "Creates a new ToolDefinition in the database.", "builtin", "metacognition.meta_tools.create_tool"),
        ("list_blueprints", "Returns all available CognitiveBlueprints with their step topology.", "builtin", "metacognition.meta_tools.list_blueprints"),
        ("create_blueprint", "Creates a new CognitiveBlueprint with linked ReasoningSteps.", "builtin", "metacognition.meta_tools.create_blueprint"),
        ("review_benchmark_results", "Fetches and summarises benchmark results for analysis.", "builtin", "metacognition.meta_tools.review_benchmark_results"),
        ("create_benchmark_scenario", "Creates a new BenchmarkScenario from the agent's analysis.", "builtin", "metacognition.meta_tools.create_benchmark_scenario"),
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

    # Seed Architect Blueprint
    bp, _ = CognitiveBlueprint.objects.get_or_create(
        name="The Architect",
        defaults={'description': "Meta-agent blueprint capable of self-composing tools and blueprints."}
    )

    # Step 1: Understand
    step1, _ = ReasoningStep.objects.get_or_create(
        blueprint=bp,
        name="Understand Request",
        defaults={
            'system_prompt': "You are The Architect. Analyze the user's request and determine if you need to create a new cognitive blueprint, a new tool, or review benchmark results. Output your strategic analysis.",
            'is_start_node': True,
        }
    )

    # Step 2: Research
    step2, _ = ReasoningStep.objects.get_or_create(
        blueprint=bp,
        name="Research Existing Tools",
        defaults={
            'system_prompt': "List all available tools and blueprints to see what you can reuse.",
            'action_hook': "list_available_tools",
            'is_start_node': False,
        }
    )

    # Step 3: Compose
    step3, _ = ReasoningStep.objects.get_or_create(
        blueprint=bp,
        name="Compose Solution",
        defaults={
            'system_prompt': "Based on your research, compose the solution. If a new blueprint is needed, format your output to trigger 'create_blueprint'. If a new tool is needed, use 'create_tool'.",
            'action_hook': "create_blueprint", # Assuming we want to create a blueprint for now
            'is_start_node': False,
        }
    )

    # Link steps
    step1.on_success_step = step2
    step1.save()
    step2.on_success_step = step3
    step2.save()


def reverse_seed_architect(apps, schema_editor):
    pass # No reverse logic for now

class Migration(migrations.Migration):
    dependencies = [
        ('metacognition', '0007_seed_tool_definitions'),
    ]
    operations = [
        migrations.RunPython(seed_architect, reverse_seed_architect),
    ]
