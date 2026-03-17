from django.core.management.base import BaseCommand
from benchmarking.models import BenchmarkCorpus, Experiment, ScenarioGroup
from benchmarking.runner import run_benchmark_suite


class Command(BaseCommand):
    help = 'Runs a benchmark suite defined in the database.'

    def add_arguments(self, parser):
        parser.add_argument('corpus_name', type=str, help='Name of the BenchmarkCorpus to run')
        parser.add_argument('experiment_name', type=str, help='Name of the Experiment (creates one if not exists)')

    def handle(self, *args, **options):
        corpus_name = options['corpus_name']
        exp_name = options['experiment_name']

        # 1. Load / Create Context
        try:
            corpus = BenchmarkCorpus.objects.get(name=corpus_name)
        except BenchmarkCorpus.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Corpus '{corpus_name}' not found."))
            return

        experiment, created = Experiment.objects.get_or_create(
            name=exp_name,
            defaults={'description': 'Auto-generated run', 'configuration': {}}
        )

        # Ensure experiment has a corpus linked (if not already)
        if not experiment.corpus:
            experiment.corpus = corpus
            experiment.save()

        # Note: If the experiment doesn't have a scenario_group, the runner might fail 
        # unless we assign one here or rely on legacy logic.
        # Delegate to the shared runner
        run_benchmark_suite(experiment, corpus, log_callback=self.stdout.write)
