import hashlib

from django import forms
from django.contrib import admin
from .models import Document
# Register your models here.


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = "__all__"

    # def clean(self):
    #     cleaned_data = super().clean()
    #     file_upload = cleaned_data.get("file")
    #     hasher = hashlib.sha256()
    #     f = file_upload.open('rb')
    #     for chunk in iter(lambda: f.read(4096), b""):
    #         hasher.update(chunk)
    #     cleaned_data["content_hash"] = hasher.hexdigest()
    #     print(cleaned_data)
    #     self.cleaned_data = cleaned_data
    #     print(self.cleaned_data)
    #     return cleaned_data

@admin.action(description="Make a short AI summary of the sections as a content-rich index for FAISS search.")
def process_document(modeladmin, request, queryset):
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']
    rag_service.ingest_queryset_documents(queryset)


class DocumentAdmin(admin.ModelAdmin):
    fields = ("title", "file", "chunk_size", "chunk_overlap", "metadata", "indexing_strategy", "currently_indexed")
    readonly_fields = ("metadata",)
    list_display = ("title", "file", "uploaded_at", "indexing_strategy", "metadata", "currently_indexed")
    search_fields = ("title", "file")
    form = DocumentForm
    actions = [process_document,]

    class Meta:
        model = Document


from django.contrib import admin
from django.db.models import Sum
from django.shortcuts import render
from .models import Document, VectorIndexExplorer
from llm_api.api import service_registry  # Import your service


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
        ingested_documents = len(rag_service.indexed_hashes)
        total_vectors = rag_service.db.index.ntotal
        total_chunks = len(rag_service.db.docstore._dict)

        # 4. Prepare Context
        context = {
            'title': 'Vector Index Explorer',
            'query': query,
            'results': results,
            'document_file_count': document_file_count,
            'ingested_documents': ingested_documents,
            'total_vectors': total_vectors,
            'total_chunks': total_chunks,
            # Required for Admin visual structure:
            'opts': self.model._meta,
            'site_header': admin.site.site_header,
            'has_permission': True,
        }

        # 5. Render Custom Template
        return render(request, "admin/vector_explorer.html", context)



admin.site.register(Document, DocumentAdmin)