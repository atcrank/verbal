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
        new_bp = CognitiveBlueprint.objects.create(
            name=f"Copy of {bp.name}",
            description=bp.description,
            is_autonomous=bp.is_autonomous,
            is_canonical=False,
            parent=bp
        )

        step_mapping = {}
        # First pass: copy the nodes
        for old_step in original_steps:
            new_step = ReasoningStep.objects.get(pk=old_step.pk)
            new_step.pk = None
            new_step.blueprint = new_bp
            new_step.is_canonical = False
            new_step.performance_score = 0.0
            new_step.save()
            
            # Copy M2M fields
            new_step.available_tools.set(old_step.available_tools.all())
            new_step.parallel_steps.set(old_step.parallel_steps.all())
            
            step_mapping[old_step.pk] = new_step

        # Second pass: relink the edges
        for old_step in original_steps:
            new_step = step_mapping[old_step.pk]
            if old_step.on_success_step_id:
                new_step.on_success_step = step_mapping.get(old_step.on_success_step_id)
            if old_step.on_failure_step_id:
                new_step.on_failure_step = step_mapping.get(old_step.on_failure_step_id)
            new_step.save()

    modeladmin.message_user(request, f"Successfully cloned {queryset.count()} blueprint(s).", level=messages.SUCCESS)

@admin.register(CognitiveBlueprint)
class CognitiveBlueprintAdmin(admin.ModelAdmin):
    form = CognitiveBlueprintForm
    list_display = ('name', 'description', 'step_count', 'is_canonical', 'family_success_probability', 'blueprint_family')
    inlines = [ReasoningStepInline]
    search_fields = ('name', 'description')
    list_filter = ('is_canonical', 'is_autonomous')
    actions = [clone_blueprint]
    readonly_fields = ('resolved_steps_display',)

    def step_count(self, obj):
        return obj.steps.count()

    def blueprint_family(self, obj):
        current = obj
        seen = set()
        while current.parent and current.parent.id not in seen:
            seen.add(current.id)
            current = current.parent
        return current.name

    def resolved_steps_display(self, obj):
        try:
            groups = ReasoningStep.objects.active_for_blueprint(obj)
        except Exception as e:
            return f"Error resolving steps: {e}"
        if not groups:
            return "No active steps."
        html = "<ul>"
        for root_id, variants in groups.items():
            html += f"<li>Lineage Root {root_id}<ul>"
            for step in variants:
                html += f"<li><b>{step.name}</b> (ID: {step.id}, Intent: '{step.variant_intent or 'N/A'}', Weight: {step.selection_weight})</li>"
            html += "</ul></li>"
        html += "</ul>"
        from django.utils.safestring import mark_safe
        return mark_safe(html)

    resolved_steps_display.short_description = "Active Steps by Lineage"

@admin.action(description="Evolve Step (Create Child Variant)")
def evolve_step(modeladmin, request, queryset):
    for step in queryset:
        step.create_variant(variant_intent="Manual Admin Evolution")
    modeladmin.message_user(request, f"Created {queryset.count()} new child variants.", level=messages.SUCCESS)

@admin.action(description="Activate Variant & Retire Parent")
def activate_variant_retire_parent(modeladmin, request, queryset):
    count = 0
    for step in queryset:
        if step.parent_step:
            step.is_active = True
            step.is_pending_review = False
            step.save()
            parent = step.parent_step
            parent.is_active = False
            parent.save()
            count += 1
    modeladmin.message_user(request, f"Activated {count} variant(s) and retired their parent step(s).", level=messages.SUCCESS)

@admin.action(description="Reject Variant")
def reject_variant(modeladmin, request, queryset):
    queryset.update(is_active=False, is_pending_review=False)
    modeladmin.message_user(request, f"Rejected {queryset.count()} variant(s).", level=messages.SUCCESS)

@admin.register(ReasoningStep)
class ReasoningStepAdmin(admin.ModelAdmin):
    list_display = ('name', 'blueprint', 'is_canonical', 'is_active', 'lineage_depth', 'is_pending_review', 'proposed_by')
    list_filter = ('blueprint', 'is_canonical', 'is_active', 'is_pending_review', 'proposed_by')
    actions = [evolve_step, activate_variant_retire_parent, reject_variant]

    def lineage_depth(self, obj):
        depth = 0
        current = obj
        seen = set()
        while current.parent_step and current.parent_step.id not in seen:
            seen.add(current.id)
            current = current.parent_step
            depth += 1
        return depth


@admin.register(ModerationList)
class ModerationListAdmin(admin.ModelAdmin):
    search_fields = ('name', 'concepts')

@admin.register(ResponseSchema)
class ResponseSchemaAdmin(admin.ModelAdmin):
    search_fields = ('name', 'description')