from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.utils import timezone
from .models import (BenchmarkCorpus, BenchmarkScenario, Experiment, Investigation, ScenarioGroup,
                     BenchmarkRun, BenchmarkResult, Document, FineTuningDataset)
from .runner import run_benchmark_suite

@admin.action(description="Run Benchmark")
def run_experiment_benchmark(modeladmin, request, queryset):
    for experiment in queryset:
        if not experiment.corpus:
            modeladmin.message_user(request, f"Experiment '{experiment.name}' has no corpus assigned. Skipping.", level=messages.WARNING)
            continue
        try:
            # This runs synchronously. For large suites, this might timeout the browser.
            # In production, this should be offloaded to verbal_tasks.
            run_record = run_benchmark_suite(experiment, experiment.corpus)
            if run_record:
                modeladmin.message_user(request, f"Completed Run #{run_record.id} for {experiment.name}.", level=messages.SUCCESS)
        except Exception as e:
            modeladmin.message_user(request, f"Error running {experiment.name}: {e}", level=messages.ERROR)

@admin.action(description="Run all experiments in this investigation")
def run_all_experiments_in_investigation(modeladmin, request, queryset):
    for investigation in queryset:
        experiments_to_run = investigation.experiments.all()
        if not experiments_to_run:
            modeladmin.message_user(request, f"No experiments found for investigation '{investigation.name}'.", level=messages.WARNING)
            continue
        run_experiment_benchmark(modeladmin, request, experiments_to_run)

@admin.action(description="Promote generated response to Ideal Answer")
def promote_to_ideal(modeladmin, request, queryset):
    count = 0
    for result in queryset:
        scenario = result.scenario
        scenario.ideal_answer = result.generated_response
        scenario.save()
        count += 1
    modeladmin.message_user(request, f"Updated {count} scenarios with new ideal answers.", level=messages.SUCCESS)

@admin.action(description="Constructor: Generate Full Config Matrix from this Experiment")
def generate_matrix_action(modeladmin, request, queryset):
    count = 0
    for exp in queryset:
        generated = exp.generate_comprehensive_matrix()
        count += len(generated)
    modeladmin.message_user(request, f"Successfully constructed {count} new experiment permutations!", level=messages.SUCCESS)

# Add it to your ExperimentAdmin class:
# actions = [generate_matrix_action, ...]

@admin.register(BenchmarkCorpus)
class BenchmarkCorpusAdmin(admin.ModelAdmin):
    filter_horizontal = ('documents',)

from .curation import curate_and_export_dataset
from .exporters import export_scenarios
import os
from django.conf import settings

@admin.action(description="Curate & Split into Fine-Tuning Dataset (85% Train / 15% Val)")
def curate_and_split_action(modeladmin, request, queryset):
    count = 0
    for group in queryset:
        if not group.scenarios.exists():
            modeladmin.message_user(request, f"Skipped '{group.name}' because it has no scenarios.", level=messages.WARNING)
            continue
        try:
            ds = curate_and_export_dataset(
                name=f"{group.name} Curated",
                scenario_group_ids=[group.id],
                split_ratio=0.85,
                format="sharegpt"
            )
            count += 1
            modeladmin.message_user(
                request,
                f"✅ Curated '{ds.name}': {ds.train_example_count} train examples, "
                f"{ds.val_example_count} held-out validation scenarios.",
                level=messages.SUCCESS
            )
        except Exception as e:
            modeladmin.message_user(request, f"Error curating '{group.name}': {e}", level=messages.ERROR)

