from django.contrib import admin, messages
from .models import CognitiveBlueprint, ReasoningStep, ModerationList, ResponseSchema


class ReasoningStepInline(admin.StackedInline):
    model = ReasoningStep
    extra = 1
    fk_name = 'blueprint'
    # Using StackedInline because the prompt text fields are large
    fieldsets = (
        (None, {
            'fields': (
                ('name', 'is_start_node'),
                'system_prompt',
                ('output_schema', 'action_hook'),
                'evaluation_criteria',
                ('on_success_step', 'on_failure_step'),
                'max_retries'
            )
        }),
    )


@admin.action(description="Clone selected Blueprint(s) and their Steps")
def clone_blueprint(modeladmin, request, queryset):
    for bp in queryset:
        original_steps = list(bp.steps.all())

        # Copy Blueprint
        bp.pk = None
        bp.name = f"Copy of {bp.name}"
        bp.save()

        step_mapping = {}
        # First pass: copy the nodes
        for step in original_steps:
            old_id = step.pk
            step.pk = None
            step.blueprint = bp
            step.save()
            step_mapping[old_id] = step

        # Second pass: relink the edges
        for old_step, new_step in zip(original_steps, step_mapping.values()):
            if old_step.on_success_step_id:
                new_step.on_success_step = step_mapping.get(old_step.on_success_step_id)
            if old_step.on_failure_step_id:
                new_step.on_failure_step = step_mapping.get(old_step.on_failure_step_id)
            new_step.save()

    modeladmin.message_user(request, f"Successfully cloned {queryset.count()} blueprint(s).", level=messages.SUCCESS)

@admin.register(CognitiveBlueprint)
class CognitiveBlueprintAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'step_count')
    inlines = [ReasoningStepInline]
    search_fields = ('name', 'description')
    actions = [clone_blueprint,
               ]
    def step_count(self, obj):
        return obj.steps.count()


@admin.register(ModerationList)
class ModerationListAdmin(admin.ModelAdmin):
    search_fields = ('name', 'concepts')

@admin.register(ResponseSchema)
class ResponseSchemaAdmin(admin.ModelAdmin):
    search_fields = ('name', 'description')