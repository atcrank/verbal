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
            user_prompt="Step 2: Propose experimental factors",
            generated_response="Factor A: Dose (Low/High), Factor B: Timing (Pre/Post)."
        )
        log3 = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            user_prompt="Step 3: Confounding analysis",
            generated_response="Potential confounders identified."
        )

        url = reverse('demo_ui:branch_conversation', kwargs={'log_id': log2.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        
        # Check that a new branched conversation was created with all historical turns back to root
        branches = Conversation.objects.filter(user=self.user).exclude(id=conv.id)
        self.assertEqual(branches.count(), 1)
        branch = branches.first()
        self.assertEqual(branch.title, "Branch: Original Dialogue (Turn 2)")
        self.assertEqual(branch.logs.count(), 2)
        
        branch_logs = list(branch.logs.order_by('created_at'))
        self.assertIsNone(branch_logs[0].parent_log)
        self.assertEqual(branch_logs[1].parent_log, branch_logs[0])
        self.assertEqual(branch_logs[0].user_prompt, "Step 1: Define hypothesis")
        self.assertEqual(branch_logs[1].user_prompt, "Step 2: Propose experimental factors")
        self.assertContains(response, "Factor A: Dose")
        self.assertContains(response, "Branch from here")

    @patch('llm_api.ai_service.AIService.generate_response2', return_value=["Mock assistant reply"])
    @patch('llm_api.ai_service.AIService.clean_response', side_effect=lambda x: x)
    @patch('llm_api.ai_service.AIService.count_conversation_tokens', return_value=10)
    def test_send_message_links_parent_log_and_uses_1500_tokens(self, mock_count, mock_clean, mock_gen):
        conv = Conversation.objects.create(user=self.user, title="Interactive Chat")
        
        # Turn 1
        url = reverse('demo_ui:send_message')
        res1 = self.client.post(url, {
            'conversation_id': str(conv.id),
            'user_prompt': 'First message',
        })
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(conv.logs.count(), 1)
        log1 = conv.logs.first()
        self.assertIsNone(log1.parent_log)
        
        # Verify max_new_tokens was 1500
        mock_gen.assert_called_with(
            messages=[
                {"role": "system", "content": "You are a helpful study design assistant."},
                {"role": "user", "content": "First message"}
            ],
            max_new_tokens=1500,
            log_kwargs={"skip_log": True},
            user=self.user
        )
        
        # Turn 2
        res2 = self.client.post(url, {
            'conversation_id': str(conv.id),
            'user_prompt': 'Second message',
        })
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(conv.logs.count(), 2)
        log2 = conv.logs.order_by('-created_at').first()
        self.assertEqual(log2.parent_log, log1)

    @patch('demo_ui.views.task_generate_response')
    @patch('llm_api.ai_service.AIService.count_conversation_tokens', return_value=15)
    def test_send_message_dispatches_task_and_renders_streaming_markup(self, mock_count, mock_task):
        mock_task.enqueue.return_value = None
        conv = Conversation.objects.create(user=self.user, title="Async Chat")
        url = reverse('demo_ui:send_message')
        resp = self.client.post(url, {
            'conversation_id': str(conv.id),
            'user_prompt': 'Explain factorial design',
            'max_new_tokens': 1200
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_task.enqueue.call_count, 1)
        kwargs = mock_task.enqueue.call_args.kwargs
        self.assertEqual(kwargs['max_new_tokens'], 1200)
        self.assertEqual(kwargs['conversation_id'], str(conv.id))
        self.assertEqual(kwargs['user_id'], self.user.id)
        run_id = kwargs['run_id']

        content = resp.content.decode('utf-8')
        self.assertIn(f'id="gen-stream-{run_id}"', content)
        self.assertIn('data-signals="{isStreaming: true}"', content)
        self.assertIn(f'/demo/stream_generation/?run_id={run_id}', content)
        self.assertIn('Generating response...', content)

    def test_stream_generation_fast_path(self):
        """Verifies Datastar SSE response when generation already completed before stream connected."""
        conv = Conversation.objects.create(user=self.user, title="Fast path chat")
        log = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            user_prompt="Hello",
            generated_response="Finished generation text."
        )
        url = reverse('demo_ui:stream_generation') + f"?run_id=test-run-123&log_id={log.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")
        body = b"".join(resp.streaming_content).decode('utf-8')
        self.assertIn("datastar-merge-fragments", body)
        self.assertIn("Finished generation text.", body)
        self.assertIn('"isStreaming": false', body)

    def test_stream_generation_sse_completed_event(self):
        def mock_events(channel):
            yield {"event": "completed", "data": {"final_response": "Asynchronously streamed text!"}}

        with patch('demo_ui.views.subscribe_pg_events_sync', side_effect=mock_events):
            conv = Conversation.objects.create(user=self.user, title="Event stream chat")
            log = PromptResponseLog.objects.create(
                user=self.user,
                conversation=conv,
                user_prompt="Hello",
                generated_response='<div id="gen-stream-run-abc">placeholder</div>'
            )
            url = reverse('demo_ui:stream_generation') + f"?run_id=run-abc&log_id={log.id}"
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")
            body = b"".join(resp.streaming_content).decode('utf-8')
            self.assertIn("datastar-merge-fragments", body)
            self.assertIn("Asynchronously streamed text!", body)
            self.assertIn('"status": "completed"', body)

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

    def test_broadcast_stream_endpoint(self):
        """Verifies SSE broadcast stream headers and non-blocking delivery."""
        res = self.client.get(reverse('broadcast_stream') + '?timeout=0')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "text/event-stream")
        content = b"".join(res.streaming_content).decode("utf-8")
        self.assertIn("retry: 3000", content)
        self.assertIn("data:", content)


    def test_docs_serve_landing_page(self):
        res = self.client.get('/docs/')
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("Content-Type", ""))

    def test_root_landing_page(self):
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'landing.html')
        content = res.content.decode('utf-8')
        self.assertIn("Reason", content)
        self.assertIn("/demo/", content)
        self.assertIn("/wiki/", content)
        self.assertIn("/work/", content)
        self.assertIn("/docs/", content)
        self.assertIn("/api/docs", content)
        self.assertIn("/admin/", content)

    def test_wiki_index_no_git_badge(self):
        res = self.client.get('/wiki/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertNotIn("Git-Backed", content)
        self.assertNotIn("linear-gradient(135deg, #fff, #94a3b8)", content)
        self.assertIn("Grips Knowledge Base", content)


class DeploymentHardeningPhase3Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='phase3_tester', password='password123')
        self.client = Client()
        self.client.login(username='phase3_tester', password='password123')

    def test_set_active_provider_toggle(self):
        """Verifies switching active inference provider via set_active_provider view."""
        from llm_api.models import ExternalAIModel, UserActiveModel, UserAPIKey
        
        ext_model = ExternalAIModel.objects.create(
            name="Claude 3.5 Sonnet",
            provider="anthropic",
            api_model_name="claude-3-5-sonnet-20241022"
        )
        UserAPIKey.objects.create(
            user=self.user,
            provider="anthropic",
            api_key="sk-test-anthropic-key"
        )

        url = reverse('demo_ui:set_active_provider')

        # 1. Switch to external model
        res = self.client.post(url, {
            'use_external': 'true',
            'model_id': str(ext_model.id)
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Claude 3.5 Sonnet")
        pref = UserActiveModel.objects.get(user=self.user)
        self.assertTrue(pref.use_external)
        self.assertEqual(pref.active_external, ext_model)

        # 2. Switch back to local GPU
        res = self.client.post(url, {
            'use_external': 'false'
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Local GPU / Container")
        pref.refresh_from_db()
        self.assertFalse(pref.use_external)

    def test_katex_static_assets_available(self):
        """Verifies that offline KaTeX assets are available in static files."""
        from django.contrib.staticfiles import finders
        self.assertIsNotNone(finders.find('vendor/katex/katex.min.css'))
        self.assertIsNotNone(finders.find('vendor/katex/katex.min.js'))
        self.assertIsNotNone(finders.find('vendor/katex/contrib/auto-render.min.js'))

    def test_visualizer_rendering_and_grouping(self):
        """Verifies blueprint visualizer generation and log grouping with thinking trace."""
        from metacognition.models import CognitiveBlueprint, ReasoningStep
        from metacognition.visualizer import render_blueprint_visualizer_html
        from demo_ui.views import group_conversation_logs_for_display

        bp = CognitiveBlueprint.objects.create(name="Scientific Method BP", description="Testing BP")
        s1 = ReasoningStep.objects.create(blueprint=bp, name="Formulate Hypothesis", is_start_node=True)
        s2 = ReasoningStep.objects.create(blueprint=bp, name="Design Experiment")
        s1.on_success_step = s2
        s1.save()

        # Render visualizer directly
        viz_html = render_blueprint_visualizer_html(bp, completed_steps=["Formulate Hypothesis"], active_step="Design Experiment")
        self.assertIn("blueprint-visualizer", viz_html)
        self.assertIn("Formulate Hypothesis", viz_html)
        self.assertIn("Design Experiment", viz_html)
        self.assertIn("viz-success", viz_html)
        self.assertIn("viz-active", viz_html)
        self.assertIn("➔", viz_html)

        # Test log grouping
        conv = Conversation.objects.create(user=self.user, title="BP Test Conv")
        log1 = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            blueprint=bp,
            reasoning_step=s1,
            user_prompt="Explain photosynthesis",
            generated_response="Hypothesis: Light converts CO2 and H2O to glucose.",
            input_tokens=10,
            output_tokens=15,
            step_status="SUCCESS"
        )
        log2 = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            parent_log=log1,
            blueprint=bp,
            reasoning_step=s2,
            user_prompt="Next step",
            generated_response="Final Answer: Photosynthesis converts light energy into chemical energy.",
            input_tokens=20,
            output_tokens=25,
            step_status="SUCCESS"
        )

        grouped = group_conversation_logs_for_display([log1, log2])
        self.assertEqual(len(grouped), 1)
        leaf = grouped[0]
        self.assertEqual(leaf.user_prompt, "Explain photosynthesis")
        self.assertEqual(leaf.input_tokens, 30)
        self.assertEqual(leaf.output_tokens, 40)
        self.assertEqual(len(leaf.thinking_steps), 1)
        self.assertEqual(leaf.thinking_steps[0]["step_title"], "Step 1: Formulate Hypothesis")
        self.assertIn("blueprint-visualizer", leaf.visualizer_html)

    def test_blueprint_sets_discrimination_in_chat(self):
        """Verifies chat UI only includes REASONING blueprints, filtering out Grips and System routines."""
        from metacognition.models import CognitiveBlueprint

        CognitiveBlueprint.objects.all().delete()
        bp_reasoning = CognitiveBlueprint.objects.create(
            name="Conversational Strategist",
            description="Thinking pattern",
            category="REASONING"
        )
        bp_grips = CognitiveBlueprint.objects.create(
            name="LintGripsEdge",
            description="Grips graph linter",
            category="GRIPS"
        )
        bp_system = CognitiveBlueprint.objects.create(
            name="NM_Housekeeping",
            description="System housekeeping",
            category="SYSTEM"
        )
        bp_malformed = CognitiveBlueprint.objects.create(
            name=":CognitiveBlueprintProposal",
            description="Malformed proposal",
            category="SYSTEM"
        )

        response = self.client.get(reverse('demo_ui:index'))
        self.assertEqual(response.status_code, 200)
        blueprints = response.context['blueprints']
        bp_names = [b.name for b in blueprints]

        self.assertIn("Conversational Strategist", bp_names)
        self.assertNotIn("LintGripsEdge", bp_names)
        self.assertNotIn("NM_Housekeeping", bp_names)
        self.assertNotIn(":CognitiveBlueprintProposal", bp_names)

    def test_grips_blueprints_tab_endpoint(self):
        """Verifies the dedicated Grips Knowledge Blueprints pathway renders cleanly."""
        from metacognition.models import CognitiveBlueprint, ReasoningStep

        CognitiveBlueprint.objects.all().delete()
        bp = CognitiveBlueprint.objects.create(
            name="LintGripsEdge",
            description="Rewrites edge justifications without placeholders.",
            category="GRIPS"
        )
        s1 = ReasoningStep.objects.create(blueprint=bp, name="Rewrite Justification", is_start_node=True)
        s2 = ReasoningStep.objects.create(blueprint=bp, name="Verify Justification Quality")
        s1.on_success_step = s2
        s1.save()
        s2.on_failure_step = s1 # Loop back
        s2.save()

        response = self.client.get(reverse('demo_ui:grips_blueprints_tab'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LintGripsEdge")
        self.assertContains(response, "Level-1 Self-Check")
        self.assertContains(response, "Rewrite Justification")
        self.assertContains(response, "Verify Justification Quality")
        self.assertContains(response, "Loop-back on failure")

    def test_prepare_log_for_display_preserves_streaming_markup(self):
        """Verifies _prepare_log_for_display does not mangle pre-rendered Datastar HTML markup."""
        from demo_ui.views import _prepare_log_for_display

        markup = (
            '<div id="blueprint-exec-12345" data-signals="{isStreaming: true}" '
            'data-on-load="@get(\'/api/meta/stream_blueprint/?run_id=12345&log_id=99\')">'
            '<div id="blueprint-visualizer" class="blueprint-visualizer"></div>'
            '<div id="blueprint-status" class="agent-step active">Executing...</div>'
            '<details id="blueprint-thinking-trace" class="blueprint-thinking-trace" open>'
            '<summary id="thinking-trace-summary">Thinking</summary>'
            '<div id="monologue-stream" class="thinking-trace-content"></div>'
            '</details>'
            '<div id="blueprint-final-response"></div>'
            '</div>'
        )
        conv = Conversation.objects.create(user=self.user)
        log = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            user_prompt="Run blueprint",
            generated_response=markup
        )
        prepared = _prepare_log_for_display(log)
        self.assertEqual(str(prepared.html_response), markup)
        self.assertNotIn("<p>", str(prepared.html_response))
        self.assertIn('id="monologue-stream"', str(prepared.html_response))

    def test_stream_generation_fast_path_patches_tokens(self):
        """Verifies stream_generation yields Datastar patches updating output and input token counts."""
        from demo_ui.views import stream_generation
        from django.test import RequestFactory

        conv = Conversation.objects.create(user=self.user)
        log = PromptResponseLog.objects.create(
            user=self.user,
            conversation=conv,
            user_prompt="Hello",
            generated_response="This is the completed AI answer.",
            input_tokens=14,
            output_tokens=32
        )

        factory = RequestFactory()
        req = factory.get(f"/demo/stream_generation/?run_id=test-run&log_id={log.id}")
        req.user = self.user

        response = stream_generation(req)
        body = b"".join(list(response.streaming_content)).decode("utf-8")
        self.assertIn(f"token-out-{log.id}", body)
        self.assertIn("32", body)
        self.assertIn(f"token-in-{log.id}", body)
        self.assertIn("14", body)

    def test_seed_grips_blueprints_level1_self_checks_and_cleanup(self):
        """Verifies seed blueprints contain level-1 loop-backs and corruptions are cleaned up."""
        from metacognition.seed import seed_all, cleanup_legacy_corruptions
        from metacognition.models import CognitiveBlueprint, bypass_canonical_lock

        # Create corrupted legacy records
        with bypass_canonical_lock():
            CognitiveBlueprint.objects.create(name=":CognitiveBlueprintProposal", description="Corrupt")
            CognitiveBlueprint.objects.create(name="]CognitiveBlueprintProposal", description="Corrupt")
            CognitiveBlueprint.objects.create(name="CognitiveBlueprintProposal", description="Duplicate")

        # Run seed
        seed_all()

        # Check legacy corruptions are removed
        self.assertFalse(CognitiveBlueprint.objects.filter(name__startswith=":").exists())
        self.assertFalse(CognitiveBlueprint.objects.filter(name__startswith="]").exists())
        self.assertFalse(CognitiveBlueprint.objects.filter(name="CognitiveBlueprintProposal").exists())

        # Check canonical Grips blueprints have category="GRIPS" and loopbacks
        for name in ["LintGripsEdge", "DigestDocumentChunk", "EvaluateConceptNeighbors", "EvaluateCrossDomain", "LintGripsNode"]:
            bp = CognitiveBlueprint.objects.filter(name=name).first()
            self.assertIsNotNone(bp, f"Blueprint {name} should exist")
            self.assertEqual(bp.category, "GRIPS", f"{name} should have category GRIPS")
            steps = list(bp.steps.all())
            self.assertGreaterEqual(len(steps), 2, f"{name} should have at least 2 steps (action + self-check)")
            step1 = steps[0]
            step2 = steps[1]
            # Verify loop-back connection exists between step 1 and step 2
            has_loopback = (step2.on_failure_step == step1) or (step2.on_success_step == step1)
            self.assertTrue(has_loopback, f"{name} should have a level-1 loop-back connection between step 2 and step 1")

