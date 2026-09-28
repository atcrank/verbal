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
from benchmarking.long_context_evaluator import run_long_context_evaluation
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

