from django.db import models
from background_resources.models import Document, RAGChunk
from llm_api.models import LocalAIModel, ExternalAIModel
import itertools


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

class Experiment(models.Model):
    """Defines the configuration being tested (The 'A' or 'B')."""
    investigation = models.ForeignKey(Investigation, on_delete=models.CASCADE, related_name='experiments', null=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    corpus = models.ForeignKey(BenchmarkCorpus, on_delete=models.CASCADE, blank=True, null=True)
    scenario_group = models.ForeignKey(ScenarioGroup, on_delete=models.CASCADE, blank=True, null=True)
    selected_model = models.ForeignKey(LocalAIModel, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific AI Model to use for this experiment.")
    # We store config as JSON so we can track chunk_sizes, prompts, models, etc.
    configuration = models.JSONField(default=dict, blank=True,
                                     help_text="Snapshot of settings: {'chunk_size': 500, 'model': 'gpt-4'}")
    iterations = models.IntegerField(default=1, help_text="Number of times to run each scenario in the experiment.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


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
