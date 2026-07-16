from django.contrib import admin, messages
from django.utils.html import format_html
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
            # In production, this should be offloaded to Celery.
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

from .exporters import export_scenarios
import os
from django.conf import settings

@admin.action(description="Export to new Fine-Tuning Dataset")
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
        task_calculate_dataset_metrics.delay(dataset.id)
        
        count += 1
        
    if count > 0:
        modeladmin.message_user(request, f"Successfully exported {count} datasets and queued metrics calculation.", level=messages.SUCCESS)

class FineTuningDatasetInline(admin.TabularInline):
    model = FineTuningDataset
    fields = ('name', 'file_path', 'format', 'created_at')
    readonly_fields = ('name', 'file_path', 'format', 'created_at')
    extra = 0
    show_change_link = True
    can_delete = False

from .tasks import task_train_lora

@admin.action(description="Train LoRA on this dataset")
def train_lora_on_dataset(modeladmin, request, queryset):
    count = 0
    for dataset in queryset:
        task_train_lora.delay(dataset.id)
        count += 1
    modeladmin.message_user(request, f"Dispatched {count} LoRA training tasks to Celery.", level=messages.SUCCESS)

@admin.register(FineTuningDataset)
class FineTuningDatasetAdmin(admin.ModelAdmin):
    list_display = ('name', 'scenario_group', 'format', 'example_count', 'adequacy_status', 'currency_status', 'created_at')
    search_fields = ('name', 'file_path')
    list_filter = ('format',)
    readonly_fields = ('example_count', 'total_tokens', 'semantic_diversity_score', 'estimated_training_minutes', 'adequacy_status', 'currency_status')
    actions = [train_lora_on_dataset]

    @admin.display(description='Dataset Adequacy')
    def adequacy_status(self, obj):
        if obj.example_count < 50:
            return format_html('<span style="color: red; font-weight: bold;">🔴 Too Small</span>')
        elif obj.semantic_diversity_score is not None and obj.semantic_diversity_score < 0.2:
            return format_html('<span style="color: orange; font-weight: bold;">⚠️ Low Diversity</span>')
        elif obj.example_count > 0:
            return format_html('<span style="color: green; font-weight: bold;">✅ Good</span>')
        return "Unknown"

    @admin.display(description='Currency Status')
    def currency_status(self, obj):
        if obj.is_stale:
            return format_html('<span style="color: orange; font-weight: bold;">⚠️ Stale (Source updated)</span>')
        return format_html('<span style="color: green; font-weight: bold;">🟢 Up to date</span>')

@admin.register(ScenarioGroup)
class ScenarioGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'updated_at')
    search_fields = ('name', 'description')
    filter_horizontal = ('scenarios',)
    inlines = [FineTuningDatasetInline]
    actions = [export_to_dataset]

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

@admin.register(BenchmarkRun)
class BenchmarkRunAdmin(admin.ModelAdmin):
    list_display = ('experiment', 'corpus', 'timestamp', 'average_rag_score', 'average_semantic_score', 'eval_success_rate')
    readonly_fields = ('configuration_snapshot',)
    inlines = [BenchmarkResultInline]

@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = ('run', 'scenario', 'rag_recall_score', 'semantic_score')
    readonly_fields = ('run', 'scenario', 'prompt_text', 'raw_retrieved_text', 'generated_response', 'duration_seconds', 'rag_recall_score', 'semantic_score')
    actions = [promote_to_ideal]