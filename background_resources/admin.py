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
def process_summary(modeladmin, request, queryset):
    Document.generate_summaries(queryset)


class DocumentAdmin(admin.ModelAdmin):
    fields = ("title", "file", "chunk_size", "chunk_overlap", "metadata")
    readonly_fields = ("metadata",)
    list_display = ("title", "file", "uploaded_at", "metadata")
    search_fields = ("title", "file")
    form = DocumentForm
    actions = [process_summary]

    class Meta:
        model = Document


admin.site.register(Document, DocumentAdmin)