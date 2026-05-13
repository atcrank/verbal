import re
from django.shortcuts import render
from django.contrib import admin
from django.utils.safestring import mark_safe
from .models import Reference, Citation, CitationGraphExplorer


class CitationInline(admin.TabularInline):
    model = Citation
    fk_name = 'source_reference'
    extra = 0
    fields = ('target_reference', 'context_text', 'raw_reference_string')


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ('title', 'year', 'journal', 'publisher', 'document')
    search_fields = ('title', 'authors', 'journal', 'doi')
    list_filter = ('year', 'publisher')
    inlines = [CitationInline]
    readonly_fields = ('pdf_viewer',)

    fieldsets = (
        (None, {
            'fields': ('title', 'authors', 'abstract', 'document', 'pdf_viewer')
        }),
        ('Publication Info', {
            'fields': ('journal', 'publisher', 'year', 'publication_date', 'volume', 'issue', 'pages', 'doi'),
            'classes': ('collapse',)
        }),
        ('Cached Data', {
            'fields': ('extended_metadata', 'tei_xml',),
            'classes': ('collapse',)
        }),
    )

    def pdf_viewer(self, obj):
        if obj.document and obj.document.file:
            # Point to our new, frame-friendly API endpoint
            # Append #view=FitH to force the browser to fit the PDF to the iframe's width
            url = f"/api/llm/view_document/{obj.document.id}/#view=FitH"
            return mark_safe(
                f'<iframe src="{url}" width="100%" height="100%" style="border: 1px solid #ccc;"></iframe>'
            )
        return "No PDF associated with this reference."

    pdf_viewer.short_description = "Source Document Viewer"


@admin.register(Citation)
class CitationAdmin(admin.ModelAdmin):
    list_display = ('source_reference', 'target_reference')
    search_fields = ('source_reference__title', 'target_reference__title', 'raw_reference_string')
    autocomplete_fields = ('source_reference', 'target_reference')


@admin.register(CitationGraphExplorer)
class CitationGraphExplorerAdmin(admin.ModelAdmin):
    # Hide the standard "Add/Change/Delete" buttons
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        citations = Citation.objects.select_related('source_reference', 'target_reference').all()

        def clean_title(text):
            if not text: return "Unknown"
            # Strip characters that confuse Mermaid's syntax parser
            clean = re.sub(r'["\'\[\]\(\)\{\}<>]', '', text)
            return clean[:40] + "..." if len(clean) > 40 else clean

        standard_edges = []
        year_groups = {}
        year_edges = []

        for cit in citations:
            src = cit.source_reference
            tgt = cit.target_reference

            src_id = f"ref_{src.id}"
            src_title = clean_title(src.title)

            if tgt:
                tgt_id = f"ref_{tgt.id}"
                tgt_title = clean_title(tgt.title)
            else:
                tgt_id = f"unlinked_{cit.id}"
                tgt_title = "Unlinked: " + clean_title(cit.raw_reference_string[:30])

            # 1. Standard Graph Edge
            standard_edges.append(f'    {src_id}["{src_title}"] --> {tgt_id}["{tgt_title}"]')

            # 2. Year Graph Logic (Grouping into subgraphs)
            src_year = src.year if src.year else "Unknown Year"
            if src_year not in year_groups: year_groups[src_year] = {}
            year_groups[src_year][src_id] = src_title

            if tgt:
                tgt_year = tgt.year if tgt.year else "Unknown Year"
                if tgt_year not in year_groups: year_groups[tgt_year] = {}
                year_groups[tgt_year][tgt_id] = tgt_title
            else:
                if "Unknown Year" not in year_groups: year_groups["Unknown Year"] = {}
                year_groups["Unknown Year"][tgt_id] = tgt_title

            year_edges.append(f'    {tgt_id} <-- {src_id}')

            # Build Standard Graph String
        standard_graph = "graph RL\n" + "\n".join(
            standard_edges) if standard_edges else "graph RL\n    A[No Citations Found]"

        # Build Year Graph String
        year_graph = "graph RL\n"
        sorted_years = sorted([y for y in year_groups.keys() if str(y).isdigit()])
        if "Unknown Year" in year_groups:
            sorted_years.append("Unknown Year")

        for year in sorted_years:
            year_graph += f'    subgraph "{year}"\n'
            for ref_id, title in year_groups[year].items():
                year_graph += f'        {ref_id}["{title}"]\n'
            year_graph += f'    end\n'

        year_graph += "\n".join(year_edges)

        context = {
            'title': 'Citation Graph Explorer',
            'standard_graph': standard_graph,
            'year_graph': year_graph,
            'opts': self.model._meta,
            'site_header': admin.site.site_header,
            'has_permission': True,
        }
        return render(request, "admin/grobid_client/citation_explorer.html", context)