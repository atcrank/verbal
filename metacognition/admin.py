from django.contrib import admin
from .models import CognitiveBlueprint, ReasoningStep, ModerationList


class ReasoningStepInline(admin.StackedInline):
    model = ReasoningStep
    extra = 1
    fk_name = 'blueprint'
    # Using StackedInline because the prompt text fields are large
    fields = (
        'name',
        'is_start_node',
        'system_prompt',
        'output_schema',
        'evaluation_criteria',
        ('on_success_step', 'on_failure_step')
    )


@admin.register(CognitiveBlueprint)
class CognitiveBlueprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'step_count')
    inlines = [ReasoningStepInline]
    search_fields = ('name', 'description')

    def step_count(self, obj):
        return obj.steps.count()