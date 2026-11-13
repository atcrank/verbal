# WS5: Grobid — Semantic Citation Enrichment

## Goal

Enhance the grobid_client to:
1. Classify citation relationships using the Grips `KnowledgeEdge.RelationshipTypes` vocabulary.
2. Generate LLM citation summaries explaining *why* a citation was made.
3. Auto-create Grips `KnowledgeEdge` entries from classified citations.
4. Implement Reference Resolution — link ghost References to Documents uploaded later.
5. Add BibTeX export capability.
6. Improve the Citation Graph Explorer in admin.

## Prerequisites

- No hard dependencies on other workstreams. Can run in parallel with WS3/WS4.
- Uses the Grips `KnowledgeEdge.RelationshipTypes` which already exist — no model changes needed in grips.

## Key Files

| File | Role |
|------|------|
| `grobid_client/models.py` | `Reference` (line ~5), `Citation` (line ~51), `CitationGraphExplorer` (line ~75) |
| `grobid_client/tasks.py` | `task_extract_grobid_metadata()` — the main extraction pipeline. `grobid_tei_to_semantic_chunks()` — semantic chunking |
| `grobid_client/admin.py` | Admin configuration including Citation Graph Explorer |
| `grobid_client/api.py` | `process_pdf_with_grobid()` — Docker GROBID client |
| `grobid_client/tests.py` | Minimal — just `from django.test import TestCase` |
| `grips/models.py` | `KnowledgeEdge` (line ~100) with `RelationshipTypes`: DEPENDS_ON, INCLUDES, EXEMPLIFIES, RELATED_TO |
| `grips/tasks.py` | Grips curation tasks |
| `background_resources/models.py` | `Document` model |

## Current Grobid State

### What exists
- PDF → GROBID → TEI XML parsing with 3-algorithm cascade (deterministic → heuristic → LLM fallback)
- `Reference` model with full bibliographic metadata (title, authors, year, DOI, journal, etc.)
- `Citation` model with `source_reference`, `target_reference`, `raw_reference_string`, `context_text`
- Ghost References: when a cited work isn't in our library, a Reference with `document=None` is created
- `CitationGraphExplorer` — admin-hooked dummy model for graph visualization (currently a stub)
- Semantic chunking from TEI XML sections for RAG

### What's missing
- No citation relationship classification (HOW the citation relates)
- No citation summary (WHY the citation was made)
- No integration with Grips knowledge graph
- Ghost References are never linked when matching Documents are uploaded later
- No BibTeX export
- Citation Graph Explorer is a stub

## Design Decisions (All Resolved)

1. **Citation taxonomy**: Use the existing Grips `KnowledgeEdge.RelationshipTypes`:
   - `DEPENDS_ON` — "Depends On (Causal / Prerequisite)" — cited as foundational work, method dependency
   - `INCLUDES` — "Includes / Comprises" — cited as a component of a larger framework
   - `EXEMPLIFIES` — "Exemplifies / Instantiates" — cited as an example or case study
   - `RELATED_TO` — "Is Related To (Catchall)" — alternatives, comparisons, tangential references

   This aligns citations with the knowledge graph vocabulary, enabling direct edge creation.

2. **Grips integration path**: ConceptNodes trace to Chunks → Documents → References. When a Citation has classified relationship and both source/target References have linked Documents, and those Documents have associated ConceptNodes (via their chunks), auto-create a `KnowledgeEdge` between the ConceptNodes.

3. **Reference Resolution**: Match by DOI first (exact), then by title similarity (fuzzy). Trigger on `Document.post_save` signal.

## Changes Required

### A. `grobid_client/models.py` — Citation Enrichment

1. **Add fields to `Citation`**:
   ```python
   relationship_type = models.CharField(
       max_length=50, 
       choices=[
           ('DEPENDS_ON', 'Depends On (Causal / Prerequisite)'),
           ('INCLUDES', 'Includes / Comprises (Part-Whole)'),
           ('EXEMPLIFIES', 'Exemplifies / Instantiates (Idea-Example)'),
           ('RELATED_TO', 'Is Related To (Catchall)'),
       ],
       blank=True,
       help_text="Relationship type using the Grips KnowledgeEdge vocabulary."
   )
   citation_summary = models.TextField(
       blank=True,
       help_text="LLM-generated one-sentence summary of why this citation was made in context."
   )
   ```

