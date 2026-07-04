import os
import tempfile
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from unittest.mock import patch
from background_resources.models import Document

class DemoUIViewsTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        # Clean existing documents
        Document.objects.all().delete()
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client = Client()
        self.client.login(username='testuser', password='password123')

    def test_list_documents_empty(self):
        url = reverse('demo_ui:list_documents')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No documents ingested yet.")

    @patch('background_resources.tasks.task_process_documents.delay')
    def test_upload_document_success(self, mock_delay):
        # Create a mock file
        mock_file = SimpleUploadedFile("test_doc.txt", b"Mock RAG content.", content_type="text/plain")
        url = reverse('demo_ui:upload_document')
        
        # Post the upload
        response = self.client.post(url, {
            'title': 'Test Ingested Document',
            'author': 'Test Author',
            'file': mock_file
        })
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "✓ Ingestion started.")
        
        # Check Document created
        self.assertEqual(Document.objects.count(), 1)
        doc = Document.objects.first()
        self.assertEqual(doc.title, 'Test Ingested Document')
        self.assertEqual(doc.author, 'Test Author')
        
        # Check Celery task was triggered with the correct document ID
        mock_delay.assert_called_once_with([doc.id])

    def test_list_documents_with_content(self):
        # Pre-create a document
        mock_file = SimpleUploadedFile("test_doc.txt", b"Mock RAG content.", content_type="text/plain")
        doc = Document.objects.create(
            title='Pre-existing Document',
            author='Author X',
            file=mock_file,
            currently_indexed=True
        )
        
        # Create a ReadingStrategy and a StrategyChunkUsage to mock an indexed status
        from background_resources.models import ReadingStrategy, RAGChunk, StrategyChunkUsage
        from django.contrib.contenttypes.models import ContentType
        
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
        self.assertContains(response, "🟢 Indexed")
        self.assertContains(response, os.path.basename(doc.file.name))


import shutil
from llm_api.models import Conversation

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
        # Create a mock file in the workspace
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
        # Create a mock file in the workspace
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
        # Requesting a traversal file outside workspace
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
