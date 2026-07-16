import logging
logger = logging.getLogger(__name__)

import os
import json
from django.conf import settings
from langchain_postgres import PGVector
from langchain_core.documents import Document as LangchainDocument
from llm_api.apps import service_registry
from sqlalchemy import create_engine


class GripsService:
    """
    Manages the Vector Index for the Knowledge Graph (ConceptNodes).
    Enables semantic retrieval of wiki concepts for LLM context and deduplication.
    """

    def __init__(self, collection_name="verbal_grips"):
        self.collection_name = collection_name
        self.db = None
        self.embeddings = None
        self.indexed_concepts = {}  # Map ConceptNode ID to FAISS ID

    def load_models(self):
        # Reuse the lightweight embedding model from the RAG service
        self.embeddings = service_registry.rag_service.embeddings
        db_config = settings.DATABASES['default']
        user = db_config.get("USER", "")
        password = db_config.get("PASSWORD", "")
        host = db_config.get("HOST", "127.0.0.1")
        port = db_config.get("PORT", "5432")
        db_name = db_config.get("NAME", "")
        connection_string = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"
        self.engine = create_engine(connection_string)

        self.db = PGVector(embeddings=self.embeddings,
                           collection_name=self.collection_name,
                           connection=self.engine,
                           use_jsonb=True)

    def disconnect(self):
        """Closes SQLAlchemy connection to prevent database locks during test teardown"""
        if hasattr(self, 'engine') and self.engine:
            self.engine.dispose()


    def index_concept_node(self, node):
        """Embeds and indexes a ConceptNode for semantic retrieval."""
        if not self.db:
            self.load_models()

        # Delete the old embedding if we are updating an existing node
        old_faiss_id = self.indexed_concepts.get(node.id)
        if old_faiss_id:
            try:
                self.db.delete([old_faiss_id])
            except ValueError:
                pass

        # Create a dense representation of the concept for embedding
        search_text = f"Title: {node.title}\nContext: {node.focus_hint}\nNarrative: {node.narrative_content}"
        if node.structured_claims:
            search_text += f"\nClaims: {json.dumps(node.structured_claims)}"

        doc = LangchainDocument(
            page_content=search_text,
            metadata={"concept_id": node.id, "domain_id": node.domain_id, "title": node.title, "slug": node.slug}
        )

        self.db.add_documents([doc], ids=[str(node.id)])

    def get_grips_context(self, query: str, domain_id: int = None, k: int = 5, max_distance: float = 1.5):
        """
        Retrieves the most relevant ConceptNodes with quality filtering.

        Uses PGVector distance (lower = better). Results beyond max_distance
        are dropped. Remaining results are re-ranked by a quality score that
        rewards:
        - Rich narrative content (>200 chars)
        - Connected concepts (edge count)
        - Structured claims presence
        - Grobid-sourced chunks (higher confidence source material)
        """
        if not self.db:
            self.load_models()

        try:
            filter_dict = {"domain_id": domain_id} if domain_id else None
            docs_and_scores = self.db.similarity_search_with_score(query, k=k * 2, filter=filter_dict)
        except Exception as e:
            logger.info(f'Error retrieving Grips context: {e}')
            return []

        if not docs_and_scores:
            return []

        # Gate by distance threshold (Finding 1.3)
        gated = [(doc, score) for doc, score in docs_and_scores if score <= max_distance]
        if not gated:
            logger.info(f'Grips: All {len(docs_and_scores)} results exceeded max_distance={max_distance}')
            return []

        # Quality re-ranking: compute a quality-adjusted score for each result
        from grips.models import ConceptNode
        ranked = []
        for doc, distance in gated:
            concept_id = doc.metadata.get('concept_id')
            quality_boost = 1.0  # Multiplier on distance (lower = better)

            if concept_id:
                try:
                    node = ConceptNode.objects.select_related('source_chunk').get(id=concept_id)

                    # Narrative richness: nodes with substantial content are more useful
                    narrative_len = len(node.narrative_content or '')
                    if narrative_len > 500:
                        quality_boost *= 0.85
                    elif narrative_len > 200:
                        quality_boost *= 0.92

                    # Edge density: well-connected nodes are higher quality
                    edge_count = node.outgoing_edges.count() + node.incoming_edges.count()
                    if edge_count >= 3:
                        quality_boost *= 0.85
                    elif edge_count >= 1:
                        quality_boost *= 0.92

                    # Structured claims: concepts with computable claims are richer
                    if node.structured_claims:
                        quality_boost *= 0.92

                    # Grobid-sourced boost: concepts generated from Grobid-parsed
                    # documents have higher source fidelity
                    if node.source_chunk:
                        from background_resources.models import GrobidReadingStrategy
                        is_grobid = GrobidReadingStrategy.objects.filter(
                            usages__chunk=node.source_chunk
                        ).exists()
                        if is_grobid:
                            quality_boost *= 0.88

                except ConceptNode.DoesNotExist:
                    pass

            adjusted_distance = distance * quality_boost
            ranked.append((doc, adjusted_distance))

        ranked.sort(key=lambda x: x[1])
        top_results = ranked[:k]

        logger.info(f'Grips: {len(top_results)}/{len(docs_and_scores)} results after quality filtering')
        return [doc for doc, score in top_results]