@admin.action(description="Export to new Fine-Tuning Dataset (100% Train)")
def export_to_dataset(modeladmin, request, queryset):
    count = 0
    # Ensure datasets directory exists
    datasets_dir = os.path.join(settings.BASE_DIR, "datasets")
    os.makedirs(datasets_dir, exist_ok=True)
    
    for group in queryset:
        if not group.scenarios.exists():
            modeladmin.message_user(request, f"Skipped '{group.name}' because it has no scenarios.", level=messages.WARNING)
            continue
            
        jsonl_data = export_scenarios(group.id, format="sharegpt")
        filename = f"dataset_group_{group.id}_{timezone.now().strftime('%Y%m%d%H%M%S')}.jsonl"
        file_path = os.path.join(datasets_dir, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(jsonl_data)
            
        dataset = FineTuningDataset.objects.create(
            name=f"{group.name} Export",
            scenario_group=group,
            file_path=file_path,
            format="sharegpt"
        )
        
        from .tasks import task_calculate_dataset_metrics
        task_calculate_dataset_metrics.enqueue(dataset.id)
        
        count += 1
        
    if count > 0:
        modeladmin.message_user(request, f"Successfully exported {count} datasets and queued metrics calculation.", level=messages.SUCCESS)

class FineTuningDatasetInline(admin.TabularInline):
    model = FineTuningDataset
    fk_name = 'scenario_group'
    fields = ('name', 'validation_group', 'train_example_count', 'val_example_count', 'format', 'created_at')
    readonly_fields = ('name', 'validation_group', 'train_example_count', 'val_example_count', 'format', 'created_at')
    extra = 0
    show_change_link = True
    can_delete = False

from .tasks import task_train_lora

@admin.action(description="Train LoRA on this dataset")
def train_lora_on_dataset(modeladmin, request, queryset):
    count = 0
    for dataset in queryset:
        task_train_lora.enqueue(dataset.id)
        count += 1
    modeladmin.message_user(request, f"Dispatched {count} LoRA training task(s).", level=messages.SUCCESS)

@admin.register(FineTuningDataset)
class FineTuningDatasetAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'scenario_group', 'validation_group', 'format',
        'train_example_count', 'val_example_count', 'split_ratio',
        'adequacy_status', 'currency_status', 'created_at'
    )
    search_fields = ('name', 'file_path')
    list_filter = ('format',)
    readonly_fields = (
        'example_count', 'train_example_count', 'val_example_count', 'split_ratio',
        'total_tokens', 'semantic_diversity_score', 'estimated_training_minutes',
        'adequacy_status', 'currency_status', 'metadata'
    )
    actions = [train_lora_on_dataset]

    @admin.display(description='Dataset Adequacy')
    def adequacy_status(self, obj):
        if obj.example_count < 50:
            return mark_safe('<span style="color: red; font-weight: bold;">🔴 Too Small</span>')
        elif obj.semantic_diversity_score is not None and obj.semantic_diversity_score < 0.2:
            return mark_safe('<span style="color: orange; font-weight: bold;">⚠️ Low Diversity</span>')
        elif obj.example_count > 0:
            return mark_safe('<span style="color: green; font-weight: bold;">✅ Good</span>')
        return "Unknown"

    @admin.display(description='Currency Status')
    def currency_status(self, obj):
        if obj.is_stale:
            return mark_safe('<span style="color: orange; font-weight: bold;">⚠️ Stale (Source updated)</span>')
        return mark_safe('<span style="color: green; font-weight: bold;">🟢 Up to date</span>')

@admin.register(ScenarioGroup)
class ScenarioGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'updated_at')
    search_fields = ('name', 'description')
    filter_horizontal = ('scenarios',)
    inlines = [FineTuningDatasetInline]
    actions = [curate_and_split_action, export_to_dataset]

@admin.action(description="Copy selected scenarios to a new Scenario Group")
def create_new_scenario_group(modeladmin, request, queryset):
    group = ScenarioGroup.objects.create(
        name=f"Exported Group - {timezone.now().strftime('%Y-%m-%d %H:%M')}",
        description="Auto-generated from selected scenarios."
    )
    group.scenarios.set(queryset)
    url = reverse("admin:benchmarking_scenariogroup_change", args=[group.id])
    modeladmin.message_user(request, f"New group created with {queryset.count()} scenarios. You can rename it below.", level=messages.SUCCESS)
    return HttpResponseRedirect(url)

