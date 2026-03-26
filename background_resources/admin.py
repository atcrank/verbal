from django import forms
from django.shortcuts import render
from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.forms import BaseGenericInlineFormSet
from django.utils.html import format_html
from llm_api.apps import service_registry  # Import your service
from .models import (Document,
                     VectorIndexExplorer,
                     PromptStrategy,
                     RegexStrategy,
                     ReadingStrategy,
                     AbbreviationsReadingStrategy,
                     RAGChunk, StrategyChunkUsage,
                     )

from benchmarking.generators import generate_scenarios_for_document

@admin.action(description="Ingest document(s) according to its indexing strategy.")
def process_document(modeladmin, request, queryset):
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']
    rag_service.ingest_queryset_documents(queryset)


@admin.action(description="Execute this Reading Strategy")
def process_reading(modeladmin, request, queryset):
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']
    rag_service.ingest_queryset_reading_strategies(queryset)

@admin.action(description="Generate Synthetic Benchmarks")
def generate_benchmarks(modeladmin, request, queryset):
    for doc in queryset:
        try:
            count = generate_scenarios_for_document(doc, stride=5) # Default stride
            modeladmin.message_user(request, f"Generated {count} scenarios for {doc.title}.", level=messages.SUCCESS)
        except Exception as e:
            modeladmin.message_user(request, f"Error generating for {doc.title}: {e}", level=messages.ERROR)

class RAGChunkAdmin(admin.ModelAdmin):

    fields = ('chunk_id', 'text_content', 'hit_count', 'last_accessed', 'in_vector_index', 'in_byte_store')
    readonly_fields = ('hit_count', 'last_accessed')
    list_display = ('chunk_id', 'short_content', 'hit_count', 'last_accessed')
    # TODO: Verify that hit_count and last_accessed are not affected by admin saves, updates.

    def short_content(self, obj):
        return obj.text_content[:50] + "..." if obj.text_content else ""

    class Meta:
        model = RAGChunk

class StrategyChunkUsageInline(GenericTabularInline):
    model = StrategyChunkUsage
    fields = ('chunk_content', 'role', 'chunk_hit_count')
    readonly_fields = ('chunk_content', 'chunk_hit_count') 
    extra = 0
    can_delete = True
    show_change_link = True
    ct_field = "content_type"
    ct_fk_field = "object_id"

    def chunk_content(self, obj):
        return obj.chunk.text_content[:100] + "..." if obj.chunk.text_content else ""
    
    def chunk_hit_count(self, obj):
        return obj.chunk.hit_count

class TestableChunkInline(StrategyChunkUsageInline):
    # Adds a radio button to select a specific chunk for testing
    readonly_fields = ('select_for_test',) + StrategyChunkUsageInline.readonly_fields
    fields = ('select_for_test',) + StrategyChunkUsageInline.fields
    extra = 0

    def select_for_test(self, obj):
        return format_html('<input type="radio" name="selected_test_chunk" value="{}" />', obj.chunk.chunk_id)
    select_for_test.short_description = "Test Source"

class DefaultChunksFormSet(BaseGenericInlineFormSet):
    """
    A custom formset that swaps the instance (Parent) from the Higher-Order Strategy
    to the Default ReadingStrategy of the same document.
    This allows us to display the 'Source Chunks' in the inline.
    """
    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        # If we are editing an existing strategy (instance has a document)
        if instance and getattr(instance, 'document_id', None):
            from .models import ReadingStrategy
            # Find the default strategy for this document
            default_strat = ReadingStrategy.objects.filter(
                document_id=instance.document_id, 
                strategy_description="Default Chunking"
            ).first()
            if default_strat:
                # Swap the instance so the inline loads the Default Strategy's chunks
                kwargs['instance'] = default_strat
        super().__init__(*args, **kwargs)

class DefaultChunksInline(TestableChunkInline):
    formset = DefaultChunksFormSet
    verbose_name = "Source Chunk (Default Reading)"
    verbose_name_plural = "Source Chunks (Default Reading)"
    
    # We make this read-only to prevent editing the source chunks from a derived view
    def has_change_permission(self, request, obj=None):
        return False
    def has_add_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

class ReadingStrategyAdmin(admin.ModelAdmin):
    fields = ('document', 'strategy_description', 'chunk_size_override', 'chunk_overlap_override')
    list_display = ('document', 'strategy_description', 'chunk_size_override', 'chunk_overlap_override')
    inlines = [StrategyChunkUsageInline,]
    actions = [process_reading, ]

    class Meta:
        model = ReadingStrategy


class ReadingStrategyInline(admin.TabularInline):
    model = ReadingStrategy
    parent_model = Document
    fields = ('document', 'strategy_description', 'chunk_size_override', 'chunk_overlap_override')
    extra = 0

    class Meta:
        model = ReadingStrategy


class RegexStrategyAdmin(admin.ModelAdmin):
    change_form_template = "admin/background_resources/regexstrategy/change_form.html"
    fields = ('document', 'strategy_description', 'strategy_details', 'chunk_size_override', 'chunk_overlap_override', 'regex', )
    list_display = ('document',  'strategy_description', 'strategy_details')
    inlines = [DefaultChunksInline]
    extra = 1
    actions = [process_reading, ]

    class Meta:
        model = RegexStrategy


