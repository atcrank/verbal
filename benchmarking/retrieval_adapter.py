"""
Adapter for executing diverse retrieval strategies (Document Chunk RAG,
GRIPS Knowledge Graph, and Unified Super-Retriever with Lineage Deduplication)
in Benchmarking runs, live SSE telemetry, and automated evaluation.
"""

import logging
from typing import Tuple, List, Dict, Any
from llm_api.apps import service_registry

logger = logging.getLogger(__name__)


def retrieve_benchmark_context(
    query: str,
    strategy: str,
    rag_service=None,
    grips_service=None,
    k: int = 3,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Retrieves grounding context for a benchmark scenario query according to the chosen strategy.
    
    Strategies supported:
      - 'none', 'direct': Pure LLM zero-shot, returns empty string.
      - 'default', 'dense', 'chunk': Raw document chunk retrieval via RAGService.
      - 'grobid', 'regex', 'abbreviations', 'prompt': Specific chunk reading strategies.
      - 'grips', 'grips_wiki': Pure GRIPS Knowledge Graph / Wiki Concept retrieval.
      - 'unified_dedup', 'unified': Unified RAG + GRIPS with lineage-aware deduplication.
      - 'unified_raw', 'unified_nodedup': Unified RAG + GRIPS without deduplication.

    Returns:
        Tuple of (context_text_block, metadata_items)
    """
    strat = (strategy or "none").lower()
    if strat in ("none", "direct"):
        return "", []

    rag_svc = rag_service or getattr(service_registry, "rag_service", None)
    grips_svc = grips_service or getattr(service_registry, "grips_service", None)

    # 1. Pure GRIPS Knowledge Graph / Wiki Retrieval
    if strat in ("grips", "grips_wiki", "kg"):
        if not grips_svc:
            logger.info("GripsService unavailable or not initialized; bypassing grips_wiki.")
            return "", []
        try:
            docs = grips_svc.get_grips_context(query, k=k)
            parts = []
            meta = []
            for d in docs:
                title = d.metadata.get("title", "Concept")
                parts.append(f"Concept [{title}]:\n{d.page_content}")
                meta.append({"source": "grips", "title": title, "metadata": d.metadata})
            return "\n\n".join(parts), meta
        except Exception as err:
            logger.warning(f"Error in grips_wiki retrieval for benchmark: {err}")
            return "", []

    # 2. Unified Super-Retriever (with or without Lineage-Aware Deduplication)
    if strat in ("unified_dedup", "unified", "unified_raw", "unified_nodedup"):
        dedup = strat in ("unified_dedup", "unified")
        try:
            from background_resources.retrieval import unified_retrieve, format_context_block
            results = unified_retrieve(
                query=query,
                rag_service=rag_svc,
                grips_service=grips_svc,
                rag_k=k,
                grips_k=k,
                deduplicate=dedup,
            )
            text_block = format_context_block(results)
            meta = [
                {
                    "source": r.source,
                    "title": r.doc.metadata.get("title") or r.doc.metadata.get("filename", "Unknown"),
                    "boosted_distance": r.boosted_distance,
                    "is_duplicate": r.is_duplicate,
                }
                for r in results
            ]
            return text_block, meta
        except Exception as err:
            logger.warning(f"Error in unified retrieval (dedup={dedup}) for benchmark: {err}")
            return "", []

    # 3. Standard Document Chunk Strategies (via RAGService)
    if rag_svc:
        try:
            docs = rag_svc.get_context(query, k=k)
            parts = [d.page_content for d in docs]
            meta = [d.metadata for d in docs]
            return "\n\n".join(parts), meta
        except Exception as err:
            logger.warning(f"Error in RAG chunk retrieval ({strat}) for benchmark: {err}")
            return "", []

    return "", []
