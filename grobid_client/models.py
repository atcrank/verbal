from django.db import models
from background_resources.models import Document


class Reference(models.Model):
    """Parsed metadata for a specific document in our library."""
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name='grobid_metadata', null=True, blank=True)

    tei_xml = models.TextField(blank=True, null=True, help_text="Cached output from Grobid")

    title = models.CharField(max_length=1000, blank=True, null=True)
    authors = models.TextField(blank=True, help_text="Comma separated list of parsed authors")
    abstract = models.TextField(blank=True)
    
    # Extended Grobid Bibliography Fields
    journal = models.CharField(max_length=1000, blank=True, null=True, help_text="Journal, conference, or publication name")
    publisher = models.CharField(max_length=255, blank=True, null=True)
    year = models.CharField(max_length=200, blank=True, null=True)
    publication_date = models.CharField(max_length=50, blank=True, null=True)
    volume = models.CharField(max_length=50, blank=True, null=True)
    issue = models.CharField(max_length=50, blank=True, null=True)
    pages = models.CharField(max_length=50, blank=True, null=True)
    doi = models.CharField(max_length=100, blank=True, null=True)
    extended_metadata = models.JSONField(default=dict, blank=True, null=True,
                                         help_text="Extended structured metadata from TEI XML")

    parsed_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        # Explicitly truncate strings to prevent PostgreSQL DataErrors during programmatic inserts
        if self.title and len(str(self.title)) > 996: self.title = str(self.title)[:996] + "..."
        if self.journal and len(str(self.journal)) > 996: self.journal = str(self.journal)[:996] + "..."
        if self.publisher and len(str(self.publisher)) > 251: self.publisher = str(self.publisher)[:251] + "..."
        if self.year:
            self.year = str(self.year)
            if len(self.year) > 196: self.year = self.year[:196] + "..."
        if self.publication_date and len(str(self.publication_date)) > 46: self.publication_date = str(self.publication_date)[:46] + "..."
        if self.volume and len(str(self.volume)) > 46: self.volume = str(self.volume)[:46] + "..."
        if self.issue and len(str(self.issue)) > 46: self.issue = str(self.issue)[:46] + "..."
        if self.pages and len(str(self.pages)) > 46: self.pages = str(self.pages)[:46] + "..."
        if self.doi and len(str(self.doi)) > 96: self.doi = str(self.doi)[:96] + "..."

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        if self.title:
            return self.title
        if self.document:
            return f"Metadata for: {self.document.title}"
        return f"Reference #{self.id}"


class Citation(models.Model):
    """A directed edge representing one document citing another."""
    source_reference = models.ForeignKey(
        Reference, on_delete=models.CASCADE, related_name='outgoing_citations',
        help_text="The document that contains the bibliography."
    )

    target_reference = models.ForeignKey(
        Reference, on_delete=models.SET_NULL, null=True, blank=True, related_name='incoming_citations',
        help_text="The cited reference."
    )

    # Grobid extracts incredible detail per citation
    raw_reference_string = models.TextField(help_text="The raw bibliography string.")
    context_text = models.TextField(blank=True, null=True, help_text="The sentence(s) in the source document where this citation occurs.")

    def __str__(self):
        source_title = self.source_reference.title or f"Ref #{self.source_reference.id}"
        if self.target_reference:
            target_title = self.target_reference.title or f"Ref #{self.target_reference.id}"
            return f"[{source_title}] cites [{target_title}]"
        return f"[{source_title}] cites [Unlinked Reference]"


class CitationGraphExplorer(models.Model):
    """Dummy model to hook the Graph Explorer into the admin."""

    class Meta:
        managed = False
        verbose_name_plural = "Citation Graph Explorer"