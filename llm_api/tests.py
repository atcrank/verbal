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
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client.login(username='testuser', password='password123')
        
        config = SystemConfiguration.get_solo()
        config.hosting_backend = 'pytorch'
        model, _ = LocalAIModel.objects.get_or_create(
            hf_model_id="Qwen/Qwen2.5-3B-Instruct", 
            defaults={"name": "Qwen/Qwen2.5-3B-Instruct"}
        )
        config.active_local_model = model
        config.save()

    @tag('e2e')
    def test_generate_response_with_real_rag(self):
        """
        Test the /generate_response/ endpoint with the full stack.
        Verifies that RAG retrieves the ingested document and the LLM generates a response.
        """
        payload = {
            "system_prompt": "You are a helpful assistant.",
            "user_prompt": "What is the capital of France?",
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
        print(f"📄 RAG Context Used: {log.rag_selections[:100]}...")
        self.assertIn("France", log.rag_selections)
        self.assertIn("Paris", log.rag_selections)