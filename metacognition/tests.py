from django.test import TestCase
from django.test import tag
from unittest.mock import patch
from django.contrib.auth.models import User
from .models import CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition
from .tasks import run_blueprint
from .actions import (
    ExecutionPlan, CheckParsingAction, CheckParsingArgs, TaskCompleteAction, TaskCompleteArgs,
    python_sandbox, process_task_queue, TaskQueue, TaskItem
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
        task_complete_tool = ToolDefinition.objects.get_or_create(name="TASK_COMPLETE", defaults={"description": "Task complete", "python_path": "metacognition.meta_tools.TASK_COMPLETE"})[0]
        self.active_step.available_tools.add(tool, task_complete_tool)
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
    @patch('metacognition.tasks.service_registry.ai_service.generate_outline')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_agentic_loop_and_success(self, mock_clean, mock_outline, mock_nlp, mock_rag):
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
        mock_outline.side_effect = [
            {"tool_calls": [{"name": "document_reader", "args": {"action": "fetch_chunk", "target_id": "chunk_0"}}]},
            {"tool_calls": [{"name": "TASK_COMPLETE", "args": {"final_answer": "success with final answer."}}]}
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
                from llm_api.models import Conversation
                conv_loop1 = Conversation.objects.create(user_id=self.user.id, title="Test 1")
                initial_state = AgentState(
                    working_memory=[HumanMessage(content="What is the sentence?")],
                    rag_context="",
                    resume_to=None,
                    token_budget_remaining=None,
                    route_to=None,
                    conversation_id=str(conv_loop1.id),
                    user_id=self.user.id,
                    step_count=0,
                    max_steps=5,
                    retries_remaining={},
                    internal_monologue=[],
                    scratch={"primary_rag_doc_meta": {"chunk_index": 1, "indexed_hash": "test_hash"}}
                )
                config = {"configurable": {"thread_id": str(conv_loop1.id)}}
                result = graph.invoke(initial_state, config)

        # Assertions
        self.assertNotIn("error", result)
        monologue = result.get("internal_monologue", [])
        
        print("MONOLOGUE LOOP:", json.dumps(monologue, indent=2))
        
        self.assertEqual(len(monologue), 2, "Should have looped exactly twice.")
        
        # Verify the Draft Answer was extracted correctly on the final success pass
        self.assertIn("success with final answer.", monologue[-1]["output"])

    @patch('llm_api.ai_service.AIService.supports_native_tools', return_value=True)
    @patch('metacognition.tasks.service_registry.rag_service.get_context')
    @patch('metacognition.tasks.service_registry.nlp_service.get_lemmatized_tokens')
    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_agentic_max_retries_exhaustion(self, mock_clean, mock_generate, mock_nlp, mock_rag, mock_native):
        """
        Tests that the graph gracefully aborts if the LLM gets stuck in an infinite loop.
        """
        mock_rag.return_value = [LangchainDocument(page_content="Context", metadata={"chunk_index": 5, "indexed_hash": "test"})]
        mock_nlp.return_value = ["dummy"]
        mock_clean.side_effect = lambda x: x

        tool_call_list = [{
            "name": "document_reader",
            "args": {"action": "fetch_chunk", "target_id": "chunk_0"},
            "id": "call_loop"
        }]
        
        # Return a tool call infinitely to exhaust max_retries
        mock_generate.return_value = [tool_call_list]

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
                from llm_api.models import Conversation
                conv_loop2 = Conversation.objects.create(user_id=self.user.id, title="Test 2")
                initial_state = AgentState(
                    working_memory=[HumanMessage(content="Fetch forever.")],
                    rag_context="",
                    resume_to=None,
                    token_budget_remaining=None,
                    route_to=None,
                    conversation_id=str(conv_loop2.id),
                    user_id=self.user.id,
                    step_count=0,
                    max_steps=5,
                    retries_remaining={},
                    internal_monologue=[],
                    scratch={"primary_rag_doc_meta": {"chunk_index": 5, "indexed_hash": "test"}}
                )
                config = {"configurable": {"thread_id": str(conv_loop2.id)}}
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
    @patch('metacognition.tasks.service_registry.ai_service.clean_response')
    def test_execution_plan_and_success(self, mock_clean, mock_nlp, mock_rag, mock_outline):
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
            max_retries=2
        )
        tool = ToolDefinition.objects.get_or_create(name="handle_execution_plan", defaults={"description": "Execution tool"})[0]
        plan_step.available_tools.add(tool)
        plan_step.on_failure_step = plan_step
        plan_step.save()


        
        import json
        call1_json = json.dumps([{"name": "handle_execution_plan", "args": {"analysis": "Need to syntax check code", "queue": [{"tool": "CHECK_PARSING", "parameters": {"code": "print('Hello World')"}, "expected_outcome": "Valid syntax"}]}, "id": "call_1"}])
        call2_json = json.dumps([{"name": "handle_execution_plan", "args": {"analysis": "Parsed successfully", "queue": [{"tool": "TASK_COMPLETE", "parameters": {"final_answer": "The answer is test"}, "expected_outcome": "Done"}]}, "id": "call_2"}])

        mock_outline.side_effect = [
            {"tool_calls": [{"name": "handle_execution_plan", "args": {"analysis": "Need to syntax check code", "queue": [{"tool": "CHECK_PARSING", "parameters": {"code": "print('Hello World')"}, "expected_outcome": "Valid syntax"}]}}]},
            {"tool_calls": [{"name": "handle_execution_plan", "args": {"analysis": "Parsed successfully", "queue": [{"tool": "TASK_COMPLETE", "parameters": {"final_answer": "The answer is test"}, "expected_outcome": "Done"}]}}]}
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
            mock_post.return_value.json.return_value = {"stdout": "hello\n", "stderr": "", "returncode": 0}
            
            new_state = python_sandbox(state, {"code": llm_output})
            self.assertIn("Sandbox Execution Succeeded", new_state["working_prompt"])
            self.assertEqual(new_state["route_to"], "SUCCESS")



    def test_python_sandbox_api_failure(self):
        state = {"working_prompt": "", "route_to": None}
        llm_output = "bad_code()"
        with patch('requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"stdout": "", "stderr": "NameError: name 'bad_code' is not defined", "returncode": 1}
            
            new_state = python_sandbox(state, {"code": llm_output})
            self.assertIn("Sandbox Execution Failed", new_state["working_prompt"])
            self.assertEqual(new_state["route_to"], "SELF")

    def test_process_task_queue_loop(self):
        state = {
            "scratch": {
                "queue": [
                    {"goal": "Task 1", "delegated_blueprint": None},
                    {"goal": "Task 2", "delegated_blueprint": None}
                ]
            },
            "working_prompt": "",
            "route_to": None
        }
        new_state = process_task_queue(state, None)
        self.assertEqual(len(new_state["scratch"]["queue"]), 1)
        self.assertIn("Task 1", new_state["working_prompt"])
        self.assertEqual(new_state["route_to"], "SELF")

    @patch('metacognition.tasks.run_blueprint')
    def test_nested_blueprint_delegation(self, mock_run):
        mock_run.return_value = {"final_response": "Delegated answer"}
        
        bp = CognitiveBlueprint.objects.create(name="SubBP")
        
        from llm_api.models import Conversation
        conv_loop3 = Conversation.objects.create(user_id=self.user.id, title="Test 3")
        state = {
            "scratch": {
                "queue": [
                    {"goal": "Task 1", "delegated_blueprint": bp.name}
                ]
            },
            "working_prompt": "",
            "conversation_id": str(conv_loop3.id),
            "user_id": self.user.id,
            "route_to": None
        }
        new_state = process_task_queue(state, None)
        
        mock_run.assert_called_once_with(bp.id, "Task 1", conversation_id=str(conv_loop3.id), user_id=self.user.id)
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
class MetacognitionE2EExternalProxyTests(TestCase):
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
        # Dynamically discover the active model to avoid 404s
        import urllib.request
        import json
        import subprocess
        
        active_model_name = "google/gemma-4-E2B-it"
        try:
            ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
            names = ps.stdout.split()
            if "verbal_ollama" in names:
                req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
                with urllib.request.urlopen(req, timeout=2) as response:
                    data = json.loads(response.read().decode())
                    models = data.get("models", [])
                    if models:
                        active_model_name = models[0]["name"]
            elif "verbal_vllm" in names:
                req = urllib.request.Request("http://127.0.0.1:8003/v1/models")
                with urllib.request.urlopen(req, timeout=2) as response:
                    data = json.loads(response.read().decode())
                    models = data.get("data", [])
                    if models:
                        active_model_name = models[0]["id"]
        except Exception:
            pass
            
        from django.conf import settings
        ext_model = ExternalAIModel.objects.create(
            name="Live Inference Server",
            provider="openai",
            api_url="http://127.0.0.1:8001/api/llm/v1/chat/completions",
            api_model_name=active_model_name
        )
        UserActiveModel.objects.update_or_create(
            user=cls.test_system_user,
            defaults={"active_external": ext_model, "use_external": True}
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


@tag('e2e')
class MetacognitionE2ELocalProxyTests(TestCase):
    """
    End-to-end tests that hit the REAL, running local inference server (Ollama or vLLM).
    These tests ensure that the native SystemConfiguration proxy routing works without
    User-specific external overrides.
    """
    @classmethod
    def setUpClass(cls):
        from django.test.utils import override_settings
        cls.settings_override = override_settings(
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
            OLLAMA_BASE_URL="http://127.0.0.1:11434",
            VLLM_BASE_URL="http://127.0.0.1:8003",
        )
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        from llm_api.apps import service_registry
        if getattr(service_registry, '_rag_service', None):
            service_registry._rag_service.disconnect()
        if getattr(service_registry, '_grips_service', None):
            service_registry._grips_service.disconnect()
        super().tearDownClass()

    def setUp(self):
        # We can just re-use the setup from the mocked tests to create the DB objects
        self.mocked_tests = MetacognitionTraversalTests()
        self.mocked_tests.setUp()
        
        # Determine which container is currently running to target it dynamically
        import subprocess
        import urllib.request
        import json
        running_backend = 'pytorch'
        active_model_name = "google/gemma-4-E2B-it"
        try:
            ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
            names = ps.stdout.split()
            if "verbal_ollama" in names:
                running_backend = "ollama"
                try:
                    req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
                    with urllib.request.urlopen(req, timeout=2) as response:
                        data = json.loads(response.read().decode())
                        models = data.get("models", [])
                        if models:
                            active_model_name = models[0]["name"]
                except Exception:
                    pass
            elif "verbal_vllm" in names:
                running_backend = "vllm"
                try:
                    req = urllib.request.Request("http://127.0.0.1:8003/v1/models")
                    with urllib.request.urlopen(req, timeout=2) as response:
                        data = json.loads(response.read().decode())
                        models = data.get("data", [])
                        if models:
                            active_model_name = models[0]["id"]
                except Exception:
                    pass
        except:
            pass
            
        print(f"\n>>> Local E2E Tests targeting backend: {running_backend} with model {active_model_name}")

        from llm_api.models import SystemConfiguration, LocalAIModel
        config = SystemConfiguration.get_solo()
        config.hosting_backend = running_backend
        if running_backend == 'ollama':
            model, _ = LocalAIModel.objects.get_or_create(hf_model_id=active_model_name, defaults={"name": active_model_name})
            config.active_ollama_model = model
        elif running_backend == 'vllm':
            model, _ = LocalAIModel.objects.get_or_create(hf_model_id=active_model_name, defaults={"name": active_model_name})
            config.active_vllm_model = model
        elif running_backend == 'pytorch':
            model, _ = LocalAIModel.objects.get_or_create(hf_model_id=active_model_name, defaults={"name": active_model_name})
            config.active_local_model = model
            
        config.save()

    def test_e2e_local_linear_blueprint(self):
        """
        Runs the simple IDEA protocol against the native local proxy.
        """
        print("\n>>> Running Local E2E test for IDEA protocol...")
        result = run_blueprint(self.mocked_tests.idea_bp.id, "What are the pros and cons of using Django?", user_id=self.mocked_tests.user.id)
        
        self.assertNotIn("error", result, f"E2E run failed with an error: {result.get('error')}")
        monologue = result.get("internal_monologue", [])
        self.assertEqual(len(monologue), 3, "E2E run should have produced a 3-step monologue.")
        for step in monologue:
            self.assertNotIn("Generation failed", str(step.get("output", "")))
        print("✅ Local E2E test for IDEA protocol passed.")

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


class BlueprintEvolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='evolveuser', password='password123')
        self.bp = CognitiveBlueprint.objects.create(name="Evolvable BP")
        self.step_a = ReasoningStep.objects.create(
            blueprint=self.bp, name="Step A", is_start_node=True, system_prompt="Prompt A"
        )
        self.step_b = ReasoningStep.objects.create(
            blueprint=self.bp, name="Step B", system_prompt="Prompt B"
        )
        self.step_a.on_success_step = self.step_b
        self.step_a.save()

    def test_resolve_active_steps_no_variants(self):
        from metacognition.compiler import resolve_active_steps
        resolved, _ = resolve_active_steps(self.bp)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(resolved[self.step_a.id].id, self.step_a.id)
        self.assertEqual(resolved[self.step_b.id].id, self.step_b.id)

    def test_resolve_active_steps_selects_active_leaf(self):
        from metacognition.compiler import resolve_active_steps
        # Retire root step A (or keep it active, but let's test with variant overrides)
        self.step_a.is_active = False
        self.step_a.save()
        
        variant_1 = self.step_a.create_variant(
            variant_intent="V1", is_active=True, selection_weight=3.0
        )
        variant_2 = self.step_a.create_variant(
            variant_intent="V2", is_active=True, selection_weight=7.0
        )
        
        v1_count = 0
        v2_count = 0
        for _ in range(100):
            resolved, _ = resolve_active_steps(self.bp)
            selected = resolved[self.step_a.id]
            if selected.id == variant_1.id:
                v1_count += 1
            elif selected.id == variant_2.id:
                v2_count += 1
                
        self.assertGreater(v2_count, 0)
        self.assertGreater(v1_count, 0)
        self.assertGreater(v2_count, v1_count)

    def test_resolve_active_steps_excludes_inactive(self):
        from metacognition.compiler import resolve_active_steps
        self.step_a.is_active = False
        self.step_a.save()
        
        variant_1 = self.step_a.create_variant(
            variant_intent="V1", is_active=True, selection_weight=5.0
        )
        variant_2 = self.step_a.create_variant(
            variant_intent="V2", is_active=False, selection_weight=5.0
        )
        
        for _ in range(20):
            resolved, _ = resolve_active_steps(self.bp)
            selected = resolved[self.step_a.id]
            self.assertEqual(selected.id, variant_1.id)

    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    def test_edge_remapping_through_variants(self, mock_generate):
        mock_generate.return_value = ["Success result"]
        # Create a variant of step B (which is step_a's success target)
        self.step_b.is_active = False
        self.step_b.save()
        variant_b = self.step_b.create_variant(
            variant_intent="V_B", is_active=True, system_prompt="Variant B Prompt"
        )
        
        from metacognition.compiler import compile_graph_from_blueprint
        graph = compile_graph_from_blueprint(self.bp)
        
        # Verify graph compilation works and start node exists
        self.assertIsNotNone(graph)
        
        # Invoke the graph. It should run step A, evaluate success (no criteria so default to SUCCESS),
        # then route to B's canonical ID, executing variant_b!
        from metacognition.state import AgentState
        from langchain_core.messages import HumanMessage
        
        from llm_api.models import Conversation
        conv_remap = Conversation.objects.create(user_id=self.user.id, title="Test 4")
        state = AgentState(
            working_memory=[],
            rag_context="",
            route_to=None,
            resume_to=None,
            conversation_id=str(conv_remap.id),
            user_id=self.user.id,
            step_count=0,
            max_steps=5,
            retries_remaining={},
            internal_monologue=[],
            scratch={},
            token_budget_remaining=8000
        )
        
        config = {"configurable": {"thread_id": "test-remap-1"}}
        final_state = graph.invoke(state, config)
        
        monologue = final_state.get("internal_monologue", [])
        self.assertEqual(len(monologue), 2)
        self.assertEqual(monologue[0]["step_name"], "Step A")
        self.assertEqual(monologue[1]["step_name"], "Step B")
        self.assertEqual(monologue[1]["system_prompt"], "Variant B Prompt")

    def test_create_variant_sets_pending_review(self):
        variant = self.step_a.create_variant(
            variant_intent="Intent review",
            is_pending_review=True,
            proposed_by="system"
        )
        self.assertTrue(variant.is_pending_review)
        self.assertEqual(variant.proposed_by, "system")
        self.assertEqual(variant.parent_step.id, self.step_a.id)

    def test_blueprint_parent_lineage(self):
        # Test clone sets parent
        from metacognition.admin import clone_blueprint
        class DummyRequest:
            pass
        
        # Set performance scores on steps to check family_success_probability
        self.step_a.performance_score = 0.8
        self.step_a.save()
        self.step_b.performance_score = 0.9
        self.step_b.save()
        
        self.assertAlmostEqual(self.bp.family_success_probability, 0.72)
        
        class DummyAdmin:
            def message_user(self, *args, **kwargs):
                pass
        
        clone_blueprint(DummyAdmin(), DummyRequest(), CognitiveBlueprint.objects.filter(id=self.bp.id))
        
        cloned = CognitiveBlueprint.objects.filter(name="Copy of Evolvable BP").first()
        self.assertIsNotNone(cloned)
        self.assertEqual(cloned.parent.id, self.bp.id)
        # Cloned blueprint step scores initially 0.0, so family success is None
        self.assertIsNone(cloned.family_success_probability)

    def test_summarizer_truncates_memory(self):
        from metacognition.summarizer import summarize_if_needed
        from langchain_core.messages import SystemMessage, HumanMessage
        
        sys_msg = SystemMessage(content="System prompt", id="sys_1")
        long_text = " ".join(["word"] * 30)
        messages = [sys_msg] + [HumanMessage(content=f"User msg {i}: {long_text}", id=f"msg_{i}") for i in range(20)]
        
        state = {
            "working_memory": messages,
            "token_budget_remaining": 499 # Use spec budget
        }
        
        result = summarize_if_needed(state)
        self.assertIn("working_memory", result)
        remove_msgs = result["working_memory"]
        self.assertGreater(len(remove_msgs), 0)
        
        from langchain_core.messages import RemoveMessage
        for rm in remove_msgs:
            self.assertIsInstance(rm, RemoveMessage)
            
        removed_ids = {rm.id for rm in remove_msgs}
        self.assertNotIn("sys_1", removed_ids)
        self.assertNotIn("msg_19", removed_ids)

    @patch('metacognition.tasks.service_registry.ai_service.generate_response2')
    def test_prompt_response_log_records_variant(self, mock_generate):
        mock_generate.return_value = ["Success result"]
        self.step_a.is_active = False
        self.step_a.save()
        variant_a = self.step_a.create_variant(
            variant_intent="V_A", is_active=True, system_prompt="Variant A Prompt"
        )
        
        from metacognition.compiler import compile_graph_from_blueprint
        graph = compile_graph_from_blueprint(self.bp)
        
        from metacognition.state import AgentState
        from langchain_core.messages import HumanMessage
        
        from llm_api.models import Conversation
        conv_log = Conversation.objects.create(user_id=self.user.id, title="Test 5")
        state = AgentState(
            working_memory=[HumanMessage(content="User prompt")],
            rag_context="",
            route_to=None,
            resume_to=None,
            conversation_id=str(conv_log.id),
            user_id=self.user.id,
            step_count=0,
            max_steps=5,
            retries_remaining={},
            internal_monologue=[],
            scratch={},
            token_budget_remaining=8000
        )
        
        config = {"configurable": {"thread_id": "test-log-1"}}
        graph.invoke(state, config)
        
        mock_generate.assert_called()
        call_args = mock_generate.call_args_list[0]
        log_kwargs = call_args.kwargs.get("log_kwargs", {})
        self.assertEqual(log_kwargs.get("reasoning_step_id"), variant_a.id)


class AsyncStreamingAndGovernanceTests(TestCase):
    """
    Tests for Level 1 Tasks 5, 6, and 7:
    - Datastar SSE framing and event dispatch
    - Human-in-the-loop dynamic tool approval and LangGraph checkpoint resumption
    - Blueprint stop/interrupt and cancellation flag handling
    - Async API endpoints
    """

    def setUp(self):
        self.user = User.objects.create_user(username='gov_user', password='password123')
        self.bp = CognitiveBlueprint.objects.create(name="Governance BP", description="Tests governance & streaming")
        self.step = ReasoningStep.objects.create(
            blueprint=self.bp,
            name="Approval Step",
            is_start_node=True,
            system_prompt="Run tools if needed",
            max_retries=1
        )

    def test_datastar_sse_framing(self):
        """Task 5: Verifies that DatastarSSE formats SSE events strictly according to protocol."""
        from metacognition.datastar import DatastarSSE

        # 1. Merge fragments
        frag_sse = DatastarSSE.merge_fragments("<div id='test'>Hello</div>", selector="#test", merge_mode="morph")
        self.assertIn("event: datastar-merge-fragments", frag_sse)
        self.assertIn("data: selector #test", frag_sse)
        self.assertIn("data: fragments <div id='test'>Hello</div>", frag_sse)

        # 2. Merge signals
        sig_sse = DatastarSSE.merge_signals({"isRunning": True, "step": 2})
        self.assertIn("event: datastar-merge-signals", sig_sse)
        self.assertIn('"isRunning": true', sig_sse)

        # 3. Execute script
        script_sse = DatastarSSE.execute_script("console.log('test')")
        self.assertIn("event: datastar-execute-script", script_sse)
        self.assertIn("data: script console.log('test')", script_sse)

    def test_dynamic_tool_requires_approval_enforcement(self):
        """Task 6: Verifies that dynamic tools created by manage_dynamic_tools require approval."""
        from metacognition.meta_tools import manage_dynamic_tools

        script = "def dynamic_multiplier(state, params):\n    return int(params.get('val', 1)) * 2\n"
        res = manage_dynamic_tools(
            state={},
            params={"name": "dynamic_multiplier", "description": "Multiplies numbers", "script_content": script}
        )
        self.assertIn("Successfully created dynamic tool", res)

        tool = ToolDefinition.objects.get(name="dynamic_multiplier")
        self.assertTrue(tool.requires_approval, "Dynamic tool MUST enforce requires_approval = True")
        self.assertTrue(tool.is_active)

    @patch('llm_api.ai_service.AIService.generate_outline')
    @patch('llm_api.ai_service.AIService.generate_response2')
    @patch('llm_api.ai_service.AIService.clean_response')
    def test_human_in_the_loop_suspension_and_resumption(self, mock_clean, mock_generate, mock_outline):
        """Task 6: Verifies LangGraph suspends when unapproved tool is called, and resumes on approval."""
        from metacognition.tasks import run_blueprint, task_resume_blueprint_async
        from metacognition.models import AgentCheckpoint

        # Create tool with approval requirement
        restricted_tool = ToolDefinition.objects.create(
            name="restricted_tool",
            description="Restricted action",
            tool_type="builtin",
            python_path="metacognition.meta_tools.TASK_COMPLETE",
            requires_approval=True
        )
        self.step.available_tools.add(restricted_tool)

        # 1. Model requests to call restricted_tool via outline schema
        mock_outline.return_value = {"tool_calls": [{"name": "restricted_tool", "args": {"arg1": "val1"}}]}
        mock_clean.side_effect = lambda x: x

        run_id = "test-hil-run-1"
        res = run_blueprint(self.bp.id, "Test HIL", user_id=self.user.id, run_id=run_id)

        # Assert graph suspended at USER_INPUT_REQUIRED
        self.assertEqual(res.get("route_to"), "USER_INPUT_REQUIRED")
        self.assertIsNotNone(res.get("pending_approval"))
        self.assertEqual(res["pending_approval"]["tool_name"], "restricted_tool")

        thread_id = res["thread_id"]
        # Verify checkpoint saved in Postgres
        checkpoints = AgentCheckpoint.objects.filter(thread_id=thread_id)
        self.assertTrue(checkpoints.exists(), "Checkpoint must be stored when graph is suspended for approval.")

        # 2. Resume execution by approving tool
        mock_outline.return_value = {"tool_calls": []}
        mock_generate.return_value = ["Task finished successfully after authorization."]
        resume_res = task_resume_blueprint_async(
            blueprint_id=self.bp.id,
            thread_id=thread_id,
            run_id=run_id,
            approved_tool="restricted_tool"
        )

        self.assertNotIn("error", resume_res)
        self.assertEqual(resume_res.get("run_id"), run_id)

    @patch('llm_api.ai_service.AIService.generate_outline')
    @patch('llm_api.ai_service.AIService.generate_response2')
    @patch('llm_api.ai_service.AIService.clean_response')
    def test_blueprint_stop_and_cancellation(self, mock_clean, mock_generate, mock_outline):
        """Task 7: Verifies that setting the Redis cancellation flag aborts graph execution."""
        from metacognition.events import set_cancellation_flag, is_cancelled
        from metacognition.tasks import run_blueprint

        mock_clean.side_effect = lambda x: x
        mock_generate.return_value = ["Standard generation"]
        mock_outline.return_value = {}

        run_id = "test-cancel-run-99"
        set_cancellation_flag(run_id)
        self.assertTrue(is_cancelled(run_id))

        res = run_blueprint(self.bp.id, "Prompt after cancel", user_id=self.user.id, run_id=run_id)

        # Graph should halt immediately
        monologue = res.get("internal_monologue", [])
        self.assertTrue(any("cancelled by user" in m.get("output", "").lower() for m in monologue))

    def test_api_endpoints_dispatch_cancel_approve(self):
        """Task 5, 6, 7 API Endpoints: Verifies dispatch, cancel, and approve REST endpoints."""
        from django.test import Client
        import json

        client = Client()
        client.force_login(self.user)

        # 1. Dispatch Blueprint
        resp = client.post(
            "/api/meta/dispatch_blueprint/",
            data=json.dumps({"blueprint_id": self.bp.id, "user_prompt": "Async test prompt"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "dispatched")
        run_id = data.get("run_id")
        self.assertIsNotNone(run_id)
        self.assertIn("/api/meta/stream_blueprint/?run_id=", data.get("stream_url"))

        # 2. Cancel Blueprint
        resp_cancel = client.post(
            "/api/meta/cancel_blueprint/",
            data=json.dumps({"run_id": run_id}),
            content_type="application/json"
        )
        self.assertEqual(resp_cancel.status_code, 200)
        self.assertEqual(resp_cancel.json().get("status"), "cancellation_requested")

        # 3. Approve Tool
        resp_approve = client.post(
            "/api/meta/approve_tool/",
            data=json.dumps({
                "run_id": run_id,
                "thread_id": f"conv1_{self.bp.name}",
                "tool_name": "restricted_tool",
                "blueprint_id": self.bp.id
            }),
            content_type="application/json"
        )
        self.assertEqual(resp_approve.status_code, 200)
        self.assertEqual(resp_approve.json().get("status"), "resumed")


class ReasoningStepStateTreeTests(TestCase):
    """
    Task 8 Tests for StateTree Formatting and ReasoningStep.include_state_tree flag.
    """

    def setUp(self):
        from llm_api.models import Conversation
        self.user = User.objects.create_user(username='st_user', password='password123')
        self.conv = Conversation.objects.create(
            user=self.user,
            title="StateTree Test",
            state_tree={
                "macro_objective": "Optimize RAG Indexing",
                "active_task": "task_chunk_sweep",
                "tasks": {
                    "task_chunk_sweep": {"title": "Scan orphan chunks", "status": "IN_PROGRESS"},
                    "task_lint": {"title": "Lint concept graph", "status": "PENDING"}
                },
                "working_hypotheses": ["Orphan chunks slow down cosine distance filtering"],
                "open_questions": ["Is HNSW indexing active on all chunks?"]
            }
        )
        self.bp = CognitiveBlueprint.objects.create(name="StateTree BP")
        self.step = ReasoningStep.objects.create(
            blueprint=self.bp,
            name="State Aware Step",
            is_start_node=True,
            system_prompt="Analyze current tasks and propose fixes.",
            include_state_tree=True
        )

    def test_format_state_tree_helper(self):
        """Verifies that _format_state_tree produces structured, readable Markdown."""
        from metacognition.compiler import _format_state_tree

        formatted = _format_state_tree(self.conv.state_tree)
        self.assertIn("### Conversation State Tree:", formatted)
        self.assertIn("- **Objective:** Optimize RAG Indexing", formatted)
        self.assertIn("- **Active Task:** task_chunk_sweep", formatted)
        self.assertIn("- [IN_PROGRESS] Scan orphan chunks", formatted)
        self.assertIn("- [PENDING] Lint concept graph", formatted)
        self.assertIn("- **Working Hypotheses:**", formatted)
        self.assertIn("- **Open Questions:**", formatted)

    @patch('llm_api.ai_service.AIService.generate_response2')
    @patch('llm_api.ai_service.AIService.clean_response')
    def test_include_state_tree_flag_controls_prompt_injection(self, mock_clean, mock_generate):
        """Verifies include_state_tree=True injects state_tree, while False suppresses it."""
        from metacognition.compiler import compile_graph_from_blueprint
        from metacognition.state import AgentState
        from langchain_core.messages import HumanMessage

        mock_clean.side_effect = lambda x: x
        mock_generate.return_value = ["Analysis complete."]

        graph = compile_graph_from_blueprint(self.bp)

        # 1. Test with include_state_tree = True
        state = AgentState(
            working_memory=[HumanMessage(content="What is my task?")],
            rag_context="",
            route_to=None,
            resume_to=None,
            conversation_id=str(self.conv.id),
            user_id=self.user.id,
            step_count=0,
            max_steps=5,
            retries_remaining={},
            internal_monologue=[],
            scratch={},
            token_budget_remaining=8000
        )
        config = {"configurable": {"thread_id": "st-test-1"}}
        res = graph.invoke(state, config)

        call_args = mock_generate.call_args_list[0]
        prompt_used = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt", "")
        self.assertIn("Conversation State Tree", str(prompt_used))
        self.assertIn("Optimize RAG Indexing", str(prompt_used))

        # 2. Test with include_state_tree = False
        self.step.include_state_tree = False
        self.step.save()
        mock_generate.reset_mock()

        graph2 = compile_graph_from_blueprint(self.bp)
        config2 = {"configurable": {"thread_id": "st-test-2"}}
        res2 = graph2.invoke(state, config2)

        call_args2 = mock_generate.call_args_list[0]
        prompt_used2 = call_args2.args[0] if call_args2.args else call_args2.kwargs.get("prompt", "")
        self.assertNotIn("Conversation State Tree", str(prompt_used2))


class NightManagerReportingTests(TestCase):
    """
    Tests the diagnostic auditing and reporting functions for NightManager.
    """
    def setUp(self):
        self.nm_user, _ = User.objects.get_or_create(username="NightManager")
        from llm_api.models import Conversation, PromptResponseLog
        from grips.models import Domain, ConceptNode

        self.conv = Conversation.objects.create(
            user=self.nm_user,
            title="NightManager: NM_Housekeeping",
            state_tree={
                "tasks": {
                    "rag_chunk_optimization": {"status": "COMPLETED"},
                    "grips_link_verification": {"status": "pending"}
                },
                "working_hypotheses": ["Vector index 384 hnsw performs best"],
                "open_questions": ["Is grobid service reachable?"]
            }
        )
        self.log = PromptResponseLog.objects.create(
            user=self.nm_user,
            conversation=self.conv,
            model_name="Gemma4-2B",
            user_prompt="Run housekeeping",
            generated_response="Housekeeping complete",
            generation_duration_ms=450.0,
            input_tokens=100,
            output_tokens=50,
            step_status="SUCCESS"
        )
        self.domain = Domain.objects.create(name="CognitiveArchitecture")
        self.node = ConceptNode.objects.create(
            domain=self.domain,
            title="ReasoningStep Speciation",
            slug="reasoningstep-speciation",
            narrative_content="Evolutionary prompt variants",
            needs_linting=False
        )

    def test_audit_nightmanager_performance_structure(self):
        from metacognition.reporting import audit_nightmanager_performance, format_performance_report_markdown
        report = audit_nightmanager_performance(since_days=7)

        self.assertIn("sessions", report)
        self.assertIn("state_tree_health", report)
        self.assertIn("knowledge_artifacts", report)
        self.assertIn("blueprint_evolution", report)

        self.assertEqual(report["sessions"]["total_lifetime_conversations"], 1)
        self.assertEqual(report["sessions"]["recent_prompt_logs"], 1)
        self.assertEqual(report["state_tree_health"]["resolved_tasks"], 1)
        self.assertEqual(report["state_tree_health"]["pending_tasks"], 1)
        self.assertEqual(report["knowledge_artifacts"]["total_concept_nodes"], 1)

        md = format_performance_report_markdown(report)
        self.assertIn("NightManager Performance & Architecture Audit", md)
        self.assertIn("Gemma4-2B", md)
        self.assertIn("ReasoningStep Variants Pending Review", md)

    def test_inspect_nightmanager_command(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command('inspect_nightmanager', '--days', '7', stdout=out)
        output_str = out.getvalue()
        self.assertIn("NightManager Performance & Architecture Audit", output_str)

    def test_inspect_nightmanager_tool(self):
        from metacognition.meta_tools import inspect_nightmanager_performance
        res = inspect_nightmanager_performance({}, {"days": 7})
        self.assertIn("NightManager Performance & Architecture Audit", res)


class ActionSchemaPersistenceTests(TestCase):
    """
    Tests that structured schema action nodes persist database objects and resolve state_tree tasks.
    """
    def setUp(self):
        self.user, _ = User.objects.get_or_create(username="testuser")
        from llm_api.models import Conversation
        self.bp = CognitiveBlueprint.objects.create(name="Schema Test BP")
        self.step = ReasoningStep.objects.create(
            blueprint=self.bp,
            name="Target Step",
            system_prompt="Original prompt",
            evaluation_criteria="Original criteria"
        )
        self.conv = Conversation.objects.create(
            user=self.user,
            title="Schema Conv",
            state_tree={
                "tasks": {
                    "optimize_target_step": {"status": "pending"}
                }
            }
        )

    def test_create_prompt_variant_persists_reasoning_step(self):
        from metacognition.actions import PromptVariant, create_prompt_variant
        pv = PromptVariant(
            target_step_id=self.step.id,
            variant_intent="Stricter JSON compliance",
            reasoning="Small model failed to format valid JSON in logs.",
            new_system_prompt="Refined prompt with strict JSON output.",
            new_evaluation_criteria="Did the LLM output valid JSON?"
        )
        state = {"conversation_id": str(self.conv.id), "user_id": self.user.id}
        res = create_prompt_variant(state, pv)

        self.assertEqual(res["route_to"], "SUCCESS")
        self.assertIn("Created pending ReasoningStep variant", res["working_prompt"])

        variant = ReasoningStep.objects.filter(parent_step=self.step, is_pending_review=True).first()
        self.assertIsNotNone(variant)
        self.assertEqual(variant.system_prompt, "Refined prompt with strict JSON output.")
        self.assertEqual(variant.variant_intent, "Stricter JSON compliance")
        self.assertEqual(variant.proposed_by, "system")

        # Verify state tree task resolution
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.state_tree["tasks"]["optimize_target_step"]["status"], "COMPLETED")

    def test_handle_grips_expansion_persists_concept(self):
        from metacognition.actions import GripsExpansionProposal, handle_grips_expansion
        from grips.models import ConceptNode, Domain

        proposal = GripsExpansionProposal(
            domain_name="CognitiveScience",
            title="State Tree Snapshotting",
            focus_hint="Immutable DAG conversation audit trails",
            narrative_content="State trees capture granular cognitive tasks per step.",
            structured_claims=[{"subject": "State Tree", "predicate": "captures", "object": "Task State"}]
        )
        state = {"conversation_id": str(self.conv.id)}
        res = handle_grips_expansion(state, proposal)

        self.assertEqual(res["route_to"], "SUCCESS")
        node = ConceptNode.objects.filter(title="State Tree Snapshotting").first()
        self.assertIsNotNone(node)
        self.assertEqual(node.domain.name, "CognitiveScience")
        self.assertTrue(node.needs_linting)


class SubBlueprintStateTreePropagationTests(TestCase):
    """
    Tests bidirectional state_tree synchronization across parent and child sub-blueprints.
    """
    def setUp(self):
        self.user, _ = User.objects.get_or_create(username="NightManager")
        from llm_api.models import Conversation
        self.parent_conv = Conversation.objects.create(
            user=self.user,
            title="NightManager: NightManager",
            state_tree={
                "tasks": {
                    "phase0_housekeeping": {"status": "COMPLETED"},
                    "phase1_eval": {"status": "pending"}
                },
                "working_hypotheses": ["Initial hypothesis"]
            }
        )

    def test_merge_state_trees_helper(self):
        from metacognition.compiler import _merge_state_trees

        parent_tree = {
            "tasks": {
                "task1": {"status": "pending"},
                "task2": {"status": "pending"}
            },
            "working_hypotheses": ["Hypothesis A"],
            "open_questions": ["Question 1"]
        }
        child_tree = {
            "tasks": {
                "task1": {"status": "COMPLETED"},
                "task3": {"status": "pending"}
            },
            "working_hypotheses": ["Hypothesis A", "Hypothesis B"],
            "open_questions": ["Question 2"]
        }
        merged = _merge_state_trees(parent_tree, child_tree)

        self.assertEqual(merged["tasks"]["task1"]["status"], "COMPLETED")
        self.assertEqual(merged["tasks"]["task2"]["status"], "pending")
        self.assertEqual(merged["tasks"]["task3"]["status"], "pending")
        self.assertEqual(merged["working_hypotheses"], ["Hypothesis A", "Hypothesis B"])
        self.assertEqual(merged["open_questions"], ["Question 1", "Question 2"])



