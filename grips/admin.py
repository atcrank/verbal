import os
from django.conf import settings
from django.urls import path
from django.template.response import TemplateResponse
from django.contrib import admin, messages
from django.utils.safestring import mark_safe
from .models import Domain, ConceptNode, KnowledgeEdge, PromptRecipies, CeleryStatus
from .tasks import generate_concept_narrative, task_lint_concept_node
from verbal_config.celery import app as celery_app


@admin.action(description="Generate narrative content for selected concepts")
def generate_narrative_action(modeladmin, request, queryset):
    """Admin action to trigger the Celery task for generating narrative content."""
    # 1. Check if Celery workers are actually running and reachable
    try:
        ping_result = celery_app.control.ping(timeout=1.0)
        if not ping_result:
            modeladmin.message_user(request, "The Celery queuing service is not available (no workers running). Please run the toggle script.", level=messages.ERROR)
            return
    except Exception as e:
        modeladmin.message_user(request, "The Celery queuing service is not available (Broker connection failed).", level=messages.ERROR)
        return

    count = 0
    for node in queryset:
        generate_concept_narrative.delay(node.id)
        count += 1
    modeladmin.message_user(request, f"Queued content generation for {count} concept(s).", level=messages.SUCCESS)


@admin.register(ConceptNode)
class ConceptNodeAdmin(admin.ModelAdmin):
    list_display = ('title', 'domain', 'slug', 'needs_linting', 'last_linted_at')
    list_filter = ('domain', 'needs_linting')
    search_fields = ('title', 'slug', 'focus_hint', 'narrative_content')
    prepopulated_fields = {'slug': ('title',)}
    actions = [generate_narrative_action, task_lint_concept_node]
    readonly_fields = ('rendered_narrative',)
    
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


@admin.register(PromptRecipies)
class PromptRecipeAdmin(admin.ModelAdmin):
    list_display = ('name', 'domain', 'recommended_model', 'needs_linting', 'last_linted_at')
    list_filter = ('domain', 'needs_linting', 'recommended_model')
    search_fields = ('name', 'system_prompt_template')
    autocomplete_fields = ('domain', 'recommended_model')


@admin.register(CeleryStatus)
class CeleryStatusAdmin(admin.ModelAdmin):
    # Disable add/change/delete buttons so it behaves purely as a dashboard link
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
        i = celery_app.control.inspect()
        worker_stats = {}
        try:
            active_tasks = i.active()
            queued_tasks = i.reserved()
            if active_tasks:
                for worker, tasks in active_tasks.items():
                    worker_stats[worker] = {
                        'active': tasks,
                        'queued': queued_tasks.get(worker, []) if queued_tasks else []
                    }
        except Exception:
            pass  # Worker/Broker is down

        log_content = "Log file not found or empty."
        log_path = os.path.join(settings.BASE_DIR, "celery.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        log_content = "".join(lines[-100:])  # Read last 100 lines
            except Exception as e:
                log_content = f"Error reading log: {e}"

        context = dict(
            self.admin_site.each_context(request),
            title="Celery Status Dashboard",
            worker_stats=worker_stats,
            log_content=log_content,
        )
        return TemplateResponse(request, "admin/grips/celery_status_dashboard.html", context)


from .tasks import task_digest_corpus_level_1, task_digest_corpus_level_2, task_digest_corpus_level_3, \
    task_lint_concept_node
from verbal_config.celery import app as celery_app
from background_resources.models import Document


@admin.action(description="Level 1 Digest: Ingest Domain Documents")
def digest_corpus_level_1_action(modeladmin, request, queryset):
    """Admin action to trigger the Level 1 corpus digestion task."""
    try:
        ping_result = celery_app.control.ping(timeout=1.0)
        if not ping_result:
            modeladmin.message_user(request, "The Celery queuing service is not available (no workers running).",
                                    level=messages.ERROR)
            return
    except Exception as e:
        modeladmin.message_user(request, "The Celery queuing service is not available (Broker connection failed).",
                                level=messages.ERROR)
        return

    count = 0
    for domain in queryset:
        doc_ids = list(domain.documents.values_list('id', flat=True))
        for doc_id in doc_ids:
            task_digest_corpus_level_1.delay(domain.id, doc_id)
            count += 1
    modeladmin.message_user(request, f"Queued {count} document(s) across selected domain(s) for Level 1 digestion.",
                            level=messages.SUCCESS)


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'document_count')
    search_fields = ('name', 'description', 'style_guide')
    filter_horizontal = ('documents',)
    actions = [digest_corpus_level_1_action]
    
    def document_count(self, obj):
        return obj.documents.count()
    document_count.short_description = "Corpus Size"

@admin.action(description="Run Automated Linting on selected concepts")
def lint_concepts_action(modeladmin, request, queryset):
    """Admin action to trigger the automated LLM linting task."""
    try:
        if not celery_app.control.ping(timeout=1.0):
            modeladmin.message_user(request, "Celery service not available.", level=messages.ERROR)
            return
    except Exception:
        modeladmin.message_user(request, "Celery service not available.", level=messages.ERROR)
        return

    count = 0
    for node in queryset:
        task_lint_concept_node.delay(node.id)
        count += 1
    modeladmin.message_user(request, f"Queued {count} concept(s) for automated linting.", level=messages.SUCCESS)