@admin.register(BenchmarkScenario)
class BenchmarkScenarioAdmin(admin.ModelAdmin):
    list_display = ('question', 'short_answer', 'source_doc', 'source_chunk')
    search_fields = ('question', 'ideal_answer', 'source_chunk__page_content')
    actions = [create_new_scenario_group]
    
    def short_answer(self, obj):
        return obj.ideal_answer[:50]

class ExperimentInline(admin.TabularInline):
    model = Experiment
    fields = ('name', 'corpus', 'scenario_group', 'selected_model', 'iterations', 'configuration')
    extra = 0
    show_change_link = True

@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'view_dashboard')
    inlines = [ExperimentInline]
    actions = [run_all_experiments_in_investigation]
    
    def view_dashboard(self, obj):
        url = reverse('investigation_dashboard', args=[obj.pk])
        return format_html('<a class="button" href="{}">View Dashboard</a>', url)
    view_dashboard.short_description = "Actions"

@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ('name', 'investigation', 'corpus', 'scenario_group', 'selected_model', 'iterations', 'created_at')
    list_filter = ('investigation', 'corpus')
    actions = [run_experiment_benchmark, generate_matrix_action]

class BenchmarkResultInline(admin.TabularInline):
    model = BenchmarkResult
    readonly_fields = ('scenario', 'rag_recall_score', 'semantic_score', 'duration_seconds')
    exclude = ('raw_retrieved_text', 'generated_response', 'prompt_text') # Too large for inline
    extra = 0
    can_delete = False
    show_change_link = True

