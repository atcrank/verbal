import os
from django.conf import settings
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
        cls.settings_override = override_settings()
        cls.settings_override.enable()
        super().setUpClass()

        from llm_api.apps import service_registry
        cls.ai_service = service_registry.ai_service

        from django.contrib.auth.models import User
        from llm_api.models import ExternalAIModel, UserActiveModel

        cls.test_system_user, _ = User.objects.get_or_create(username='test_system_user')

        # Check if live inference server at 127.0.0.1:8001 is running; skip if not
        import socket
        import unittest
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', 8001)) != 0:
                raise unittest.SkipTest("Live inference server is not running on 127.0.0.1:8001")

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

    @classmethod
    def tearDownClass(cls):
        from llm_api.models import SystemConfiguration
        config = SystemConfiguration.get_solo()
        config.active_local_model = None
        config.active_ollama_model = None
        config.active_vllm_model = None
        config.save()
        super().tearDownClass()


class NightManagerToolTests(TestCase):
    """
    Tests the specialized tools available to the NightManager for Sysadmin duties.
    """
    def test_django_shell_script_safe(self):
        from metacognition.meta_tools import django_shell_script, write_django_model
        from metacognition.models import CognitiveBlueprint
        
        # Test 1: Dynamic Python calculation executed safely in Docker sandbox
        script = """
import math
vals = [1, 4, 9, 16, 25]
result = sum([math.sqrt(v) for v in vals])
print(f"Computed sum: {result}")
"""
        result = django_shell_script({}, {"script_content": script})
        self.assertIn("Computed sum: 15.0", result)
        
        # Test 2: Security AST visitor blocks forbidden modules (e.g. os/subprocess) and hard deletes
        bad_script = """
import os
os.system("rm -rf /")
"""
        result_bad = django_shell_script({}, {"script_content": bad_script})
        self.assertIn("Security violation", result_bad)

        delete_script = """
CognitiveBlueprint.objects.all().delete()
"""
        result_del = django_shell_script({}, {"script_content": delete_script})
        self.assertIn("Hard deletes via .delete() are blocked", result_del)


        # Test 3: NightManager engages with database via structured write_django_model
        # Requires self-modification allowance (e.g. DEVELOPMENT mode)
        with self.settings(ALLOW_AGENT_SELF_MODIFICATION=True):
            bp_res = write_django_model({}, {
                "app_label": "metacognition",
                "model_name": "CognitiveBlueprint",
                "action": "create",
                "parameters": {"name": "NM Managed Blueprint"}
            })
            self.assertIn("Successfully created CognitiveBlueprint", bp_res)
            self.assertTrue(CognitiveBlueprint.objects.filter(name="NM Managed Blueprint").exists())


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

    def test_clone_and_modify_blueprint(self):
        """Verifies deep copying and edge rewiring of blueprints and steps."""
        from metacognition.meta_tools import clone_and_modify_blueprint
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        
        source_bp = CognitiveBlueprint.objects.create(name="Original Strategy", description="Source description")
        step1 = ReasoningStep.objects.create(blueprint=source_bp, name="Step 1", is_start_node=True, system_prompt="Prompt 1")
        step2 = ReasoningStep.objects.create(blueprint=source_bp, name="Step 2", system_prompt="Prompt 2")
        step1.on_success_step = step2
        step1.save()
        
        res = clone_and_modify_blueprint({}, {
            "source_id": source_bp.id,
            "name": "Cloned Strategy",
            "step_modifications": {
                "Step 1": {"system_prompt": "Modified Prompt 1"}
            }
        })
        self.assertIn("Successfully cloned blueprint", res)
        cloned_bp = CognitiveBlueprint.objects.filter(name="Cloned Strategy").first()
        self.assertIsNotNone(cloned_bp)
        self.assertEqual(cloned_bp.parent, source_bp)
        self.assertEqual(cloned_bp.steps.count(), 2)
        cloned_step1 = cloned_bp.steps.filter(name="Step 1").first()
        self.assertEqual(cloned_step1.system_prompt, "Modified Prompt 1")
        self.assertIsNotNone(cloned_step1.on_success_step)
        self.assertEqual(cloned_step1.on_success_step.blueprint, cloned_bp)
        self.assertEqual(cloned_step1.on_success_step.name, "Step 2")



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
        resume_res = task_resume_blueprint_async.func(
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
        self.assertIsNotNone(data.get("task_id"))
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

    def test_execute_blueprint_async_mode(self):
        """Ticket 2.1: Verifies /api/meta/execute_blueprint/ returns task_id and stream_url with async_mode=True."""
        import json
        from django.test import Client
        from unittest.mock import patch, MagicMock

        client = Client()
        client.force_login(self.user)

        with patch("metacognition.api.task_run_blueprint_async") as mock_task:
            mock_task.enqueue.return_value = MagicMock(id="mock-blueprint-task-999")

            resp = client.post(
                "/api/meta/execute_blueprint/",
                data=json.dumps({
                    "blueprint_id": self.bp.id,
                    "user_prompt": "Async execution test",
                    "async_mode": True
                }),
                content_type="application/json"
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("status"), "dispatched")
            self.assertEqual(data.get("task_id"), "mock-blueprint-task-999")
            self.assertIn("/api/meta/stream_blueprint/?run_id=", data.get("stream_url"))


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

    @patch('llm_api.apps.service_registry.ai_service.generate_response2')
    @patch('llm_api.apps.service_registry.ai_service.clean_response')
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


class MetacognitionStateTreeCompactionTests(TestCase):
    """
    Tests for LangGraph AgentState state_tree reducer and NightManager state tree compaction task.
    """

    def setUp(self):
        from llm_api.models import Conversation
        self.user = User.objects.create_user(username='meta_st_user', password='password123')
        self.conv = Conversation.objects.create(
            user=self.user,
            title="Metacognition State Tree Test",
            state_tree={
                "macro_objective": "Test Metacognition Working Memory",
                "active_task": "task_active",
                "established_facts": ["PostgreSQL 18 active"],
                "settled_milestones": {
                    f"m_{i}": {"title": f"Milestone {i}", "status": "COMPLETED"}
                    for i in range(15)
                },
                "tasks": {
                    "task_active": {"title": "Active Task", "status": "IN_PROGRESS"}
                }
            }
        )

    def test_update_compact_state_tree_reducer(self):
        """Verifies update_compact_state_tree merges trees and runs fast inline compaction."""
        from metacognition.state import update_compact_state_tree

        left = {
            "tasks": {
                "t1": {"title": "Task 1", "status": "IN_PROGRESS"}
            },
            "established_facts": ["Fact 1"]
        }
        right = {
            "tasks": {
                "t1": {"title": "Task 1", "status": "COMPLETED"},
                "t2": {"title": "Task 2", "status": "IN_PROGRESS"}
            },
            "established_facts": ["Fact 2"]
        }
        merged = update_compact_state_tree(left, right)
        self.assertEqual(merged["tasks"]["t1"]["status"], "COMPLETED")
        self.assertEqual(merged["tasks"]["t2"]["status"], "IN_PROGRESS")
        self.assertEqual(merged["established_facts"], ["Fact 1", "Fact 2"])

    def test_task_compact_conversation_state_trees_task(self):
        """Verifies NightManager task detects bloated state_trees, reduces them, and synthesizes breadcrumbs."""
        from metacognition.tasks import task_compact_conversation_state_trees
        import json

        initial_len = len(json.dumps(self.conv.state_tree))
        self.assertGreater(initial_len, 500)

        # Run compaction with threshold lower than initial_len
        res = task_compact_conversation_state_trees.func(threshold_chars=400)
        self.assertEqual(res.get("status"), "success")
        self.assertEqual(res.get("compacted_count"), 1)

        self.conv.refresh_from_db()
        new_len = len(json.dumps(self.conv.state_tree))
        self.assertLess(new_len, initial_len)
        self.assertIn("ancient_discussions", self.conv.state_tree)
        breadcrumbs = self.conv.state_tree["ancient_discussions"]
        self.assertGreater(len(breadcrumbs), 0)
        self.assertIn("start me off again", breadcrumbs[0]["prompt_anchor"])

    def test_seed_nightmanager_schedules_state_tree_compaction(self):
        """Verifies seed_nightmanager registers the Nightly State Tree Compaction task."""
        from verbal_tasks.models import ScheduledTask
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition, bypass_canonical_lock
        from metacognition.seed import seed_nightmanager

        with bypass_canonical_lock():
            seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)

        compaction_task = ScheduledTask.objects.filter(name="Nightly State Tree Compaction").first()
        self.assertIsNotNone(compaction_task)
        self.assertEqual(compaction_task.cron_expression, "0 22 * * *")
        self.assertEqual(compaction_task.task_name, "metacognition.tasks.task_compact_conversation_state_trees")
        self.assertTrue(compaction_task.is_active)


