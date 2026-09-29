import json
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
    Investigation, Experiment, BenchmarkRun, BenchmarkResult,
    FineTuningDataset
)
from benchmarking.generators import generate_scenarios_for_document
from benchmarking.runner import EvaluationScore, run_benchmark_suite
from benchmarking.long_context_evaluator import run_long_context_evaluation
from benchmarking.hardware import (
    HardwareProfile,
    TrainingConfig,
    detect_hardware_profile,
    recommend_training_config,
)
from benchmarking.training import train_lora_adapter, TrainingResult
from benchmarking.closed_loop import run_closed_loop_ab_evaluation, ABEvaluationResult
from llm_api.apps import service_registry
from llm_api.models import LoRAAdapter, LocalAIModel

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
        )
        cls.settings_override.enable()
        super().setUpClass()
        os.makedirs(TEST_FILES_DIR, exist_ok=True)

        print("\n>>> 🚀 USING LIVE INFERENCE SERVER FOR BENCHMARKING <<<")
        cls.ai_service = service_registry.ai_service
        
        # Configure External API to point to the live local server
        from django.contrib.auth.models import User
        from llm_api.models import ExternalAIModel, UserActiveModel
        
        cls.test_system_user, _ = User.objects.get_or_create(username='test_system_user')
        ext_api, _ = ExternalAIModel.objects.get_or_create(
            name="Live Inference Server",
            provider="openai",
            api_url="http://127.0.0.1:8001/api/llm/v1/chat/completions",
            api_model_name="local-model"
        )
        UserActiveModel.objects.update_or_create(
            user=cls.test_system_user,
            defaults={"active_external": ext_api, "use_external": True}
        )
        
        # Force all LLM calls to use the external API config
        cls.original_outline = cls.ai_service.generate_outline
        cls.original_resp = cls.ai_service.generate_response2
        
        cls.ai_service.generate_outline = lambda *args, **kwargs: cls.original_outline(*args, **{**kwargs, 'user': cls.test_system_user})
        cls.ai_service.generate_response2 = lambda *args, **kwargs: cls.original_resp(*args, **{**kwargs, 'user': cls.test_system_user})

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

        # Restore original ai_service methods
        if hasattr(cls, 'original_outline'):
            cls.ai_service.generate_outline = cls.original_outline
            cls.ai_service.generate_response2 = cls.original_resp

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

    @tag('e2e')
    def test_7_generation_metrics_captured(self):
        """Verify generation metrics are captured correctly."""
        print("\n>>> Test 7: Generation Metrics")
        from llm_api.ai_service import get_last_generation_metrics
        
        # Make a real call
        messages = [{"role": "user", "content": "Say 'hello world' and nothing else."}]
        responses = self.ai_service.generate_response2(messages=messages, max_new_tokens=10, num_return_sequences=1)
        
        metrics = get_last_generation_metrics()
        self.assertIsNotNone(metrics)
        self.assertGreater(metrics.total_duration_ms, 0)
        self.assertGreater(metrics.tokens_per_second, 0)
        self.assertGreater(metrics.output_tokens, 0)

    @tag('e2e')
    def test_8_benchmark_result_throughput(self):
        """Verify tokens_per_second is captured in BenchmarkResult."""
        print("\n>>> Test 8: Benchmark Result Throughput")
        corpus = BenchmarkCorpus.objects.create(name="Throughput Test Corpus")
        scen1 = BenchmarkScenario.objects.create(
            question="What is 2+2? Answer briefly.",
            ideal_answer="4"
        )
        group = ScenarioGroup.objects.create(name="Throughput Group")
        group.scenarios.add(scen1)
        
        exp = Experiment.objects.create(
            name="Throughput Test Exp",
            corpus=corpus,
            scenario_group=group,
            configuration={"rag_strategy": "none", "generation_target": "direct"}
        )
        
        run_record = run_benchmark_suite(exp, corpus)
        result = BenchmarkResult.objects.get(run=run_record, scenario=scen1)
        
        self.assertIn("tokens_per_second", result.extra_metrics)
        self.assertIn("generation_duration_ms", result.extra_metrics)
        self.assertGreater(result.extra_metrics["tokens_per_second"], 0)

    @tag('e2e')
    def test_9_long_context_cumulative_tokens(self):
        """Verify long context evaluator tracks cumulative tokens."""
        print("\n>>> Test 9: Long Context Evaluator Cumulative Tokens")
        corpus = BenchmarkCorpus.objects.create(name="LC Test Corpus")
        group = ScenarioGroup.objects.create(name="LC Group")
        
        for i in range(3):
            s = BenchmarkScenario.objects.create(
                question=f"Question {i}: Give me a very short sentence.",
                ideal_answer=f"Answer {i}"
            )
            group.scenarios.add(s)
            
        exp = Experiment.objects.create(
            name="LC Test Exp",
            corpus=corpus,
            scenario_group=group,
            configuration={"rag_strategy": "none", "context_mode": "chat"}
        )
        
        run_record = run_long_context_evaluation(exp, corpus)
        results = BenchmarkResult.objects.filter(run=run_record).order_by('id')
        
        self.assertEqual(results.count(), 3)
        
        prev_tokens = -1
        for res in results:
            self.assertIn("cumulative_input_tokens", res.extra_metrics)
            current_tokens = res.extra_metrics["cumulative_input_tokens"]
            self.assertGreater(current_tokens, prev_tokens)
            prev_tokens = current_tokens