class RegexStrategyInline(admin.TabularInline):
    model = RegexStrategy
    parent_model = Document
    extra = 0
    fields = ('document', 'regex', 'strategy_description', 'strategy_details')

    class Meta:
        model = RegexStrategy


class AbbreviationsStrategyAdmin(admin.ModelAdmin):
    change_form_template = "admin/background_resources/abbreviationsreadingstrategy/change_form.html"
    fields = ('document', 'strategy_description')
    list_display = ('document',  'strategy_description', 'chunk_size_override', 'chunk_overlap_override')
    inlines = [DefaultChunksInline,]
    actions = [process_reading, ]

    class Meta:
        model = AbbreviationsReadingStrategy


class AbbreviationStrategyInline(admin.TabularInline):
    model = AbbreviationsReadingStrategy
    parent_model = Document
    extra = 0
    fields = ('strategy_description',)


    class Meta:
        model = AbbreviationsReadingStrategy


class PromptStrategyAdmin(admin.ModelAdmin):
    change_form_template = "admin/background_resources/promptstrategy/change_form.html"
    fields = ('document', 'strategy_description', 'prompt')
    list_display = ('document', 'strategy_description', 'prompt')
    inlines = [DefaultChunksInline,]
    actions = [process_reading, ]

    class Meta:
        model = PromptStrategy


class PromptStrategyInline(admin.TabularInline):
    model = PromptStrategy
    parent_model = Document
    extra = 0
    fields = ('document', 'strategy_description', 'prompt')

    class Meta:
        model = AbbreviationsReadingStrategy


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = "__all__"


class DocumentAdmin(admin.ModelAdmin):
    fields = ("title", "file", "chunk_size", "chunk_overlap", "metadata")
    readonly_fields = ("metadata",)
    list_display = ("title", "file", "uploaded_at", "metadata")
    search_fields = ("title", "file")
    form = DocumentForm
    actions = [process_document, generate_benchmarks]
    inlines = [ReadingStrategyInline, RegexStrategyInline, PromptStrategyInline, AbbreviationStrategyInline]

    class Meta:
        model = Document


@admin.register(VectorIndexExplorer)
class VectorIndexExplorerAdmin(admin.ModelAdmin):
    # Hide the standard "Add" button
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # 1. Initialize RAG Service (Consider caching this if slow)
        rag_service = service_registry['rag_service']

        # Handle Aggressive Cleanup Request
        if request.method == "POST" and "clean_orphans" in request.POST:
            f_count, s_count = rag_service.clean_orphaned_store_data()
            self.message_user(request, f"Aggressive Cleanup Complete: Removed {f_count} orphaned vectors and {s_count} orphaned byte-store files.", level=messages.SUCCESS)

        # 2. Handle Search
        query = request.GET.get('q', '')
        results = []
        if query:
            try:
                # Use your existing get_context method
                results = rag_service.get_context(query, k=5)
            except Exception as e:
                self.message_user(request, f"Search Error: {e}", level='error')

        # 3. Gather Stats
        document_file_count = Document.objects.count()
        total_readings = ReadingStrategy.objects.count()
        total_vectors = rag_service.db.index.ntotal
        total_chunks = len(rag_service.db.docstore._dict)
        
        # Run Data Integrity Audit
        audit_results = rag_service.audit_stores()

        # 4. Prepare Context
        context = {
            'title': 'Vector Index Explorer',
            'query': query,
            'results': results,
            'document_file_count': document_file_count,
            'total_readings': total_readings,
            'total_vectors': total_vectors,
            'total_chunks': total_chunks,
            'audit': audit_results,
            # Required for Admin visual structure:
            'opts': self.model._meta,
            'site_header': admin.site.site_header,
            'has_permission': True,
        }

        # 5. Render Custom Template
        return render(request, "admin/vector_explorer.html", context)




admin.site.register(Document, DocumentAdmin)
admin.site.register(RAGChunk, RAGChunkAdmin)
admin.site.register(ReadingStrategy, ReadingStrategyAdmin)
admin.site.register(PromptStrategy, PromptStrategyAdmin)
admin.site.register(RegexStrategy, RegexStrategyAdmin)
admin.site.register(AbbreviationsReadingStrategy, AbbreviationsStrategyAdmin)

# Custom Model Ordering in Admin
def get_app_list(self, request, app_label=None):
    """
    Return a sorted list of all the installed apps that have been
    registered in this site.
    """
    app_dict = self._build_app_dict(request, app_label)
    app_list = sorted(app_dict.values(), key=lambda x: x['name'].lower())

    # Sort the models customly within each app.
    for app in app_list:
        if app['app_label'] == 'background_resources':
            ordering = {
                'Document': 1,
                'Chunk': 2,
                'ReadingStrategy': 3,
                'PromptStrategy': 4,
                'RegexStrategy': 5,
                'AbbreviationsReadingStrategy': 6,
                'VectorIndexExplorer': 7
            }
            app['models'].sort(key=lambda x: ordering.get(x['object_name'], 100))

        if app['app_label'] == 'benchmarking':
            benchmark_ordering = {
                'BenchmarkCorpus': 1,
                'Experiment': 2,
                'BenchmarkRun': 3
            }
            app['models'].sort(key=lambda x: benchmark_ordering.get(x['object_name'], 100))
    
    return app_list

admin.AdminSite.get_app_list = get_app_list
