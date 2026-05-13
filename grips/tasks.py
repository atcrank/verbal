from celery import shared_task
from llm_api.apps import service_registry
from background_resources.models import Document, RAGChunk
from .models import ConceptNode, Domain
from pydantic import BaseModel, Field, field_validator
from typing import List


class StructuredClaim(BaseModel):
    subject: str
    predicate: str
    object: str

class ConceptDraft(BaseModel):
    thought_process: str = Field(description="Think step-by-step to plan the entry. Identify and resolve ambiguities based on the title and focus hint.")
    narrative: str = Field(description="The dense, Markdown-formatted explanation unifying the concept.")
    claims: List[StructuredClaim] = Field(description="Atomic, symbolically computable facts derived from the narrative.")

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
       - Example Claim: {{"subject": "Fire Engine", "predicate": "is used for", "object": "Vehicle Rescue"}}
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


from typing import List
from pydantic import BaseModel, Field
from background_resources.models import Document
from django.utils import timezone
from .models import KnowledgeEdge
import re


class SubConcept(BaseModel):
    title: str = Field(description="Name of the concept")
    focus_hint: str = Field(description="Context or intent for this concept")
    summary: str = Field(description="A brief summary of what the document says about this concept")


class DocumentDigest(BaseModel):
    overall_summary: str = Field(description="High-level summary of the entire document")
    concept_nodes: List[SubConcept] = Field(description="Distinct concepts identified in the document")

class BatchConceptExtraction(BaseModel):
    concept_nodes: List[SubConcept] = Field(description="Distinct concepts identified in this section of the document")


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def task_digest_corpus_level_1(self, domain_id: int, document_id: int):
    """Level 1: Overall summary for a document with concept nodes (Extended TOC)."""
    try:
        domain = Domain.objects.get(id=domain_id)
        doc = Document.objects.get(id=document_id)
    except Exception as e:
        return f"Error loading domain/doc: {e}"

    ai_service = service_registry.ai_service
    rag_service = service_registry.rag_service

    # Retrieve chunks (grab first ~15 chunks for the high-level digest to avoid token bloat)

    chunks, existing_ids = rag_service.convert_chunk_store_document(doc)
    print("existing_ids")

    if not chunks and existing_ids:
        chunks = RAGChunk.objects.filter(chunk_id__in=existing_ids)
    
    if not chunks:
        return f"No chunks could be made or found for document {doc.title}"

    batch_size = 15
    all_sub_concepts = []
    overall_summary = "No summary generated."
    
    print(f"Starting iterative Map-Reduce digest for '{doc.title}' ({len(chunks)} chunks)...")

    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i + batch_size]
        batch_text = "\n\n".join([c.page_content if hasattr(c, 'page_content') else c.text_content for c in batch_chunks])
        
        if i == 0:
            # First Batch: Get the overall summary AND the first set of concepts
            prompt = f"""
            You are an expert ontologist and systems analyst. Digest the following opening section of a document into the domain of '{domain.name}'.
            Provide an overall summary of the document based on this introduction, and extract its key concepts into a clear summary and explanation.
            
            Document Title: {doc.title}
            
            Content Section:
            {batch_text}
            """
            result = ai_service.generate_outline(
                messages=[{"role": "user", "content": prompt}],
                response_schema=DocumentDigest,
                max_new_tokens=2500
            )
            if isinstance(result, dict):
                if "error" in result:
                    overall_summary = f"Summary generation failed: {result['details']}"
                    continue
                result = DocumentDigest.model_validate(result)
            elif isinstance(result, str):
                result = DocumentDigest.model_validate_json(result)
                
            overall_summary = result.overall_summary
            all_sub_concepts.extend(result.concept_nodes)
            
        else:
            # Subsequent Batches: Only extract sub-concepts to save time/tokens
            prompt = f"""
            You are an expert ontologist. Extract the key concepts from this section of the document into the domain of '{domain.name}'.
            
            Document Title: {doc.title}
            
            Content Section:
            {batch_text}
            """
            result = ai_service.generate_outline(messages=[{"role": "user", "content": prompt}], response_schema=BatchConceptExtraction, max_new_tokens=1500)
            if isinstance(result, dict):
                if "error" in result:
                    print(f"Batch extraction failed: {result['details']}")
                    continue
                result = BatchConceptExtraction.model_validate(result)
            elif isinstance(result, str):
                result = BatchConceptExtraction.model_validate_json(result)
                
            all_sub_concepts.extend(result.concept_nodes)

    safe_title = re.sub(r'[^a-zA-Z0-9]', '-', doc.title.lower())[:50]
    root_node, _ = ConceptNode.objects.get_or_create(
        domain=domain,
        slug=f"doc-{doc.id}-{safe_title}",
        defaults={
            "title": f"Doc: {doc.title[:200]}",
            "focus_hint": "Level 1 Document Summary",
            "narrative_content": overall_summary,
            "needs_linting": True
        }
    )
    print("Document concept_node created.")

    for idx, concept in enumerate(all_sub_concepts):
        safe_concept = re.sub(r'[^a-zA-Z0-9]', '-', concept.title.lower())[:50]
        child_node, _ = ConceptNode.objects.get_or_create(
            domain=domain,
            slug=f"doc-{doc.id}-c{idx}-{safe_concept}",
            defaults={
                "title": concept.title,
                "focus_hint": f"From Doc: {doc.title[:50]} - {concept.focus_hint}",
                "narrative_content": concept.summary,
                "needs_linting": True
            }
        )
        KnowledgeEdge.objects.get_or_create(
            source=root_node,
            target=child_node,
            relationship_type='INCLUDES',
            defaults={"justification": "Level 1 Extended TOC Extraction"}
        )
        print("created and linked sub-concepts.")

    return f"Level 1 Digest complete for {domain.name}"


