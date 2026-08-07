from django.db import models
from django.urls import reverse
from django.contrib.postgres.fields import ArrayField
from django.utils.html import format_html
from django.templatetags.static import static
from llm_api.models import LocalAIModel
from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver


class Domain(models.Model):
    """High-level knowledge areas (e.g., 'Quantum Physics', 'HR Policies')."""
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(help_text="Broad description of this knowledge domain.")
    style_guide = models.TextField(
        blank=True,
        help_text=format_html(
            'Instructions for the LLM on how to format or reason about concepts in this domain. '
            '<a href="{url}" target="_blank">View the style guide docs</a>.',
            url=static("docs/style_guides.html")
        )
    )
    documents = models.ManyToManyField(
        'background_resources.Document',
        related_name='domains',
        blank=True,
        help_text="Documents that form the foundational knowledge corpus for this domain."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def get_admin_url(self):
        return reverse("admin:grips_domain_change", args=[self.id])


class ConceptNode(models.Model):
    """The core 'Wiki Page' - a dense, cross-referenced knowledge element."""
    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="concepts")
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255, help_text="Used for internal [[wiki-linking]]")
    focus_hint = models.CharField(
        max_length=500, 
        blank=True, 
        help_text="Optional hint to disambiguate the topic for the AI (e.g., 'Focus purely on physical extrication, not vehicle deployment')."
    )
    source_chunk = models.ForeignKey('background_resources.RAGChunk', on_delete=models.SET_NULL, null=True, blank=True, related_name='sourced_concepts', help_text="The Primary RAGChunk which is summarised in this concept.")

    # Human/Model readable narrative
    narrative_content = models.TextField(
        blank=True, null=True,
        help_text=format_html(
            'The dense, Markdown-formatted explanation. <a href="{url}" target="_blank">Markdown Tips & Tricks</a>',
            url=static("docs/markdown_tips.html")
        )
    )

    # The Symbolically Computable Format
    structured_claims = models.JSONField(
        default=list,
        help_text="List of atomic claims (e.g., [{'subject': 'A', 'predicate': 'is', 'object': 'B'}]) for programmatic linting.",
        blank=True,
        null=True
    )

    class ConceptNodeFlags(models.TextChoices):
        NEEDS_CITATION = 'needs_citation', 'Needs Citation'
        STYLE_VIOLATION = 'style_violation', 'Style Violation'
        NEEDS_CLARIFICATION = 'needs_clarification', 'Needs Clarification'
        ORPHANED = 'orphaned', 'Orphaned (Missing Links)'

    issue_flags = ArrayField(
        models.CharField(max_length=32, choices=ConceptNodeFlags.choices),
        default=list,
        blank=True,
        help_text="Wikipedia-style issue tags indicating problems with the narrative."
    )

    # State tracking for the "Linters"
    needs_linting = models.BooleanField(default=True,
                                        help_text="Flagged when updated so the background linter can verify it.")
    last_linted_at = models.DateTimeField(null=True, blank=True)
    linting_report = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.title

    def get_admin_url(self):
        return reverse("admin:grips_conceptnode_change", args=[self.id])

    def get_wiki_url(self):
        # Placeholder path. Once you build a frontend view, change this to use reverse()
        return f"/wiki/concepts/{self.slug}/"

@receiver(pre_delete, sender=ConceptNode)
def delete_concept_vector(sender, instance, **kwargs):
     """When a ConceptNode is deleted, ensure its vector is removed from PGVector."""
     from llm_api.apps import service_registry
     grips_service = service_registry.grips_service
     if grips_service:
         try:
             grips_service.db.delete([str(instance.id)])
         except Exception:
             pass

@receiver(post_save, sender=ConceptNode)
def index_concept_vector(sender, instance, created, **kwargs):
    """When a ConceptNode is saved, asynchronously index it into PGVector."""
    from grips.tasks import task_index_concept_node
    # Delay to celery so we don't block the web request
    task_index_concept_node.delay(instance.id)

class KnowledgeEdge(models.Model):
    """Defines exact, computable relationships between ConceptNodes."""

    class RelationshipTypes(models.TextChoices):
        DEPENDS_ON = 'DEPENDS_ON', 'Depends On (Causal / Prerequisite)'
        INCLUDES = 'INCLUDES', 'Includes / Comprises (Part-Whole)'
        EXEMPLIFIES = 'EXEMPLIFIES', 'Exemplifies / Instantiates (Idea-Example)'
        RELATED_TO = 'RELATED_TO', 'Is Related To (Catchall) - alternatives, constraints, exclusions, negations'

    source = models.ForeignKey(ConceptNode, on_delete=models.CASCADE, related_name="outgoing_edges")
    target = models.ForeignKey(ConceptNode, on_delete=models.CASCADE, related_name="incoming_edges")
    relationship_type = models.CharField(max_length=50, choices=RelationshipTypes.choices)

    # Allows an LLM to explain *why* this relationship exists, or to specify the 
    # specific catchall relation (e.g., 'is_alternative_to', 'constrains/modifies', 'is_excluded_by')
    justification = models.TextField(blank=True, help_text="Explanation for the relatioship or specific catchall relationship (e.g., is_alternative_to)")

    needs_linting = models.BooleanField(default=True,
                                        help_text="Flagged when updated so the background linter can verify it.")
    last_linted_at = models.DateTimeField(null=True, blank=True)
    linting_report = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ('source', 'target', 'relationship_type')

    def __str__(self):
        return f"{self.source.slug} --[{self.relationship_type}]--> {self.target.slug}"





class CeleryStatus(models.Model):
    """Dummy model to hook the Celery dashboard into the admin."""
    class Meta:
        managed = False
        verbose_name_plural = "Celery Status Dashboard"