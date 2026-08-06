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