2. **Add BibTeX method to `Reference`**:
   ```python
   def to_bibtex(self) -> str:
       """Generate a BibTeX-formatted entry from reference metadata."""
       # Determine entry type
       entry_type = "article" if self.journal else "misc"
       
       # Generate citation key: AuthorYear format
       first_author = self.authors.split(",")[0].strip().split()[-1] if self.authors else "Unknown"
       year = self.year[:4] if self.year else "XXXX"
       key = f"{first_author}{year}"
       
       fields = []
       if self.title: fields.append(f"  title = {{{self.title}}}")
       if self.authors: fields.append(f"  author = {{{self.authors}}}")
       if self.year: fields.append(f"  year = {{{self.year}}}")
       if self.journal: fields.append(f"  journal = {{{self.journal}}}")
       if self.publisher: fields.append(f"  publisher = {{{self.publisher}}}")
       if self.doi: fields.append(f"  doi = {{{self.doi}}}")
       if self.volume: fields.append(f"  volume = {{{self.volume}}}")
       if self.issue: fields.append(f"  number = {{{self.issue}}}")
       if self.pages: fields.append(f"  pages = {{{self.pages}}}")
       
       return f"@{entry_type}{{{key},\n" + ",\n".join(fields) + "\n}"
   ```

### B. `grobid_client/tasks.py` — Citation Classification

After creating each `Citation` in `task_extract_grobid_metadata()`, if `context_text` is non-empty, classify the relationship:

```python
from pydantic import BaseModel, Field

class CitationClassification(BaseModel):
    relationship_type: str = Field(
        description="One of: DEPENDS_ON, INCLUDES, EXEMPLIFIES, RELATED_TO"
    )
    citation_summary: str = Field(
        description="One sentence explaining why the source document cites the target."
    )

def classify_citation(citation, ai_service):
    """Classify a citation's relationship type and generate a summary."""
    if not citation.context_text:
        return
    
    source_title = citation.source_reference.title or "Source Document"
    target_title = citation.target_reference.title if citation.target_reference else "Cited Work"
    
    prompt = f"""Analyze this citation context and classify the relationship.

Source document: "{source_title}"
Cited work: "{target_title}"

Citation context (the paragraph where the citation appears):
{citation.context_text[:2000]}

Classify the relationship as one of:
- DEPENDS_ON: The source depends on or builds upon the cited work (foundational method, prerequisite theory)
- INCLUDES: The cited work is a component or part of a larger framework discussed in the source
- EXEMPLIFIES: The cited work is used as an example, case study, or instantiation
- RELATED_TO: The cited work is compared, contrasted, or tangentially referenced

Also write a one-sentence summary of WHY this citation was made."""

    result = ai_service.generate_outline(
        messages=[{"role": "user", "content": prompt}],
        response_schema=CitationClassification,
        max_new_tokens=200,
        log_kwargs={"skip_log": True}  # Don't clutter PromptResponseLog with classification calls
    )
    
    if isinstance(result, CitationClassification) or (isinstance(result, dict) and "relationship_type" in result):
        if isinstance(result, dict):
            result = CitationClassification.model_validate(result)
        
        citation.relationship_type = result.relationship_type
        citation.citation_summary = result.citation_summary
        citation.save()
```

Call `classify_citation()` after each `Citation.objects.get_or_create()` in `task_extract_grobid_metadata()`.

### C. Grips Integration — Auto-create KnowledgeEdges

After classifying a citation, check if both source and target References have linked Documents with ConceptNodes:

```python
def create_grips_edge_from_citation(citation):
    """Auto-create a KnowledgeEdge if both references have linked ConceptNodes."""
    if not citation.relationship_type or not citation.target_reference:
        return
    
    from grips.models import ConceptNode, KnowledgeEdge
    from background_resources.models import RAGChunk
    
    # Find ConceptNodes linked to source document
    source_doc = citation.source_reference.document
    target_doc = citation.target_reference.document if citation.target_reference else None
    
    if not source_doc or not target_doc:
        return
    
    # ConceptNodes are linked to documents via chunks
    source_chunks = RAGChunk.objects.filter(document=source_doc)
    target_chunks = RAGChunk.objects.filter(document=target_doc)
    
    # Find ConceptNodes that reference these chunks (via their source_chunks)
    source_concepts = ConceptNode.objects.filter(
        source_chunks__in=source_chunks
    ).distinct()
    target_concepts = ConceptNode.objects.filter(
        source_chunks__in=target_chunks
    ).distinct()
    
    if not source_concepts.exists() or not target_concepts.exists():
        return
    
    # Create edge between the first matching concepts
    # (More sophisticated matching could use title similarity)
    source_concept = source_concepts.first()
    target_concept = target_concepts.first()
    
    KnowledgeEdge.objects.get_or_create(
        source=source_concept,
        target=target_concept,
        relationship_type=citation.relationship_type,
        defaults={
            "justification": citation.citation_summary or f"Citation relationship from {citation.source_reference.title}"
        }
    )
```

**Note**: The `ConceptNode.source_chunks` relationship may need verification — check the actual ConceptNode model to confirm how chunks link to concepts. The link may be via `Domain` or via metadata. Adjust the query accordingly.

### D. Reference Resolution

#### New signal in `grobid_client/tasks.py` or a new `grobid_client/signals.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from background_resources.models import Document
from .models import Reference

