import os
import re
import yaml
import subprocess
from django.core.management.base import BaseCommand
from django.conf import settings
from grips.models import ConceptNode, KnowledgeEdge
from background_resources.models import Document
from grobid_client.models import Reference, Citation

class Command(BaseCommand):
    help = "Exports Grips and Documents to Google Open Knowledge Format (OKF) directory."

    def handle(self, *args, **options):
        export_dir = os.path.join(settings.BASE_DIR, 'workspaces', 'grips_okf')
        os.makedirs(export_dir, exist_ok=True)
        
        concepts_dir = os.path.join(export_dir, 'concepts')
        docs_dir = os.path.join(export_dir, 'documents')
        refs_dir = os.path.join(export_dir, 'references')
        
        os.makedirs(concepts_dir, exist_ok=True)
        os.makedirs(docs_dir, exist_ok=True)
        os.makedirs(refs_dir, exist_ok=True)
        
        self.stdout.write(f"Exporting OKF to {export_dir}...")
        
        # 1. Export ConceptNodes
        concepts = ConceptNode.objects.select_related('domain', 'source_chunk').all()
        for node in concepts:
            domain_name = node.domain.name if node.domain else "uncategorized"
            safe_domain = re.sub(r'[^a-zA-Z0-9]', '-', domain_name.lower())
            abstraction = "derived" if (node.source_chunk or node.slug.startswith("doc-")) else "abstract"
            
            node_dir = os.path.join(concepts_dir, safe_domain, abstraction)
            os.makedirs(node_dir, exist_ok=True)
            
            filename = f"{node.slug}.md"
            filepath = os.path.join(node_dir, filename)
            
            frontmatter = {
                'type': 'concept',
                'title': node.title,
                'domain': node.domain.name if node.domain else None,
                'slug': node.slug,
            }
            if node.focus_hint:
                frontmatter['focus_hint'] = node.focus_hint
            if node.structured_claims:
                frontmatter['claims'] = node.structured_claims
                
            content = f"---\n{yaml.dump(frontmatter, sort_keys=False)}---\n\n"
            content += f"# {node.title}\n\n"
            if node.narrative_content:
                content += node.narrative_content + "\n\n"
                
            # Add Relations
            edges = KnowledgeEdge.objects.filter(source=node).select_related('target')
            
            if edges.exists() or node.source_chunk:
                content += "## Graph Links\n\n"
                
                if node.source_chunk:
                    filename_meta = node.source_chunk.metadata.get('filename')
                    if filename_meta:
                        content += f"- **Derived From:** {filename_meta}\n"
                    
                for edge in edges:
                    content += f"- **{edge.relationship_type}:** [[{edge.target.slug}]]"
                    if edge.justification:
                        content += f" ({edge.justification})"
                    content += "\n"
                    
            with open(filepath, 'w') as f:
                f.write(content)
                
        # 2. Export Documents and Citations
        docs = Document.objects.all()
        for doc in docs:
            ext = os.path.splitext(doc.file.name)[1].lower().replace('.', '') if doc.file else 'misc'
            if not ext: ext = 'misc'
            ext_dir = os.path.join(docs_dir, ext)
            os.makedirs(ext_dir, exist_ok=True)
            
            safe_title = re.sub(r'[^a-zA-Z0-9]', '-', doc.title.lower())[:50]
            filename = f"doc-{doc.id}-{safe_title}.md"
            filepath = os.path.join(ext_dir, filename)
            
            frontmatter = {
                'type': 'document',
                'title': doc.title,
            }
            if doc.author:
                frontmatter['author'] = doc.author
                
            content = f"---\n{yaml.dump(frontmatter, sort_keys=False)}---\n\n"
            content += f"# {doc.title}\n\n"
            
            # Check Grobid Metadata
            if hasattr(doc, 'grobid_metadata') and doc.grobid_metadata:
                ref = doc.grobid_metadata
                if ref.abstract:
                    content += f"## Abstract\n{ref.abstract}\n\n"
                    
                citations = Citation.objects.filter(source_reference=ref).select_related('target_reference__document')
                if citations.exists():
                    content += "## Citations\n\n"
                    for cit in citations:
                        if cit.target_reference and cit.target_reference.document:
                            target_doc = cit.target_reference.document
                            safe_t = re.sub(r'[^a-zA-Z0-9]', '-', target_doc.title.lower())[:50]
                            content += f"- [[doc-{target_doc.id}-{safe_t}]]\n"
                        elif cit.target_reference:
                            target_ref = cit.target_reference
                            safe_t = re.sub(r'[^a-zA-Z0-9]', '-', (target_ref.title or 'unknown').lower())[:50]
                            content += f"- [[ref-{target_ref.id}-{safe_t}]]\n"
                        else:
                            content += f"- {cit.raw_reference_string[:100]}...\n"
                            
            with open(filepath, 'w') as f:
                f.write(content)

        # 2.5 Export Orphaned References
        orphaned_refs = Reference.objects.filter(document__isnull=True)
        for ref in orphaned_refs:
            safe_t = re.sub(r'[^a-zA-Z0-9]', '-', (ref.title or 'unknown').lower())[:50]
            filename = f"ref-{ref.id}-{safe_t}.md"
            filepath = os.path.join(refs_dir, filename)
            
            frontmatter = {
                'type': 'reference',
                'title': ref.title or 'Unknown Title',
            }
            if ref.authors:
                frontmatter['authors'] = ref.authors
                
            content = f"---\n{yaml.dump(frontmatter, sort_keys=False)}---\n\n"
            content += f"# {ref.title or 'Unknown Reference'}\n\n"
            
            with open(filepath, 'w') as f:
                f.write(content)

        self.stdout.write(self.style.SUCCESS("Files written successfully."))
        
        # 3. Git Automation
        try:
            if not os.path.exists(os.path.join(export_dir, '.git')):
                subprocess.run(['git', 'init'], cwd=export_dir, check=True, capture_output=True)
            
            subprocess.run(['git', 'add', '.'], cwd=export_dir, check=True, capture_output=True)
            
            status = subprocess.run(['git', 'status', '--porcelain'], cwd=export_dir, capture_output=True, text=True)
            if status.stdout.strip():
                subprocess.run(['git', 'commit', '-m', 'Automated OKF Export'], cwd=export_dir, check=True, capture_output=True)
                self.stdout.write(self.style.SUCCESS("Git commit created successfully."))
            else:
                self.stdout.write("No changes to commit in Git.")
                
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Git automation failed (is git installed?): {e}"))
