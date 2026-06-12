import os
import shutil
from pathlib import Path
from django.test import TestCase, Client, override_settings, tag
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.contrib.auth.models import User

from background_resources.models import Document, ReadingStrategy
from benchmarking.models import (
    BenchmarkCorpus, ScenarioGroup, BenchmarkScenario, 
    Investigation, Experiment, BenchmarkRun, BenchmarkResult
)
from benchmarking.generators import generate_scenarios_for_document
from benchmarking.runner import EvaluationScore, run_benchmark_suite
from llm_api.apps import service_registry

# Define isolated test paths
TEST_BASE_DIR = Path(settings.BASE_DIR) / "test_data_benchmarking"
TEST_VECTOR_STORE = TEST_BASE_DIR / "vector_store"
TEST_CHUNK_STORE = TEST_BASE_DIR / "chunk_store"
TEST_FILES_DIR = TEST_BASE_DIR / "files"

class BenchmarkingIntegrationTests(TestCase):
    """
    Integration tests for the Benchmarking and Science tools.
    Uses real AI/RAG services.
    """

    @classmethod
    def setUpClass(cls):
        cls.settings_override = override_settings(
            MEDIA_ROOT=TEST_FILES_DIR,
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
        )
        cls.settings_override.enable()
        super().setUpClass()
        os.makedirs(TEST_FILES_DIR, exist_ok=True)

        print("\n>>> 🚀 INITIALIZING BENCHMARKING SERVICES (This may take time) <<<")
        cls.ai_service = service_registry.ai_service
        cls.rag_service = service_registry.rag_service
        cls.grips_service = service_registry.grips_service

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TEST_BASE_DIR):
            shutil.rmtree(TEST_BASE_DIR)

        # CRITICAL: Disconnect SQLAlchemy pools to allow test DB to be dropped
        if service_registry._rag_service:
            service_registry._rag_service.disconnect()
        if service_registry._grips_service:
            service_registry._grips_service.disconnect()

        super().tearDownClass()
        cls.settings_override.disable()

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'password123')

    def _create_dummy_document(self, name, content, chunk_size=200):
        """Helper to create a fast, ingestable document."""
        file_path = TEST_FILES_DIR / name
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        with open(file_path, 'rb') as f:
            django_file = SimpleUploadedFile(name=name, content=f.read(), content_type='text/plain')
            
        doc = Document.objects.create(title=name, file=django_file, chunk_size=chunk_size, chunk_overlap=20)
        return doc

    @tag('e2e')
    def test_1_create_standard_candle(self):
        """Ensure the management command successfully builds the data structures."""
        print("\n>>> Test 1: Create Standard Candle")
        call_command('create_standard_candle')
        
        # Verify objects were created
        self.assertTrue(Investigation.objects.filter(name="Standard Candle Investigation").exists())
        corpus = BenchmarkCorpus.objects.get(name="Standard Candle Corpus")
        self.assertEqual(corpus.documents.count(), 2)
        
        group = ScenarioGroup.objects.get(name="Standard Candle Validation Set")
        self.assertEqual(group.scenarios.count(), 6) # 3 for Paris, 3 for Apollo
        
        experiment = Experiment.objects.get(name="Baseline Run")
        self.assertEqual(experiment.corpus, corpus)
        self.assertEqual(experiment.scenario_group, group)

    @tag('e2e')
    def test_2_run_fast_benchmark(self):
        """
        Ensure the runner executes correctly.
        We create a tiny custom experiment instead of running the full Standard Candle 
        to save GPU inference time during testing.
        """
        print("\n>>> Test 2: Run Fast Benchmark")
        doc = self._create_dummy_document("tiny_test.txt", "The quick brown fox jumps over the lazy dog.")
        corpus = BenchmarkCorpus.objects.create(name="Tiny Corpus")
        corpus.documents.add(doc)
        
        group = ScenarioGroup.objects.create(name="Tiny Group")
        scenario = BenchmarkScenario.objects.create(
            question="What color is the fox?",
            ideal_answer="Brown",
            expected_keywords=["brown", "fox"]
        )
        group.scenarios.add(scenario)
        
        exp = Experiment.objects.create(
            name="Tiny Run",
            corpus=corpus,
            scenario_group=group,
            iterations=1,
            configuration={"chunk_size": 100}
        )
        
        run_record = run_benchmark_suite(exp, corpus)
        
        self.assertIsNotNone(run_record)
        self.assertTrue(BenchmarkResult.objects.filter(run=run_record).exists())
        self.assertIsNotNone(run_record.average_rag_score)
        self.assertIsNotNone(run_record.average_semantic_score)

    @tag('e2e')
    def test_3_generate_synthetic_scenarios(self):
        """Ensure the LLM can generate valid JSON scenarios from a document."""
        BenchmarkScenario.objects.all().delete()
        print("\n>>> Test 3: Generate Synthetic Scenarios")
        content = (
            "Water hammer is a pressure surge or wave caused when a fluid in motion is forced to stop "
            "or change direction suddenly. This phenomenon commonly occurs when a valve closes suddenly "
            "at an end of a pipeline system, and a pressure wave propagates in the pipe. It is also "
            "called hydraulic shock. This pressure wave can cause major problems, from noise and vibration "
            "to pipe collapse. It is possible to reduce the effects of the water hammer pulses with "
            "accumulators, expansion tanks, surge tanks, blowoff valves, and other features."
        )
        doc = self._create_dummy_document("water_hammer.txt", content, chunk_size=1000)
        
        # Call the generator
        count = generate_scenarios_for_document(doc, stride=1, group_name="Synthetic Test")
        print("Count is ", count)
        self.assertGreater(count, 0, "Should have generated at least one scenario.")
        group = ScenarioGroup.objects.get(name="Synthetic Test")
        self.assertEqual(group.scenarios.count(), count)
        
        # Check content quality
        scenario = group.scenarios.first()
        self.assertTrue(len(scenario.question) > 5)
        self.assertTrue(len(scenario.expected_keywords) > 0)

    @tag('e2e')
    def test_4_grid_experiment_creation(self):
        """Test the Investigation mathematical utility."""
        print("\n>>> Test 4: Grid Experiment Creation")
        inv = Investigation.objects.create(name="Grid Test")
        
        param_grid = {
            "chunk_size": [100, 200],
            "chunk_overlap": [10, 20]
        }
        
        experiments = inv.create_grid_experiments(
            base_name="GridExp", corpus=None, scenario_group=None,
            base_config={"model": "gpt-4"}, param_grid=param_grid
        )
        
        # 2 sizes * 2 overlaps = 4 experiments
        self.assertEqual(len(experiments), 4)
        self.assertEqual(Experiment.objects.filter(investigation=inv).count(), 4)

    @tag('e2e')
    def test_5_evaluation_score_clamping(self):
        """Verify Pydantic clamps LLM hallucinations to the 1-5 scale."""
        print("\n>>> Test 5: Evaluation Clamping")
        e1 = EvaluationScore(reasoning="Incredible.", score=10)
        self.assertEqual(e1.score, 5, "Should clamp max to 5")
        
        e2 = EvaluationScore(reasoning="Terrible.", score=-5)
        self.assertEqual(e2.score, 1, "Should clamp min to 1")

    @tag('e2e')
    def test_6_dashboard_view(self):
        """Ensure the pivot logic in the dashboard doesn't crash."""
        print("\n>>> Test 6: Dashboard View Load")
        self.client.login(username='admin', password='password123')
        inv = Investigation.objects.create(name="Empty Dashboard Test")
        
        response = self.client.get(f"/benchmarking/dashboard/{inv.id}/")
        self.assertEqual(response.status_code, 200)
