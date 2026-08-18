import os
import shutil
import json
from pathlib import Path
from django.test import TestCase, Client, override_settings, tag
from django.contrib.auth.models import User
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from llm_api.apps import service_registry
from llm_api.models import PromptResponseLog
from background_resources.models import Document, ReadingStrategy

# Define test paths (Isolated from production)
TEST_BASE_DIR = Path(settings.BASE_DIR) / "test_data_llm_api"
TEST_VECTOR_STORE = TEST_BASE_DIR / "vector_store"
TEST_CHUNK_STORE = TEST_BASE_DIR / "chunk_store"
TEST_FILES_DIR = TEST_BASE_DIR / "files"

class LlmApiIntegrationTests(TestCase):
    """
    Full Integration Tests for the LLM API.
    Uses REAL services (AI, RAG, NLP) with no mocks.
    """

    @classmethod
    def setUpClass(cls):
        # 1. Override Settings to use test directories
        cls.settings_override = override_settings(
            VECTOR_STORE=TEST_VECTOR_STORE,
            CHUNK_STORE=TEST_CHUNK_STORE,
            FILES=TEST_FILES_DIR,
            MEDIA_ROOT=TEST_FILES_DIR,
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
        )
        cls.settings_override.enable()
        super().setUpClass()

        # 2. Create Test Directories
        os.makedirs(TEST_VECTOR_STORE, exist_ok=True)
        os.makedirs(TEST_CHUNK_STORE, exist_ok=True)
        os.makedirs(TEST_FILES_DIR, exist_ok=True)

        # 3. Initialize Real Services
        print("\n>>> 🚀 INITIALIZING REAL SERVICES FOR API TEST <<<")
        cls.ai_service = service_registry['ai_service']
        cls.rag_service = service_registry['rag_service']
        
        # Force load models (Lazy loading would happen on first request, but we do it here for clarity)
        if cls.ai_service.model is None:
            cls.ai_service.load_models()
            
        # 4. Ingest Test Data for RAG
        # We create a document so the RAG service has something to find.
        content = "The capital of France is Paris. It is known for the Eiffel Tower and the Louvre Museum."
        file_path = TEST_FILES_DIR / "france.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        with open(file_path, 'rb') as f:
            django_file = SimpleUploadedFile(
                name="france.txt",
                content=f.read(),
                content_type='text/plain'
            )
        
        doc = Document.objects.create(
            title="France Info", 
            file=django_file,
            chunk_size=500,
            chunk_overlap=50
        )
        strategy = ReadingStrategy.objects.create(document=doc, strategy_description="Default")
        strategy.read_document(cls.rag_service)

    @classmethod
    def tearDownClass(cls):
        # Cleanup test data
        if os.path.exists(TEST_BASE_DIR):
            shutil.rmtree(TEST_BASE_DIR)
            # Sever SQLAlchemy connection pools
        if hasattr(cls, 'rag_service'):
            cls.rag_service.disconnect()

        # CRITICAL: Free PyTorch VRAM to prevent OOM across test suites
        if hasattr(cls, 'ai_service'):
            cls.ai_service.model = None
            cls.ai_service.tokenizer = None
            if hasattr(cls.ai_service, '_generator_cache'):
                cls.ai_service._generator_cache.clear()
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        super().tearDownClass()
        cls.settings_override.disable()

    def setUp(self):
        from llm_api.models import SystemConfiguration, LocalAIModel
        from django.core.management import call_command
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.login(username='testuser', password='password123')
        
        config = SystemConfiguration.get_solo()
        config.hosting_backend = 'pytorch'
        model, _ = LocalAIModel.objects.get_or_create(
            hf_model_id="google/gemma-4-E2B-it", 
            defaults={"name": "Gemma 4 E2B"}
        )
        config.active_local_model = model
        config.save()
        
        # Ensure blueprints are available for the /generate_response/ endpoint which uses Deep_Reader
        from metacognition.seed import seed_all
        seed_all()

    @tag('e2e')
    def test_generate_response_with_real_rag(self):
        """
        Test the /generate_response/ endpoint with the full stack.
        Verifies that RAG retrieves the ingested document and the LLM generates a response.
        """
        payload = {
            "system_prompt": "You are a helpful assistant. Use the provided context to answer the question.",
            "user_prompt": "What are the specific details mentioned in the internal company France document regarding its capital?",
            "max_new_tokens": 50
        }

        response = self.client.post("/api/llm/generate_response/", data=payload, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # 1. Verify Response Structure
        self.assertIn("cleaned_response", data)
        self.assertIn("conversation_id", data)
        
        ai_response = data["cleaned_response"]
        print(f"\n🤖 Real AI Response: {ai_response}")
        self.assertTrue(len(ai_response) > 0)

        # 2. Verify RAG Usage via Logs
        # We check the database log to ensure the RAG service actually injected the context
        log = PromptResponseLog.objects.last()
        self.assertEqual(log.user, self.user)
        
        # The log should contain the text from our ingested file
        print(f"📄 RAG Context Used: {log.rag_selections}...")
        rag_str = str(log.rag_selections)
        self.assertIn("France", rag_str)
        self.assertIn("Paris", rag_str)


class ConversationBranchAndReplayTests(TestCase):
    """
    Task 8 Tests:
    - Eliminates system prompt multiplication in Conversation.as_messages()
    - Traces true DAG branch paths via leaf_log_id
    - Universal state_tree_snapshot capturing on PromptResponseLog
    """

    def setUp(self):
        from llm_api.models import Conversation
        self.user = User.objects.create_user(username='branch_user', password='password123')
        self.conv = Conversation.objects.create(user=self.user, title="Branching Experiment")

    def test_as_messages_no_system_prompt_multiplication(self):
        """Verifies that multiple step turns never stack intermediate system prompts."""
        log_0 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            system_prompt="Root System Persona: You are an experiment designer.",
            user_prompt="Let's start experiment 1.",
            generated_response="Starting experiment 1."
        )
        log_1 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_0,
            system_prompt="Step 1 Internal Prompt: Analyze chunk outliers.",
            user_prompt="Here is chunk data.",
            generated_response="Outlier detected in chunk 4."
        )
        log_2 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_1,
            system_prompt="Step 2 Internal Prompt: Synthesize final conclusion.",
            user_prompt="Synthesize results.",
            generated_response="Experiment complete."
        )

        messages = self.conv.as_messages(leaf_log_id=log_2.id)

        # 1. Exactly ONE system prompt at index 0
        system_msgs = [m for m in messages if m.get("role") == "system"]
        self.assertEqual(len(system_msgs), 1, "Must have exactly 1 system message at index 0")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "Root System Persona: You are an experiment designer.")

        # 2. Intermediate step system prompts must NOT appear in the chat history
        for m in messages[1:]:
            self.assertNotEqual(m.get("role"), "system")
            self.assertNotIn("Step 1 Internal Prompt", m.get("content", ""))
            self.assertNotIn("Step 2 Internal Prompt", m.get("content", ""))

        # 3. Turns are in correct chronological sequence
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "Let's start experiment 1.")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[3]["role"], "user")
        self.assertEqual(messages[4]["role"], "assistant")
        self.assertEqual(messages[5]["role"], "user")
        self.assertEqual(messages[6]["role"], "assistant")
        self.assertEqual(len(messages), 7)

    def test_as_messages_dag_branching_path(self):
        """Verifies that as_messages(leaf_log_id) traces only the selected branch path back to root."""
        # Root turn
        log_root = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            system_prompt="Root System",
            user_prompt="Root Prompt",
            generated_response="Root Response"
        )

        # Branch A: Root -> A1 -> A2
        log_A1 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_root,
            system_prompt="Branch A1 Prompt",
            user_prompt="Branch A1 User",
            generated_response="Branch A1 Assistant"
        )
        log_A2 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_A1,
            system_prompt="Branch A2 Prompt",
            user_prompt="Branch A2 User",
            generated_response="Branch A2 Assistant"
        )

        # Branch B: Root -> B1 -> B2
        log_B1 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_root,
            system_prompt="Branch B1 Prompt",
            user_prompt="Branch B1 User",
            generated_response="Branch B1 Assistant"
        )
        log_B2 = PromptResponseLog.objects.create(
            conversation=self.conv,
            user=self.user,
            parent_log=log_B1,
            system_prompt="Branch B2 Prompt",
            user_prompt="Branch B2 User",
            generated_response="Branch B2 Assistant"
        )

        # Trace Branch A
        msgs_A = self.conv.as_messages(leaf_log_id=log_A2.id)
        user_contents_A = [m["content"] for m in msgs_A if m.get("role") == "user"]
        self.assertEqual(user_contents_A, ["Root Prompt", "Branch A1 User", "Branch A2 User"])
        self.assertNotIn("Branch B1 User", user_contents_A)
        self.assertNotIn("Branch B2 User", user_contents_A)

        # Trace Branch B
        msgs_B = self.conv.as_messages(leaf_log_id=log_B2.id)
        user_contents_B = [m["content"] for m in msgs_B if m.get("role") == "user"]
        self.assertEqual(user_contents_B, ["Root Prompt", "Branch B1 User", "Branch B2 User"])
        self.assertNotIn("Branch A1 User", user_contents_B)
        self.assertNotIn("Branch A2 User", user_contents_B)

    def test_prompt_response_log_state_tree_snapshot(self):
        """Verifies that logging automatically captures an immutable copy of Conversation.state_tree."""
        from llm_api.ai_service import AIService

        initial_tree = {
            "macro_objective": "Test Snapshotting",
            "active_task": "task_1",
            "tasks": {
                "task_1": {"title": "Parse equations", "status": "IN_PROGRESS"}
            }
        }
        self.conv.state_tree = initial_tree
        self.conv.save()

        # Simulate generation logging
        from llm_api.ai_service import AIService
        service = AIService()
        log_kwargs = {
            "user_id": self.user.id,
            "conversation_id": str(self.conv.id),
            "log_ids": []
        }
        service._log_generation(
            messages=[
                {"role": "system", "content": "Step prompt"},
                {"role": "user", "content": "User input"}
            ],
            generated_texts=["Assistant output"],
            log_kwargs=log_kwargs
        )

        self.assertTrue(len(log_kwargs["log_ids"]) > 0)
        log = PromptResponseLog.objects.get(id=log_kwargs["log_ids"][0])
        self.assertEqual(log.state_tree_snapshot, initial_tree)

        # Mutate Conversation.state_tree afterwards; snapshot must remain immutable
        self.conv.state_tree["active_task"] = "task_2"
        self.conv.save()

        log.refresh_from_db()
        self.assertEqual(log.state_tree_snapshot["active_task"], "task_1", "Snapshot must be immutable")