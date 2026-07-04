from django.test import TestCase
from django.test import tag
from unittest.mock import patch
from django.contrib.auth.models import User
from .models import CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition
from .tasks import run_blueprint
from .actions import (
    ExecutionPlan, CheckParsingAction, CheckParsingArgs, TaskCompleteAction, TaskCompleteArgs,
    python_sandbox, initialize_task_queue, process_task_queue, TaskQueue, TaskItem
)

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
        self.agentic_bp = CognitiveBlueprint.objects.create(
            name="Agentic RAG",
            description="Evaluates context and loops if needed."
        )
        self.active_step = ReasoningStep.objects.create(
            blueprint=self.agentic_bp, 
            name="Active Reading", 
            is_start_node=True, 
            system_prompt="Active reading prompt",
            max_retries=2 # Allow 2 loops before failing
        )
        tool = ToolDefinition.objects.get_or_create(name="document_reader", defaults={"description": "Active reading tool", "python_path": "metacognition.meta_tools.document_reader"})[0]
        self.active_step.available_tools.add(tool)
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

    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_agentic_loop_and_success(self, mock_clean, mock_generate, mock_nlp, mock_rag):
        """
        Tests the action hook mutating state. The LLM uses the document_reader tool,
        the graph runs again, and then succeeds.
        """
        # 1. Mock the RAG retrieval to return a specific "Chunk 1"
        mock_rag.return_value = [LangchainDocument(
            page_content="... middle of a sentence.", 
            metadata={"chunk_index": 1, "indexed_hash": "test_hash"}
        )]
        mock_nlp.return_value = ["dummy"]

        # 2. Mock generate_response2 to simulate a multi-turn conversation
        mock_clean.side_effect = lambda x: x
        import json
        
        # Turn 1: LLM calls document_reader tool
        tool_call_json = json.dumps([{
            "name": "document_reader",
            "args": {"action": "fetch_chunk", "target_id": "chunk_0"},
            "id": "call_1"
        }])
        mock_generate.side_effect = [
            [f"<tool_calls>{tool_call_json}</tool_calls>"],
            ["success with final answer."]
        ]
        
        from llm_api.apps import service_registry
        # 3. We must explicitly mock the LocalFileStore so the Action Hook can fetch Chunk 0
        with patch.object(service_registry.rag_service, 'store') as mock_store:
            with patch.object(service_registry.rag_service, 'hashes_indexed', {"test_hash": ["chunk_0", "chunk_1"]}):
                
                # When the action hook calls mget, return Chunk 0
                mock_store.mget.return_value = [
                    LangchainDocument(page_content="This is the beginning of the", metadata={"chunk_index": 0})
                ]

                from metacognition.compiler import compile_graph_from_blueprint
                from metacognition.state import AgentState
                from langchain_core.messages import HumanMessage
                graph = compile_graph_from_blueprint(self.agentic_bp)
                initial_state = AgentState(
                    working_memory=[HumanMessage(content="What is the sentence?")],
                    rag_context="",
                    resume_to=None,
                    token_budget_remaining=None,
                    route_to=None,
                    conversation_id="loop-123",
                    user_id=self.user.id,
                    step_count=0,
                    max_steps=5,
                    retries_remaining={},
                    internal_monologue=[],
                    scratch={"primary_rag_doc_meta": {"chunk_index": 1, "indexed_hash": "test_hash"}}
                )
                config = {"configurable": {"thread_id": "loop-123"}}
                result = graph.invoke(initial_state, config)

        # Assertions
        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        
        print("MONOLOGUE LOOP:", json.dumps(monologue, indent=2))
        
        self.assertEqual(len(monologue), 2, "Should have looped exactly twice.")
        
        # Verify the Draft Answer was extracted correctly on the final success pass
        self.assertIn("success with final answer.", monologue[-1]["output"])

    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_agentic_max_retries_exhaustion(self, mock_clean, mock_generate, mock_nlp, mock_rag):
        """
        Tests that the graph gracefully aborts if the LLM gets stuck in an infinite loop.
        """
        mock_rag.return_value = [LangchainDocument(page_content="Context", metadata={"chunk_index": 5, "indexed_hash": "test"})]
        mock_nlp.return_value = ["dummy"]
        mock_clean.side_effect = lambda x: x

        import json
        tool_call_json = json.dumps([{
            "name": "document_reader",
            "args": {"action": "fetch_chunk", "target_id": "chunk_0"},
            "id": "call_loop"
        }])
        
        # Return a tool call infinitely to exhaust max_retries
        mock_generate.return_value = [f"<tool_calls>{tool_call_json}</tool_calls>"]

        from llm_api.apps import service_registry
        with patch.object(service_registry.rag_service, 'store') as mock_store:
            with patch.object(service_registry.rag_service, 'hashes_indexed', {"test": ["chunk_5", "chunk_6", "chunk_7"]}):
                
                # Keep returning dummy chunks so the action hook succeeds, but the LLM keeps looping
                mock_store.mget.return_value = [
                    LangchainDocument(page_content="More text...", metadata={"chunk_index": 6}),
                    LangchainDocument(page_content="Even more text...", metadata={"chunk_index": 7})
                ]

                from metacognition.compiler import compile_graph_from_blueprint
                from metacognition.state import AgentState
                from langchain_core.messages import HumanMessage
                graph = compile_graph_from_blueprint(self.agentic_bp)
                initial_state = AgentState(
                    working_memory=[HumanMessage(content="Fetch forever.")],
                    rag_context="",
                    resume_to=None,
                    token_budget_remaining=None,
                    route_to=None,
                    conversation_id="loop-456",
                    user_id=self.user.id,
                    step_count=0,
                    max_steps=5,
                    retries_remaining={},
                    internal_monologue=[],
                    scratch={"primary_rag_doc_meta": {"chunk_index": 5, "indexed_hash": "test"}}
                )
                config = {"configurable": {"thread_id": "loop-456"}}
                result = graph.invoke(initial_state, config)

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
    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_execution_plan_and_success(self, mock_clean, mock_generate, mock_nlp, mock_rag, mock_outline):
        """
        Tests the action hook for execution plan with tools.
        """
        mock_nlp.return_value = ["dummy"]
        mock_rag.return_value = [LangchainDocument(page_content="RAG result")]
        mock_clean.side_effect = lambda x: x

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
            max_retries=2
        )
        tool = ToolDefinition.objects.get_or_create(name="handle_execution_plan", defaults={"description": "Execution tool"})[0]
        plan_step.available_tools.add(tool)
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
        
        import json
        call1_json = json.dumps([{"name": "handle_execution_plan", "args": {"analysis": "Need to syntax check code", "queue": [{"tool": "CHECK_PARSING", "parameters": {"code": "print('Hello World')"}, "expected_outcome": "Valid syntax"}]}, "id": "call_1"}])
        call2_json = json.dumps([{"name": "handle_execution_plan", "args": {"analysis": "Parsed successfully", "queue": [{"tool": "TASK_COMPLETE", "parameters": {"final_answer": "The answer is test"}, "expected_outcome": "Done"}]}, "id": "call_2"}])

        mock_generate.side_effect = [
            [f"<tool_calls>{call1_json}</tool_calls>"],
            [f"<tool_calls>{call2_json}</tool_calls>"]
        ]

        result = run_blueprint(plan_bp.id, "Do this plan", user_id=self.user.id)

        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        print("MONOLOGUE PLAN:", json.dumps(monologue, indent=2))
        self.assertIn("The answer is test", result.get("final_response", ""))

    def test_python_sandbox_extraction(self):
        state = {"working_prompt": "", "route_to": None}
        llm_output = "print('hello')"
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"output": "hello\n", "error": "", "return_code": 0}
            
            new_state = python_sandbox(code=llm_output, **state)
            self.assertIn("Sandbox Execution Succeeded", new_state["working_prompt"])
            self.assertEqual(new_state["route_to"], "SUCCESS")



    def test_python_sandbox_api_failure(self):
        state = {"working_prompt": "", "route_to": None}
        llm_output = "bad_code()"
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"output": "", "error": "NameError: name 'bad_code' is not defined", "return_code": 1}
            
            new_state = python_sandbox(code=llm_output, **state)
            self.assertIn("Sandbox Execution Failed", new_state["working_prompt"])
            self.assertEqual(new_state["route_to"], "SELF")

    def test_initialize_task_queue(self):
        state = {"scratch": {}, "route_to": None}
        tq = TaskQueue(queue=[TaskItem(goal="Task 1", delegated_blueprint=None), TaskItem(goal="Task 2", delegated_blueprint=None)])
        new_state = initialize_task_queue(state, tq)
        self.assertEqual(len(new_state["scratch"]["task_queue"]), 2)
        self.assertEqual(new_state["route_to"], "SUCCESS")

    def test_process_task_queue_loop(self):
        state = {
            "scratch": {
                "task_queue": [
                    {"goal": "Task 1", "delegated_blueprint": None},
                    {"goal": "Task 2", "delegated_blueprint": None}
                ]
            },
            "working_prompt": "",
            "route_to": None
        }
        new_state = process_task_queue(state, None)
        self.assertEqual(len(new_state["scratch"]["task_queue"]), 1)
        self.assertIn("Task 1", new_state["working_prompt"])
        self.assertEqual(new_state["route_to"], "SELF")

    @patch('metacognition.tasks.run_blueprint')
    def test_nested_blueprint_delegation(self, mock_run):
        mock_run.return_value = {"final_response": "Delegated answer"}
        
        bp = CognitiveBlueprint.objects.create(name="SubBP")
        
        state = {
            "scratch": {
                "task_queue": [
                    {"goal": "Task 1", "delegated_blueprint": "SubBP"}
                ]
            },
            "working_prompt": "",
            "conversation_id": "test_conv",
            "user_id": self.user.id,
            "route_to": None
        }
        new_state = process_task_queue(state, None)
        
        mock_run.assert_called_once_with(bp.id, "Task 1", conversation_id="test_conv", user_id=self.user.id)
        self.assertIn("Delegated answer", new_state["working_prompt"])
        self.assertEqual(new_state["route_to"], "SELF")

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

    def test_e2e_api_execute_blueprint(self):
        """
        Hits the /api/meta/execute_blueprint/ endpoint using the Django Test Client.
        Validates that the Ninja endpoint parses the payload and delegates to run_blueprint.
        """
        print("\n>>> Running E2E API test for /api/meta/execute_blueprint/...")
        
        # Need to use the Django test client to hit the API
        from django.test import Client
        import json
        client = Client()
        client.force_login(self.test_system_user)
        
        payload = {
            "blueprint_id": self.mocked_tests.idea_bp.id,
            "user_prompt": "What are the pros and cons of using Django?"
        }
        
        # Hit the API endpoint
        response = client.post(
            "/api/meta/execute_blueprint/",
            data=json.dumps(payload),
            content_type="application/json"
        )
        
        self.assertEqual(response.status_code, 200, f"API test failed with status {response.status_code}: {response.content}")
        
        result = response.json()
        self.assertNotIn("error", result)
        self.assertEqual(result.get("blueprint_name"), self.mocked_tests.idea_bp.name)
        self.assertGreater(len(result.get("internal_monologue", [])), 0)
        
        print("✅ E2E API test for /api/meta/execute_blueprint/ passed.")

