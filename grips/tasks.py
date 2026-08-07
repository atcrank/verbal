import logging
logger = logging.getLogger(__name__)

import re
from typing import List, Literal
from django.utils import timezone
from celery import shared_task, chord
from pydantic import BaseModel, Field, field_validator

from llm_api.apps import service_registry
from background_resources.models import Document, RAGChunk
from .models import ConceptNode, Domain, KnowledgeEdge


class StructuredClaim(BaseModel):
    predicate: Literal['DEPENDS_ON', 'INCLUDES', 'EXEMPLIFIES', 'RELATED_TO'] = Field(description="The relationship type (e.g. INCLUDES for part-whole, DEPENDS_ON for causal/prerequisite).")
    subject: str
    object: str

class ConceptDraft(BaseModel):
    thought_process: str = Field(description="Think step-by-step to plan the entry. Identify and resolve ambiguities based on the title and focus hint.")
    narrative: str = Field(description="The dense, Markdown-formatted explanation unifying the concept.")
    source_chunk_id: str = Field(default="", description="The specific CHUNK ID from the text ")
    claims: List[StructuredClaim] = Field(description="Atomic facts using strict relational predicates.")

    @field_validator('narrative', mode='before')
    @classmethod
    def clean_narrative(cls, v):
        # Handle cases where the model outputs a list of strings instead of a string
        if isinstance(v, list):
            v = "\n\n".join(str(item) for item in v)
        if isinstance(v, str):
            # Clean up literal escaped newlines or incorrect slashes
            v = v.replace('\\n', '\n').replace('/n', '\n')
        return v


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 3}
)
def generate_concept_narrative(self, concept_id: int):
    """
    Celery task to generate the narrative_content for a ConceptNode.
    This is the core "writer" function for the wiki.
    """
    try:
        node = ConceptNode.objects.get(id=concept_id)
    except ConceptNode.DoesNotExist:
        return f"ConceptNode with id {concept_id} not found."

    ai_service = service_registry.ai_service
    rag_service = service_registry.rag_service

    # 1. Use the node's title to find relevant context from our background resources.
    # This is a placeholder for a more sophisticated RAG query.
    query_text = node.title
    if node.focus_hint:
        query_text += f" ({node.focus_hint})"
    context_chunks = rag_service.get_context(query_text, k=5)
    context_text = "\n\n".join([chunk.page_content for chunk in context_chunks])

    # 2. Get the style guide from the domain to instruct the LLM.
    style_guide = node.domain.style_guide or "Write a clear, dense, and encyclopedic entry."

    # 3. Construct the prompt.
    prompt = f"""
    You are an objective, authoritative encyclopedic agent. Your task is to write a definitive wiki entry.
    Do NOT act as a conversational assistant. DO NOT include meta-commentary, preambles, or conversational filler.

    **Topic:** {node.title}
    **Domain:** {node.domain.name}
    **Focus Hint/Constraints:** {node.focus_hint or "None provided. Interpret based on domain."}

    **Relevant Context from internal documents:**
    ---
    {context_text}
    ---

    INSTRUCTIONS:
    1. Use `thought_process` to identify ambiguities and synthesize the context.
    2. Write the `narrative` as a well-structured Markdown document. 
       - Do NOT say things like "Based on the provided documents..." or "More information is needed." Just write the facts objectively.
       - Ensure proper use of Markdown headings (##, ###).
       - Stop generating when the topic is fully covered. Do not append simulated user prompts.
    3. Extract 3 to 5 definitive, atomic facts from your narrative into the `claims` list. 
       - Example Claim: {{"predicate": "INCLUDES", "subject": "Fire Engine", "object": "Water Pump"}}
    """

    # 4. Generate the content.
    try:
        draft = ai_service.generate_outline(
            messages=prompt,
            response_schema=ConceptDraft,
            max_new_tokens=1500
        )
        # In case the proxy returns a raw dictionary or string, make sure it's validated
        if isinstance(draft, dict):
            if "error" in draft:
                return f"Generation failed for {node.title}: {draft['details']}"
            draft = ConceptDraft.model_validate(draft)
        elif isinstance(draft, str):
            draft = ConceptDraft.model_validate_json(draft)
    except Exception as e:
        return f"Failed to parse generation for {node.title}: {e}"

    # 5. Save the result.
    node.narrative_content = draft.narrative
    # Convert the pydantic models to dicts for the JSONField
    node.structured_claims = [claim.model_dump() for claim in draft.claims]
    node.save(update_fields=['narrative_content', 'structured_claims'])

    # Optional: Immediately queue a linting task.
    # lint_concept_node.delay(node.id)

    return f"Successfully generated narrative for '{node.title}'."

