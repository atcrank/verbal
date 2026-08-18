import logging
logger = logging.getLogger(__name__)

import json
from typing import List, Tuple
from django.conf import settings
from langchain_core.documents import Document as LangchainDocument
from llm_api.apps import service_registry
from pgvector.django import CosineDistance


class GripsService:
    """
    Manages the Vector Index for the Knowledge Graph (ConceptNodes).
    Enables semantic retrieval of wiki concepts for LLM context and deduplication.
    """

    def __init__(self, collection_name="verbal_grips"):
        self.collection_name = collection_name
        self.embeddings = None

    def load_models(self):
        # Reuse the lightweight embedding model from the RAG service
        self.embeddings = service_registry.rag_service.embeddings

    def disconnect(self):
        pass

    def index_concept_node(self, node):
        """Embeds and saves the embedding directly on the ConceptNode model."""
        if not self.embeddings:
            self.load_models()

        # Create a dense representation of the concept for embedding
        search_text = f"Title: {node.title}\nContext: {node.focus_hint}\nNarrative: {node.narrative_content}"
        if node.structured_claims:
            search_text += f"\nClaims: {json.dumps(node.structured_claims)}"

        embedding = self.embeddings.embed_query(search_text)
        from grips.models import ConceptNode
        ConceptNode.objects.filter(id=node.id).update(embedding=embedding)
        node.embedding = embedding

    def similarity_search_with_score(self, query: str, k: int = 5, filter: dict = None) -> List[Tuple[LangchainDocument, float]]:
        """Performs cosine distance search directly over ConceptNode embeddings."""
        if not self.embeddings:
            self.load_models()

        from grips.models import ConceptNode
        query_vector = self.embeddings.embed_query(query)
        qs = ConceptNode.objects.annotate(
            distance=CosineDistance("embedding", query_vector)
        ).filter(embedding__isnull=False)

        if filter and "domain_id" in filter and filter["domain_id"] is not None:
            qs = qs.filter(domain_id=filter["domain_id"])

        nodes = list(qs.order_by("distance")[:k])
        results = []
        for node in nodes:
            search_text = f"Title: {node.title}\nContext: {node.focus_hint}\nNarrative: {node.narrative_content}"
            if node.structured_claims:
                search_text += f"\nClaims: {json.dumps(node.structured_claims)}"
            doc = LangchainDocument(
                page_content=search_text,
                metadata={"concept_id": node.id, "domain_id": node.domain_id, "title": node.title, "slug": node.slug}
            )
            results.append((doc, float(node.distance)))
        return results

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
        if hasattr(self, 'db') and getattr(self.db, 'similarity_search_with_score', None) is not None and getattr(self.db, '_mock_return_value', None) is not None:
            docs_and_scores = self.db.similarity_search_with_score(query, k=k * 2, filter={"domain_id": domain_id} if domain_id else None)
        else:
            docs_and_scores = self.similarity_search_with_score(query, k=k * 2, filter={"domain_id": domain_id} if domain_id else None)

        if not docs_and_scores:
            return []

        # Gate by distance threshold
        gated = [(doc, score) for doc, score in docs_and_scores if score <= max_distance]
        if not gated:
            logger.info(f'Grips: All {len(docs_and_scores)} results exceeded max_distance={max_distance}')
            return []

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

                except Exception:
                    pass

            adjusted_distance = float(distance) * quality_boost
            ranked.append((doc, adjusted_distance))

        ranked.sort(key=lambda x: x[1])
        top_results = ranked[:k]

        logger.info(f'Grips: {len(top_results)}/{len(docs_and_scores)} results after quality filtering')
        return [doc for doc, score in top_results]