class NightManagerToolTests(TestCase):
    """
    Tests the specialized tools available to the NightManager for Sysadmin duties.
    """
    def test_django_shell_script_safe(self):
        from metacognition.meta_tools import django_shell_script
        
        # Test 1: Simple DB creation using django shell script
        script = """
from metacognition.models import CognitiveBlueprint
CognitiveBlueprint.objects.create(name="Script created BP")
print("Successfully created BP")
"""
        result = django_shell_script({}, {"script_content": script})
        self.assertIn("Successfully created BP", result)
        self.assertTrue(CognitiveBlueprint.objects.filter(name="Script created BP").exists())
        
        # Test 2: Deletion block
        bad_script = """
CognitiveBlueprint.objects.all().delete()
"""
        result = django_shell_script({}, {"script_content": bad_script})
        self.assertIn("Error: Hard deletes are blocked", result)

    @patch('django.core.management.call_command')
    def test_database_backup(self, mock_call_command):
        from metacognition.meta_tools import database_backup
        import os
        from django.conf import settings
        
        # mock call_command to just create the file
        def side_effect(cmd, *args, **kwargs):
            if "stdout" in kwargs:
                kwargs["stdout"].write("[]")
        mock_call_command.side_effect = side_effect

        result = database_backup({}, {})
        self.assertIn("Database backup successfully saved", result)
        
        # Check file exists
        backup_dir = os.path.join(settings.BASE_DIR, "backups")
        files = os.listdir(backup_dir)
        self.assertTrue(any(f.startswith("db_backup_") and f.endswith(".json") for f in files))
