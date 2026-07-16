from django.contrib import admin, messages
from django import forms
from django.core.exceptions import ValidationError
from .models import CognitiveBlueprint, ReasoningStep, ModerationList, ResponseSchema


class CognitiveBlueprintForm(forms.ModelForm):
    class Meta:
        model = CognitiveBlueprint
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.is_canonical:
            if self.has_changed():
                raise ValidationError("This is a canonical blueprint and is read-only. Please use the 'Clone Blueprint' action to create an editable variant.")
        return cleaned_data

class ReasoningStepFormSet(forms.models.BaseInlineFormSet):
    def clean(self):
        super().clean()
        # If any inline form has changed and the parent is canonical, block it.
        if self.instance.pk and self.instance.is_canonical:
            for form in self.forms:
                if form.has_changed():
                    raise ValidationError("Cannot mutate reasoning steps of a canonical blueprint. Clone the blueprint first.")

class ReasoningStepInline(admin.StackedInline):
    model = ReasoningStep
    formset = ReasoningStepFormSet
    extra = 1
    fk_name = 'blueprint'
    # Using StackedInline because the prompt text fields are large
    fieldsets = (
        (None, {
            'fields': (
                ('name', 'is_start_node', 'is_canonical', 'is_active'),
                'system_prompt',
                ('output_schema', 'available_tools'),
                'evaluation_criteria',
                ('on_success_step', 'on_failure_step'),
                ('max_retries', 'lora_adapter')
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
        bp.is_canonical = False
        bp.save()

        step_mapping = {}
        # First pass: copy the nodes
        for step in original_steps:
            old_id = step.pk
            step.pk = None
            step.blueprint = bp
            step.is_canonical = False
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
    form = CognitiveBlueprintForm
    list_display = ('name', 'description', 'step_count', 'is_canonical')
    inlines = [ReasoningStepInline]
    search_fields = ('name', 'description')
    list_filter = ('is_canonical', 'is_autonomous')
    actions = [clone_blueprint]
    def step_count(self, obj):
        return obj.steps.count()

@admin.action(description="Evolve Step (Create Child Variant)")
def evolve_step(modeladmin, request, queryset):
    for step in queryset:
        step.create_variant(variant_intent="Manual Admin Evolution")
    modeladmin.message_user(request, f"Created {queryset.count()} new child variants.", level=messages.SUCCESS)

@admin.register(ReasoningStep)
class ReasoningStepAdmin(admin.ModelAdmin):
    list_display = ('name', 'blueprint', 'is_canonical', 'is_active')
    list_filter = ('blueprint', 'is_canonical', 'is_active')
    actions = [evolve_step]


@admin.register(ModerationList)
class ModerationListAdmin(admin.ModelAdmin):
    search_fields = ('name', 'concepts')

@admin.register(ResponseSchema)
class ResponseSchemaAdmin(admin.ModelAdmin):
    search_fields = ('name', 'description')