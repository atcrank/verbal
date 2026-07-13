"""
Unified retrieval service that merges RAG and Grips results with
lineage-aware deduplication.

Promotes the lineage boost pattern from demo_ui/views.py into a shared
layer so all consumers (api.py, actions.py, meta_tools.py, demo_ui)
get deduplicated, boosted results.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.documents import Document as LangchainDocument

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A unified retrieval result from either RAG or Grips."""
    doc: LangchainDocument
    source: Literal["rag", "grips"]
    original_distance: float
    boosted_distance: float
    concept_id: int | None = None
    source_chunk_id: str | None = None
    is_duplicate: bool = False
    metadata: dict = field(default_factory=dict)


def unified_retrieve(
    query: str,
    rag_service=None,
    grips_service=None,
    rag_k: int = 4,
    grips_k: int = 4,
    max_distance: float = 1.5,
    domain_id: int | None = None,
    deduplicate: bool = True,
    lineage_boost_factor: float = 0.8,
) -> list[RetrievalResult]:
    """
    Retrieves from both RAG and Grips, deduplicates by lineage, and
    returns a unified sorted list.

    Deduplication logic:
    When a ConceptNode has a source_chunk FK pointing to a RAG chunk that
    also appears in results, the Grips concept (the higher-quality summary)
    is kept and boosted, while the raw RAG chunk is marked as duplicate and
    excluded from the final output. This prevents the same information
    appearing twice at different abstraction levels.

    Lineage boost:
    When a RAG chunk and its derived ConceptNode both appear, both get a
    distance reduction (lower = better). The duplicate is then suppressed.

    Args:
        query: The search query.
        rag_service: RAGService instance (or None to skip RAG).
        grips_service: GripsService instance (or None to skip Grips).
        rag_k: Number of RAG results to fetch.
        grips_k: Number of Grips results to fetch.
        max_distance: PGVector distance threshold (lower = better).
        domain_id: Optional Grips domain filter.
        deduplicate: Whether to suppress duplicates via lineage.
        lineage_boost_factor: Multiplier for lineage-linked results (< 1.0).

    Returns:
        Sorted list of RetrievalResult, lowest distance first.
    """
    results: list[RetrievalResult] = []

    # ── Grips retrieval ──────────────────────────────────────────────
    grips_source_chunk_ids: set[str] = set()

    if grips_service:
        try:
            grips_docs = grips_service.get_grips_context(
                query, domain_id=domain_id, k=grips_k, max_distance=max_distance,
            )
            # get_grips_context now returns quality-filtered docs (Finding 1.3).
            # We need (doc, score) pairs for unified ranking. Since the quality
            # filtering already happened inside get_grips_context, we re-query
            # with scores here for the lineage boost math.
            grips_scored = grips_service.db.similarity_search_with_score(
                query, k=grips_k * 2,
                filter={"domain_id": domain_id} if domain_id else None,
            )
            # Build a lookup of concept_id -> distance for grips docs actually returned
            grips_doc_ids = {d.metadata.get('concept_id') for d in grips_docs}
            grips_score_map = {}
            for doc, score in grips_scored:
                cid = doc.metadata.get('concept_id')
                if cid in grips_doc_ids and cid not in grips_score_map:
                    grips_score_map[cid] = score

            for doc in grips_docs:
                concept_id = doc.metadata.get('concept_id')
                distance = grips_score_map.get(concept_id, max_distance)

                # Resolve source_chunk linkage for deduplication
                source_chunk_id = None
                if concept_id:
                    try:
                        from grips.models import ConceptNode
                        node = ConceptNode.objects.only('source_chunk_id').get(id=concept_id)
                        if node.source_chunk_id:
                            source_chunk_id = str(node.source_chunk_id)
                            grips_source_chunk_ids.add(source_chunk_id)
                    except Exception:
                        pass

                results.append(RetrievalResult(
                    doc=doc,
                    source="grips",
                    original_distance=distance,
                    boosted_distance=distance,
                    concept_id=concept_id,
                    source_chunk_id=source_chunk_id,
                ))
        except Exception as e:
            logger.warning(f'Unified retrieve: Grips search failed: {e}')

    # ── RAG retrieval ────────────────────────────────────────────────
    if rag_service:
        try:
            rag_docs = rag_service.get_context(query, k=rag_k, max_distance=max_distance)
            # get_context returns filtered docs; we need distances for ranking.
            # Re-query for scores (same pattern as Grips above)
            rag_scored = rag_service.db.similarity_search_with_score(query, k=rag_k * 2)
            rag_score_map = {}
            for doc, score in rag_scored:
                doc_id = str(doc.metadata.get('id', ''))
                if doc_id and doc_id not in rag_score_map:
                    rag_score_map[doc_id] = score

            for doc in rag_docs:
                chunk_id = str(doc.metadata.get('chunk_id', doc.metadata.get('id', '')))
                distance = rag_score_map.get(
                    str(doc.metadata.get('id', '')), max_distance
                )

                is_dup = deduplicate and chunk_id in grips_source_chunk_ids

                results.append(RetrievalResult(
                    doc=doc,
                    source="rag",
                    original_distance=distance,
                    boosted_distance=distance,
                    is_duplicate=is_dup,
                    metadata={"chunk_id": chunk_id},
                ))
        except Exception as e:
            logger.warning(f'Unified retrieve: RAG search failed: {e}')

    # ── Lineage boost ────────────────────────────────────────────────
    if deduplicate:
        # Boost Grips results that have lineage with RAG results
        rag_chunk_ids = {
            r.metadata.get("chunk_id", "")
            for r in results if r.source == "rag"
        }
        for r in results:
            if r.source == "grips" and r.source_chunk_id in rag_chunk_ids:
                r.boosted_distance *= lineage_boost_factor
            elif r.source == "rag" and not r.is_duplicate:
                chunk_id = r.metadata.get("chunk_id", "")
                if chunk_id in grips_source_chunk_ids:
                    r.boosted_distance *= lineage_boost_factor

    # ── Final sort and dedup ─────────────────────────────────────────
    active_results = [r for r in results if not r.is_duplicate]
    active_results.sort(key=lambda r: r.boosted_distance)

    return active_results


def format_context_block(results: list[RetrievalResult]) -> str:
    """
    Formats unified retrieval results into a text block suitable for
    injection into an LLM prompt.
    """
    if not results:
        return ""

    parts = []
    for i, r in enumerate(results, 1):
        if r.source == "grips":
            title = r.doc.metadata.get('title', 'Unknown Concept')
            parts.append(f"[{i}] Concept [{title}]:\n{r.doc.page_content}")
        else:
            filename = r.doc.metadata.get('filename', 'Unknown Source')
            parts.append(f"[{i}] Source: {filename}\n{r.doc.page_content}")

    return "\n\n".join(parts)
