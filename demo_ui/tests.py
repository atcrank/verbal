import os
import json
import shutil
import tempfile
from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from background_resources.models import Document, ReadingStrategy, RAGChunk, StrategyChunkUsage
from django.contrib.contenttypes.models import ContentType
from llm_api.models import Conversation, PromptResponseLog
from verbal_config.broadcast_service import BroadcastService


class DemoUIViewsTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        Document.objects.all().delete()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client = Client()
        self.client.login(username='testuser', password='password123')

    def test_list_documents_empty(self):
        url = reverse('demo_ui:list_documents')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No documents uploaded yet.")

    @patch('demo_ui.views.task_process_documents')
    def test_upload_document_success(self, mock_task):
        mock_file = SimpleUploadedFile("test_doc.txt", b"Mock RAG content.", content_type="text/plain")
        url = reverse('demo_ui:upload_document')
        
        response = self.client.post(url, {
            'title': 'Test Ingested Document',
            'author': 'Test Author',
            'file': mock_file
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingestion started.")
        
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.first()
        self.assertEqual(doc.title, 'Test Ingested Document')
        self.assertEqual(doc.author, 'Test Author')
        mock_task.enqueue.assert_called_once_with([doc.id])

    def test_list_documents_with_content(self):
        mock_file = SimpleUploadedFile("test_doc.txt", b"Mock RAG content.", content_type="text/plain")
        doc = Document.objects.create(
            title='Pre-existing Document',
            author='Author X',
            file=mock_file,
            currently_indexed=True
        )
        
        strategy = ReadingStrategy.objects.create(document=doc, strategy_description="Default Chunking")
        chunk = RAGChunk.objects.create(chunk_id="test-chunk-1", text_content="Mock RAG content.")
        
        StrategyChunkUsage.objects.create(
            chunk=chunk,
            content_type=ContentType.objects.get_for_model(ReadingStrategy),
            object_id=strategy.id,
            role=StrategyChunkUsage.Role.CREATED
        )
        
        url = reverse('demo_ui:list_documents')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pre-existing Document")
        self.assertContains(response, "Indexed")

    @patch('demo_ui.views.task_process_documents')
    def test_trigger_document_ingestion(self, mock_task):
        mock_file = SimpleUploadedFile("pending_doc.txt", b"Some unindexed text.", content_type="text/plain")
        doc = Document.objects.create(
            title='Pending Doc',
            file=mock_file,
            currently_indexed=False
        )
        url = reverse('demo_ui:trigger_document_ingestion', kwargs={'document_id': doc.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        mock_task.enqueue.assert_called_once_with([doc.id])

    def test_branch_conversation(self):
        conv = Conversation.objects.create(user=self.user, title="Original Dialogue")
        log1 = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            user_prompt="Step 1: Define hypothesis",
            generated_response="Hypothesis defined as X -> Y."
        )
        log2 = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            parent_log=log1,
            user_prompt="Step 2: Propose experimental factors",
            generated_response="Factor A: Dose (Low/High), Factor B: Timing (Pre/Post)."
        )

        url = reverse('demo_ui:branch_conversation', kwargs={'log_id': log2.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        
        # Check that a new branched conversation was created
        branches = Conversation.objects.filter(user=self.user).exclude(id=conv.id)
        self.assertEqual(branches.count(), 1)
        branch = branches.first()
        self.assertTrue(branch.title.startswith("Branch:"))
        self.assertEqual(branch.logs.count(), 2)
        self.assertContains(response, "Factor A: Dose")
        self.assertContains(response, "Branch from here")

    def test_preview_context_item(self):
        mock_file = SimpleUploadedFile("guide.txt", b"Guide content.", content_type="text/plain")
        doc = Document.objects.create(title="Study Guidelines", author="Dr. Smith", file=mock_file)

        url = reverse('demo_ui:preview_context_item') + f"?model=Document&id={doc.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Study Guidelines")
        self.assertContains(response, "Estimated Prompt Tokens:")

    @patch('llm_api.ai_service.AIService.count_conversation_tokens', return_value=12)
    def test_calculate_context_tokens(self, mock_count):
        url = reverse('demo_ui:calculate_context_tokens')
        payload = json.dumps([
            {"model": "Document", "id": "1", "content": "This is a five token test."},
            {"model": "ConceptNode", "id": "2", "content": "Another short sentence context."}
        ])
        response = self.client.post(url, {'included_context': payload})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('total_tokens', data)
        self.assertEqual(data['total_tokens'], 24)


class WorkspaceDownloadViewsTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='testuser2', password='password123')
        self.client = Client()
        self.client.login(username='testuser2', password='password123')
        self.conversation = Conversation.objects.create(
            user=self.user,
            title="Test Conversation"
        )
        self.workspace_dir = self.conversation.get_workspace_dir()
        os.makedirs(self.workspace_dir, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir)

    def test_get_conversation_oob_files(self):
        file_path = os.path.join(self.workspace_dir, "output_report.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("Generated survey metrics report.")

        url = reverse('demo_ui:get_conversation', kwargs={'conversation_id': self.conversation.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "output_report.txt")
        self.assertContains(response, "hx-swap-oob=\"true\"")
        self.assertContains(response, "Download")

    def test_download_file_success(self):
        filename = "output_report.txt"
        file_path = os.path.join(self.workspace_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("Some file content.")

        url = reverse('demo_ui:download_file', kwargs={
            'conversation_id': self.conversation.id,
            'filename': filename
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Disposition'], 'attachment; filename="output_report.txt"')
        self.assertEqual(b"".join(response.streaming_content), b"Some file content.")

    def test_download_file_traversal_blocked(self):
        url = reverse('demo_ui:download_file', kwargs={
            'conversation_id': self.conversation.id,
            'filename': '../conftest.py'
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)

    def test_download_file_not_found(self):
        url = reverse('demo_ui:download_file', kwargs={
            'conversation_id': self.conversation.id,
            'filename': 'non_existent.txt'
        })
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class BroadcastEndpointsTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff_user = User.objects.create_user(username='staffuser', password='password123', is_staff=True)
        self.regular_user = User.objects.create_user(username='studentuser', password='password123', is_staff=False)
        self.client = Client()
        BroadcastService.clear_broadcast()

    def tearDown(self):
        BroadcastService.clear_broadcast()

    def test_broadcast_service_lifecycle(self):
        self.assertIsNone(BroadcastService.get_current_broadcast())
        
        BroadcastService.set_broadcast("OK group, 5 minutes.", level="warning", duration_seconds=60)
        b = BroadcastService.get_current_broadcast()
        self.assertIsNotNone(b)
        self.assertEqual(b['message'], "OK group, 5 minutes.")
        self.assertEqual(b['level'], "warning")

        BroadcastService.clear_broadcast()
        self.assertIsNone(BroadcastService.get_current_broadcast())

    def test_broadcast_api_send_and_current(self):
        # Regular user cannot send broadcast
        self.client.login(username='studentuser', password='password123')
        res = self.client.post(reverse('broadcast_send'), {'message': 'Unauthorized msg'})
        self.assertEqual(res.status_code, 403)

        # Staff user can send broadcast
        self.client.login(username='staffuser', password='password123')
        res = self.client.post(
            reverse('broadcast_send'),
            data=json.dumps({'message': "OK times up, redirecting to exercise 2", 'level': 'redirect', 'redirect_url': '/demo/'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get('success'))

        # Public current endpoint returns active broadcast
        res = self.client.get(reverse('broadcast_current'))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['message'], "OK times up, redirecting to exercise 2")
        self.assertEqual(data['level'], "redirect")

    def test_docs_serve_landing_page(self):
        res = self.client.get('/docs/')
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("Content-Type", ""))