class DatasetCurationAndValidationSplitTests(TestCase):
    """Unit tests for multi-source dataset curation and automated validation splitting."""

    def setUp(self):
        self.test_dir = Path(settings.BASE_DIR) / "test_data_curation"
        os.makedirs(self.test_dir, exist_ok=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_harvest_from_scenario_group(self):
        """Verify harvesting extraction from existing ScenarioGroups."""
        from benchmarking.curation import harvest_from_scenario_group
        group = ScenarioGroup.objects.create(name="Curation Harvest Group")
        for i in range(5):
            s = BenchmarkScenario.objects.create(
                question=f"Question {i} on Causal Bounds?",
                ideal_answer=f"Answer {i} detailing Manski bounds.",
                expected_keywords=["bounds", "causal"]
            )
            group.scenarios.add(s)

        cands = harvest_from_scenario_group(group.id)
        self.assertEqual(len(cands), 5)
        self.assertEqual(cands[0].source_type, "scenario")
        self.assertIn("Causal Bounds", cands[0].prompt)
        self.assertEqual(cands[0].expected_keywords, ["bounds", "causal"])

    def test_harvest_from_prompt_logs(self):
        """Verify harvesting only high-signal thumbs-up production logs."""
        from llm_api.models import PromptResponseLog
        from benchmarking.curation import harvest_from_prompt_logs

        # Approved log
        PromptResponseLog.objects.create(
            user_prompt="Explain instrumental variables.",
            generated_response="An instrumental variable is exogenous...",
            user_feedback=1,
            step_status="SUCCESS"
        )
        # Unapproved or negative feedback log
        PromptResponseLog.objects.create(
            user_prompt="What is p-hacking?",
            generated_response="P-hacking is...",
            user_feedback=0,
            step_status="SUCCESS"
        )
        # Failed step log
        PromptResponseLog.objects.create(
            user_prompt="Calculate ATE.",
            generated_response="",
            user_feedback=1,
            step_status="FAILED"
        )

        cands = harvest_from_prompt_logs(min_feedback=1)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].prompt, "Explain instrumental variables.")
        self.assertEqual(cands[0].source_type, "prompt_log")

    def test_split_candidates_deterministic(self):
        """Verify deterministic train/val splitting logic with edge case guards."""
        from benchmarking.curation import CurationCandidate, split_candidates
        cands = [CurationCandidate(prompt=f"Q{i}", completion=f"A{i}", source_type="scenario") for i in range(10)]

        train, val = split_candidates(cands, split_ratio=0.8, seed=42)
        self.assertEqual(len(train), 8)
        self.assertEqual(len(val), 2)

        # Confirm non-overlapping
        train_prompts = set(c.prompt for c in train)
        val_prompts = set(c.prompt for c in val)
        self.assertEqual(len(train_prompts.intersection(val_prompts)), 0)

        # Confirm seed reproducibility
        train2, val2 = split_candidates(cands, split_ratio=0.8, seed=42)
        self.assertEqual([c.prompt for c in train], [c.prompt for c in train2])
        self.assertEqual([c.prompt for c in val], [c.prompt for c in val2])

    def test_curate_and_export_dataset_end_to_end(self):
        """Verify end-to-end dataset creation, file writing, and validation ScenarioGroup auto-generation."""
        import json
        from benchmarking.curation import curate_and_export_dataset
        from benchmarking.models import FineTuningDataset

        group = ScenarioGroup.objects.create(name="Source Group for Curation")
        for i in range(10):
            s = BenchmarkScenario.objects.create(
                question=f"Unique Question {i}: What is DAG factor {i}?",
                ideal_answer=f"Unique Answer {i}: DAG factor {i} represents confounder node."
            )
            group.scenarios.add(s)

        dataset = curate_and_export_dataset(
            name="Causal Inference Tuning",
            scenario_group_ids=[group.id],
            split_ratio=0.8,
            format="sharegpt",
            seed=42,
            datasets_dir=str(self.test_dir)
        )

        self.assertIsInstance(dataset, FineTuningDataset)
        self.assertEqual(dataset.train_example_count, 8)
        self.assertEqual(dataset.val_example_count, 2)
        self.assertEqual(dataset.split_ratio, 0.8)
        self.assertIsNotNone(dataset.validation_group)
        self.assertEqual(dataset.validation_group.scenarios.count(), 2)
        self.assertTrue(os.path.exists(dataset.file_path))

        # Inspect the exported JSONL content
        with open(dataset.file_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 8)
        self.assertIn("conversations", lines[0])
        self.assertEqual(lines[0]["conversations"][0]["from"], "system")
        self.assertEqual(lines[0]["conversations"][1]["from"], "human")
        self.assertEqual(lines[0]["conversations"][2]["from"], "gpt")

        # Verify held-out validation scenarios do not appear in training file
        train_prompts = [line["conversations"][1]["value"] for line in lines]
        for val_scenario in dataset.validation_group.scenarios.all():
            self.assertNotIn(val_scenario.question, train_prompts)

        # Verify staleness detection
        self.assertFalse(dataset.is_stale)
        # Advance updated_at to simulate source group update after creation
        from django.utils import timezone
        ScenarioGroup.objects.filter(id=group.id).update(
            updated_at=timezone.now() + timezone.timedelta(seconds=5)
        )
        dataset.refresh_from_db()
        self.assertTrue(dataset.is_stale)


