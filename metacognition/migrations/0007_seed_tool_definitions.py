from django.db import migrations

def seed_tool_definitions(apps, schema_editor):
    ToolDefinition = apps.get_model('metacognition', 'ToolDefinition')
    
    # Existing hardcoded action registry
    ACTION_REGISTRY = {
        "handle_research": "metacognition.actions.handle_research",
        "handle_execution_plan": "metacognition.actions.handle_execution_plan",
        "handle_difficult_prompt": "metacognition.actions.handle_difficult_prompt",
        "handle_result_critique": "metacognition.actions.handle_result_critique",
        "handle_active_reading": "metacognition.actions.handle_active_reading",
    }
    
    for name, python_path in ACTION_REGISTRY.items():
        ToolDefinition.objects.get_or_create(
            name=name,
            defaults={
                'description': f"Legacy action hook: {name}",
                'tool_type': 'builtin',
                'python_path': python_path,
                'is_active': True,
                'is_promoted': True,  # Grandfathered in
            }
        )

def reverse_seed_tool_definitions(apps, schema_editor):
    ToolDefinition = apps.get_model('metacognition', 'ToolDefinition')
    ToolDefinition.objects.filter(name__in=[
        "handle_research", "handle_execution_plan", "handle_difficult_prompt", 
        "handle_result_critique", "handle_active_reading"
    ]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('metacognition', '0006_reasoningstep_parallel_steps_agentcheckpoint_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_tool_definitions, reverse_seed_tool_definitions),
    ]
