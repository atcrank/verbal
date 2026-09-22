from unittest.mock import MagicMock
from django.test import TestCase

from langchain_core.documents import Document as LangchainDocument
from background_resources.retrieval import RetrievalResult, unified_retrieve

class TestRetrievalLogic(TestCase):

    def test_unified_retrieve_deduplication(self):
        # Mock RAG Service
        mock_rag_service = MagicMock()
        mock_rag_service.get_context.return_value = [
            LangchainDocument(page_content="rag1", metadata={"chunk_id": "chunk_A", "id": "chunk_A"}),
            LangchainDocument(page_content="rag2", metadata={"chunk_id": "chunk_B", "id": "chunk_B"})
        ]
        # PGVector search_with_score typically returns (Document, distance) where lower is better
        mock_rag_service.db.similarity_search_with_score.return_value = [
            (LangchainDocument(page_content="rag1", metadata={"chunk_id": "chunk_A", "id": "chunk_A"}), 1.0),
            (LangchainDocument(page_content="rag2", metadata={"chunk_id": "chunk_B", "id": "chunk_B"}), 1.2)
        ]

        # Mock Grips Service
        mock_grips_service = MagicMock()
        mock_grips_service.get_grips_context.return_value = [
            # Pretend this Grip has concept_id 1
            LangchainDocument(page_content="grip1", metadata={"concept_id": 1, "title": "Concept 1"}),
        ]
        mock_grips_service.db.similarity_search_with_score.return_value = [
            (LangchainDocument(page_content="grip1", metadata={"concept_id": 1}), 1.1)
        ]

        from unittest.mock import patch
        
        class MockChunk:
            chunk_id = "chunk_A"
            
        class MockNode:
            source_chunk = MockChunk()
            
        with patch('grips.models.ConceptNode.objects.select_related') as mock_sr:
            mock_sr.return_value.only.return_value.get.return_value = MockNode()

            results = unified_retrieve(
                query="test",
                rag_service=mock_rag_service,
                grips_service=mock_grips_service,
                deduplicate=True,
                lineage_boost_factor=0.8
            )

        # We expect:
        # Grip1 has source_chunk_id="chunk_A". RAG returns chunk_A and chunk_B.
        # Since Grip1 is derived from chunk_A, chunk_A should be marked as duplicate and suppressed.
        # Grip1 should receive lineage boost (1.1 * 0.8 = 0.88).
        # RAG chunk_B should have distance 1.2.
        
        self.assertEqual(len(results), 2, "Should return 2 results (1 grip, 1 non-duplicate rag)")
        
        # They are sorted by distance: 0.88 (grip1), 1.2 (rag2)
        self.assertEqual(results[0].source, "grips")
        self.assertEqual(results[0].boosted_distance, 1.1 * 0.8)
        
        self.assertEqual(results[1].source, "rag")
        self.assertEqual(results[1].metadata.get("chunk_id"), "chunk_B")

    def test_unified_retrieve_no_deduplication(self):
        # Mock RAG Service
        mock_rag_service = MagicMock()
        mock_rag_service.get_context.return_value = [
            LangchainDocument(page_content="rag1", metadata={"chunk_id": "chunk_A", "id": "chunk_A"}),
        ]
        mock_rag_service.db.similarity_search_with_score.return_value = [
            (LangchainDocument(page_content="rag1", metadata={"chunk_id": "chunk_A", "id": "chunk_A"}), 1.0),
        ]

        # Mock Grips Service
        mock_grips_service = MagicMock()
        mock_grips_service.get_grips_context.return_value = [
            LangchainDocument(page_content="grip1", metadata={"concept_id": 1, "title": "Concept 1"}),
        ]
        mock_grips_service.db.similarity_search_with_score.return_value = [
            (LangchainDocument(page_content="grip1", metadata={"concept_id": 1}), 1.1)
        ]
        
        from unittest.mock import patch
        class MockNode:
            source_chunk_id = "chunk_A"
            
        with patch('grips.models.ConceptNode.objects.only') as mock_only:
            mock_only.return_value.get.return_value = MockNode()

            results = unified_retrieve(
                query="test",
                rag_service=mock_rag_service,
                grips_service=mock_grips_service,
                deduplicate=False
            )

        # No deduplication: both are returned, no boost
        self.assertEqual(len(results), 2)
        # RAG distance 1.0, Grip distance 1.1
        self.assertEqual(results[0].source, "rag")
        self.assertEqual(results[1].source, "grips")