# --- NEW CORPUS DIGESTION & LINTING PIPELINES ---

class SubConcept(BaseModel):
    title: str = Field(description="Name of the concept")
    focus_hint: str = Field(description="Context or intent for this concept")
    summary: str = Field(description="A brief summary of what the document says about this concept")
    claims: List[StructuredClaim] = Field(default_factory=list, description="Structured claims relating this concept to others.")


class DocumentDigest(BaseModel):
    overall_summary: str = Field(description="High-level summary of the entire document")
    concept_nodes: List[SubConcept] = Field(description="Distinct concepts identified in the document")

class BatchConceptExtraction(BaseModel):
    concept_nodes: List[SubConcept] = Field(description="Distinct concepts identified in this section of the document")


class ConceptRelationshipEvaluation(BaseModel):
    reasoning: str = Field(description="Analyze the relationship between the two concepts.")
    relationship_action: Literal["MERGE", "EDGE", "DISTINCT"] = Field(
        description="MERGE if they describe the exact same core concept. EDGE if they are related but distinct. DISTINCT if unrelated."
    )
    edge_predicate: Literal['DEPENDS_ON', 'INCLUDES', 'EXEMPLIFIES', 'RELATED_TO'] = Field(
        default="RELATED_TO", description="If EDGE, choose the relationship predicate."
    )


class UnifiedConceptDraft(BaseModel):
    title: str = Field(description="Domain-level title for the unified concept.")
    narrative: str = Field(
        description="Unified encyclopedic explanation. MUST cite the source nodes using [[slug]] syntax.")
    claims: List[StructuredClaim] = Field(default_factory=list,
                                          description="Structured claims for the unified concept.")

@shared_task
def task_digest_corpus_level_1(domain_id: int, document_id: int):
    """MASTER TASK: Queues DigestDocumentChunk blueprint for each document chunk."""
    try:
        domain = Domain.objects.get(id=domain_id)
        doc = Document.objects.get(id=document_id)
    except Exception as e:
        return f"Error loading domain/doc: {e}"

    rag_service = service_registry.rag_service

    chunks = []
    existing_ids = []
    if hasattr(doc, 'grobid_metadata') and doc.grobid_metadata and doc.grobid_metadata.tei_xml:
        try:
            chunks, existing_ids = rag_service.convert_chunk_store_document_grobid(doc)
        except Exception as e:
            logger.info(f'Skipping Grobid chunking: {e}')

    if not chunks and not existing_ids:
        chunks, existing_ids = rag_service.convert_chunk_store_document(doc)

        if not chunks and existing_ids:
            chunks = rag_service.store.mget(existing_ids)
            chunks = [c for c in chunks if c]

        if not chunks:
            return f"No chunks could be made or found for document {doc.title}"

        batch_size = 15
        
        from metacognition.models import CognitiveBlueprint
        from metacognition.tasks import task_run_blueprint_async
        
        try:
            bp = CognitiveBlueprint.objects.get(name="DigestDocumentChunk")
        except CognitiveBlueprint.DoesNotExist:
            return "DigestDocumentChunk blueprint not found. Run seed."

        batch_count = 0
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_text = "\n\n".join(
                [c.page_content if hasattr(c, 'page_content') else c.text_content for c in batch_chunks])
            
            task_prompt = (
                f"Digest the following text chunk into the domain of '{domain.name}'.\n\n"
                f"Document Title: {doc.title}\n"
                f"Domain: {domain.name}\n\n"
                f"Content Section:\n{batch_text}\n\n"
                f"Extract the key operational concepts and their operational logic (claims). Use the `create_concept_nodes_tool` with domain_id={domain.id} and document_id={doc.id}."
            )
            
            task_run_blueprint_async.delay(bp.id, task_prompt, None, None)
            batch_count += 1

        return f"Queued {batch_count} DigestDocumentChunk blueprint tasks for {doc.title}."