class BenchmarkingStudioUITests(TestCase):
    """
    Unit tests for the Consolidated Reactive Benchmarking Studio UI (WS18 Step 2).
    Tests Matrix Composer, Datastar SSE streaming, Side-by-Side Diff Inspector,
    One-Click Gold Promotion, CSV exports, and Web Curation.
    """

    def setUp(self):
        self.client = Client()
        self.test_dir = Path(settings.BASE_DIR) / "test_data_curation_ui"
        os.makedirs(self.test_dir, exist_ok=True)

        self.corpus = BenchmarkCorpus.objects.create(name="Studio Test Corpus", description="For UI tests")
        self.group = ScenarioGroup.objects.create(name="Studio Test Group")
        self.scenario_1 = BenchmarkScenario.objects.create(
            question="What is the difference between S-Learner and T-Learner?",
            expected_keywords=["S-Learner", "T-Learner", "single model", "treatment indicator"],
            ideal_answer="S-Learner fits a single model with treatment indicator; T-Learner fits two separate models."
        )
        self.scenario_2 = BenchmarkScenario.objects.create(
            question="How does back-door criterion identify causal effects?",
            expected_keywords=["back-door", "confounding", "d-separation", "DAG"],
            ideal_answer="Back-door criterion blocks all non-causal paths between treatment and outcome."
        )
        self.group.scenarios.add(self.scenario_1, self.scenario_2)

        self.investigation = Investigation.objects.create(
            name="Studio Causal Investigation",
            description="Testing reactive studio workflows"
        )
        self.experiment = Experiment.objects.create(
            investigation=self.investigation,
            corpus=self.corpus,
            scenario_group=self.group,
            name="Qwen vs Llama Matrix Trial",
            configuration={"hosting_backend": "pytorch", "rag_strategy": "none"}
        )

    def tearDown(self):
        if hasattr(self, "test_dir") and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        super().tearDown()

    def test_studio_view_renders_successfully(self):
        """Studio main view renders with 200 OK, consolidated dark styles, and composer controls."""
        response = self.client.get("/benchmarking/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "benchmarking/studio.html")
        self.assertIn("investigations", response.context)
        self.assertIn("scenario_groups", response.context)
        content = response.content.decode("utf-8")
        self.assertIn("Benchmarking Studio & Flywheel", content)
        self.assertIn("Matrix Composer", content)
        self.assertIn("Live Telemetry", content)
        self.assertIn("studio.css", content)

    def test_matrix_composer_run_api(self):
        """Matrix composer POST creates Investigation, Experiment, and BenchmarkRun with SSE connect frame."""
        post_data = {
            "investigation_id": "new",
            "experiment_name": "Async Matrix Test",
            "model_id": "Qwen/Qwen2.5-7B-Instruct",
            "hosting_backend": "vllm",
            "scenario_group_id": self.group.id,
            "rag_strategy": "chunk",
            "iterations": 1,
            "chunk_size": 256,
        }
        response = self.client.post("/benchmarking/api/run/", data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        content = response.content.decode("utf-8")
        self.assertIn("event: datastar-merge-fragments", content)
        self.assertIn("/benchmarking/stream/", content)

        # Verify DB records created
        exp = Experiment.objects.get(name="Async Matrix Test")
        self.assertEqual(exp.configuration["hosting_backend"], "vllm")
        self.assertEqual(exp.configuration["rag_strategy"], "chunk")
        self.assertEqual(BenchmarkRun.objects.filter(experiment=exp).count(), 1)

    def test_stream_benchmark_run_sse(self):
        """Live SSE streaming generator executes scenarios and yields Datastar fragment merges."""
        run = BenchmarkRun.objects.create(
            experiment=self.experiment,
            corpus=self.corpus,
            configuration_snapshot=self.experiment.configuration
        )

        response = self.client.get(f"/benchmarking/stream/{run.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        stream_chunks = list(response.streaming_content)
        self.assertGreater(len(stream_chunks), 0)
        full_stream = b"".join(stream_chunks).decode("utf-8")

        self.assertIn("event: datastar-merge-fragments", full_stream)
        self.assertIn("data: selector #run-monitor", full_stream)
        self.assertIn("Completed", full_stream)

        # Verify results were persisted for both scenarios
        results = BenchmarkResult.objects.filter(run=run)
        self.assertEqual(results.count(), 2)
        for res in results:
            self.assertIsNotNone(res.rag_score)
            self.assertIsNotNone(res.semantic_score)

    def test_scenario_detail_api(self):
        """Scenario detail API yields inspector fragment with question and expected keywords."""
        response = self.client.get(f"/benchmarking/api/scenario/{self.scenario_1.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = response.content.decode("utf-8")
        self.assertIn("S-Learner", content)
        self.assertIn("data: selector #inspector-content", content)

    def test_inspect_result_diff_and_promote_to_gold(self):
        """Result diff viewer displays candidate vs gold comparison and promotes candidate on click."""
        run = BenchmarkRun.objects.create(
            experiment=self.experiment,
            corpus=self.corpus,
            configuration_snapshot=self.experiment.configuration
        )
        result = BenchmarkResult.objects.create(
            run=run,
            scenario=self.scenario_2,
            prompt_text=self.scenario_2.question,
            raw_retrieved_text="",
            generated_response="Back-door criterion blocks confounding paths using d-separation on the DAG.",
            duration_seconds=0.25,
            rag_recall_score=1.0,
            semantic_score=0.95,
            faithfulness_score=0.90,
            relevance_score=0.95,
        )

        # 1. Inspect Diff
        diff_res = self.client.get(f"/benchmarking/api/diff/{result.id}/")
        self.assertEqual(diff_res.status_code, 200)
        diff_content = diff_res.content.decode("utf-8")
        self.assertIn("Model Output Candidate", diff_content)
        self.assertIn("Gold Standard Reference", diff_content)
        self.assertIn("Promote to Gold Standard", diff_content)

        # 2. Promote to Gold
        promote_res = self.client.post(f"/benchmarking/api/promote/{result.id}/")
        self.assertEqual(promote_res.status_code, 200)
        promote_content = promote_res.content.decode("utf-8")
        self.assertIn("Promoted to Gold Standard", promote_content)

        self.scenario_2.refresh_from_db()
        self.assertEqual(self.scenario_2.ideal_response, result.response)

    def test_export_run_csv(self):
        """Export CSV endpoint produces well-formatted CSV with candidate answers and scores."""
        run = BenchmarkRun.objects.create(
            experiment=self.experiment,
            corpus=self.corpus,
            configuration_snapshot=self.experiment.configuration
        )
        BenchmarkResult.objects.create(
            run=run,
            scenario=self.scenario_1,
            prompt_text=self.scenario_1.question,
            raw_retrieved_text="",
            generated_response="Model output for S vs T learner.",
            duration_seconds=0.3,
            rag_recall_score=0.8,
            semantic_score=0.85,
            faithfulness_score=0.9,
            relevance_score=0.95
        )

        response = self.client.get(f"/benchmarking/export/csv/{run.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(f"benchmark_run_{run.id}_results.csv", response["Content-Disposition"])

        content = response.content.decode("utf-8")
        self.assertIn("Result ID,Scenario ID,Question", content)
        self.assertIn("Model output for S vs T learner", content)

    def test_curate_dataset_api(self):
        """Curate dataset endpoint executes curation and returns Datastar fragment with split metrics."""
        post_data = {
            "dataset_name": "Studio Web Curated Set",
            "scenario_group_ids": [str(self.group.id)],
            "split_ratio": "0.5",
            "min_feedback": "1",
            "include_chat_logs": "false",
            "datasets_dir": str(self.test_dir),
        }
        response = self.client.post("/benchmarking/api/curate/", data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        content = response.content.decode("utf-8")
        self.assertIn("data: selector #curation-result-container", content)
        self.assertIn("Curation Complete", content)
        self.assertIn("Train Examples", content)
        self.assertIn("Val Scenarios", content)

    def test_matrix_composer_combinatorial_grid_api(self):
        """Combinatorial matrix composer creates Cartesian product of Experiments & BenchmarkRuns under Investigation."""
        post_data = {
            "investigation_id": "new",
            "experiment_name": "Grid Investigation",
            "model_ids": ["Qwen/Qwen2.5-7B-Instruct", "meta-llama/Llama-3.1-8B"],
            "hosting_backends": ["vllm", "sglang"],
            "scenario_group_id": self.group.id,
            "rag_strategies": ["none", "default"],
            "iterations": 1,
            "chunk_size": 512,
        }
        response = self.client.post("/benchmarking/api/run/", data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        content = response.content.decode("utf-8")
        self.assertIn("event: datastar-merge-fragments", content)
        self.assertIn("/benchmarking/stream/investigation/", content)

        # 2 models * 2 backends * 2 rag strategies = 8 experiments
        inv = Investigation.objects.get(name="Grid Investigation")
        experiments = inv.experiments.all()
        self.assertEqual(experiments.count(), 8)

        # Verify all 8 experiments have a BenchmarkRun created
        for exp in experiments:
            self.assertEqual(BenchmarkRun.objects.filter(experiment=exp).count(), 1)

    def test_stream_investigation_matrix_sse(self):
        """Streaming whole-matrix investigation executes all experiments and renders comparative scorecard."""
        inv = Investigation.objects.create(name="Matrix Test Inv")
        exp1 = Experiment.objects.create(
            investigation=inv,
            corpus=self.corpus,
            scenario_group=self.group,
            name="Matrix Exp 1",
            configuration={"target_model": "Qwen/7B", "hosting_backend": "vllm", "rag_strategy": "none"}
        )
        exp2 = Experiment.objects.create(
            investigation=inv,
            corpus=self.corpus,
            scenario_group=self.group,
            name="Matrix Exp 2",
            configuration={"target_model": "Llama/8B", "hosting_backend": "sglang", "rag_strategy": "default"}
        )
        run1 = BenchmarkRun.objects.create(experiment=exp1, corpus=self.corpus, configuration_snapshot=exp1.configuration)
        run2 = BenchmarkRun.objects.create(experiment=exp2, corpus=self.corpus, configuration_snapshot=exp2.configuration)

        response = self.client.get(f"/benchmarking/stream/investigation/{inv.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        stream_chunks = list(response.streaming_content)
        self.assertGreater(len(stream_chunks), 0)
        full_stream = b"".join(stream_chunks).decode("utf-8")

        self.assertIn("event: datastar-merge-fragments", full_stream)
        self.assertIn("data: selector #run-monitor", full_stream)
        self.assertIn("Comparative Experiment Scorecard", full_stream)
        self.assertIn("Matrix Completed", full_stream)

        # Verify results for both runs
        self.assertEqual(BenchmarkResult.objects.filter(run=run1).count(), 2)
        self.assertEqual(BenchmarkResult.objects.filter(run=run2).count(), 2)

        run1.refresh_from_db()
        run2.refresh_from_db()
        self.assertIsNotNone(run1.average_rag_score)
        self.assertIsNotNone(run1.average_semantic_score)
        self.assertIsNotNone(run2.average_rag_score)
        self.assertIsNotNone(run2.average_semantic_score)

    def test_retrieve_benchmark_context_adapter(self):
        """Retrieval adapter handles none, grips_wiki, and unified strategies cleanly."""
        from benchmarking.retrieval_adapter import retrieve_benchmark_context
        from langchain_core.documents import Document as LangchainDocument
        from unittest.mock import MagicMock

        # 1. 'none'
        text, meta = retrieve_benchmark_context("Test query", "none")
        self.assertEqual(text, "")
        self.assertEqual(meta, [])

        # 2. 'grips_wiki'
        mock_grips = MagicMock()
        mock_grips.get_grips_context.return_value = [
            LangchainDocument(page_content="Breadcrumb relay operates at 3.5-6.5 GHz UWB.", metadata={"title": "Relay Node"})
        ]
        text, meta = retrieve_benchmark_context("UWB query", "grips_wiki", grips_service=mock_grips)
        self.assertIn("Concept [Relay Node]:", text)
        self.assertIn("Breadcrumb relay", text)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["source"], "grips")

        # 3. 'unified_dedup' (mocked services)
        mock_rag = MagicMock()
        mock_rag.get_context.return_value = []
        text_uni, meta_uni = retrieve_benchmark_context("Any query", "unified_dedup", rag_service=mock_rag, grips_service=mock_grips)
        self.assertIsInstance(text_uni, str)

    def test_grips_and_unified_matrix_composer_and_diff(self):
        """Combinatorial matrix supports grips_wiki & unified_dedup, and diff viewer displays retrieved grounding context."""
        post_data = {
            "investigation_id": "new",
            "experiment_name": "Grips & Unified Matrix",
            "model_ids": ["Qwen/Qwen2.5-7B-Instruct"],
            "hosting_backends": ["pytorch"],
            "scenario_group_id": self.group.id,
            "rag_strategies": ["grips_wiki", "unified_dedup"],
            "iterations": 1,
            "chunk_size": 512,
        }
        response = self.client.post("/benchmarking/api/run/", data=post_data)
        self.assertEqual(response.status_code, 200)

        inv = Investigation.objects.get(name="Grips & Unified Matrix")
        exps = list(inv.experiments.all().order_by("id"))
        self.assertEqual(len(exps), 2)
        strategies = [e.configuration["rag_strategy"] for e in exps]
        self.assertIn("grips_wiki", strategies)
        self.assertIn("unified_dedup", strategies)

        # Verify Diff Inspector surfaces the retrieved grounding context
        run = BenchmarkRun.objects.create(experiment=exps[0], corpus=self.corpus, configuration_snapshot=exps[0].configuration)
        res = BenchmarkResult.objects.create(
            run=run,
            scenario=self.scenario_1,
            prompt_text=self.scenario_1.question,
            raw_retrieved_text="Concept [S-Learner]: S-Learner fits a single base learner with treatment indicator W.",
            generated_response="S-Learner uses a single base learner.",
            duration_seconds=0.4,
            rag_recall_score=1.0,
            semantic_score=0.9,
            faithfulness_score=0.95,
            relevance_score=0.95,
        )

        diff_res = self.client.get(f"/benchmarking/api/diff/{res.id}/")
        self.assertEqual(diff_res.status_code, 200)
        diff_html = diff_res.content.decode("utf-8")
        self.assertIn("Retrieved Grounding Context", diff_html)
        self.assertIn("Concept [S-Learner]", diff_html)
        self.assertIn("GRIPS_WIKI", diff_html)

    def test_estimate_scenario_latency_calibration(self):
        """Hardware latency estimator calculates realistic durations for consumer vs datacenter compute."""
        from benchmarking.hardware import HardwareProfile, estimate_scenario_latency

        # 1. Consumer tier (e.g. GTX 1660 Ti, 6GB) -> realistic ~20-40s per prompt
        consumer_profile = HardwareProfile(
            device_type="cuda",
            total_vram_gb=6.0,
            compute_capability=(7, 5),
            device_name="NVIDIA GeForce GTX 1660 Ti",
        )
        lat_consumer = estimate_scenario_latency(consumer_profile, backend="pytorch", rag_strategy="none")
        self.assertGreaterEqual(lat_consumer, 15.0)
        self.assertLessEqual(lat_consumer, 45.0)

        # 2. Datacenter tier (e.g. A100 / A40, 80GB) -> sub-6s per prompt
        datacenter_profile = HardwareProfile(
            device_type="cuda",
            total_vram_gb=80.0,
            compute_capability=(8, 0),
            device_name="NVIDIA A100-SXM4-80GB",
        )
        lat_datacenter = estimate_scenario_latency(datacenter_profile, backend="pytorch", rag_strategy="none")
        self.assertGreaterEqual(lat_datacenter, 1.0)
        self.assertLessEqual(lat_datacenter, 6.0)

        # 3. Strategy overhead
        lat_with_rag = estimate_scenario_latency(consumer_profile, backend="pytorch", rag_strategy="unified_dedup")
        self.assertGreater(lat_with_rag, lat_consumer)

        # 4. Studio view context check
        response = self.client.get("/benchmarking/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("estimated_latency_per_query", response.context)
        self.assertGreater(response.context["estimated_latency_per_query"], 0)


class HardwareAwareTrainingAndABEvalTests(TestCase):
    """
    WS18 Step 3: Comprehensive tests for Hardware-Aware QLoRA Training Harness,
    Dynamic Hyperparameter Scaling across compute tiers, and Closed-Loop A/B Evaluation.
    """

    def setUp(self):
        self.client = Client()
        self.test_dir = Path(settings.BASE_DIR) / "test_data_training_step3"
        os.makedirs(self.test_dir, exist_ok=True)

        self.corpus = BenchmarkCorpus.objects.create(name="Step 3 Corpus", description="For training tests")
        self.val_group = ScenarioGroup.objects.create(name="Val Holdout Group")
        self.scenario_1 = BenchmarkScenario.objects.create(
            question="What is the difference between S-Learner and T-Learner?",
            expected_keywords=["S-Learner", "T-Learner", "single model"],
            ideal_answer="S-Learner fits a single model with treatment indicator; T-Learner fits two separate models."
        )
        self.scenario_2 = BenchmarkScenario.objects.create(
            question="How does back-door criterion identify causal effects?",
            expected_keywords=["back-door", "confounding", "d-separation"],
            ideal_answer="Back-door criterion blocks all non-causal paths between treatment and outcome."
        )
        self.val_group.scenarios.add(self.scenario_1, self.scenario_2)

        # Create dummy JSONL files for dataset
        self.train_file = self.test_dir / "train.jsonl"
        with open(self.train_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"prompt": "Explain S-Learner", "response": "S-Learner uses one model."}) + "\n")
        self.val_file = self.test_dir / "val.jsonl"
        with open(self.val_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"prompt": "Explain T-Learner", "response": "T-Learner uses two models."}) + "\n")

        self.dataset = FineTuningDataset.objects.create(
            name="Causal Step 3 Dataset",
            file_path=str(self.train_file),
            train_example_count=1,
            val_example_count=1,
            validation_group=self.val_group,
            metadata={"source": "test"},
        )

    def tearDown(self):
        if hasattr(self, "test_dir") and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)
        super().tearDown()

    def test_hardware_detection_and_dynamic_scaling(self):
        """Dynamic hardware profile correctly categorizes tiers and tunes batch/grad-accum/precision."""
        # 1. Real profile detection
        hw = detect_hardware_profile()
        self.assertIn(hw.tier, ["consumer", "workstation", "datacenter", "cpu"])
        self.assertIsInstance(hw.total_vram_gb, float)

        # 2. Datacenter profile scaling (e.g. A100 / H100 with 80GB VRAM)
        dc_hw = HardwareProfile(
            device_type="cuda",
            total_vram_gb=80.0,
            compute_capability=(9, 0),
            device_name="NVIDIA H100 SXM",
            supports_bf16=True,
            supports_flash_attn=True,
        )
        self.assertEqual(dc_hw.tier, "datacenter")
        dc_cfg = recommend_training_config(dc_hw)
        self.assertGreaterEqual(dc_cfg.batch_size, 4)
        self.assertLessEqual(dc_cfg.gradient_accumulation_steps, 4)
        self.assertEqual(dc_cfg.precision, "bf16")
        self.assertGreaterEqual(dc_cfg.max_seq_length, 4096)

        # 3. Workstation profile scaling (e.g. RTX 3090 / 4090 with 24GB VRAM)
        ws_hw = HardwareProfile(
            device_type="cuda",
            total_vram_gb=24.0,
            compute_capability=(8, 6),
            device_name="NVIDIA GeForce RTX 3090",
            supports_bf16=True,
        )
        self.assertEqual(ws_hw.tier, "workstation")
        ws_cfg = recommend_training_config(ws_hw)
        self.assertEqual(ws_cfg.batch_size, 4)
        self.assertEqual(ws_cfg.gradient_accumulation_steps, 4)
        self.assertEqual(ws_cfg.quantization, "4bit")

        # 4. Consumer profile scaling (e.g. GTX 1660 Ti or 6GB-12GB GPU)
        cons_hw = HardwareProfile(
            device_type="cuda",
            total_vram_gb=6.0,
            compute_capability=(7, 5),
            device_name="NVIDIA GeForce GTX 1660 Ti",
            supports_bf16=False,
        )
        self.assertEqual(cons_hw.tier, "consumer")
        cons_cfg = recommend_training_config(cons_hw)
        self.assertEqual(cons_cfg.batch_size, 1)
        self.assertGreaterEqual(cons_cfg.gradient_accumulation_steps, 16)
        self.assertEqual(cons_cfg.quantization, "4bit")
        self.assertTrue(cons_cfg.gradient_checkpointing)

        # 5. CPU fallback scaling
        cpu_hw = HardwareProfile(device_type="cpu", total_vram_gb=0.0)
        self.assertEqual(cpu_hw.tier, "cpu")
        cpu_cfg = recommend_training_config(cpu_hw)
        self.assertEqual(cpu_cfg.quantization, "none")
        self.assertEqual(cpu_cfg.precision, "fp32")

    def test_train_lora_adapter_dry_run_and_registration(self):
        """Dry-run training writes adapter structure and registers LoRAAdapter in database."""
        out_dir = self.test_dir / "adapters"
        result = train_lora_adapter(
            dataset=self.dataset,
            base_model_id="google/gemma-4-E2B-it",
            output_adapter_name="step3_test_lora",
            output_dir=str(out_dir),
            dry_run=True,
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.adapter)
        self.assertEqual(result.adapter.name, "step3_test_lora")
        self.assertEqual(result.adapter.dataset, self.dataset)
        self.assertTrue(os.path.exists(result.weights_path))
        self.assertTrue(os.path.exists(os.path.join(result.weights_path, "adapter_config.json")))

        # Verify in DB
        adapter_in_db = LoRAAdapter.objects.get(name="step3_test_lora")
        self.assertEqual(adapter_in_db.file_path, result.weights_path)
        self.assertEqual(adapter_in_db.base_model.name, "google/gemma-4-E2B-it")

    def test_run_closed_loop_ab_evaluation(self):
        """Automated A/B evaluation benchmarks base vs adapter on held-out group and calculates deltas."""
        base_model, _ = LocalAIModel.objects.get_or_create(
            name="google/gemma-4-E2B-it",
            defaults={"hf_model_id": "google/gemma-4-E2B-it"}
        )
        adapter = LoRAAdapter.objects.create(
            name="certified_causal_lora",
            base_model=base_model,
            dataset=self.dataset,
            file_path=str(self.test_dir / "certified_weights")
        )

        eval_res = run_closed_loop_ab_evaluation(adapter=adapter)
        self.assertIsInstance(eval_res, ABEvaluationResult)
        self.assertEqual(eval_res.adapter, adapter)
        self.assertIn(eval_res.verdict, ["IMPROVED", "NEUTRAL", "REGRESSED"])
        self.assertIsNotNone(eval_res.investigation)
        self.assertIsNotNone(eval_res.base_run)
        self.assertIsNotNone(eval_res.adapter_run)
        self.assertIn("A/B Evaluation Certification Report", eval_res.summary_markdown)
        self.assertIsInstance(eval_res.delta_semantic_pct, float)
        self.assertIsInstance(eval_res.delta_relevance_pct, float)

    def test_hardware_profile_api(self):
        """GET /benchmarking/api/hardware/ returns valid JSON description of host hardware and recommendations."""
        response = self.client.get("/benchmarking/api/hardware/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertIn("hardware", data)
        self.assertIn("recommended_config", data)
        self.assertIn("tier", data["hardware"])
        self.assertIn("batch_size", data["recommended_config"])

    def test_train_adapter_api(self):
        """POST /benchmarking/api/train/ executes training and returns Datastar SSE with training results."""
        post_data = {
            "dataset_id": str(self.dataset.id),
            "base_model_id": "google/gemma-4-E2B-it",
            "output_name": "web_trained_lora",
            "lora_r": "16",
            "lora_alpha": "32",
            "epochs": "1",
            "learning_rate": "0.0002",
            "dry_run": "true",
        }
        response = self.client.post("/benchmarking/api/train/", data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = response.content.decode("utf-8")
        self.assertIn("data: selector #training-status-container", content)
        self.assertIn("Adapter Ready", content)
        self.assertIn("web_trained_lora", content)
        self.assertIn("Closed-Loop Verification", content)

        # Check JSON format support
        json_response = self.client.post(
            "/benchmarking/api/train/",
            data=post_data,
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(json_response.status_code, 200)
        self.assertTrue(json_response.json()["success"])

    def test_ab_evaluation_api(self):
        """POST /benchmarking/api/ab-eval/<id>/ triggers evaluation and returns Datastar SSE verdict."""
        base_model, _ = LocalAIModel.objects.get_or_create(
            name="google/gemma-4-E2B-it",
            defaults={"hf_model_id": "google/gemma-4-E2B-it"}
        )
        adapter = LoRAAdapter.objects.create(
            name="web_eval_lora",
            base_model=base_model,
            dataset=self.dataset,
            file_path=str(self.test_dir / "eval_weights")
        )
        response = self.client.post(f"/benchmarking/api/ab-eval/{adapter.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        content = response.content.decode("utf-8")
        self.assertIn(f"data: selector #ab-eval-container-{adapter.id}", content)
        self.assertIn("A/B Verification Outcome", content)
        self.assertIn("Open Comparative Investigation Report", content)

        # Check JSON format support
        json_response = self.client.post(
            f"/benchmarking/api/ab-eval/{adapter.id}/",
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(json_response.status_code, 200)
        self.assertIn("verdict", json_response.json())


class AdaptiveBenchmarkHubAndLeaderboardTests(TestCase):
    """
    Tests for the Adaptive Benchmark Hub, Nuanced Leaderboard, and Grouped History.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(username="hub_admin", password="password", email="admin@test.com")
        self.client.force_login(self.user)

        self.model_a = LocalAIModel.objects.create(name="Gemma 2B", hf_model_id="google/gemma-2-2b-it")
        self.model_b = LocalAIModel.objects.create(name="Qwen 4B", hf_model_id="Qwen/Qwen-4B")

        self.corpus = BenchmarkCorpus.objects.create(name="Hub Test Corpus")
        self.sg1 = ScenarioGroup.objects.create(name="Robotics Core")
        self.scen1 = BenchmarkScenario.objects.create(
            question="What is step 1?", ideal_answer="Turn on power."
        )
        self.sg1.scenarios.add(self.scen1)

        self.sg2 = ScenarioGroup.objects.create(name="Medical Diagnosis")
        self.scen2 = BenchmarkScenario.objects.create(
            question="Diagnosis for fever?", ideal_answer="Viral infection."
        )
        self.sg2.scenarios.add(self.scen2)

        self.inv = Investigation.objects.create(name="Hub Architecture Investigation")

    def test_operational_status_healthy_and_crashed(self):
        from benchmarking.hub import get_operational_status

        # Create healthy experiment and run
        exp_healthy = Experiment.objects.create(
            investigation=self.inv,
            name="Healthy Run Exp",
            scenario_group=self.sg1,
            selected_model=self.model_a,
            configuration={"hosting_backend": "ollama", "ai_model_id": self.model_a.hf_model_id}
        )
        run_healthy = BenchmarkRun.objects.create(
            experiment=exp_healthy,
            corpus=self.corpus,
            average_semantic_score=0.85,
            configuration_snapshot={"hosting_backend": "ollama", "ai_model_id": self.model_a.hf_model_id}
        )
        BenchmarkResult.objects.create(
            run=run_healthy,
            scenario=self.scen1,
            prompt_text="Q1",
            raw_retrieved_text="Context",
            generated_response="Valid completion response here.",
            duration_seconds=12.0,
            rag_recall_score=0.8,
            semantic_score=0.85,
            extra_metrics={"tokens_per_second": 4.5}
        )

        status_healthy = get_operational_status()
        self.assertTrue(status_healthy["has_runs"])
        self.assertFalse(status_healthy["is_failed"])
        self.assertEqual(status_healthy["latest_run"].id, run_healthy.id)

        # Create failed/crashed run
        exp_crashed = Experiment.objects.create(
            investigation=self.inv,
            name="Crashed Run Exp",
            scenario_group=self.sg2,
            selected_model=self.model_b,
            configuration={"hosting_backend": "vllm", "ai_model_id": self.model_b.hf_model_id}
        )
        run_crashed = BenchmarkRun.objects.create(
            experiment=exp_crashed,
            corpus=self.corpus,
            average_semantic_score=None,
            configuration_snapshot={"hosting_backend": "vllm", "ai_model_id": self.model_b.hf_model_id}
        )
        BenchmarkResult.objects.create(
            run=run_crashed,
            scenario=self.scen2,
            prompt_text="Q2",
            raw_retrieved_text="",
            generated_response="GenerationFailed: Generation failed after 2 attempts. Container timed out.",
            duration_seconds=0.0,
            rag_recall_score=0.0,
            semantic_score=-0.05,
            extra_metrics={"error": "Connection refused"}
        )

        status_crashed = get_operational_status()
        self.assertTrue(status_crashed["has_runs"])
        self.assertTrue(status_crashed["is_failed"])
        self.assertIsNotNone(status_crashed["defect_record"])
        self.assertEqual(status_crashed["defect_record"].defect_category, "Hosting Backend Defect")
        self.assertIn("vllm", status_crashed["error_message"].lower())

    def test_smart_opportunities_discovery(self):
        from benchmarking.hub import get_smart_opportunities

        # In setUp, model_a and model_b have no runs on sg1 or sg2
        opps = get_smart_opportunities(limit=5)
        self.assertGreaterEqual(len(opps), 2)
        pairs = [(o.model_hf_id, o.scenario_group_id) for o in opps]
        self.assertIn((self.model_a.hf_model_id, self.sg1.id), pairs)

        # Now register a completed run for model_a on sg1
        exp = Experiment.objects.create(
            investigation=self.inv,
            name="Model A on SG1",
            scenario_group=self.sg1,
            selected_model=self.model_a,
            configuration={"ai_model_id": self.model_a.hf_model_id}
        )
        run = BenchmarkRun.objects.create(
            experiment=exp,
            corpus=self.corpus,
            average_semantic_score=0.75,
            configuration_snapshot={"ai_model_id": self.model_a.hf_model_id}
        )

        opps_after = get_smart_opportunities(limit=5)
        pairs_after = [(o.model_hf_id, o.scenario_group_id) for o in opps_after]
        self.assertNotIn((self.model_a.hf_model_id, self.sg1.id), pairs_after)

    def test_nuanced_leaderboard_pareto_badges(self):
        from benchmarking.hub import get_nuanced_leaderboard

        # 1. Quality leader
        exp_q = Experiment.objects.create(
            investigation=self.inv, name="High Quality", scenario_group=self.sg1, selected_model=self.model_a
        )
        run_q = BenchmarkRun.objects.create(
            experiment=exp_q,
            corpus=self.corpus,
            average_semantic_score=0.92,
            average_faithfulness=0.88,
            configuration_snapshot={"hosting_backend": "pytorch", "ai_model_id": self.model_a.hf_model_id, "rag_strategy": "unified_dedup"}
        )
        BenchmarkResult.objects.create(
            run=run_q, scenario=self.scen1, generated_response="High precision causal deduction.",
            duration_seconds=40.0, rag_recall_score=0.9, semantic_score=0.92, faithfulness_score=0.88,
            extra_metrics={"tokens_per_second": 3.0}
        )

        # 2. Speed leader
        exp_s = Experiment.objects.create(
            investigation=self.inv, name="High Speed", scenario_group=self.sg1, selected_model=self.model_b
        )
        run_s = BenchmarkRun.objects.create(
            experiment=exp_s,
            corpus=self.corpus,
            average_semantic_score=0.70,
            average_faithfulness=0.65,
            configuration_snapshot={"hosting_backend": "ollama", "ai_model_id": self.model_b.hf_model_id, "rag_strategy": "none"}
        )
        BenchmarkResult.objects.create(
            run=run_s, scenario=self.scen1, generated_response="Fast concise reply.",
            duration_seconds=10.0, rag_recall_score=0.5, semantic_score=0.70, faithfulness_score=0.65,
            extra_metrics={"tokens_per_second": 12.0}
        )

        # 3. Defective / Crashed run (must be separated into defect_entries, not scored_entries)
        exp_crashed = Experiment.objects.create(
            investigation=self.inv, name="Crashed Run", scenario_group=self.sg1, selected_model=self.model_b
        )
        run_crashed = BenchmarkRun.objects.create(
            experiment=exp_crashed,
            corpus=self.corpus,
            average_semantic_score=None,
            configuration_snapshot={"hosting_backend": "vllm", "ai_model_id": self.model_b.hf_model_id, "rag_strategy": "none"}
        )
        BenchmarkResult.objects.create(
            run=run_crashed, scenario=self.scen1, generated_response="GenerationFailed: vLLM timeout",
            duration_seconds=0.0, rag_recall_score=0.0, semantic_score=-0.05,
            extra_metrics={"error": "Connection reset"}
        )

        lb_data = get_nuanced_leaderboard()
        self.assertGreaterEqual(len(lb_data.scored_entries), 2)
        # Verify defective run is NOT in scored entries
        self.assertFalse(any(e.backend == "vllm" for e in lb_data.scored_entries))
        # Verify defective run IS in defect entries
        self.assertTrue(any(d.defect_category == "Hosting Backend Defect" for d in lb_data.defect_entries))

        quality_leader = next((e for e in lb_data.scored_entries if "🥇 Quality Leader" in e.badges), None)
        self.assertIsNotNone(quality_leader)
        self.assertEqual(quality_leader.model_name, self.model_a.hf_model_id.split("/")[-1])

        speed_leader = next((e for e in lb_data.scored_entries if "⚡ Speed Leader" in e.badges), None)
        self.assertIsNotNone(speed_leader)
        self.assertEqual(speed_leader.model_name, self.model_b.hf_model_id.split("/")[-1])

    def test_grouped_history_and_api_endpoints(self):
        from benchmarking.hub import get_grouped_history

        exp = Experiment.objects.create(
            investigation=self.inv, name="Test History", scenario_group=self.sg1, selected_model=self.model_a
        )
        run = BenchmarkRun.objects.create(
            experiment=exp, corpus=self.corpus, average_semantic_score=0.80,
            configuration_snapshot={"hosting_backend": "ollama", "ai_model_id": self.model_a.hf_model_id}
        )
        BenchmarkResult.objects.create(
            run=run, scenario=self.scen1, generated_response="Good output", duration_seconds=15.0,
            rag_recall_score=0.8, semantic_score=0.80, extra_metrics={}
        )

        # Test grouping axes
        history_sg = get_grouped_history(group_by="scenario_group")
        self.assertTrue(any(h.group_key == self.sg1.name for h in history_sg))

        history_backend = get_grouped_history(group_by="hosting_backend")
        self.assertTrue(any("Ollama" in h.group_key for h in history_backend))

        # Test API endpoints
        resp_lb = self.client.get("/benchmarking/api/leaderboard/")
        self.assertEqual(resp_lb.status_code, 200)
        self.assertEqual(resp_lb["Content-Type"], "text/event-stream")
        self.assertEqual(resp_lb["Cache-Control"], "no-cache, no-store, must-revalidate")
        self.assertIn("hub-leaderboard-container", resp_lb.content.decode("utf-8"))

        resp_gh = self.client.get(f"/benchmarking/api/grouped-history/?group_by=investigation")
        self.assertEqual(resp_gh.status_code, 200)
        self.assertEqual(resp_gh["Content-Type"], "text/event-stream")
        self.assertEqual(resp_gh["Cache-Control"], "no-cache, no-store, must-revalidate")
        self.assertIn("hub-history-container", resp_gh.content.decode("utf-8"))
        self.assertIn("Investigation Project", resp_gh.content.decode("utf-8"))


class TestGoldStandardsAndSuiteInspection(TestCase):
    """
    Tests for Scenario Group Suite Browser, detailed suite review modal,
    in-place gold standard editing, and 11/10 candidate promotion.
    """
    def setUp(self):
        self.corpus = BenchmarkCorpus.objects.create(name="Suite Test Corpus")
        self.scenario_group = ScenarioGroup.objects.create(
            name="Robotics Causal Reasoning",
            description="Testing physical mechanics and causal reasoning under fault conditions."
        )
        self.scenario_1 = BenchmarkScenario.objects.create(
            question="What causes pressure relief valves to chatter under fluctuating backpressure?",
            ideal_answer="Chatter is caused by rapid cycling when backpressure exceeds the seat re-seating force.",
            expected_keywords=["chatter", "backpressure", "re-seating force"]
        )
        self.scenario_2 = BenchmarkScenario.objects.create(
            question="How should hydraulic actuator drift be compensated during payload transition?",
            ideal_answer="Using proportional counterbalance valves with pilot-operated check valves.",
            expected_keywords=["counterbalance", "pilot-operated", "drift"]
        )
        self.scenario_group.scenarios.add(self.scenario_1, self.scenario_2)

        self.inv = Investigation.objects.create(name="Suite Investigation")
        self.exp = Experiment.objects.create(
            investigation=self.inv,
            name="Robotics Test Exp",
            scenario_group=self.scenario_group,
            corpus=self.corpus
        )
        self.run = BenchmarkRun.objects.create(
            experiment=self.exp,
            corpus=self.corpus,
            configuration_snapshot={"ai_model_id": "google/gemma-2-9b-it", "hosting_backend": "vllm"}
        )
        self.candidate_result = BenchmarkResult.objects.create(
            run=self.run,
            scenario=self.scenario_1,
            prompt_text=self.scenario_1.question,
            raw_retrieved_text="Documentation on hydraulic pressure valve harmonics and fluid dynamics.",
            generated_response="Chatter occurs due to acoustic resonance and impedance mismatches between the valve spring stiffness and the rapid backpressure gradient.",
            duration_seconds=0.45,
            rag_recall_score=0.95,
            semantic_score=0.98,
            faithfulness_score=0.95,
            relevance_score=0.96,
        )

    def test_switch_scenario_group_api(self):
        """GET /benchmarking/api/scenario-group/<id>/scenarios/ returns filtered scenario catalog partial."""
        res = self.client.get(f"/benchmarking/api/scenario-group/{self.scenario_group.id}/scenarios/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/event-stream")
        content = res.content.decode("utf-8")
        self.assertIn("event: datastar-patch-elements", content)
        self.assertIn("scenario-catalog-container", content)
        self.assertIn("Robotics Causal Reasoning", content)
        self.assertIn("pressure relief valves", content)

    def test_suite_review_modal_api(self):
        """GET /benchmarking/api/scenario-group/<id>/review/ returns suite editor modal with top candidates."""
        res = self.client.get(f"/benchmarking/api/scenario-group/{self.scenario_group.id}/review/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/event-stream")
        content = res.content.decode("utf-8")
        self.assertIn("suite-editor-modal", content)
        self.assertIn("Robotics Causal Reasoning", content)
        self.assertIn("Top Model Candidates", content)
        self.assertIn("acoustic resonance", content)
        self.assertIn("Promote to Gold Standard", content)

    def test_update_scenario_api(self):
        """POST /benchmarking/api/scenario/<id>/update/ updates question, ideal_answer, keywords in-place."""
        new_gold = "Updated gold standard answer for valve dynamics."
        new_kws = "chatter, harmonics, resonance"
        res = self.client.post(
            f"/benchmarking/api/scenario/{self.scenario_1.id}/update/",
            data={
                "question": "Updated Question text?",
                "ideal_answer": new_gold,
                "expected_keywords": new_kws,
            }
        )
        self.assertEqual(res.status_code, 200)
        self.scenario_1.refresh_from_db()
        self.assertEqual(self.scenario_1.ideal_answer, new_gold)
        self.assertEqual(self.scenario_1.question, "Updated Question text?")
        self.assertIn("harmonics", self.scenario_1.expected_keywords)

    def test_add_scenario_to_group_api(self):
        """POST /benchmarking/api/scenario-group/<id>/add-scenario/ creates scenario and links to group."""
        res = self.client.post(
            f"/benchmarking/api/scenario-group/{self.scenario_group.id}/add-scenario/",
            data={
                "question": "What is cavitation in axial piston pumps?",
                "ideal_answer": "Formation of vapor bubbles caused by local static pressure falling below vapor pressure.",
                "expected_keywords": "cavitation, vapor pressure, piston",
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.scenario_group.scenarios.count(), 3)
        created = self.scenario_group.scenarios.get(question__contains="cavitation")
        self.assertIn("vapor pressure", created.ideal_answer)

    def test_promote_candidate_in_suite(self):
        """POST /benchmarking/api/promote/<result_id>/ sets ideal_answer to candidate completion."""
        res = self.client.post(f"/benchmarking/api/promote/{self.candidate_result.id}/")
        self.assertEqual(res.status_code, 200)
        self.scenario_1.refresh_from_db()
        self.assertEqual(self.scenario_1.ideal_answer, self.candidate_result.response)
        self.assertIn("acoustic resonance", self.scenario_1.ideal_answer)




