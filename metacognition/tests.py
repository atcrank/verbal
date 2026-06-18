from django.test import TestCase
from django.test import tag
from unittest.mock import patch
from django.contrib.auth.models import User
from .models import CognitiveBlueprint, ReasoningStep, ResponseSchema
from .tasks import run_blueprint
from background_resources.rag_service import ActiveReadingEvaluation
from .actions import ExecutionPlan, CheckParsingAction, CheckParsingArgs, TaskCompleteAction, TaskCompleteArgs

from langchain_core.documents import Document as LangchainDocument


class MetacognitionTraversalTests(TestCase):
    """
    Tests the graph traversal, retries, and action hooks of Cognitive Blueprints.
    We heavily mock the AI and RAG services to test the Python logic in milliseconds
    without requiring a running inference server or GPU.
    """

    def setUp(self):
        # --- 0. Create a dummy user ---
        self.user = User.objects.create_user(username='testuser', password='password123')

        # --- 1. Build the IDEA Protocol Blueprint (Linear Sequence) ---
        self.idea_bp = CognitiveBlueprint.objects.create(
            name="IDEA protocol",
            description="Linear 3-step evaluation."
        )
        self.step1 = ReasoningStep.objects.create(
            blueprint=self.idea_bp, name="Negative take", is_start_node=True,
            system_prompt="Negative prompt"
        )
        self.step2 = ReasoningStep.objects.create(
            blueprint=self.idea_bp, name="Positive take",
            system_prompt="Positive prompt"
        )
        self.step3 = ReasoningStep.objects.create(
            blueprint=self.idea_bp, name="Best take",
            system_prompt="Balanced prompt"
        )
        # Link the graph
        self.step1.on_success_step = self.step2
        self.step2.on_success_step = self.step3
        self.step1.save()
        self.step2.save()

        # --- 2. Build the Agentic RAG Blueprint (Looping & Action Hooks) ---
        self.rag_schema = ResponseSchema.objects.create(
            name="RAG_eval", 
            schema_type="pydantic", 
            pydantic_model_name="ActiveReadingEvaluation"
        )
        self.agentic_bp = CognitiveBlueprint.objects.create(
            name="Agentic RAG",
            description="Evaluates context and loops if needed."
        )
        self.active_step = ReasoningStep.objects.create(
            blueprint=self.agentic_bp, 
            name="Active Reading", 
            is_start_node=True, 
            system_prompt="Active reading prompt",
            output_schema=self.rag_schema,
            action_hook="handle_active_reading",  # CRITICAL: Maps to the Python function
            max_retries=2 # Allow 2 loops before failing
        )
        # Link to itself on failure
        self.active_step.on_failure_step = self.active_step
        self.active_step.save()

    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    def test_linear_blueprint_traversal(self, mock_nlp, mock_rag, mock_clean, mock_generate):
        """Tests that a linear blueprint successfully steps from 1 -> 2 -> 3."""
        
        # Setup Mocks
        mock_rag.return_value = [LangchainDocument(page_content="Dummy context", metadata={})]
        mock_nlp.return_value = ["dummy"] # Bypass spacy
        mock_generate.return_value = ["Mocked LLM Output"]
        mock_clean.side_effect = lambda x: x # Passthrough

        # Run the executor
        result = run_blueprint(self.idea_bp.id, "Evaluate this problem.", user_id=self.user.id)

        # Assertions
        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        
        self.assertEqual(len(monologue), 3, "Should have executed exactly 3 steps.")
        self.assertEqual(monologue[0]["step_name"], "Negative take")
        self.assertEqual(monologue[1]["step_name"], "Positive take")
        self.assertEqual(monologue[2]["step_name"], "Best take")

    @patch('metacognition.tasks.service_registry.ai_service.generate_outline')
    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    def test_agentic_loop_and_success(self, mock_nlp, mock_rag, mock_outline):
        """
        Tests the action hook mutating state. The LLM asks for the PREVIOUS chunk,
        the action hook appends it, the loop runs again, and then succeeds.
        """
        # 1. Mock the RAG retrieval to return a specific "Chunk 1"
        mock_rag.return_value = [LangchainDocument(
            page_content="... middle of a sentence.", 
            metadata={"chunk_index": 1, "indexed_hash": "test_hash"}
        )]
        mock_nlp.return_value = ["dummy"]

        # 2. Mock the LLM Outline Generation to simulate a sequence of thoughts
        mock_outline.side_effect = [
            # Attempt 1: The LLM realizes it needs the previous chunk
            ActiveReadingEvaluation(
                reasoning="Starts mid-sentence.", 
                context_status="NEED_PREVIOUS_CHUNK", 
                draft_answer=""
            ),
            # Attempt 2: The LLM sees the new context and successfully answers
            ActiveReadingEvaluation(
                reasoning="Makes sense now.", 
                context_status="SUFFICIENT", 
                draft_answer="The full answer."
            )
        ]

        # 3. We must explicitly mock the LocalFileStore so the Action Hook can fetch Chunk 0
        with patch('background_resources.rag_service.RAGService.store') as mock_store:
            with patch('background_resources.rag_service.RAGService.hashes_indexed', {"test_hash": ["chunk_0", "chunk_1"]}):
                
                # When the action hook calls mget, return Chunk 0
                mock_store.mget.return_value = [
                    LangchainDocument(page_content="This is the beginning of the", metadata={"chunk_index": 0})
                ]

                # Run the executor
                result = run_blueprint(self.agentic_bp.id, "What is the sentence?", user_id=self.user.id)

        # Assertions
        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        
        self.assertEqual(len(monologue), 2, "Should have looped exactly twice.")
        
        # Verify the Draft Answer was extracted correctly on the final success pass
        self.assertIn("The full answer.", result.get("final_response"))
        
        # We can also verify that the LLM was called twice
        self.assertEqual(mock_outline.call_count, 2)

    @patch('metacognition.tasks.service_registry.ai_service.generate_outline')
    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    def test_agentic_max_retries_exhaustion(self, mock_nlp, mock_rag, mock_outline):
        """
        Tests that the graph gracefully aborts if the LLM gets stuck in an infinite loop.
        """
        mock_rag.return_value = [LangchainDocument(page_content="Context", metadata={"chunk_index": 5, "indexed_hash": "test"})]
        mock_nlp.return_value = ["dummy"]

        # Simulate an LLM that stubbornly keeps asking for more chunks indefinitely
        mock_outline.return_value = ActiveReadingEvaluation(
            reasoning="Still need more.", 
            context_status="NEED_NEXT_CHUNK", 
            draft_answer=""
        )

        with patch('background_resources.rag_service.RAGService.store') as mock_store:
            with patch('background_resources.rag_service.RAGService.hashes_indexed', {"test": ["chunk_5", "chunk_6", "chunk_7"]}):
                
                # Keep returning dummy chunks so the action hook succeeds, but the LLM keeps looping
                mock_store.mget.return_value = [
                    LangchainDocument(page_content="More text...", metadata={"chunk_index": 6})
                ]

                # We set max_retries to 2 in setUp. 
                # Execution 1 (Initial) -> Retry 2 -> Retry 1 -> Retry 0 (Abort!)
                result = run_blueprint(self.agentic_bp.id, "Fetch forever.", user_id=self.user.id)

        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        
        # Initial run + 2 retries = 3 iterations before aborting
        self.assertEqual(len(monologue), 3)
        
        # The final output should contain the abort message
        self.assertTrue(monologue[-1].get("failed", False))
        self.assertIn("ABORTED: Max retries reached", monologue[-1]["output"])

    @patch('metacognition.tasks.service_registry.ai_service.generate_outline')
    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    def test_execution_plan_and_success(self, mock_nlp, mock_rag, mock_outline):
        """
        Tests the action hook for execution plan with tools.
        """
        mock_nlp.return_value = ["dummy"]
        mock_rag.return_value = [LangchainDocument(page_content="RAG result")]

        schema = ResponseSchema.objects.create(
            name="Plan_Eval",
            schema_type="pydantic",
            pydantic_model_name="ExecutionPlan"
        )
        plan_bp = CognitiveBlueprint.objects.create(name="Plan BP")
        plan_step = ReasoningStep.objects.create(
            blueprint=plan_bp, name="Plan step", is_start_node=True,
            system_prompt="Make a plan",
            output_schema=schema,
            action_hook="handle_execution_plan",
            max_retries=2
        )
        plan_step.on_failure_step = plan_step
        plan_step.save()

        mock_outline.side_effect = [
            ExecutionPlan(
                analysis="Need to syntax check code",
                queue=[CheckParsingAction(tool="CHECK_PARSING", parameters=CheckParsingArgs(code="print('Hello World')"), expected_outcome="Valid syntax")]
            ),
            ExecutionPlan(
                analysis="Parsed successfully",
                queue=[TaskCompleteAction(tool="TASK_COMPLETE", parameters=TaskCompleteArgs(final_answer="The answer is test"), expected_outcome="Done")]
            )
        ]

        result = run_blueprint(plan_bp.id, "Do this plan", user_id=self.user.id)

        self.assertNotIn("error", result)
        self.assertEqual(mock_outline.call_count, 2)
        self.assertIn("The answer is test", result.get("final_response", ""))

    @classmethod
    def tearDownClass(cls):
        from llm_api.apps import service_registry
        if getattr(service_registry, '_rag_service', None):
            service_registry._rag_service.disconnect()
        if getattr(service_registry, '_grips_service', None):
            service_registry._grips_service.disconnect()
        super().tearDownClass()