class TestGripsQualityFiltering(TestCase):
    def test_get_grips_context_quality_filter(self):
        from grips.services import GripsService
        
        service = GripsService()
        service.db = MagicMock()
        
        # Mock 3 docs returned from PGVector:
        # Doc 1: Distance 1.0 (Good), Concept 1
        # Doc 2: Distance 1.4 (Okay), Concept 2
        # Doc 3: Distance 1.6 (Exceeds max_distance 1.5), Concept 3
        service.db.similarity_search_with_score.return_value = [
            (LangchainDocument(page_content="c1", metadata={"concept_id": 1}), 1.0),
            (LangchainDocument(page_content="c2", metadata={"concept_id": 2}), 1.4),
            (LangchainDocument(page_content="c3", metadata={"concept_id": 3}), 1.6),
        ]
        
        from unittest.mock import patch
        
        # Mock ConceptNode for Concept 1 (High Quality: long narrative, edges)
        class HighQualityNode:
            narrative_content = "a" * 600
            structured_claims = [{"a": "b"}]
            source_chunk = None
            outgoing_edges = MagicMock()
            incoming_edges = MagicMock()
            
        HighQualityNode.outgoing_edges.count.return_value = 2
        HighQualityNode.incoming_edges.count.return_value = 2

        # Mock ConceptNode for Concept 2 (Low Quality: short, no edges)
        class LowQualityNode:
            narrative_content = "short"
            structured_claims = []
            source_chunk = None
            outgoing_edges = MagicMock()
            incoming_edges = MagicMock()
            
        LowQualityNode.outgoing_edges.count.return_value = 0
        LowQualityNode.incoming_edges.count.return_value = 0
        
        def mock_get(id):
            if id == 1: return HighQualityNode()
            if id == 2: return LowQualityNode()
            raise Exception("Not found")

        with patch('grips.models.ConceptNode.objects.select_related') as mock_sr:
            mock_sr.return_value.get.side_effect = mock_get
            
            docs = service.get_grips_context("test", max_distance=1.5)

        # We expect:
        # Doc 3 is dropped (distance 1.6 > 1.5)
        # Doc 1 has quality boost: 1.0 * 0.85 (length) * 0.85 (edges) * 0.92 (claims) = ~0.66
        # Doc 2 has no boost: 1.4
        
        self.assertEqual(len(docs), 2)
        # Sorted by boosted distance, Doc 1 should be first.
        self.assertEqual(docs[0].metadata["concept_id"], 1)
        self.assertEqual(docs[1].metadata["concept_id"], 2)


class TestGripsServiceIntegration(TestCase):
    """Real database integration tests for GripsService using native pgvector.django."""

    def test_concept_node_embedding_and_retrieval(self):
        from grips.models import Domain, ConceptNode
        from grips.services import GripsService

        domain = Domain.objects.create(name="Biology", description="Study of living organisms")
        node = ConceptNode.objects.create(
            domain=domain,
            title="Cell Membrane",
            slug="cell-membrane",
            focus_hint="Lipid bilayer structure and transport",
            narrative_content="The cell membrane is a biological membrane that separates and protects the interior of all cells from the outside environment. It consists of a lipid bilayer with embedded proteins.",
            structured_claims=[{"subject": "Cell Membrane", "predicate": "is_composed_of", "object": "Lipid Bilayer"}]
        )

        service = GripsService()
        service.index_concept_node(node)

        # Refresh from database and verify embedding is stored
        node.refresh_from_db()
        self.assertIsNotNone(node.embedding)
        self.assertEqual(len(node.embedding), 384)

        # Retrieve via get_grips_context
        results = service.get_grips_context("lipid bilayer and cell protection", domain_id=domain.id, k=3, max_distance=1.5)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].metadata["concept_id"], node.id)
        self.assertEqual(results[0].metadata["title"], "Cell Membrane")