class ReasoningStepVariantPruningTests(TestCase):
    """
    Unit tests for Ticket 2.3: NightManager ReasoningStep Variant Pruning and Lineage Management.
    Validates depth capping, ancestor compression, dead-end leaf pruning, and graph edge rewiring.
    """

    def setUp(self):
        from metacognition.models import CognitiveBlueprint, ReasoningStep, bypass_canonical_lock
        with bypass_canonical_lock():
            self.bp = CognitiveBlueprint.objects.create(
                name="Pruning Test Blueprint",
                description="Test blueprint for variant pruning",
                is_canonical=False
            )
            # Create a 4-step pipeline: Start -> Step A -> Step B -> Step C
            self.start_step = ReasoningStep.objects.create(
                blueprint=self.bp,
                name="Start Node",
                is_start_node=True,
                is_canonical=False,
                system_prompt="Start prompt"
            )
            self.step_a = ReasoningStep.objects.create(
                blueprint=self.bp,
                name="Root Step A",
                is_canonical=True,
                system_prompt="Root step A prompt"
            )
            self.step_b = ReasoningStep.objects.create(
                blueprint=self.bp,
                name="Root Step B",
                is_canonical=False,
                system_prompt="Root step B prompt"
            )
            self.start_step.on_success_step = self.step_a
            self.start_step.save()
            self.step_a.on_success_step = self.step_b
            self.step_a.save()

    def test_lineage_depth_calculation(self):
        """Validates that get_lineage_depth correctly computes generational distance from root."""
        from metacognition.pruning import get_lineage_depth
        from metacognition.models import bypass_canonical_lock

        self.assertEqual(get_lineage_depth(self.step_a), 0)

        with bypass_canonical_lock():
            var_1 = self.step_a.create_variant(variant_intent="Gen 1")
            var_2 = var_1.create_variant(variant_intent="Gen 2")
            var_3 = var_2.create_variant(variant_intent="Gen 3")

        self.assertEqual(get_lineage_depth(var_1), 1)
        self.assertEqual(get_lineage_depth(var_2), 2)
        self.assertEqual(get_lineage_depth(var_3), 3)

    def test_compress_ancestors_reparents_active_champion(self):
        """
        Validates that when depth exceeds max_depth, inactive intermediate ancestors are pruned
        and the active champion is re-parented directly to the root, preserving depth budget.
        """
        from metacognition.pruning import compress_lineage_ancestors, get_lineage_depth
        from metacognition.models import bypass_canonical_lock

        with bypass_canonical_lock():
            # Create lineage: Step A (Root, d=0) -> Var 1 (d=1) -> Var 2 (d=2) -> Var 3 (d=3) -> Var 4 (d=4)
            var_1 = self.step_a.create_variant(variant_intent="Gen 1")
            var_1.is_active = False  # Retired
            var_1.save()

            var_2 = var_1.create_variant(variant_intent="Gen 2")
            var_2.is_active = False  # Retired
            var_2.save()

            var_3 = var_2.create_variant(variant_intent="Gen 3")
            var_3.is_active = False  # Retired
            var_3.save()

            var_4 = var_3.create_variant(variant_intent="Gen 4")
            var_4.is_active = True  # Active Champion
            var_4.save()

        self.assertEqual(get_lineage_depth(var_4), 4)

        # Compress with max_depth=2
        pruned_ids = compress_lineage_ancestors(self.bp, max_depth=2)
        self.assertIn(var_1.id, pruned_ids)
        self.assertIn(var_2.id, pruned_ids)
        self.assertIn(var_3.id, pruned_ids)

        var_4.refresh_from_db()
        self.assertEqual(var_4.parent_step_id, self.step_a.id)
        self.assertEqual(get_lineage_depth(var_4), 1)
        self.assertTrue(var_4.is_active)

    def test_rewire_edges_on_pruning(self):
        """
        Validates that pruning a step safely rewires all incoming on_success_step, on_failure_step,
        and parallel_steps references to the active replacement step.
        """
        from metacognition.pruning import rewire_step_references
        from metacognition.models import bypass_canonical_lock

        with bypass_canonical_lock():
            child_variant = self.step_b.create_variant(variant_intent="Better B")
            child_variant.is_active = True
            child_variant.save()

        # Step A points to Step B
        self.assertEqual(self.step_a.on_success_step_id, self.step_b.id)

        # Rewire Step B references to child_variant
        rewire_step_references(self.step_b, child_variant)

        self.step_a.refresh_from_db()
        self.assertEqual(self.step_a.on_success_step_id, child_variant.id)

    def test_canonical_steps_never_pruned(self):
        """Ensures that canonical steps (is_canonical=True) are immune from pruning."""
        from metacognition.pruning import prune_blueprint_variants
        from metacognition.models import ReasoningStep

        self.assertTrue(self.step_a.is_canonical)
        pruned_info = prune_blueprint_variants(self.bp, max_depth=1)
        self.assertNotIn(self.step_a.id, pruned_info.get("pruned_step_ids", []))
        self.assertTrue(ReasoningStep.objects.filter(id=self.step_a.id).exists())

    def test_lone_active_variant_never_pruned(self):
        """Ensures an active step is never pruned if it is the sole active step in its lineage."""
        from metacognition.pruning import prune_dead_leaf_variants
        from metacognition.models import ReasoningStep

        # self.step_b is active and the sole step in its lineage
        pruned = prune_dead_leaf_variants(self.bp)
        self.assertNotIn(self.step_b.id, pruned)
        self.assertTrue(ReasoningStep.objects.filter(id=self.step_b.id).exists())

    def test_prune_dead_leaf_variants(self):
        """Validates that rejected or stagnant non-canonical leaf variants are removed."""
        from metacognition.pruning import prune_dead_leaf_variants
        from metacognition.models import ReasoningStep, bypass_canonical_lock

        with bypass_canonical_lock():
            # Create an active child and a rejected sibling
            good_child = self.step_b.create_variant(variant_intent="Active Child")
            good_child.is_active = True
            good_child.save()

            rejected_leaf = self.step_b.create_variant(variant_intent="Rejected Idea")
            rejected_leaf.is_active = False
            rejected_leaf.is_pending_review = False
            rejected_leaf.save()

        pruned_ids = prune_dead_leaf_variants(self.bp)
        self.assertIn(rejected_leaf.id, pruned_ids)
        self.assertFalse(ReasoningStep.objects.filter(id=rejected_leaf.id).exists())
        self.assertTrue(ReasoningStep.objects.filter(id=good_child.id).exists())

    def test_dry_run_mode(self):
        """Validates that dry_run=True identifies candidates without deleting or mutating records."""
        from metacognition.pruning import prune_blueprint_variants
        from metacognition.models import ReasoningStep, bypass_canonical_lock

        with bypass_canonical_lock():
            dead_leaf = self.step_b.create_variant(variant_intent="Dead Leaf")
            dead_leaf.is_active = False
            dead_leaf.is_pending_review = False
            dead_leaf.save()

        res = prune_blueprint_variants(self.bp, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertIn(dead_leaf.id, res["pruned_step_ids"])
        # Assert record still exists in database
        self.assertTrue(ReasoningStep.objects.filter(id=dead_leaf.id).exists())

    def test_task_prune_reasoning_step_variants(self):
        """Verifies the NightManager task executes and returns audit results."""
        from metacognition.tasks import task_prune_reasoning_step_variants
        res = task_prune_reasoning_step_variants.func(max_depth=4)
        self.assertEqual(res.get("status"), "success")
        self.assertIn("total_pruned", res)

    def test_seed_nightmanager_registers_pruning_task(self):
        """Verifies that seed_nightmanager registers the Weekly ReasoningStep Variant Pruning task."""
        from verbal_tasks.models import ScheduledTask
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition, bypass_canonical_lock
        from metacognition.seed import seed_nightmanager

        with bypass_canonical_lock():
            seed_nightmanager(CognitiveBlueprint, ReasoningStep, ResponseSchema, ToolDefinition)

        task = ScheduledTask.objects.filter(name="Weekly ReasoningStep Variant Pruning").first()
        self.assertIsNotNone(task)
        self.assertEqual(task.cron_expression, "0 3 * * 0")
        self.assertEqual(task.task_name, "metacognition.tasks.task_prune_reasoning_step_variants")
        self.assertTrue(task.is_active)


class SystemJanitorTests(TestCase):
    """
    Tests the hardened system_janitor tool:
    - Prunes orphaned conversation workspaces.
    - Prunes empty workspaces and workspaces containing only .git/.agents.
    - Protects reserved directories.
    - Does not corrupt internal .git metadata of active workspaces.
    """

    def setUp(self):
        import os
        from django.conf import settings
        self.user = User.objects.create_user(username='janitor_test_user', password='password123')
        self.workspaces_root = os.path.join(settings.BASE_DIR, "workspaces")
        os.makedirs(self.workspaces_root, exist_ok=True)

    def test_janitor_protects_reserved_namespaces(self):
        from metacognition.meta_tools import system_janitor
        for reserved in ["grips_okf", "agent_scripts", "conversations", "doctests", "tests"]:
            p = os.path.join(self.workspaces_root, reserved)
            os.makedirs(p, exist_ok=True)

        system_janitor({}, {})

        for reserved in ["grips_okf", "agent_scripts", "conversations", "doctests", "tests"]:
            p = os.path.join(self.workspaces_root, reserved)
            self.assertTrue(os.path.exists(p), f"Reserved namespace {reserved} must not be deleted")

    def test_janitor_deletes_orphaned_conversation_workspace(self):
        from metacognition.meta_tools import system_janitor
        import uuid
        import subprocess

        orphan_id = str(uuid.uuid4())
        orphan_dir = os.path.join(self.workspaces_root, "conversations", orphan_id)
        os.makedirs(orphan_dir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=orphan_dir, capture_output=True)
        with open(os.path.join(orphan_dir, "leftover_script.py"), "w") as f:
            f.write("print('orphaned')")

        self.assertTrue(os.path.exists(orphan_dir))
        result = system_janitor({}, {})
        self.assertFalse(os.path.exists(orphan_dir))
        self.assertIn("Janitor deleted", result)

    def test_janitor_preserves_active_conversation_workspace_and_git(self):
        from metacognition.meta_tools import system_janitor
        from llm_api.models import Conversation
        import subprocess

        conv = Conversation.objects.create(user=self.user, title="Active Janitor Test")
        active_dir = conv.get_workspace_dir()
        os.makedirs(active_dir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=active_dir, capture_output=True)
        with open(os.path.join(active_dir, "active_script.py"), "w") as f:
            f.write("print('active')")
        subprocess.run(["git", "add", "."], cwd=active_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=active_dir, capture_output=True)

        git_head = os.path.join(active_dir, ".git", "HEAD")
        self.assertTrue(os.path.exists(git_head))

        system_janitor({}, {})

        # Active directory and its git HEAD must be fully preserved
        self.assertTrue(os.path.exists(active_dir))
        self.assertTrue(os.path.exists(git_head))
        self.assertTrue(os.path.exists(os.path.join(active_dir, "active_script.py")))

        # Clean up
        conv.delete()


class ToolGovernancePolicyTests(TestCase):
    """
    Unit tests for WS17 Layered Tool Governance:
    - Tier 1 settings clamps (AIR_GAPPED, CONTROLLED, DEVELOPMENT)
    - Tier 2 tool capability categories and write_django_model allowlist
    - Tier 3 user clearance level resolution
    """

    def setUp(self):
        self.standard_user = User.objects.create_user(username="standard_user", password="password123")
        self.trusted_user = User.objects.create_user(username="trusted_user", password="password123")
        self.trusted_user.is_trusted = True
        self.trusted_user.save()
        self.admin_user = User.objects.create_superuser(username="admin_user", password="password123")

    def test_is_tool_permitted_in_air_gapped_mode(self):
        from metacognition.governance import is_tool_permitted, ToolCapability, ClearanceLevel
        from unittest.mock import MagicMock

        read_tool = MagicMock(name="read_tool", capability_category=ToolCapability.READ_ONLY, required_clearance=ClearanceLevel.STANDARD, is_active=True)
        code_tool = MagicMock(name="code_tool", capability_category=ToolCapability.CODE_EXECUTION, required_clearance=ClearanceLevel.TRUSTED, is_active=True)
        meta_tool = MagicMock(name="meta_tool", capability_category=ToolCapability.META_GOVERNANCE, required_clearance=ClearanceLevel.ADMIN, is_active=True)

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_MODEL_CODE_EXECUTION=False,
            ALLOW_AGENT_SELF_MODIFICATION=False
        ):
            # Read tool permitted for everyone
            ok, msg = is_tool_permitted(read_tool, self.standard_user)
            self.assertTrue(ok)

            # Code tool blocked by host lockdown policy
            ok, msg = is_tool_permitted(code_tool, self.standard_user)
            self.assertFalse(ok)
            self.assertIn("requires code execution", msg)

            # Even admin cannot execute code if host policy clamps it
            ok, msg = is_tool_permitted(code_tool, self.admin_user)
            self.assertFalse(ok)

            # Meta-tool blocked by host lockdown policy
            ok, msg = is_tool_permitted(meta_tool, self.admin_user)
            self.assertFalse(ok)
            self.assertIn("meta-tool", msg)

    def test_is_tool_permitted_controlled_mode_with_clearance(self):
        from metacognition.governance import is_tool_permitted, ToolCapability, ClearanceLevel
        from unittest.mock import MagicMock

        code_tool = MagicMock(name="code_tool", capability_category=ToolCapability.CODE_EXECUTION, required_clearance=ClearanceLevel.TRUSTED, is_active=True)

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="CONTROLLED",
            ALLOW_MODEL_CODE_EXECUTION=True,
            ALLOW_AGENT_SELF_MODIFICATION=False
        ):
            # Standard user has insufficient clearance for TRUSTED code tool
            ok, msg = is_tool_permitted(code_tool, self.standard_user)
            self.assertFalse(ok)
            self.assertIn("insufficient", msg)

            # Trusted user has clearance
            ok, msg = is_tool_permitted(code_tool, self.trusted_user)
            self.assertTrue(ok)

            # Admin user also has clearance
            ok, msg = is_tool_permitted(code_tool, self.admin_user)
            self.assertTrue(ok)

    def test_is_tool_permitted_inactive_tool(self):
        from metacognition.governance import is_tool_permitted, ToolCapability, ClearanceLevel
        from unittest.mock import MagicMock

        inactive_tool = MagicMock(name="dead_tool", capability_category=ToolCapability.READ_ONLY, required_clearance=ClearanceLevel.STANDARD, is_active=False)
        ok, msg = is_tool_permitted(inactive_tool, self.admin_user)
        self.assertFalse(ok)
        self.assertIn("inactive", msg)

    def test_write_django_model_allowlist_enforcement_in_air_gapped(self):
        from metacognition.meta_tools import write_django_model

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_AGENT_SELF_MODIFICATION=False
        ):
            # 1. Attacking ToolDefinition must be rejected
            res1 = write_django_model({}, {
                "app_label": "metacognition",
                "model_name": "ToolDefinition",
                "action": "create",
                "parameters": {"name": "evil_tool", "python_path": "os.system"}
            })
            self.assertIn("Error: Security violation", res1)
            self.assertIn("not in the permitted domain write allowlist", res1)

            # 2. Attacking auth.User must be rejected
            res2 = write_django_model({}, {
                "app_label": "auth",
                "model_name": "User",
                "action": "create",
                "parameters": {"username": "evil_admin"}
            })
            self.assertIn("Error: Security violation", res2)

            # 3. Attacking CognitiveBlueprint in AIR_GAPPED mode must be rejected
            res3 = write_django_model({}, {
                "app_label": "metacognition",
                "model_name": "CognitiveBlueprint",
                "action": "create",
                "parameters": {"name": "Self Mod BP"}
            })
            self.assertIn("Error: Security violation", res3)

            # 4. Domain entity (grips.ConceptNode) is permitted
            from grips.models import Domain
            domain = Domain.objects.create(name="Governance Domain")
            res4 = write_django_model({}, {
                "app_label": "grips",
                "model_name": "ConceptNode",
                "action": "create",
                "parameters": {"title": "Allowed Governance Concept", "domain_id": domain.id}
            })
            self.assertIn("Successfully created ConceptNode", res4)

    def test_write_django_model_dev_self_mod_allowed(self):
        from metacognition.meta_tools import write_django_model

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="DEVELOPMENT",
            ALLOW_AGENT_SELF_MODIFICATION=True
        ):
            # In DEVELOPMENT mode, CognitiveBlueprint is permitted for self-mod research
            res = write_django_model({}, {
                "app_label": "metacognition",
                "model_name": "CognitiveBlueprint",
                "action": "create",
                "parameters": {"name": "Dev Research Blueprint"}
            })
            self.assertIn("Successfully created CognitiveBlueprint", res)

            # But auth.User and ToolDefinition are STILL strictly blocked
            res_auth = write_django_model({}, {
                "app_label": "auth",
                "model_name": "User",
                "action": "create",
                "parameters": {"username": "still_blocked"}
            })
            self.assertIn("Error: Security violation", res_auth)

    def test_execute_tool_blocks_prohibited_execution(self):
        from metacognition.tool_executor import execute_tool
        from metacognition.models import ToolDefinition

        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_gov_code_tool",
            defaults={
                "tool_type": "builtin",
                "python_path": "metacognition.meta_tools.TASK_COMPLETE",
                "capability_category": "CODE_EXECUTION",
                "required_clearance": "TRUSTED",
            }
        )

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_MODEL_CODE_EXECUTION=False
        ):
            # Direct invocation via execute_tool in AIR_GAPPED mode must return governance violation
            res = execute_tool(code_tool, {"user": self.standard_user}, {})
            self.assertIn("Error: Governance violation", res)
            self.assertIn("requires code execution", res)

    def test_compiler_filters_tools_and_injects_directive(self):
        from unittest.mock import patch
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition
        from metacognition.compiler import compile_graph_from_blueprint

        bp = CognitiveBlueprint.objects.create(name="Gov Compiler BP", description="Test")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Gov Step",
            is_start_node=True,
            system_prompt="Base system prompt"
        )
        read_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_gov_read",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "READ_ONLY"}
        )
        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_gov_code",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "CODE_EXECUTION", "required_clearance": "TRUSTED"}
        )
        step.available_tools.add(read_tool, code_tool)

        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_MODEL_CODE_EXECUTION=False
        ):
            with patch("llm_api.ai_service.AIService.generate_response2") as mock_gen, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                mock_gen.return_value = ["Analysis complete."]
                graph = compile_graph_from_blueprint(bp)
                state = {
                    "working_memory": [],
                    "rag_context": "",
                    "route_to": None,
                    "conversation_id": "test_gov_conv",
                    "user_id": self.standard_user.id,
                    "step_count": 0,
                    "max_steps": 5,
                    "retries_remaining": {},
                    "internal_monologue": [],
                    "scratch": {},
                    "token_budget_remaining": 8000
                }
                res = graph.invoke(state, {"configurable": {"thread_id": "test_gov_conv_1"}})
                
                # Check system prompt received governance directive
                monologue = res.get("internal_monologue", [])
                self.assertTrue(len(monologue) > 0)
                sys_prompt = monologue[0].get("system_prompt", "")
                self.assertIn("[SYSTEM GOVERNANCE DIRECTIVE]", sys_prompt)
                self.assertIn("Active Lockdown Mode: AIR_GAPPED", sys_prompt)

                # Verify mock_gen received tools: only read_tool should be present, code_tool filtered out
                call_kwargs = mock_gen.call_args.kwargs
                passed_tools = call_kwargs.get("tools", [])
                tool_names = [t["function"]["name"] for t in passed_tools]
                self.assertIn("test_gov_read", tool_names)
                self.assertNotIn("test_gov_code", tool_names)

    def test_evaluate_blueprint_governance_ready(self):
        from metacognition.governance import evaluate_blueprint_governance
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Ready BP", description="Testing ready status")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Ready Step",
            is_start_node=True,
            system_prompt="Analyze problem."
        )
        read_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_ready_read",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "READ_ONLY"}
        )
        step.available_tools.add(read_tool)

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            compat = evaluate_blueprint_governance(bp, self.standard_user)
            self.assertEqual(compat["status"], "READY")
            self.assertFalse(compat["is_locked"])
            self.assertFalse(compat["is_degraded"])
            self.assertIn("Compatible", compat["tooltip"])

    def test_evaluate_blueprint_governance_locked_code_exec(self):
        from metacognition.governance import evaluate_blueprint_governance
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Code BP", description="Testing code locked status")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Sandbox Step",
            is_start_node=True,
            system_prompt="Run calculation in sandbox."
        )
        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_locked_code",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "CODE_EXECUTION"}
        )
        step.available_tools.add(code_tool)

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            compat = evaluate_blueprint_governance(bp, self.standard_user)
            self.assertEqual(compat["status"], "LOCKED")
            self.assertTrue(compat["is_locked"])
            self.assertIn("Requires Code Execution", compat["tooltip"])
            self.assertIn("AIR_GAPPED", compat["tooltip"])

    def test_evaluate_blueprint_governance_degraded(self):
        from metacognition.governance import evaluate_blueprint_governance
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Degraded BP", description="Testing degraded status")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Mixed Step",
            is_start_node=True,
            system_prompt="Research with optional sandbox."
        )
        read_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_deg_read",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "READ_ONLY"}
        )
        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_deg_code",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "CODE_EXECUTION"}
        )
        step.available_tools.add(read_tool, code_tool)

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            compat = evaluate_blueprint_governance(bp, self.standard_user)
            self.assertEqual(compat["status"], "DEGRADED")
            self.assertFalse(compat["is_locked"])
            self.assertTrue(compat["is_degraded"])
            self.assertIn("test_deg_code", compat["tooltip"])

    def test_evaluate_blueprint_governance_user_clearance(self):
        from metacognition.governance import evaluate_blueprint_governance
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Admin BP", description="Testing admin clearance")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Admin Step",
            is_start_node=True,
            system_prompt="Admin operation."
        )
        admin_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_admin_clearance_tool",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "READ_ONLY", "required_clearance": "ADMIN"}
        )
        step.available_tools.add(admin_tool)

        # Standard user is locked out
        compat_std = evaluate_blueprint_governance(bp, self.standard_user)
        self.assertTrue(compat_std["is_locked"])
        self.assertIn("clearance", compat_std["tooltip"].lower())

        # Admin user is permitted
        compat_admin = evaluate_blueprint_governance(bp, self.admin_user)
        self.assertFalse(compat_admin["is_locked"])
        self.assertEqual(compat_admin["status"], "READY")

    def test_tool_governance_middleware_header(self):
        from django.test import RequestFactory
        from django.http import HttpResponse
        from metacognition.middleware import ToolGovernanceMiddleware

        factory = RequestFactory()
        request = factory.get("/demo/")
        middleware = ToolGovernanceMiddleware(lambda req: HttpResponse("OK"))

        with self.settings(VERBAL_LOCKDOWN_LEVEL="RESTRICTED"):
            response = middleware(request)
            self.assertEqual(response.headers.get("X-Verbal-Lockdown-Level"), "RESTRICTED")

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED"):
            response = middleware(request)
            self.assertEqual(response.headers.get("X-Verbal-Lockdown-Level"), "AIR_GAPPED")

    def test_governance_context_processor(self):
        from django.test import RequestFactory
        from metacognition.context_processors import governance_context

        factory = RequestFactory()
        request = factory.get("/demo/")
        request.user = self.standard_user

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            ctx = governance_context(request)
            self.assertIn("governance", ctx)
            gov = ctx["governance"]
            self.assertEqual(gov["lockdown_level"], "AIR_GAPPED")
            self.assertEqual(gov["badge_label"], "AIR-GAPPED")
            self.assertIn("BLOCKED", gov["code_exec_display"])
            self.assertEqual(gov["user_clearance"], "STANDARD")

    def test_demo_ui_send_message_blocks_locked_blueprint(self):
        from django.test import RequestFactory
        from demo_ui.views import send_message
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Locked BP For Post", description="Test")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Code Step",
            is_start_node=True,
            system_prompt="Run code."
        )
        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_post_locked_code",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "CODE_EXECUTION"}
        )
        step.available_tools.add(code_tool)

        factory = RequestFactory()
        request = factory.post("/demo/send_message/", {
            "user_prompt": "Hello",
            "blueprint_id": str(bp.id),
        })
        request.user = self.standard_user

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            response = send_message(request)
            self.assertEqual(response.status_code, 403)
            self.assertIn("Execution Blocked by Governance Policy", response.content.decode("utf-8"))

    def test_run_blueprint_blocks_locked_blueprint(self):
        from metacognition.tasks import run_blueprint
        from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition

        bp = CognitiveBlueprint.objects.create(name="Locked BP For Task", description="Test")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Code Step",
            is_start_node=True,
            system_prompt="Run code."
        )
        code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_task_locked_code",
            defaults={"tool_type": "builtin", "python_path": "metacognition.meta_tools.TASK_COMPLETE", "capability_category": "CODE_EXECUTION"}
        )
        step.available_tools.add(code_tool)

        with self.settings(VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED", ALLOW_MODEL_CODE_EXECUTION=False):
            result = run_blueprint(bp.id, "Hello", user_id=self.standard_user.id)
            self.assertEqual(result.get("status"), 403)
            self.assertIn("Blueprint execution blocked", result.get("error", ""))


class ToolGovernanceHostingBackendTests(TestCase):
    """
    Verifies that Layered Tool Governance (WS17) is enforced across
    all three model hosting solutions:
    1. PyTorch (Local in-process / proxy)
    2. vLLM (Containerized high-throughput)
    3. Ollama (Containerized local models)

    Verifies:
    - Host-level lockdown policies (AIR_GAPPED, TEXT_ONLY, RESTRICTED)
    - Dynamic injection of [SYSTEM GOVERNANCE DIRECTIVE]
    - Dynamic payload tool stripping (prohibited tools stripped before request dispatch)
    - Tool execution gateway invariant checks blocking unauthorized execution
    - Permissive tool pass-through in DEVELOPMENT mode
    """

    def setUp(self):
        import json
        from django.contrib.auth.models import User
        from llm_api.models import SystemConfiguration, LocalAIModel, Conversation
        from metacognition.models import ToolDefinition

        self.standard_user = User.objects.create_user(username="backend_std_user", password="password123")
        self.trusted_user = User.objects.create_user(username="backend_trusted_user", password="password123")
        self.trusted_user.is_trusted = True
        self.trusted_user.save()
        from django.contrib.auth.models import Group
        trusted_group, _ = Group.objects.get_or_create(name="Trusted Operators")
        self.trusted_user.groups.add(trusted_group)

        self.admin_user = User.objects.create_superuser(username="backend_admin_user", password="password123")

        self.conv = Conversation.objects.create(user=self.standard_user, title="Backend Gov Test Conv")

        self.config = SystemConfiguration.get_solo()

        from llm_api.apps import service_registry
        self.original_ai_role = service_registry.ai_service.role
        service_registry.ai_service.role = "web"

        self.pytorch_model, _ = LocalAIModel.objects.get_or_create(
            hf_model_id="google/gemma-2-2b-it",
            defaults={"name": "Gemma 2 2B (PyTorch)"}
        )
        self.vllm_model, _ = LocalAIModel.objects.get_or_create(
            hf_model_id="mistralai/Mistral-7B-Instruct-v0.2",
            defaults={"name": "Mistral 7B (vLLM)"}
        )
        self.ollama_model, _ = LocalAIModel.objects.get_or_create(
            hf_model_id="llama3.2:3b",
            defaults={"name": "Llama 3.2 3B (Ollama)"}
        )

        self.config.active_local_model = self.pytorch_model
        self.config.active_vllm_model = self.vllm_model
        self.config.active_ollama_model = self.ollama_model
        self.config.save()

        self.read_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_backend_read_tool",
            defaults={
                "tool_type": "builtin",
                "python_path": "metacognition.meta_tools.TASK_COMPLETE",
                "capability_category": "READ_ONLY",
                "required_clearance": "STANDARD",
                "input_schema": json.dumps({
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]
                })
            }
        )
        self.code_tool, _ = ToolDefinition.objects.get_or_create(
            name="test_backend_code_tool",
            defaults={
                "tool_type": "builtin",
                "python_path": "metacognition.meta_tools.TASK_COMPLETE",
                "capability_category": "CODE_EXECUTION",
                "required_clearance": "TRUSTED",
                "input_schema": json.dumps({
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"]
                })
            }
        )

    def tearDown(self):
        try:
            from llm_api.apps import service_registry
            service_registry.ai_service.role = getattr(self, "original_ai_role", "web")
            from llm_api.models import SystemConfiguration
            config = SystemConfiguration.get_solo()
            if config:
                config.hosting_backend = "pytorch"
                config.active_local_model = None
                config.save()
        except Exception:
            pass

    def test_pytorch_backend_restricted_modes(self):
        from unittest.mock import patch, MagicMock
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.compiler import compile_graph_from_blueprint
        from metacognition.tool_executor import execute_tool

        self.config.hosting_backend = "pytorch"
        self.config.save()

        bp = CognitiveBlueprint.objects.create(name="PyTorch Gov BP", description="Test PyTorch governance")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="PyTorch Step",
            is_start_node=True,
            system_prompt="Base reasoning instructions."
        )
        step.available_tools.add(self.read_tool, self.code_tool)

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "PyTorch analysis complete."}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 10}
        }

        # 1. Test AIR_GAPPED Mode
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_MODEL_CODE_EXECUTION=False,
            ALLOW_AGENT_SELF_MODIFICATION=False
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state = {
                    "working_memory": [],
                    "rag_context": "",
                    "route_to": None,
                    "conversation_id": str(self.conv.id),
                    "user_id": self.standard_user.id,
                    "step_count": 0,
                    "max_steps": 3,
                    "retries_remaining": {},
                    "internal_monologue": [],
                    "scratch": {},
                    "token_budget_remaining": 8000
                }
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_1"}})

                self.assertTrue(mock_post.called)
                post_call = mock_post.call_args
                url = post_call.args[0] if post_call.args else post_call.kwargs.get("url", "")
                payload = post_call.kwargs.get("json", {})

                # Target endpoint is /chat/completions
                self.assertIn("/chat/completions", url)

                # Verify system prompt has governance directive injected
                messages = payload.get("messages", [])
                sys_content = messages[0].get("content", "") if messages else ""
                self.assertIn("[SYSTEM GOVERNANCE DIRECTIVE]", sys_content)
                self.assertIn("Active Lockdown Mode: AIR_GAPPED", sys_content)

                # Verify payload tools: code_tool is stripped out, only read_tool remains
                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

            # Gateway Invariant: Even if PyTorch model hallucinates a code_tool execution, execute_tool blocks it
            exec_res = execute_tool(self.code_tool, {"user": self.standard_user}, {"code": "print('attack')"})
            self.assertIn("Error: Governance violation", exec_res)
            self.assertIn("requires code execution", exec_res)

        # 2. Test RESTRICTED Mode
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="RESTRICTED",
            ALLOW_MODEL_CODE_EXECUTION=False
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state["user_id"] = self.standard_user.id
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_2"}})

                post_call = mock_post.call_args
                payload = post_call.kwargs.get("json", {})
                sys_content = payload.get("messages", [])[0].get("content", "")
                self.assertIn("Active Lockdown Mode: RESTRICTED", sys_content)

                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

    def test_vllm_backend_restricted_modes(self):
        from unittest.mock import patch, MagicMock
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.compiler import compile_graph_from_blueprint
        from metacognition.tool_executor import execute_tool

        self.config.hosting_backend = "vllm"
        self.config.save()

        bp = CognitiveBlueprint.objects.create(name="vLLM Gov BP", description="Test vLLM governance")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="vLLM Step",
            is_start_node=True,
            system_prompt="vLLM instructions."
        )
        step.available_tools.add(self.read_tool, self.code_tool)

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "vLLM output complete."}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12}
        }

        # 1. Test AIR_GAPPED Mode with vLLM
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
            ALLOW_MODEL_CODE_EXECUTION=False,
            VLLM_BASE_URL="http://vllm-test:8000"
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state = {
                    "working_memory": [],
                    "rag_context": "",
                    "route_to": None,
                    "conversation_id": str(self.conv.id),
                    "user_id": self.standard_user.id,
                    "step_count": 0,
                    "max_steps": 3,
                    "retries_remaining": {},
                    "internal_monologue": [],
                    "scratch": {},
                    "token_budget_remaining": 8000
                }
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_vllm1"}})

                self.assertTrue(mock_post.called)
                post_call = mock_post.call_args
                url = post_call.args[0] if post_call.args else post_call.kwargs.get("url", "")
                payload = post_call.kwargs.get("json", {})

                # Verify request routed to vLLM endpoint
                self.assertIn("vllm-test:8000", url)
                self.assertEqual(payload.get("model"), self.vllm_model.hf_model_id)

                # Verify directive injection in vLLM system prompt
                sys_content = payload.get("messages", [])[0].get("content", "")
                self.assertIn("[SYSTEM GOVERNANCE DIRECTIVE]", sys_content)
                self.assertIn("Active Lockdown Mode: AIR_GAPPED", sys_content)

                # Verify tools payload to vLLM strips prohibited code execution tool
                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

            # Gateway check: execution blocked
            exec_res = execute_tool(self.code_tool, {"user": self.standard_user}, {"code": "print('attack')"})
            self.assertIn("Error: Governance violation", exec_res)

        # 2. Test RESTRICTED Mode with vLLM
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="RESTRICTED",
            ALLOW_MODEL_CODE_EXECUTION=False,
            VLLM_BASE_URL="http://vllm-test:8000"
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state["user_id"] = self.standard_user.id
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_vllm2"}})

                post_call = mock_post.call_args
                payload = post_call.kwargs.get("json", {})
                sys_content = payload.get("messages", [])[0].get("content", "")
                self.assertIn("Active Lockdown Mode: RESTRICTED", sys_content)

                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

    def test_ollama_backend_restricted_modes(self):
        from unittest.mock import patch, MagicMock
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.compiler import compile_graph_from_blueprint
        from metacognition.tool_executor import execute_tool

        self.config.hosting_backend = "ollama"
        self.config.save()

        bp = CognitiveBlueprint.objects.create(name="Ollama Gov BP", description="Test Ollama governance")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Ollama Step",
            is_start_node=True,
            system_prompt="Ollama instructions."
        )
        step.available_tools.add(self.read_tool, self.code_tool)

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Ollama output complete."}}],
            "usage": {"prompt_tokens": 18, "completion_tokens": 8}
        }

        # Test TEXT_ONLY (alias for AIR_GAPPED) with Ollama
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="TEXT_ONLY",
            ALLOW_MODEL_CODE_EXECUTION=False,
            OLLAMA_BASE_URL="http://ollama-test:11434"
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state = {
                    "working_memory": [],
                    "rag_context": "",
                    "route_to": None,
                    "conversation_id": str(self.conv.id),
                    "user_id": self.standard_user.id,
                    "step_count": 0,
                    "max_steps": 3,
                    "retries_remaining": {},
                    "internal_monologue": [],
                    "scratch": {},
                    "token_budget_remaining": 8000
                }
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_ollama1"}})

                self.assertTrue(mock_post.called)
                post_call = mock_post.call_args
                url = post_call.args[0] if post_call.args else post_call.kwargs.get("url", "")
                payload = post_call.kwargs.get("json", {})

                # Verify request routed to Ollama endpoint
                self.assertIn("ollama-test:11434", url)
                self.assertEqual(payload.get("model"), self.ollama_model.hf_model_id)

                # Verify directive injection in Ollama system prompt (normalized to AIR_GAPPED)
                sys_content = payload.get("messages", [])[0].get("content", "")
                self.assertIn("[SYSTEM GOVERNANCE DIRECTIVE]", sys_content)
                self.assertIn("Active Lockdown Mode: AIR_GAPPED", sys_content)

                # Verify payload tools to Ollama excludes code tool
                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

            # Gateway check: execution blocked
            exec_res = execute_tool(self.code_tool, {"user": self.standard_user}, {"code": "print('attack')"})
            self.assertIn("Error: Governance violation", exec_res)

        # Test RESTRICTED Mode with Ollama
        with self.settings(
            VERBAL_LOCKDOWN_LEVEL="RESTRICTED",
            ALLOW_MODEL_CODE_EXECUTION=False,
            OLLAMA_BASE_URL="http://ollama-test:11434"
        ):
            with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                 patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                graph = compile_graph_from_blueprint(bp)
                state["user_id"] = self.standard_user.id
                graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_ollama2"}})

                post_call = mock_post.call_args
                payload = post_call.kwargs.get("json", {})
                sys_content = payload.get("messages", [])[0].get("content", "")
                self.assertIn("Active Lockdown Mode: RESTRICTED", sys_content)

                payload_tools = payload.get("tools", [])
                tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                self.assertIn("test_backend_read_tool", tool_names)
                self.assertNotIn("test_backend_code_tool", tool_names)

    def test_development_permissive_mode_all_backends(self):
        from unittest.mock import patch, MagicMock
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.compiler import compile_graph_from_blueprint

        bp = CognitiveBlueprint.objects.create(name="Dev Mode BP", description="Test dev mode across backends")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Dev Step",
            is_start_node=True,
            system_prompt="Dev instructions."
        )
        step.available_tools.add(self.read_tool, self.code_tool)

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Dev generation complete."}}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 15}
        }

        backends = [
            ("pytorch", "local-model"),
            ("vllm", self.vllm_model.hf_model_id),
            ("ollama", self.ollama_model.hf_model_id),
        ]

        for backend_name, expected_model in backends:
            self.config.hosting_backend = backend_name
            self.config.save()

            with self.settings(
                VERBAL_LOCKDOWN_LEVEL="DEVELOPMENT",
                ALLOW_MODEL_CODE_EXECUTION=True,
                ALLOW_TOOL_NETWORK_ACCESS=True
            ):
                with patch("requests.post", return_value=mock_post_resp) as mock_post, \
                     patch("llm_api.ai_service.AIService.supports_native_tools", return_value=True):
                    graph = compile_graph_from_blueprint(bp)
                    state = {
                        "working_memory": [],
                        "rag_context": "",
                        "route_to": None,
                        "conversation_id": str(self.conv.id),
                        "user_id": self.admin_user.id,
                        "step_count": 0,
                        "max_steps": 3,
                        "retries_remaining": {},
                        "internal_monologue": [],
                        "scratch": {},
                        "token_budget_remaining": 8000
                    }
                    graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_{backend_name}"}})

                    self.assertTrue(mock_post.called, f"requests.post was not called for backend {backend_name}")
                    post_call = mock_post.call_args
                    payload = post_call.kwargs.get("json", {})

                    # Verify system prompt has DEVELOPMENT directive
                    sys_content = payload.get("messages", [])[0].get("content", "")
                    self.assertIn("Active Lockdown Mode: DEVELOPMENT", sys_content)

                    # In DEVELOPMENT mode for an authorized operator, BOTH read and code tools pass through
                    payload_tools = payload.get("tools", [])
                    tool_names = [t.get("function", {}).get("name") for t in payload_tools]
                    self.assertIn("test_backend_read_tool", tool_names, f"Read tool missing for backend {backend_name}")
                    self.assertIn("test_backend_code_tool", tool_names, f"Code tool missing for backend {backend_name}")

    def test_guided_json_schema_tool_governance_all_backends(self):
        """
        Tests the Outlines / dynamic JSON-schema fallback pathway (when supports_native_tools is False)
        across all three backends to ensure restricted tools are never included in the schema.
        """
        import json
        from unittest.mock import patch, MagicMock
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.compiler import compile_graph_from_blueprint

        bp = CognitiveBlueprint.objects.create(name="Schema Gov BP", description="Test schema governance")
        step = ReasoningStep.objects.create(
            blueprint=bp,
            name="Schema Step",
            is_start_node=True,
            system_prompt="Schema instructions."
        )
        step.available_tools.add(self.read_tool, self.code_tool)

        mock_outline_response = json.dumps({"tool_calls": [{"name": "test_backend_read_tool", "args": {"query": "safe"}}]})

        for backend_name in ["pytorch", "vllm", "ollama"]:
            self.config.hosting_backend = backend_name
            self.config.save()

            with self.settings(
                VERBAL_LOCKDOWN_LEVEL="AIR_GAPPED",
                ALLOW_MODEL_CODE_EXECUTION=False
            ):
                with patch("llm_api.ai_service.AIService.generate_outline", return_value=mock_outline_response) as mock_outline, \
                     patch("llm_api.ai_service.AIService.supports_native_tools", return_value=False):
                    graph = compile_graph_from_blueprint(bp)
                    state = {
                        "working_memory": [],
                        "rag_context": "",
                        "route_to": None,
                        "conversation_id": str(self.conv.id),
                        "user_id": self.standard_user.id,
                        "step_count": 0,
                        "max_steps": 3,
                        "retries_remaining": {},
                        "internal_monologue": [],
                        "scratch": {},
                        "token_budget_remaining": 8000
                    }
                    graph.invoke(state, {"configurable": {"thread_id": f"{self.conv.id}_schema_{backend_name}"}})

                    self.assertTrue(mock_outline.called, f"generate_outline was not called for backend {backend_name}")
                    call_kwargs = mock_outline.call_args.kwargs
                    schema = call_kwargs.get("response_schema") or call_kwargs.get("schema", {})

                    # Extract allowed tool names in the dynamic schema
                    items = schema.get("properties", {}).get("tool_calls", {}).get("items", {}).get("anyOf", [])
                    allowed_names_in_schema = [item["properties"]["name"]["const"] for item in items if "properties" in item]

                    self.assertIn("test_backend_read_tool", allowed_names_in_schema)
                    self.assertNotIn("test_backend_code_tool", allowed_names_in_schema)