class LintingReportSchema(BaseModel):
    is_valid: bool = Field(description="True if narrative has no contradictions or style issues.")
    style_violations: List[str] = Field(description="List of domain style guide violations")
    contradictions: List[str] = Field(description="List of contradictions within the narrative")
    missing_cross_references: List[str] = Field(description="Concepts that should be linked")
    suggested_fixes: str = Field(description="Suggested rewrites to fix issues")


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def task_lint_concept_node(node_id: int):
    """Triggers the LintGripsNode metacognitive blueprint to evaluate and repair a ConceptNode."""
    try:
        node = ConceptNode.objects.get(id=node_id)
    except ConceptNode.DoesNotExist:
        return "Node not found."

    from metacognition.models import CognitiveBlueprint
    from metacognition.tasks import task_run_blueprint_async
    
    try:
        bp = CognitiveBlueprint.objects.get(name="LintGripsNode")
    except CognitiveBlueprint.DoesNotExist:
        return "LintGripsNode blueprint not found."

    task_prompt = (
        f"Evaluate this ConceptNode:\n\n"
        f"Domain: {node.domain.name}\n"
        f"Style Guide: {node.domain.style_guide or 'Maintain objective, clear, encyclopedic tone.'}\n"
        f"Title: {node.title}\n"
        f"Focus Hint: {node.focus_hint}\n"
        f"Narrative: {node.narrative_content}\n\n"
        f"Node ID: {node.id}"
    )
    
    task_run_blueprint_async.delay(bp.id, task_prompt, None, None)
    return f"Triggered linting blueprint for node {node_id}"


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 2})
def task_digest_corpus_level_2(domain_id: int, new_node_ids: List[int] = None):
    """
    Level 2 Incremental Consolidation.
    Finds semantic neighbors for newly ingested nodes and asks the LLM:
    Are these identical (Merge), related (KnowledgeEdge), or distinct?
    """
    if not new_node_ids:
        # Fetch all nodes in the domain if none provided (e.g. from admin trigger)
        new_node_ids = list(ConceptNode.objects.filter(domain_id=domain_id).values_list('id', flat=True))
        if not new_node_ids:
            return "No nodes to consolidate."

    grips_service = service_registry.grips_service
    ai_service = service_registry.ai_service

    try:
        domain = Domain.objects.get(id=domain_id)
    except Domain.DoesNotExist:
        return "Domain not found."

    actions_taken = []

    for node_id in new_node_ids:
        try:
            new_node = ConceptNode.objects.get(id=node_id)
        except ConceptNode.DoesNotExist:
            continue

        # 1. Find neighbors in the FAISS index
        search_text = f"Title: {new_node.title}\nContext: {new_node.focus_hint}\nNarrative: {new_node.narrative_content}"
        neighbors_docs = grips_service.get_grips_context(search_text, domain_id=domain_id, k=4)

        neighbor_ids = []
        for d in neighbors_docs:
            cid = d.metadata.get("concept_id")
            if cid and cid != new_node.id and cid not in neighbor_ids:
                neighbor_ids.append(cid)

        for neighbor_id in neighbor_ids[:3]:
            try:
                neighbor_node = ConceptNode.objects.get(id=neighbor_id)
            except ConceptNode.DoesNotExist:
                continue

            # Skip if an edge already exists between these two concepts
            if KnowledgeEdge.objects.filter(source=new_node, target=neighbor_node).exists() or \
                    KnowledgeEdge.objects.filter(source=neighbor_node, target=new_node).exists():
                continue

            from metacognition.models import CognitiveBlueprint
            from metacognition.tasks import task_run_blueprint_async
            
            try:
                bp = CognitiveBlueprint.objects.get(name="EvaluateConceptNeighbors")
            except CognitiveBlueprint.DoesNotExist:
                logger.error("EvaluateConceptNeighbors blueprint not found.")
                continue

            # 2. Evaluate relationship via Blueprint
            task_prompt = (
                f"Evaluate the relationship between Concept A and Concept B for domain '{domain.name}'.\n\n"
                f"Concept A (ID: {new_node.id}):\n"
                f"Title: {new_node.title}\n"
                f"Narrative: {new_node.narrative_content}\n\n"
                f"Concept B (ID: {neighbor_node.id}):\n"
                f"Title: {neighbor_node.title}\n"
                f"Narrative: {neighbor_node.narrative_content}\n\n"
                f"Use the `evaluate_relationship_tool` to record your decision with source_id={new_node.id} and target_id={neighbor_node.id}."
            )

            task_run_blueprint_async.delay(bp.id, task_prompt, None, None)
            actions_taken.append(f"Queued blueprint eval for '{new_node.title}' and '{neighbor_node.title}'")

    summary = f"Level 2 Complete. Queued {len(actions_taken)} blueprint tasks. "
    logger.info(summary)
    return summary


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 2})
def task_digest_corpus_level_3(self, domain_id: int):
    """
    Level 3: Cross-domain concept joins.
    Looks for analogous concepts in OTHER domains to bridge silos.
    """
    from llm_api.apps import service_registry
    
    grips_service = service_registry.grips_service
    ai_service = service_registry.ai_service

    try:
        domain = Domain.objects.get(id=domain_id)
    except Domain.DoesNotExist:
        return "Domain not found."

    nodes = ConceptNode.objects.filter(domain=domain)
    actions_taken = []

    for node in nodes:
        # Search all domains (domain_id=None) to find potential cross-domain analogies
        search_text = f"Title: {node.title}\nContext: {node.focus_hint}\nNarrative: {node.narrative_content}"
        neighbors_docs = grips_service.get_grips_context(search_text, domain_id=None, k=5)

        for d in neighbors_docs:
            match_domain_id = d.metadata.get("domain_id")
            match_concept_id = d.metadata.get("concept_id")

            # Skip if it's in the exact same domain, or we have a missing ID
            if not match_concept_id or match_domain_id == domain.id:
                continue

            try:
                neighbor_node = ConceptNode.objects.get(id=match_concept_id)
            except ConceptNode.DoesNotExist:
                continue

            # Skip if already linked
            if KnowledgeEdge.objects.filter(source=node, target=neighbor_node).exists() or \
               KnowledgeEdge.objects.filter(source=neighbor_node, target=node).exists():
                continue

            from metacognition.models import CognitiveBlueprint
            from metacognition.tasks import task_run_blueprint_async
            
            try:
                bp = CognitiveBlueprint.objects.get(name="EvaluateCrossDomain")
            except CognitiveBlueprint.DoesNotExist:
                logger.error("EvaluateCrossDomain blueprint not found.")
                continue

            task_prompt = (
                f"Evaluate these two concepts from DIFFERENT domains and determine if they are fundamentally analogous.\n\n"
                f"Domain A: {domain.name}\n"
                f"Concept A (ID: {node.id}):\n"
                f"Title: {node.title}\n"
                f"Narrative: {node.narrative_content}\n\n"
                f"Domain B: {neighbor_node.domain.name}\n"
                f"Concept B (ID: {neighbor_node.id}):\n"
                f"Title: {neighbor_node.title}\n"
                f"Narrative: {neighbor_node.narrative_content}\n\n"
                f"Use the `evaluate_cross_domain_tool` to record your decision with source_id={node.id} and target_id={neighbor_node.id}."
            )

            task_run_blueprint_async.delay(bp.id, task_prompt, None, None)
            actions_taken.append(f"Queued cross-domain blueprint for '{node.title}' & '{neighbor_node.title}'")

    return f"Level 3 Complete. Created {len(actions_taken)} cross-domain edges."

