from django.core.management.base import BaseCommand
from benchmarking.models import BenchmarkCorpus, Investigation, Experiment, ScenarioGroup


class Command(BaseCommand):
    help = "Sets up the 'Chunk Size Physics' investigation with 3 comparative experiments."

    def handle(self, *args, **options):
        # 1. Find the Corpus and Scenarios
        corpus_name = "Standard Candle Corpus"
        try:
            corpus = BenchmarkCorpus.objects.get(name=corpus_name)
            # We assume the Standard Candle group exists from the previous command
            group = ScenarioGroup.objects.get(name="Standard Candle Validation Set")
        except (BenchmarkCorpus.DoesNotExist, ScenarioGroup.DoesNotExist):
            self.stdout.write(self.style.ERROR(
                f"Standard Candle data not found. Run 'python manage.py create_standard_candle' first."))
            return

        # 2. Create the Investigation Container
        investigation_name = "Investigation 1: Chunk Size Physics"
        investigation, _ = Investigation.objects.get_or_create(
            name=investigation_name,
            defaults={
                "description": "Hypothesis: Smaller chunks improve precision for factoid retrieval, while larger chunks improve semantic context."
            }
        )

        self.stdout.write(f"Setting up '{investigation_name}'...")

        # 3. Define the Experimental Conditions (The Grid)
        # We vary chunk_size and chunk_overlap
        experiments_config = [
            {
                "name": "Physics - Small Chunks (200/20)",
                "config": {"chunk_size": 200, "chunk_overlap": 20}
            },
            {
                "name": "Physics - Medium Chunks (500/50)",
                "config": {"chunk_size": 500, "chunk_overlap": 50}
            },
            {
                "name": "Physics - Large Chunks (1500/150)",
                "config": {"chunk_size": 1500, "chunk_overlap": 150}
            }
        ]

        # 4. Create the Experiments
        for exp_data in experiments_config:
            experiment, created = Experiment.objects.get_or_create(
                name=exp_data["name"],
                investigation=investigation,
                defaults={
                    "description": f"Testing retrieval with size {exp_data['config']['chunk_size']}.",
                    "corpus": corpus,
                    "scenario_group": group,
                    "configuration": exp_data["config"],
                    "iterations": 1  # Start with 1 for speed, increase later
                }
            )
            if not created:
                # Update config if it already existed, to ensure it matches our definition
                experiment.configuration = exp_data["config"]
                experiment.corpus = corpus
                experiment.scenario_group = group
                experiment.save()
                self.stdout.write(f"  - Updated existing experiment: {experiment.name}")
            else:
                self.stdout.write(f"  - Created experiment: {experiment.name}")

        self.stdout.write(self.style.SUCCESS("✅ Investigation setup complete."))
        self.stdout.write("Go to the Admin Panel > Experiments, select these 3 experiments, and run 'Run Benchmark'.")