@tag('e2e')
class MetacognitionE2ETests(TestCase):
    """
    End-to-end tests that hit the REAL, running inference server.
    These are slow and require the full system to be running.
    Run with: python manage.py test --tag=e2e
    """
    @classmethod
    def setUpClass(cls):
        from django.test.utils import override_settings
        cls.settings_override = override_settings(
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
        )
        cls.settings_override.enable()
        super().setUpClass()

        from llm_api.apps import service_registry
        cls.ai_service = service_registry.ai_service

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

        cls.original_outline = cls.ai_service.generate_outline
        cls.original_resp = cls.ai_service.generate_response2

        cls.ai_service.generate_outline = lambda *args, **kwargs: cls.original_outline(*args, **{**kwargs,
                                                                                                 'user': cls.test_system_user})
        cls.ai_service.generate_response2 = lambda *args, **kwargs: cls.original_resp(*args, **{**kwargs, 'user': cls.test_system_user})

    @classmethod
    def tearDownClass(cls):
        from llm_api.apps import service_registry
        if getattr(service_registry, '_rag_service', None):
            service_registry._rag_service.disconnect()
        if getattr(service_registry, '_grips_service', None):
            service_registry._grips_service.disconnect()
        if hasattr(cls, 'original_outline'):
            cls.ai_service.generate_outline = cls.original_outline
            cls.ai_service.generate_response2 = cls.original_resp
        super().tearDownClass()


    def setUp(self):
        # We can just re-use the setup from the mocked tests to create the DB objects
        self.mocked_tests = MetacognitionTraversalTests()
        self.mocked_tests.setUp()

    def test_e2e_linear_blueprint(self):
        """
        Runs the simple IDEA protocol against the live inference server.
        This validates the full HTTP proxy and generation stack.
        """
        print("\n>>> Running E2E test for IDEA protocol...")
        result = run_blueprint(self.mocked_tests.idea_bp.id, "What are the pros and cons of using Django?", user_id=self.mocked_tests.user.id)
        
        self.assertNotIn("error", result, f"E2E run failed with an error: {result.get('error')}")
        monologue = result.get("internal_monologue", [])
        self.assertEqual(len(monologue), 3, "E2E run should have produced a 3-step monologue.")
        for step in monologue:
            self.assertNotIn("Generation failed", str(step.get("output", "")))
        print("✅ E2E test for IDEA protocol passed.")