@shared_task
def sweep_unlinted_concepts():
    """
    Periodic task to sweep for concepts that need linting.
    """
    # Batch to 20 at a time to keep the queue flowing nicely
    nodes = ConceptNode.objects.filter(needs_linting=True).order_by('last_linted_at')[:20]
    
    if not nodes:
        return "No concepts currently require linting."
        
    for node in nodes:
        node.needs_linting = False
        node.save(update_fields=['needs_linting'])
        task_lint_concept_node.delay(node.id)
        
    return f"Queued {nodes.count()} concepts for automated linting."

@shared_task
def sweep_dirty_edges():
    """
    Periodic task to sweep for edges that contain placeholder scaffold text ('Concept A')
    and trigger the Metacognition LintGripsEdge blueprint.
    """
    from metacognition.models import CognitiveBlueprint
    from metacognition.tasks import task_run_blueprint_async
    
    dirty_edges = KnowledgeEdge.objects.filter(justification__icontains='Concept A', needs_linting=True)[:20]
    
    if not dirty_edges:
        return "No dirty edges currently require linting."
        
    try:
        bp = CognitiveBlueprint.objects.get(name="LintGripsEdge")
    except CognitiveBlueprint.DoesNotExist:
        return "LintGripsEdge blueprint not found."
        
    for edge in dirty_edges:
        edge.needs_linting = False
        edge.save(update_fields=['needs_linting'])
        
        task_prompt = (
            f"Rewrite this justification:\n\n{edge.justification}\n\n"
            f"The edge is between '{edge.source.title}' and '{edge.target.title}'.\n"
            f"Edge ID: {edge.id}"
        )
        task_run_blueprint_async.delay(bp.id, task_prompt, None, None)
        
    return f"Triggered linting blueprint for {dirty_edges.count()} dirty edges."



@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def task_index_concept_node(self, node_id: int):
    """
    Asynchronously indexes a ConceptNode into PGVector for semantic search.
    Hooked to ConceptNode post_save signal.
    """
    try:
        node = ConceptNode.objects.get(id=node_id)
    except ConceptNode.DoesNotExist:
        return f"Node {node_id} not found for indexing."

    from llm_api.apps import service_registry
    if service_registry.grips_service:
        service_registry.grips_service.index_concept_node(node)
        return f"Indexed '{node.title}' (ID: {node_id})"
    return "Grips service not initialized."