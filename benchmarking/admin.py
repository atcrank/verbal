from django.contrib import admin, messages
from django.utils.html import format_html
from django.urls import reverse
from .models import (BenchmarkCorpus, BenchmarkScenario, Experiment, Investigation, ScenarioGroup,
                     BenchmarkRun, BenchmarkResult, Document)
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

@admin.register(BenchmarkCorpus)
class BenchmarkCorpusAdmin(admin.ModelAdmin):
    filter_horizontal = ('documents',)

@admin.register(ScenarioGroup)
class ScenarioGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name', 'description')
    filter_horizontal = ('scenarios',)

@admin.register(BenchmarkScenario)
class BenchmarkScenarioAdmin(admin.ModelAdmin):
    list_display = ('question', 'short_answer')
    search_fields = ('question', 'ideal_answer')
    
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
    actions = [run_experiment_benchmark]

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