class LintingReportSchema(BaseModel):
    is_valid: bool = Field(description="True if narrative has no contradictions or style issues.")
    style_violations: List[str] = Field(description="List of domain style guide violations")
    contradictions: List[str] = Field(description="List of contradictions within the narrative")
    missing_cross_references: List[str] = Field(description="Concepts that should be linked")
    suggested_fixes: str = Field(description="Suggested rewrites to fix issues")


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def task_lint_concept_node(self, node_id: int):
    """Evaluates a ConceptNode against the Domain's style guide and flags contradictions."""
    try:
        node = ConceptNode.objects.get(id=node_id)
    except ConceptNode.DoesNotExist:
        return "Node not found."

    ai_service = service_registry.ai_service

    prompt = f"""
     You are a strict knowledge graph linter. Evaluate the following ConceptNode narrative.

     Domain: {node.domain.name}
     Style Guide: {node.domain.style_guide or 'Maintain objective, clear, encyclopedic tone.'}
     Title: {node.title}
     Focus Hint: {node.focus_hint}
     Narrative: {node.narrative_content}
     """

    result = ai_service.generate_outline(
        messages=[{"role": "user", "content": prompt}],
        response_schema=LintingReportSchema,
        max_new_tokens=1500
    )
    if isinstance(result, dict):
        if "error" in result:
            return f"Linting failed for '{node.title}': {result['details']}"
        result = LintingReportSchema.model_validate(result)
    elif isinstance(result, str):
        result = LintingReportSchema.model_validate_json(result)

    node.linting_report = result.model_dump()
    node.needs_linting = not result.is_valid
    node.last_linted_at = timezone.now()
    node.save(update_fields=['linting_report', 'needs_linting', 'last_linted_at'])
    return f"Linted '{node.title}': Valid={result.is_valid}"


@shared_task
def task_digest_corpus_level_2(domain_id: int):
    # Placeholder for Level 2: Consolidate related ConceptNodes
    # 1. Fetch all Level 1 nodes in the domain
    # 2. Use LLM to group semantic duplicates
    # 3. Merge narratives and update KnowledgeEdges
    return "Level 2 Digest stub executed."


@shared_task
def task_digest_corpus_level_3(domain_id: int):
    # Placeholder for Level 3: Cross-domain concept joins
    # 1. Look for orphan sub-concepts
    # 2. Query other domains for semantic matches
    # 3. Create RELATED_TO edges bridging domains
    return "Level 3 Digest stub executed."