from django.db import models
from background_resources.models import Document, RAGChunk
from llm_api.models import LocalAIModel, ExternalAIModel
from django.utils.safestring import mark_safe
import itertools
import pandas as pd


class BenchmarkCorpus(models.Model):
    """A specific set of documents used for a test suite."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    documents = models.ManyToManyField(Document, related_name="benchmark_corpora", blank=True)

    class Meta:
        verbose_name_plural = "Benchmark Corpora"

    def __str__(self):
        return self.name

class ScenarioGroup(models.Model):
    """A reusable collection of benchmark scenarios."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    scenarios = models.ManyToManyField('BenchmarkScenario', related_name='groups', blank=True)

    def __str__(self):
        return self.name

class BenchmarkScenario(models.Model):
    """A single test case: Question + Expected Truth."""
    question = models.TextField()
    ideal_answer = models.TextField(help_text="The ground truth answer for semantic comparison.")
    expected_keywords = models.JSONField(default=list,
                                         help_text="List of strings that SHOULD be in the retrieved context.")
    source_doc = models.ForeignKey(Document, on_delete=models.CASCADE, null=True, blank=True)
    source_chunk = models.ForeignKey(RAGChunk, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.question[:50]}"

class Investigation(models.Model):
    """Organizes a set of experiments testing a specific hypothesis."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def to_dataframe(self):
        """
        Exports all benchmark results for this investigation into a flattened Pandas DataFrame.
        Nested JSON keys automatically become columns (e.g., 'extra_metrics.hop_count').
        """
        results = BenchmarkResult.objects.filter(run__experiment__investigation=self).values(
            'run__experiment__name',
            'run__id',
            'scenario__question',
            'duration_seconds',
            'rag_recall_score',
            'semantic_score',
            'faithfulness_score',
            'relevance_score',
            'extra_metrics'
        )
        
        if not results:
            return pd.DataFrame()
            
        df = pd.json_normalize(list(results))
        
        # Set a MultiIndex for elegant grouping and statistical aggregation
        df.set_index(['run__experiment__name', 'scenario__question'], inplace=True)
        return df

    def __str__(self):
        return self.name

    def create_grid_experiments(self, base_name, corpus, scenario_group, base_config, param_grid):
        """
        Tool to create a set of experiments varying in a regular way.
        param_grid: dict of {key: [list of values]}
        """
        keys = param_grid.keys()
        values_list = param_grid.values()
        combinations = list(itertools.product(*values_list))
        
        experiments = []
        for combo in combinations:
            config = base_config.copy()
            name_parts = [base_name]
            for k, v in zip(keys, combo):
                config[k] = v
                name_parts.append(f"{k}={v}")
            
            exp = Experiment.objects.create(
                investigation=self,
                name=" - ".join(name_parts),
                description=f"Auto-generated grid search. {config}",
                corpus=corpus,
                scenario_group=scenario_group,
                configuration=config
            )
            experiments.append(exp)
        return experiments

CONFIG_HELP_TEXT = mark_safe(
    "Settings JSON. Valid options:<br>"
    "<ul style='margin-left: 20px;'>"
    "<li><b>rag_strategy</b>: 'none', 'default', 'grobid', 'prompt', 'regex', 'abbreviations', 'all'</li>"
    "<li><b>chunk_size</b>: int (e.g., 1000)</li>"
    "<li><b>chunk_overlap</b>: int (e.g., 200)</li>"
    "<li><b>generation_target</b>: 'direct', 'blueprint', 'grips'</li>"
    "<li><b>blueprint_id</b>: int (Required for 'blueprint' target)</li>"
    "</ul>"
)

class Experiment(models.Model):
    """Defines the configuration being tested (The 'A' or 'B')."""
    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name='experiments', null=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    corpus = models.ForeignKey(BenchmarkCorpus, on_delete=models.CASCADE, blank=True, null=True)
    scenario_group = models.ForeignKey(ScenarioGroup, on_delete=models.CASCADE, blank=True, null=True)
    selected_model = models.ForeignKey(LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific AI Model to use for this experiment.")
    # We store config as JSON so we can track chunk_sizes, prompts, models, etc.
    configuration = models.JSONField(default=dict, blank=True, help_text=CONFIG_HELP_TEXT)
    iterations = models.IntegerField(default=1, help_text="Number of times to run each scenario in the experiment.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def generate_comprehensive_matrix(self):
        """
        Constructor that uses this Experiment as a template to generate a full suite 
        of valid RAG options and chunk sizes for A/B testing.
        """
        if not self.investigation:
            inv = Investigation.objects.create(name=f"Matrix based on {self.name}")
            self.investigation = inv
            self.save()
            
        base_config = self.configuration.copy()
        # Remove keys we are about to grid-search to prevent collisions
        base_config.pop("rag_strategy", None)
        base_config.pop("chunk_size", None)
        
        param_grid = {
            "rag_strategy": ["none", "default", "grobid", "regex", "abbreviations", "prompt", "all"],
            "chunk_size": [500, 1000, 2000]
        }
        
        experiments = self.investigation.create_grid_experiments(
            base_name=f"Matrix ({self.name})",
            corpus=self.corpus,
            scenario_group=self.scenario_group,
            base_config=base_config,
            param_grid=param_grid
        )
        
        # Ensure all generated experiments inherit the selected model and iterations
        for exp in experiments:
            exp.selected_model = self.selected_model
            exp.iterations = self.iterations
            exp.save()
            
        return experiments


class BenchmarkRun(models.Model):
    """Log of a specific execution of an Experiment on a Corpus."""
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE)
    corpus = models.ForeignKey(BenchmarkCorpus, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    average_rag_score = models.FloatField(null=True)
    average_semantic_score = models.FloatField(null=True)
    average_faithfulness = models.FloatField(null=True)
    average_relevance = models.FloatField(null=True)
    eval_success_rate = models.FloatField(null=True, help_text="Rate of valid JSON generations by the LLM Judge")
    configuration_snapshot = models.JSONField(default=dict, help_text="Configuration state at time of run")

    def __str__(self):
        return f"Run {self.id} - {self.experiment.name}"


class BenchmarkResult(models.Model):
    """The atomic result of one Scenario in one Run."""
    run = models.ForeignKey(BenchmarkRun, on_delete=models.CASCADE)
    scenario = models.ForeignKey(BenchmarkScenario, on_delete=models.CASCADE)

    prompt_text = models.TextField(default="")
    raw_retrieved_text = models.TextField()
    generated_response = models.TextField()
    duration_seconds = models.FloatField()

    rag_recall_score = models.FloatField(help_text="0.0 to 1.0 based on keyword hits")
    semantic_score = models.FloatField(help_text="Cosine similarity to ideal_answer")
    faithfulness_score = models.FloatField(null=True, blank=True, help_text="1-5 score normalized to 0-1")
    relevance_score = models.FloatField(null=True, blank=True, help_text="1-5 score normalized to 0-1")
    extra_metrics = models.JSONField(default=dict, help_text="For future evolving metrics")