@receiver(post_save, sender=Document)
def resolve_ghost_references(sender, instance, created, **kwargs):
    """When a Document is uploaded, check if it matches any ghost References."""
    if not created:
        return
    
    # 1. Match by DOI (exact)
    if hasattr(instance, 'grobid_metadata') and instance.grobid_metadata and instance.grobid_metadata.doi:
        # The document itself has a DOI from its own Grobid processing
        pass
    
    # Match ghost references (document=None) to this new document
    ghosts = Reference.objects.filter(document__isnull=True)
    
    # DOI match (most reliable)
    # We need to extract DOI from the new document — this happens async via Grobid.
    # So this signal fires too early. Instead, hook into task_extract_grobid_metadata completion.
    
    # Title match (fuzzy)
    from difflib import SequenceMatcher
    doc_title = instance.title.lower().strip()
    
    for ghost in ghosts:
        ghost_title = (ghost.title or "").lower().strip()
        if not ghost_title:
            continue
        ratio = SequenceMatcher(None, doc_title, ghost_title).ratio()
        if ratio > 0.85:
            ghost.document = instance
            ghost.save()
            break
```

**Better approach**: Hook reference resolution into the end of `task_extract_grobid_metadata()` — after the source Reference is created with its DOI, check if any ghost References share that DOI or have high title similarity.

```python
# At the end of task_extract_grobid_metadata, after source_ref is created:
def resolve_references_for_document(source_ref):
    """Match this reference's DOI/title against ghost references."""
    ghosts = Reference.objects.filter(document__isnull=True).exclude(id=source_ref.id)
    
    # DOI exact match
    if source_ref.doi:
        doi_matches = ghosts.filter(doi=source_ref.doi)
        for ghost in doi_matches:
            ghost.document = source_ref.document
            ghost.save()
    
    # Also check if THIS document matches any ghost (by DOI of the document's own reference)
    # And check title similarity for remaining ghosts
    if source_ref.title:
        from difflib import SequenceMatcher
        source_title = source_ref.title.lower().strip()
        for ghost in ghosts.filter(document__isnull=True):  # Re-query to exclude just-linked ones
            ghost_title = (ghost.title or "").lower().strip()
            if ghost_title and SequenceMatcher(None, source_title, ghost_title).ratio() > 0.85:
                ghost.document = source_ref.document
                ghost.save()
```

### E. `grobid_client/admin.py` — Improvements

1. **Citation admin**: Show `relationship_type`, `citation_summary` in list_display and make them filterable.

2. **Reference admin**: Add "Export BibTeX" action:
   ```python
   @admin.action(description="Export selected references as BibTeX")
   def export_bibtex(modeladmin, request, queryset):
       from django.http import HttpResponse
       bibtex_entries = [ref.to_bibtex() for ref in queryset]
       content = "\n\n".join(bibtex_entries)
       response = HttpResponse(content, content_type="text/plain")
       response['Content-Disposition'] = 'attachment; filename="references.bib"'
       return response
   ```

3. **CitationGraphExplorer**: Improve from stub to functional — at minimum, render a table of citation chains with relationship types and summaries. Full graph visualization (D3.js/Cytoscape) is a stretch goal.

## Testing Requirements

All tests use the venv at `../../py313/bin/python`.

### New Tests (add to `grobid_client/tests.py`)

1. **`test_citation_classification`**: Mock AI service, provide a Citation with context_text, verify `classify_citation()` sets `relationship_type` and `citation_summary`.

2. **`test_to_bibtex`**: Create a Reference with full metadata, verify `to_bibtex()` produces valid BibTeX syntax.

3. **`test_reference_resolution_by_doi`**: Create a ghost Reference with DOI, create a Document, run `resolve_references_for_document()` with matching DOI, verify the ghost gets linked.

4. **`test_reference_resolution_by_title`**: Create a ghost Reference with title "Attention Is All You Need", create a source Reference with title "Attention is All You Need" (case difference), verify fuzzy match links them.

5. **`test_grips_edge_creation`**: Create Citation with classified relationship, with both source and target References linked to Documents that have ConceptNodes, verify a `KnowledgeEdge` is created.

### Run existing tests

```bash
../../py313/bin/python manage.py test grobid_client -v2
```

## Verification Checklist

- [ ] `Citation` model has `relationship_type` and `citation_summary` fields
- [ ] `classify_citation()` correctly classifies using Grips vocabulary
- [ ] Grips `KnowledgeEdge` auto-created from classified citations (when ConceptNodes exist)
- [ ] Ghost References linked when matching Documents are uploaded (DOI match)
- [ ] Ghost References linked by title similarity (> 0.85 ratio)
- [ ] `Reference.to_bibtex()` produces valid BibTeX
- [ ] Admin "Export BibTeX" action downloads a .bib file
- [ ] Citation admin shows relationship_type and citation_summary
- [ ] Migration created for new fields
- [ ] All tests pass