class TestWikiServiceAndEndpoints(TestCase):
    """Test suite for the Grips OKF Interactive Wiki service, Git tracking, and views."""

    def test_wiki_path_jail_security(self):
        from grips.wiki_service import assert_safe_path
        
        # Valid relative path inside grips_okf
        safe = assert_safe_path("concepts/oceanography/derived/deep-sea-mining.md")
        self.assertTrue(str(safe).endswith("deep-sea-mining.md"))

        # Directory traversal attempts must raise PermissionError
        with self.assertRaises(PermissionError):
            assert_safe_path("../../etc/passwd")

        with self.assertRaises(PermissionError):
            assert_safe_path("../0945084a-044b-424b-9382-7ae121db10a1/sandbox_script.py")

        with self.assertRaises(PermissionError):
            assert_safe_path("/var/log/syslog")

    def test_wiki_read_page_and_wikilinks(self):
        from grips.wiki_service import get_page_content_and_sha, _resolve_wiki_rel_path
        
        rel_path = _resolve_wiki_rel_path("deep-sea-mining")
        self.assertTrue(rel_path.endswith("deep-sea-mining.md"))

        page = get_page_content_and_sha(rel_path)
        self.assertTrue(page['exists'])
        self.assertEqual(page['slug'], "deep-sea-mining")
        self.assertEqual(page['title'], "Deep Sea Mining")
        self.assertIn("Extracts battery minerals", page['markdown'])

    def test_wiki_link_search_api(self):
        from grips.wiki_service import search_wiki_links
        
        # Search for deep sea mining
        results = search_wiki_links("deep", limit=5)
        self.assertTrue(any(r['slug'] == 'deep-sea-mining' for r in results))

        # Search for causal statistics
        causal_results = search_wiki_links("causal", limit=5)
        self.assertTrue(len(causal_results) > 0)

    def test_wiki_backlinks_extraction(self):
        from grips.wiki_service import get_backlinks_for_slug
        
        # doc-10-c0-unconfoundedness-assumption--strong-ignorability- is included in doc-10-quasi-experimental-designs-for-causal-inference.md
        backlinks = get_backlinks_for_slug("doc-10-c0-unconfoundedness-assumption--strong-ignorability-")
        self.assertTrue(len(backlinks) > 0)
        self.assertTrue(any("quasi-experimental" in b['rel_path'].lower() for b in backlinks))

    def test_wiki_save_and_database_sync(self):
        from grips.models import ConceptNode, KnowledgeEdge, Domain
        from grips.wiki_service import save_and_commit_page, get_wiki_root
        import subprocess

        test_rel = "concepts/testing/derived/test-wiki-node.md"
        test_content = (
            "---\n"
            "type: concept\n"
            "title: Test Wiki Node\n"
            "domain: Testing\n"
            "slug: test-wiki-node\n"
            "focus_hint: Test focus hint for wiki integration\n"
            "---\n\n"
            "# Test Wiki Node\n\n"
            "This is an automated test node narrative verifying full wiki to database synchronization.\n\n"
            "## Graph Links\n\n"
            "- **DEPENDS_ON:** [[deep-sea-mining]] (Prerequisite knowledge)\n"
        )

        wiki_root = get_wiki_root()
        target_file = wiki_root / test_rel

        try:
            # 1. Save and commit
            result = save_and_commit_page(
                rel_path=test_rel,
                content=test_content,
                author_name="Wiki Test Suite",
                author_email="test@verbal.local",
                message="Add test-wiki-node for verification"
            )
            self.assertTrue(result['success'])
            self.assertIsNotNone(result['commit_sha'])
            self.assertTrue(result['db_sync']['synced'])

            # 2. Verify ConceptNode in PostgreSQL
            node = ConceptNode.objects.filter(slug="test-wiki-node").first()
            self.assertIsNotNone(node)
            self.assertEqual(node.title, "Test Wiki Node")
            self.assertEqual(node.focus_hint, "Test focus hint for wiki integration")
            self.assertIn("verifying full wiki to database synchronization", node.narrative_content)

            # 3. Verify KnowledgeEdge in PostgreSQL
            target_node = ConceptNode.objects.filter(slug="deep-sea-mining").first()
            if not target_node:
                d, _ = Domain.objects.get_or_create(name="Oceanography")
                target_node = ConceptNode.objects.create(domain=d, slug="deep-sea-mining", title="Deep Sea Mining")

            # Re-sync to verify edge connection
            save_and_commit_page(
                rel_path=test_rel,
                content=test_content,
                author_name="Wiki Test Suite",
                author_email="test@verbal.local",
                message="Sync edge"
            )
            edge = KnowledgeEdge.objects.filter(source=node, target=target_node).first()
            self.assertIsNotNone(edge)
            self.assertEqual(edge.relationship_type, KnowledgeEdge.RelationshipTypes.DEPENDS_ON)
            self.assertEqual(edge.justification, "Prerequisite knowledge")

        finally:
            # Cleanup test file and git commit
            if target_file.exists():
                target_file.unlink()
            subprocess.run(['git', 'rm', '-f', test_rel], cwd=str(wiki_root), capture_output=True, check=False)
            subprocess.run(['git', 'commit', '-m', 'Cleanup test-wiki-node'], cwd=str(wiki_root), capture_output=True, check=False)
            ConceptNode.objects.filter(slug="test-wiki-node").delete()

    def test_wiki_views_endpoints(self):
        from django.test import Client
        client = Client()

        # 1. Wiki Index
        res = client.get('/wiki/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Grips Open Knowledge Base")

        # 2. Wiki Page by slug
        res = client.get('/wiki/deep-sea-mining/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Deep Sea Mining")
        self.assertContains(res, "Edit Page")

        # 3. Wiki Query Links JSON
        res = client.get('/wiki/api/query-links/?q=ocean')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('results', data)
        self.assertTrue(any(r['slug'] == 'deep-sea-mining' for r in data['results']))

        # 4. History view
        res = client.get('/wiki/history/concepts/oceanography/derived/deep-sea-mining.md')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Revision History")

        # 5. Activity reflog
        res = client.get('/wiki/activity/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Wiki Activity Reflog")

        # 6. Traversal attack blocked
        res = client.get('/wiki/../../etc/passwd/')
        self.assertIn(res.status_code, [404, 403])

    def test_wiki_csrf_protection_and_save(self):
        import re
        import json
        import subprocess
        from django.test import Client
        from grips.wiki_service import get_wiki_root
        from grips.models import ConceptNode

        client = Client(enforce_csrf_checks=True)

        # 1. Direct GET to wiki page must set CSRF cookie and render CSRF meta tag
        res = client.get('/wiki/deep-sea-mining/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('csrftoken', res.cookies)
        self.assertContains(res, 'name="csrf-token"')

        token_match = re.search(r'name="csrf-token" content="([^"]+)"', res.content.decode())
        self.assertIsNotNone(token_match)
        csrf_token = token_match.group(1)

        test_rel = 'concepts/testing/derived/test-csrf-node.md'
        wiki_root = get_wiki_root()
        target_file = wiki_root / test_rel

        try:
            # 2. POST to save without CSRF token must fail with 403 Forbidden
            bad_client = Client(enforce_csrf_checks=True)
            bad_res = bad_client.post(
                f'/wiki/api/save/{test_rel}',
                data=json.dumps({'markdown': '# Test Node', 'message': 'CSRF test'}),
                content_type='application/json'
            )
            self.assertEqual(bad_res.status_code, 403)

            # 3. POST to save with CSRF cookie & header must succeed (200)
            good_res = client.post(
                f'/wiki/api/save/{test_rel}',
                data=json.dumps({'markdown': '# Test Node\n\nCSRF content', 'message': 'CSRF test pass'}),
                content_type='application/json',
                HTTP_X_CSRFTOKEN=csrf_token
            )
            self.assertEqual(good_res.status_code, 200)
            data = good_res.json()
            self.assertTrue(data.get('success'))
        finally:
            if target_file.exists():
                target_file.unlink()
            subprocess.run(['git', 'rm', '-f', test_rel], cwd=str(wiki_root), capture_output=True, check=False)
            subprocess.run(['git', 'commit', '-m', 'Cleanup test-csrf-node'], cwd=str(wiki_root), capture_output=True, check=False)
            ConceptNode.objects.filter(slug="test-csrf-node").delete()

    def test_clean_human_title_sanitization(self):
        from grips.wiki_service import clean_human_title

        # Strips technical doc prefixes
        self.assertEqual(
            clean_human_title("doc-11-c3-interrupted-time-series-designs"),
            "Interrupted Time Series Designs"
        )
        self.assertEqual(
            clean_human_title("doc-10-c0-unconfoundedness-assumption--strong-ignorability-"),
            "Unconfoundedness Assumption - Strong Ignorability"
        )
        self.assertEqual(
            clean_human_title("Doc: Causal Inference in Statistics: A Primer"),
            "Causal Inference in Statistics: A Primer"
        )
        self.assertEqual(
            clean_human_title("unified-179-instrumental-variables"),
            "Instrumental Variables"
        )
        self.assertEqual(
            clean_human_title("ref-210-heckman-sample-selection"),
            "Heckman Sample Selection"
        )
        # Corrupt / punctuation-only fallback
        self.assertEqual(clean_human_title(", "), "Untitled Concept")
        self.assertEqual(clean_human_title(""), "Untitled Concept")

    def test_graph_data_and_research_analytics(self):
        from grips.wiki_service import (
            get_graph_data,
            get_reading_list_analytics,
            get_synthesis_matrix_data,
        )

        # 1. Graph Data modes
        for mode in ('knowledge', 'citation', 'hybrid'):
            data = get_graph_data(mode)
            self.assertEqual(data['mode'], mode)
            self.assertIn('nodes', data)
            self.assertIn('edges', data)
            self.assertIn('mermaid_code', data)
            self.assertIn('node_count', data)
            self.assertIn('edge_count', data)

        # 2. Reading list analytics
        reading_data = get_reading_list_analytics()
        self.assertIn('seminal_papers', reading_data)
        self.assertIn('acquisition_wishlist', reading_data)
        self.assertIn('stats', reading_data)
        self.assertIn('total_documents', reading_data['stats'])

        # 3. Synthesis matrix
        matrix_data = get_synthesis_matrix_data()
        self.assertIn('concept_headers', matrix_data)
        self.assertIn('rows', matrix_data)

    def test_research_views_endpoints(self):
        from django.test import Client
        client = Client()

        # 1. Graph view
        res = client.get('/wiki/graph/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Graph Representation")
        self.assertContains(res, "vis-network.min.js")

        # 2. Graph data API
        res = client.get('/wiki/api/graph-data/?mode=knowledge')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('nodes', data)
        self.assertIn('edges', data)

        # 3. Reading list view
        res = client.get('/wiki/research/reading/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Research Reading Prioritization")

        # 4. Synthesis matrix view
        res = client.get('/wiki/research/matrix/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Comparative Literature Synthesis Matrix")

    def test_milkdown_bundle_no_external_node_process(self):
        from django.conf import settings
        bundle_path = settings.BASE_DIR / 'static' / 'vendor' / 'milkdown' / 'crepe.bundle.mjs'
        self.assertTrue(bundle_path.exists(), "crepe.bundle.mjs must exist in static vendor")
        content = bundle_path.read_text(encoding='utf-8')
        self.assertNotIn(
            'import __Process$ from "/node/process.mjs";',
            content,
            "crepe.bundle.mjs must not import /node/process.mjs"
        )
        self.assertIn('const __Process$ = { env: { NODE_ENV: "production" } };', content)