class RunResolutionFilter(admin.SimpleListFilter):
    title = 'Execution & Resolution Status'
    parameter_name = 'resolution_status'

    def lookups(self, request, model_admin):
        return (
            ('defects', '⚠️ Active Defect (Unresolved)'),
            ('resolved', '✅ Resolved Defect'),
            ('assessed', '🛡️ Assessed / Healthy'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'resolved':
            return queryset.filter(is_resolved=True)
        elif self.value() == 'defects':
            from .hub import diagnose_run_defect
            defect_ids = [r.id for r in queryset.filter(is_resolved=False) if diagnose_run_defect(r) is not None]
            return queryset.filter(id__in=defect_ids)
        elif self.value() == 'assessed':
            from .hub import diagnose_run_defect
            assessed_ids = [r.id for r in queryset.filter(is_resolved=False) if diagnose_run_defect(r) is None]
            return queryset.filter(id__in=assessed_ids)
        return queryset


@admin.action(description="Mark selected runs as Resolved (Sign-off)")
def mark_runs_resolved_action(modeladmin, request, queryset):
    count = 0
    username = request.user.username or "staff"
    for run in queryset:
        run.mark_resolved(user=request.user, notes=f"Signed off via Django Admin by {username}")
        count += 1
    modeladmin.message_user(request, f"Successfully marked {count} benchmark run(s) as resolved by {username}.", level=messages.SUCCESS)


@admin.action(description="Re-open / Unmark resolution for selected runs")
def mark_runs_unresolved_action(modeladmin, request, queryset):
    count = 0
    for run in queryset:
        run.mark_unresolved()
        count += 1
    modeladmin.message_user(request, f"Re-opened {count} benchmark run(s) as active.", level=messages.INFO)


@admin.register(BenchmarkRun)
class BenchmarkRunAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'experiment', 'corpus', 'status_badge', 'resolved_signoff',
        'timestamp', 'average_semantic_score', 'eval_success_rate'
    )
    list_filter = (RunResolutionFilter, 'is_resolved', 'corpus', 'timestamp')
    search_fields = ('id', 'experiment__name', 'experiment__investigation__name', 'resolved_by__username', 'resolution_notes')
    raw_id_fields = ('resolved_by', 'experiment', 'corpus')
    list_select_related = ('experiment', 'corpus', 'resolved_by')
    readonly_fields = (
        'status_badge', 'defect_diagnostics_display', 'resolution_details_display',
        'resolved_at', 'configuration_snapshot'
    )
    inlines = [BenchmarkResultInline]
    actions = [mark_runs_resolved_action, mark_runs_unresolved_action]

    @admin.display(description='Run Status')
    def status_badge(self, obj):
        if obj.is_resolved:
            return mark_safe(
                '<span style="background: rgba(16, 185, 129, 0.15); color: #10b981; padding: 0.2rem 0.5rem; '
                'border-radius: 4px; font-weight: 600; font-size: 0.8rem;">'
                '✅ Resolved</span>'
            )
        from .hub import diagnose_run_defect
        defect = diagnose_run_defect(obj)
        if defect:
            return format_html(
                '<span style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; padding: 0.2rem 0.5rem; '
                'border-radius: 4px; font-weight: 600; font-size: 0.8rem;" title="{}">'
                '⚠️ Defect: {}</span>',
                defect.summary, defect.defect_category
            )
        return mark_safe(
            '<span style="background: rgba(99, 102, 241, 0.15); color: #6366f1; padding: 0.2rem 0.5rem; '
            'border-radius: 4px; font-weight: 600; font-size: 0.8rem;">'
            '🛡️ Assessed</span>'
        )

    @admin.display(description='Resolution Sign-Off')
    def resolved_signoff(self, obj):
        if obj.is_resolved:
            username = obj.resolved_by.username if obj.resolved_by else "staff"
            date_str = obj.resolved_at.strftime("%Y-%m-%d") if obj.resolved_at else ""
            return format_html(
                '<strong>{}</strong> <span style="color: #64748b; font-size: 0.75rem;">({})</span>',
                username, date_str
            )
        return mark_safe('<span style="color: #64748b;">—</span>')

    @admin.display(description='Defect Diagnostics')
    def defect_diagnostics_display(self, obj):
        from .hub import diagnose_run_defect
        defect = diagnose_run_defect(obj)
        if not defect:
            return mark_safe('<span style="color: #10b981;">No runtime or infrastructure defects detected. Run produced valid completions.</span>')
        return format_html(
            '<div style="background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.3); border-radius: 6px; padding: 0.75rem;">'
            '<div style="font-weight: 700; color: #f59e0b; margin-bottom: 0.25rem;">Defect Category: {}</div>'
            '<div style="color: #cbd5e1; margin-bottom: 0.5rem;">{}</div>'
            '<div style="font-family: monospace; font-size: 0.75rem; background: rgba(15, 23, 42, 0.6); padding: 0.5rem; border-radius: 4px; color: #fda4af; max-height: 150px; overflow-y: auto;">{}</div>'
            '</div>',
            defect.defect_category, defect.summary, defect.raw_error
        )

    @admin.display(description='Resolution Sign-off Details')
    def resolution_details_display(self, obj):
        if not obj.is_resolved:
            return mark_safe('<span style="color: #f59e0b;">Unresolved. Use the "Mark selected runs as Resolved" action or click below to sign off.</span>')
        username = obj.resolved_by.username if obj.resolved_by else "staff"
        date_str = obj.resolved_at.strftime("%Y-%m-%d %H:%M:%S") if obj.resolved_at else ""
        return format_html(
            '<div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 6px; padding: 0.75rem;">'
            '<div style="font-weight: 700; color: #10b981; margin-bottom: 0.25rem;">✅ Resolved & Signed Off</div>'
            '<div style="color: #cbd5e1; margin-bottom: 0.25rem;"><strong>Signed off by:</strong> {}</div>'
            '<div style="color: #cbd5e1; margin-bottom: 0.25rem;"><strong>Signed off at:</strong> {}</div>'
            '<div style="color: #94a3b8; font-style: italic;">{}</div>'
            '</div>',
            username, date_str, obj.resolution_notes or "No additional notes"
        )


@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = ('run', 'scenario', 'rag_recall_score', 'semantic_score')
    readonly_fields = ('run', 'scenario', 'prompt_text', 'raw_retrieved_text', 'generated_response', 'duration_seconds', 'rag_recall_score', 'semantic_score')
    actions = [promote_to_ideal]