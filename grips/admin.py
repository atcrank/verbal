import os
from django.conf import settings
from django.urls import path
from django.template.response import TemplateResponse
from django.contrib import admin, messages
from django.utils.safestring import mark_safe
from .models import Domain, ConceptNode, KnowledgeEdge, CeleryStatus
from .tasks import (
    generate_concept_narrative,
    task_lint_concept_node,
    task_digest_corpus_level_1,
    task_digest_corpus_level_2,
    task_digest_corpus_level_3,
)
from background_resources.models import Document


@admin.action(description="Generate narrative content for selected concepts")
def generate_narrative_action(modeladmin, request, queryset):
    """Admin action to trigger task for generating narrative content."""
    count = 0
    for node in queryset:
        generate_concept_narrative.enqueue(node.id)
        count += 1
    modeladmin.message_user(request, f"Queued content generation for {count} concept(s).", level=messages.SUCCESS)


@admin.action(description="Level 1 Digest: Ingest Domain Documents")
def digest_corpus_level_1_action(modeladmin, request, queryset):
    """Admin action to trigger the Level 1 corpus digestion task."""
    count = 0
    for domain in queryset:
        doc_ids = list(domain.documents.values_list('id', flat=True))
        for doc_id in doc_ids:
            task_digest_corpus_level_1.enqueue(domain.id, doc_id)
            count += 1
    modeladmin.message_user(
        request,
        f"Queued {count} document(s) across selected domain(s) for Level 1 digestion.",
        level=messages.SUCCESS
    )


@admin.action(description="Level 2 Digest: In-Domain Synthesis (Unify Overlaps)")
def digest_corpus_level_2_action(modeladmin, request, queryset):
    """Admin action to trigger Level 2 synthesis for selected domains."""
    for domain in queryset:
        task_digest_corpus_level_2.enqueue(domain.id)
    modeladmin.message_user(
        request,
        f"Queued {queryset.count()} domain(s) for Level 2 in-domain synthesis.",
        level=messages.SUCCESS
    )


@admin.action(description="Level 3 Digest: Synthesize Cross-Domain Joins")
def digest_corpus_level_3_action(modeladmin, request, queryset):
    """Admin action to trigger the Level 3 cross-domain digestion task."""
    for domain in queryset:
        task_digest_corpus_level_3.enqueue(domain.id)
    modeladmin.message_user(
        request,
        f"Queued {queryset.count()} domain(s) for Level 3 cross-domain synthesis.",
        level=messages.SUCCESS
    )


@admin.action(description="Run Automated Linting on selected concepts")
def lint_concepts_action(modeladmin, request, queryset):
    """Admin action to trigger the automated LLM linting task."""
    count = 0
    for node in queryset:
        task_lint_concept_node.enqueue(node.id)
        count += 1
    modeladmin.message_user(request, f"Queued {count} concept(s) for automated linting.", level=messages.SUCCESS)


@admin.register(ConceptNode)
class ConceptNodeAdmin(admin.ModelAdmin):
    list_display = ('title', 'domain', 'slug', 'needs_linting', 'last_linted_at')
    list_filter = ('domain', 'needs_linting')
    search_fields = ('title', 'slug', 'focus_hint', 'narrative_content')
    prepopulated_fields = {'slug': ('title',)}
    actions = [generate_narrative_action, lint_concepts_action]
    readonly_fields = ('rendered_narrative',)
    raw_id_fields = ('source_chunk', )
    
    fieldsets = (
        (None, {
            'fields': ('domain', 'title', 'slug', 'focus_hint')
        }),
        ('Content', {
            'fields': ('narrative_content', 'rendered_narrative', 'structured_claims'),
            'classes': ('wide',)
        }),
        ('Linting Status', {
            'fields': ('needs_linting', 'last_linted_at', 'linting_report'),
            'classes': ('collapse',)
        }),
    )

    def rendered_narrative(self, obj):
        if not obj.narrative_content:
            return "<em>No narrative generated yet.</em>"
        try:
            import markdown
            html = markdown.markdown(obj.narrative_content, extensions=['fenced_code', 'tables'])
            return mark_safe(f'<div style="background-color: #f9f9f9; padding: 15px; border: 1px solid #ccc; border-radius: 5px;">{html}</div>')
        except ImportError:
            return mark_safe(f"<pre style='white-space: pre-wrap;'>{obj.narrative_content}</pre>")
            
    rendered_narrative.short_description = "Narrative Preview"


@admin.register(KnowledgeEdge)
class KnowledgeEdgeAdmin(admin.ModelAdmin):
    list_display = ('source', 'relationship_type', 'target', 'needs_linting', 'last_linted_at')
    list_filter = ('relationship_type', 'needs_linting')
    search_fields = ('source__title', 'target__title', 'justification')
    autocomplete_fields = ('source', 'target')


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'document_count')
    search_fields = ('name', 'description', 'style_guide')
    filter_horizontal = ('documents',)
    actions = [digest_corpus_level_1_action, digest_corpus_level_2_action, digest_corpus_level_3_action]
    
    def document_count(self, obj):
        return obj.documents.count()
    document_count.short_description = "Corpus Size"


@admin.register(CeleryStatus)
class CeleryStatusAdmin(admin.ModelAdmin):
    """Legacy redirect to the centralized verbal_tasks dashboard."""
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('', self.admin_site.admin_view(self.dashboard_view), name="grips_celerystatus_changelist"),
        ]
        return my_urls + urls

    def dashboard_view(self, request):
        from django.shortcuts import redirect
        return redirect("admin:verbal_tasks_dashboard")
