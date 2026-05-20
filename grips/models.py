from django.db import models
from django.urls import reverse
from django.utils.html import format_html
from django.templatetags.static import static
from llm_api.models import LocalAIModel


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


class PromptRecipies(models.Model):
    """
    Specific prompt ingredients and harness designs optimized for this domain.
    If the model consulting the wiki is large, this tells it *how* to use the data.
    """
    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="harnesses")
    name = models.CharField(max_length=255)
    system_prompt_template = models.TextField(
        help_text="Template injecting Domain context. Use {{ concepts }} to inject retrieved ConceptNodes."
    )
    recommended_model = models.ForeignKey(
        LocalAIModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The model this harness was optimized for."
    )

    needs_linting = models.BooleanField(default=True,
                                        help_text="Flagged when updated so the background linter can verify it.")
    last_linted_at = models.DateTimeField(null=True, blank=True)
    linting_report = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.name} ({self.domain.name})"
        
    def get_admin_url(self):
        return reverse("admin:grips_promptrecipies_change", args=[self.id])


class CeleryStatus(models.Model):
    """Dummy model to hook the Celery dashboard into the admin."""
    class Meta:
        managed = False
        verbose_name_plural = "Celery Status Dashboard"