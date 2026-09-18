import os
import re
import yaml
import logging
import threading
import subprocess
from pathlib import Path
from typing import Any
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

_git_lock = threading.Lock()

WIKILINK_PATTERN = re.compile(r'\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]')
GRAPH_EDGE_PATTERN = re.compile(
    r'^[ \t]*-[ \t]+\*\*(?P<rel>[A-Z_]+):\*\*[ \t]+\[\[(?P<target>[^\]\|]+)\]\](?:[ \t]+\((?P<just>[^\)]*)\))?',
    re.MULTILINE
)


def get_wiki_root() -> Path:
    """Returns the absolute Path to the workspaces/grips_okf wiki directory."""
    return Path(settings.BASE_DIR) / 'workspaces' / 'grips_okf'


def assert_safe_path(rel_path: str) -> Path:
    """
    Ensures rel_path resolves strictly within workspaces/grips_okf.
    Rejects directory traversal attempts, absolute paths, or access to other workspaces.
    """
    if os.path.isabs(rel_path) or rel_path.strip().startswith('/'):
        raise PermissionError(f"Access denied: absolute path '{rel_path}' is not permitted.")

    wiki_root = get_wiki_root().resolve()
    clean_rel = os.path.normpath(rel_path.strip())
    if clean_rel == '.' or clean_rel == '':
        return wiki_root

    target_path = (wiki_root / clean_rel).resolve()
    try:
        common = os.path.commonpath([str(wiki_root), str(target_path)])
    except ValueError as exc:
        raise PermissionError(f"Access denied: path '{rel_path}' is outside the wiki root.") from exc

    if common != str(wiki_root):
        raise PermissionError(f"Access denied: path '{rel_path}' escapes the wiki jail.")

    return target_path


def resolve_wiki_rel_path(path: str) -> str:
    """
    Resolves a requested path or slug to a valid relative path within workspaces/grips_okf.
    Supports:
      - Direct path: 'concepts/oceanography/derived/deep-sea-mining.md'
      - Path without ext: 'concepts/oceanography/derived/deep-sea-mining'
      - Slug: 'deep-sea-mining' or 'concepts/deep-sea-mining'
    """
    wiki_root = get_wiki_root()
    clean = path.strip().lstrip('/')
    if not clean:
        return ""

    # 1. Exact file match
    candidate = wiki_root / clean
    if candidate.is_file():
        return str(candidate.relative_to(wiki_root))

    # 2. Append .md
    if not clean.endswith('.md'):
        candidate_md = wiki_root / f"{clean}.md"
        if candidate_md.is_file():
            return str(candidate_md.relative_to(wiki_root))

    # 3. Search by slug / stem
    target_slug = Path(clean).stem
    for p in wiki_root.rglob(f"{target_slug}.md"):
        if p.is_file():
            return str(p.relative_to(wiki_root))

    # Fallback to the requested path
    if not clean.endswith('.md'):
        clean = f"{clean}.md"
    return clean


_resolve_wiki_rel_path = resolve_wiki_rel_path


def parse_frontmatter_and_body(content: str) -> tuple[dict[str, Any], str]:
    """
    Splits YAML frontmatter (between --- and ---) from the rest of the markdown.
    """
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                body = parts[2].lstrip('\r\n')
                return fm, body
            except Exception as e:
                logger.warning("Failed to parse YAML frontmatter: %s", e)
    return {}, content


def format_frontmatter_and_body(frontmatter: dict[str, Any], body: str) -> str:
    """
    Combines frontmatter dict and markdown body back into document string.
    """
    if not frontmatter:
        return body
    fm_str = yaml.dump(frontmatter, sort_keys=False)
    return f"---\n{fm_str}---\n\n{body.lstrip()}"


def clean_human_title(raw_title: str | None = None, slug: str | None = None) -> str:
    """
    Transforms raw or prefixed titles into clean, readable human titles.

    Strips technical document identifiers, category prefixes, and slug formatting.

    >>> clean_human_title('doc-11-c3-direct-vs--indirect-connections')
    'Direct vs. Indirect Connections'
    >>> clean_human_title('Doc: Neyman (1923) and Causal Inference')
    'Neyman (1923) and Causal Inference'
    >>> clean_human_title('doc-14-c9-subclassification-and-randomized-block-experiment-')
    'Subclassification and Randomized Block Experiment'
    >>> clean_human_title('unified-179-the-causal-inference-of-confounding-via-interventi')
    'The Causal Inference of Confounding via Interventi'
    >>> clean_human_title(', ')
    'Untitled Concept'
    """
    text = (raw_title or '').strip()
    if not text or re.match(r'^[\W_]+$', text):
        if slug and not re.match(r'^[\W_]+$', slug):
            text = slug
        else:
            return 'Untitled Concept'

    text = re.sub(r'^[Dd]oc:\s*', '', text)
    text = re.sub(r'^(?:doc-\d+(?:-c\d+)?|unified-\d+|ref-\d+)-', '', text, flags=re.IGNORECASE)

    if ' ' not in text and '-' in text:
        text = text.replace('--', ' - ')
        tokens = []
        for part in text.split(' '):
            if part == '-':
                if tokens and tokens[-1] in ('vs', 'vs.'):
                    continue
                tokens.append('-')
                continue
            subparts = part.split('-')
            for w in subparts:
                if not w:
                    continue
                lw = w.lower()
                if lw in {'vs', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for', 'a', 'an', 'the', 'via'} and tokens:
                    tokens.append('vs.' if lw == 'vs' else lw)
                else:
                    tokens.append(w.capitalize())
        text = ' '.join(tokens)
    else:
        text = text.replace('--', ': ')

    text = re.sub(r'[\s\-:]+$', '', text).strip()
    return text or 'Untitled Concept'


def get_page_content_and_sha(rel_path: str) -> dict[str, Any]:
    """
    Reads a wiki markdown page, extracts frontmatter, markdown, and current Git SHA.
    Enriches documents and references with full document status and Grobid citation data.
    """
    file_path = assert_safe_path(rel_path)
    wiki_root = get_wiki_root()

    if not file_path.is_file():
        raw_title = Path(rel_path).stem
        return {
            'exists': False,
            'rel_path': rel_path,
            'title': clean_human_title(raw_title, raw_title),
            'clean_title': clean_human_title(raw_title, raw_title),
            'frontmatter': {},
            'markdown': '',
            'body': '',
            'commit_sha': None,
            'last_modified': None,
            'backlinks': [],
            'is_reference': False,
            'is_full_document': False,
            'document_id': None,
            'pdf_url': None,
            'authors': None,
            'year': None,
            'doi': None,
            'incoming_citations': [],
            'outgoing_citations': [],
        }

    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        markdown = f.read()

    frontmatter, body = parse_frontmatter_and_body(markdown)
    title = frontmatter.get('title')
    if not title or re.match(r'^[\W_]+$', str(title)):
        # Fallback to first H1
        h1_match = re.search(r'^#[ \t]+(.+)$', body, re.MULTILINE)
        title = h1_match.group(1).strip() if h1_match else file_path.stem

    slug = frontmatter.get('slug') or file_path.stem
    clean_title = clean_human_title(title, slug)

    # Get latest commit SHA for this file
    commit_sha = None
    last_modified = None
    try:
        git_rel = str(file_path.relative_to(wiki_root))
        res = subprocess.run(
            ['git', 'log', '-n', '1', '--format=%h|%ad', '--date=iso', '--', git_rel],
            cwd=str(wiki_root),
            capture_output=True,
            text=True,
            check=False
        )
        if res.stdout.strip():
            parts = res.stdout.strip().split('|', 1)
            commit_sha = parts[0]
            if len(parts) > 1:
                last_modified = parts[1]
    except Exception as e:
        logger.warning("Error fetching git sha for %s: %s", rel_path, e)

    backlinks = get_backlinks_for_slug(slug)

    # Literature & Reference Enrichment
    is_reference = False
    is_full_document = False
    document_id = None
    pdf_url = None
    authors = frontmatter.get('author') or frontmatter.get('authors')
    year = frontmatter.get('year')
    doi = frontmatter.get('doi')
    incoming_citations = []
    outgoing_citations = []

    try:
        from background_resources.models import Document
        from grobid_client.models import Reference, Citation

        ref = None
        doc = None

        ref_id_m = re.search(r'ref-(\d+)', slug)
        doc_id_m = re.search(r'doc-(\d+)', slug)

        if ref_id_m:
            ref_id = int(ref_id_m.group(1))
            ref = Reference.objects.filter(id=ref_id).select_related('document').first()
            if ref:
                is_reference = True
                if ref.document:
                    doc = ref.document
                    is_full_document = True
        elif doc_id_m:
            doc_id = int(doc_id_m.group(1))
            doc = Document.objects.filter(id=doc_id).first()
            if doc:
                is_full_document = True
                is_reference = True
                ref = getattr(doc, 'grobid_metadata', None)
        elif rel_path.startswith('references'):
            is_reference = True
            ref = Reference.objects.filter(title__iexact=title).select_related('document').first()
            if ref and ref.document:
                doc = ref.document
                is_full_document = True
        elif rel_path.startswith('documents'):
            is_full_document = True
            is_reference = True
            doc = Document.objects.filter(title__iexact=title).first()
            if doc:
                ref = getattr(doc, 'grobid_metadata', None)

        if doc:
            document_id = doc.id
            if doc.file:
                pdf_url = doc.file.url

        if ref:
            authors = ref.authors or authors
            year = ref.year or year
            doi = ref.doi or doi

            in_cits = Citation.objects.filter(target_reference=ref).select_related('source_reference__document')[:30]
            for cit in in_cits:
                src_ref = cit.source_reference
                src_doc = getattr(src_ref, 'document', None)
                src_slug = (
                    f"doc-{src_doc.id}-{re.sub(r'[^a-zA-Z0-9]', '-', src_doc.title.lower())[:40]}"
                    if src_doc
                    else f"ref-{src_ref.id}-{re.sub(r'[^a-zA-Z0-9]', '-', (src_ref.title or 'ref').lower())[:40]}"
                )
                incoming_citations.append({
                    'source_title': clean_human_title(src_ref.title or (src_doc.title if src_doc else 'Unknown Source')),
                    'source_slug': src_slug,
                    'is_full_document': bool(src_doc),
                    'context_text': cit.context_text,
                    'raw_reference_string': cit.raw_reference_string,
                })

            out_cits = Citation.objects.filter(source_reference=ref).select_related('target_reference__document')[:30]
            for cit in out_cits:
                tgt_ref = cit.target_reference
                if tgt_ref:
                    tgt_doc = getattr(tgt_ref, 'document', None)
                    tgt_slug = (
                        f"doc-{tgt_doc.id}-{re.sub(r'[^a-zA-Z0-9]', '-', tgt_doc.title.lower())[:40]}"
                        if tgt_doc
                        else f"ref-{tgt_ref.id}-{re.sub(r'[^a-zA-Z0-9]', '-', (tgt_ref.title or 'ref').lower())[:40]}"
                    )
                    outgoing_citations.append({
                        'target_title': clean_human_title(tgt_ref.title or 'Unknown Target'),
                        'target_slug': tgt_slug,
                        'is_full_document': bool(tgt_doc),
                        'raw_reference_string': cit.raw_reference_string,
                    })
    except Exception as e:
        logger.warning("Error fetching literature reference metadata for %s: %s", slug, e)

    return {
        'exists': True,
        'rel_path': str(file_path.relative_to(wiki_root)),
        'slug': slug,
        'title': title,
        'clean_title': clean_title,
        'frontmatter': frontmatter,
        'markdown': markdown,
        'body': body,
        'commit_sha': commit_sha,
        'last_modified': last_modified,
        'backlinks': backlinks,
        'is_reference': is_reference,
        'is_full_document': is_full_document,
        'document_id': document_id,
        'pdf_url': pdf_url,
        'authors': authors,
        'year': year,
        'doi': doi,
        'incoming_citations': incoming_citations,
        'outgoing_citations': outgoing_citations,
    }


def save_and_commit_page(
    rel_path: str,
    content: str,
    author_name: str = "Wiki User",
    author_email: str = "wiki@verbal.local",
    message: str = "",
    base_sha: str | None = None
) -> dict[str, Any]:
    """
    Saves content to workspaces/grips_okf/<rel_path>, commits to Git,
    and synchronizes with PostgreSQL (ConceptNode, KnowledgeEdge).
    
    If base_sha is given and differs from current HEAD for that file,
    performs a 3-way merge using git merge-file.
    """
    file_path = assert_safe_path(rel_path)
    wiki_root = get_wiki_root()
    os.makedirs(file_path.parent, exist_ok=True)
    git_rel = str(file_path.relative_to(wiki_root))

    conflict_detected = False
    merged_content = content

    with _git_lock:
        # Check concurrency / 3-way merge if file already existed and base_sha was supplied
        if file_path.exists() and base_sha:
            curr_sha_res = subprocess.run(
                ['git', 'log', '-n', '1', '--format=%h', '--', git_rel],
                cwd=str(wiki_root),
                capture_output=True,
                text=True,
                check=False
            )
            curr_sha = curr_sha_res.stdout.strip()
            if curr_sha and base_sha != curr_sha:
                # Concurrent edit detected! Extract base version for 3-way merge
                base_content_res = subprocess.run(
                    ['git', 'show', f"{base_sha}:{git_rel}"],
                    cwd=str(wiki_root),
                    capture_output=True,
                    text=True,
                    check=False
                )
                if base_content_res.returncode == 0:
                    base_text = base_content_res.stdout
                    with open(file_path, 'r', encoding='utf-8') as f:
                        curr_head_text = f.read()

                    # Write temporary files for git merge-file
                    import tempfile
                    with tempfile.NamedTemporaryFile('w', delete=False) as f_user, \
                         tempfile.NamedTemporaryFile('w', delete=False) as f_base, \
                         tempfile.NamedTemporaryFile('w', delete=False) as f_head:
                        f_user.write(content)
                        f_base.write(base_text)
                        f_head.write(curr_head_text)
                        user_tmp, base_tmp, head_tmp = f_user.name, f_base.name, f_head.name

                    try:
                        merge_res = subprocess.run(
                            ['git', 'merge-file', '-p', user_tmp, base_tmp, head_tmp],
                            capture_output=True,
                            text=True,
                            check=False
                        )
                        merged_content = merge_res.stdout
                        if merge_res.returncode != 0:
                            conflict_detected = True
                    finally:
                        for p in (user_tmp, base_tmp, head_tmp):
                            if os.path.exists(p):
                                os.remove(p)

        # Write file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)

        # Git commit
        if not message:
            stem = file_path.stem
            message = f"Update wiki page '{stem}'"

        commit_env = os.environ.copy()
        commit_env['GIT_AUTHOR_NAME'] = author_name
        commit_env['GIT_AUTHOR_EMAIL'] = author_email
        commit_env['GIT_COMMITTER_NAME'] = author_name
        commit_env['GIT_COMMITTER_EMAIL'] = author_email

        subprocess.run(['git', 'add', git_rel], cwd=str(wiki_root), check=True, capture_output=True)
        commit_res = subprocess.run(
            ['git', 'commit', '-m', message],
            cwd=str(wiki_root),
            capture_output=True,
            text=True,
            env=commit_env,
            check=False
        )

        # Retrieve new commit sha
        sha_res = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=str(wiki_root),
            capture_output=True,
            text=True,
            check=False
        )
        new_sha = sha_res.stdout.strip() if sha_res.returncode == 0 else None

    # Sync to database outside git lock
    db_sync_result = sync_markdown_to_database(git_rel, merged_content)

    return {
        'success': True,
        'rel_path': git_rel,
        'commit_sha': new_sha,
        'conflict': conflict_detected,
        'merged_content': merged_content,
        'db_sync': db_sync_result,
    }


def sync_markdown_to_database(rel_path: str, content: str) -> dict[str, Any]:
    """
    Parses frontmatter and body, updating ConceptNode and KnowledgeEdge in PostgreSQL.
    """
    frontmatter, body = parse_frontmatter_and_body(content)
    slug = frontmatter.get('slug') or Path(rel_path).stem
    node_type = frontmatter.get('type', 'concept')

    if node_type != 'concept':
        return {'synced': False, 'reason': f"Non-concept type: {node_type}"}

    from grips.models import ConceptNode, KnowledgeEdge, Domain

    title = frontmatter.get('title')
    if not title:
        h1 = re.search(r'^#[ \t]+(.+)$', body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else slug.replace('-', ' ').title()

    focus_hint = frontmatter.get('focus_hint', '')
    claims = frontmatter.get('claims') or []

    # Separate narrative content from ## Graph Links
    narrative = body
    links_section = ""
    if "## Graph Links" in body:
        parts = body.split("## Graph Links", 1)
        narrative = parts[0].strip()
        links_section = parts[1]

    # Clean out the # Title from narrative_content
    narrative_lines = []
    for line in narrative.splitlines():
        if line.startswith('# ') and title in line:
            continue
        narrative_lines.append(line)
    clean_narrative = "\n".join(narrative_lines).strip()

    try:
        with transaction.atomic():
            node = ConceptNode.objects.filter(slug=slug).first()
            if not node:
                # Create or assign domain
                domain_name = frontmatter.get('domain') or 'General'
                domain, _ = Domain.objects.get_or_create(name=domain_name)
                node = ConceptNode(domain=domain, slug=slug, title=title)

            node.title = title
            node.focus_hint = focus_hint
            node.structured_claims = claims
            node.narrative_content = clean_narrative
            node.save()

            # Parse and sync KnowledgeEdge connections
            # Form: - **RELATIONSHIP:** [[target-slug]] (justification)
            edges_found = []
            for match in GRAPH_EDGE_PATTERN.finditer(links_section):
                rel_type = match.group('rel')
                target_slug = match.group('target').strip()
                justification = (match.group('just') or '').strip()

                # Validate relationship type
                valid_types = {c[0] for c in KnowledgeEdge.RelationshipTypes.choices}
                if rel_type not in valid_types:
                    rel_type = KnowledgeEdge.RelationshipTypes.RELATED_TO

                target_node = ConceptNode.objects.filter(slug=target_slug).first()
                if target_node and target_node.id != node.id:
                    edges_found.append((rel_type, target_node, justification))

            # Sync edges: update or create current ones
            active_edge_ids = []
            for rel_type, target_node, just in edges_found:
                edge, _ = KnowledgeEdge.objects.update_or_create(
                    source=node,
                    target=target_node,
                    relationship_type=rel_type,
                    defaults={'justification': just}
                )
                active_edge_ids.append(edge.id)

            # Prune removed outgoing edges that were managed under Graph Links
            node.outgoing_edges.exclude(id__in=active_edge_ids).delete()

        return {'synced': True, 'node_id': node.id, 'edges_count': len(active_edge_ids)}

    except Exception as e:
        logger.error("Error syncing wiki page %s to database: %s", rel_path, e, exc_info=True)
        return {'synced': False, 'error': str(e)}


def get_page_history(rel_path: str, limit: int = 20) -> list[dict[str, str]]:
    """Returns the git commit log for a specific wiki file."""
    file_path = assert_safe_path(rel_path)
    wiki_root = get_wiki_root()
    git_rel = str(file_path.relative_to(wiki_root))

    if not file_path.exists():
        return []

    res = subprocess.run(
        ['git', 'log', f'-n{limit}', '--format=%h|%an|%ad|%s', '--date=short', '--', git_rel],
        cwd=str(wiki_root),
        capture_output=True,
        text=True,
        check=False
    )
    history = []
    for line in res.stdout.strip().splitlines():
        if line.strip():
            parts = line.split('|', 3)
            if len(parts) == 4:
                history.append({
                    'sha': parts[0],
                    'author': parts[1],
                    'date': parts[2],
                    'message': parts[3],
                })
    return history


def get_page_diff(rel_path: str, commit_a: str, commit_b: str | None = None) -> str:
    """Returns the unified git diff for a wiki file."""
    file_path = assert_safe_path(rel_path)
    wiki_root = get_wiki_root()
    git_rel = str(file_path.relative_to(wiki_root))

    diff_range = f"{commit_a}..{commit_b}" if commit_b else f"{commit_a}^..{commit_a}"
    res = subprocess.run(
        ['git', 'diff', diff_range, '--', git_rel],
        cwd=str(wiki_root),
        capture_output=True,
        text=True,
        check=False
    )
    return res.stdout


def get_wiki_reflog(limit: int = 50) -> list[dict[str, Any]]:
    """
    Returns the workspace activity log showing commit hash, author, date,
    commit message, and the list of affected files with change status.
    """
    wiki_root = get_wiki_root()
    res = subprocess.run(
        ['git', 'log', f'-n{limit}', '--name-status', '--format=COMMIT:%h|%an|%ad|%s', '--date=short'],
        cwd=str(wiki_root),
        capture_output=True,
        text=True,
        check=False
    )

    commits = []
    current_commit = None

    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('COMMIT:'):
            if current_commit:
                commits.append(current_commit)
            raw = line[len('COMMIT:'):]
            parts = raw.split('|', 3)
            current_commit = {
                'sha': parts[0] if len(parts) > 0 else '',
                'author': parts[1] if len(parts) > 1 else '',
                'date': parts[2] if len(parts) > 2 else '',
                'message': parts[3] if len(parts) > 3 else '',
                'files': []
            }
        elif current_commit and '\t' in line:
            parts = line.split('\t', 1)
            current_commit['files'].append({
                'status': parts[0],
                'path': parts[1]
            })

    if current_commit:
        commits.append(current_commit)

    return commits


def search_wiki_links(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """
    Searches available concepts, references, and documents for autocompletion.
    """
    wiki_root = get_wiki_root()
    q = query.lower().strip()
    results = []

    for path in wiki_root.rglob('*.md'):
        if not path.is_file():
            continue
        slug = path.stem
        rel = str(path.relative_to(wiki_root))

        # Check slug and path match
        score = 0
        if slug.lower().startswith(q):
            score = 4
        elif q in slug.lower():
            score = 3
        elif q in rel.lower():
            score = 1

        # Fast read title from frontmatter or first lines
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                header = "".join([f.readline() for _ in range(12)])
            title_m = re.search(r'title:\s*[\'"]?([^\'"\n]+)[\'"]?', header)
            raw_title = title_m.group(1) if title_m else slug
            title = clean_human_title(raw_title, slug)
            if title.lower().startswith(q):
                score = max(score, 5)
            elif q in title.lower():
                score = max(score, 2)
        except Exception:
            title = clean_human_title(slug, slug)

        if score > 0 or not q:
            doc_type = 'concept'
            if rel.startswith('references'):
                doc_type = 'reference'
            elif rel.startswith('documents'):
                doc_type = 'document'

            results.append({
                'slug': slug,
                'title': title,
                'type': doc_type,
                'rel_path': rel,
                'score': score
            })

    # Sort results by score descending, then title ascending
    results.sort(key=lambda x: (-x['score'], x['title']))
    return results[:limit]


def get_backlinks_for_slug(slug: str) -> list[dict[str, str]]:
    """
    Finds all Markdown files that contain [[slug]].
    Extracts an excerpt of the context around the link.
    """
    wiki_root = get_wiki_root()
    backlinks = []
    target_pattern = re.compile(rf'\[\[{re.escape(slug)}(?:\|[^\]]+)?\]\]', re.IGNORECASE)

    for path in wiki_root.rglob('*.md'):
        if not path.is_file() or path.stem == slug:
            continue
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            match = target_pattern.search(text)
            if match:
                rel = str(path.relative_to(wiki_root))
                # Extract surrounding sentence / snippet
                start = max(0, match.start() - 60)
                end = min(len(text), match.end() + 60)
                snippet = text[start:end].replace('\n', ' ').strip()
                if start > 0:
                    snippet = f"...{snippet}"
                if end < len(text):
                    snippet = f"{snippet}..."

                # Read title
                fm, _ = parse_frontmatter_and_body(text)
                raw_title = fm.get('title') or path.stem
                title = clean_human_title(raw_title, path.stem)

                backlinks.append({
                    'rel_path': rel,
                    'slug': path.stem,
                    'title': title,
                    'snippet': snippet,
                })
        except Exception as e:
            logger.debug("Could not inspect backlink in %s: %s", path, e)

    return backlinks


def cleanup_defective_concepts(dry_run: bool = True) -> dict[str, Any]:
    """
    Finds and cleans up defective concept entries in the database and OKF workspace.

    Defective entries include:
    - Domains with punctuation/empty names (e.g. ', ')
    - Concepts with empty, whitespace, or punctuation-only titles (e.g. ', ')
    - Stub concept nodes with uninformative UUID slugs and placeholder narratives
    """
    from grips.models import Domain, ConceptNode, KnowledgeEdge
    from django.db.models import Q
    import shutil

    wiki_root = get_wiki_root()
    report = {
        'dry_run': dry_run,
        'deleted_concepts': [],
        'deleted_domains': [],
        'deleted_files': [],
        'git_commit': None,
    }

    # 1. Identify defective concepts
    defective_nodes = []
    for node in ConceptNode.objects.select_related('domain').all():
        is_defective = False
        if not node.title or re.match(r'^[\W_]+$', node.title.strip()):
            is_defective = True
        elif node.domain and re.match(r'^[\W_]+$', node.domain.name.strip()):
            is_defective = True
        elif node.narrative_content and 'Awaiting the original narrative to perform the review and revision' in node.narrative_content:
            is_defective = True
        elif re.match(r'^concept-[a-f0-9]{8}$', node.slug) and (not node.narrative_content or len(node.narrative_content.strip()) < 80):
            is_defective = True

        if is_defective:
            defective_nodes.append(node)
            report['deleted_concepts'].append({
                'id': node.id,
                'slug': node.slug,
                'title': node.title,
                'domain': node.domain.name if node.domain else None
            })

    # 2. Identify defective domains
    defective_domains = []
    for d in Domain.objects.all():
        if not d.name or re.match(r'^[\W_]+$', d.name.strip()):
            defective_domains.append(d)
            report['deleted_domains'].append({'id': d.id, 'name': d.name})

    if not dry_run:
        with transaction.atomic():
            if defective_nodes:
                node_ids = [n.id for n in defective_nodes]
                KnowledgeEdge.objects.filter(Q(source_id__in=node_ids) | Q(target_id__in=node_ids)).delete()
                for n in defective_nodes:
                    n.delete()

            for d in defective_domains:
                if not d.concepts.exists():
                    d.delete()

        # 3. Clean up defective files in workspaces/grips_okf
        bad_dir = wiki_root / 'concepts' / '--'
        if bad_dir.exists() and bad_dir.is_dir():
            for f in bad_dir.rglob('*.md'):
                report['deleted_files'].append(str(f.relative_to(wiki_root)))
            shutil.rmtree(bad_dir, ignore_errors=True)

        concepts_dir = wiki_root / 'concepts'
        if concepts_dir.exists():
            for f in concepts_dir.rglob('*.md'):
                if f.is_file():
                    try:
                        with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                            txt = fp.read()
                        if 'Awaiting the original narrative' in txt or re.match(r'^concept-[a-f0-9]{8}$', f.stem):
                            report['deleted_files'].append(str(f.relative_to(wiki_root)))
                            f.unlink()
                    except Exception:
                        pass

        # 4. Git commit in workspaces/grips_okf
        try:
            with _git_lock:
                subprocess.run(['git', 'add', '-A'], cwd=str(wiki_root), check=True, capture_output=True)
                status = subprocess.run(['git', 'status', '--porcelain'], cwd=str(wiki_root), capture_output=True, text=True)
                if status.stdout.strip():
                    commit_res = subprocess.run(
                        ['git', 'commit', '-m', 'chore(wiki): cleanup defective concepts and empty domains'],
                        cwd=str(wiki_root),
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    report['git_commit'] = commit_res.stdout.strip()
        except Exception as e:
            logger.warning("Git commit after cleanup failed: %s", e)

    return report


def get_graph_data(mode: str = 'knowledge') -> dict[str, Any]:
    """
    Constructs graph nodes and edges for Vis-Network and generates Mermaid code.
    Modes:
      - 'knowledge': ConceptNodes and KnowledgeEdges
      - 'citation': Literature References and Citations
      - 'hybrid': Concepts connected to their source Document/Reference
    """
    from grips.models import ConceptNode, KnowledgeEdge
    from grobid_client.models import Reference, Citation
    from background_resources.models import Document

    nodes = []
    edges = []
    mermaid_lines = ["graph TD"]

    if mode == 'knowledge':
        domain_colors = {
            'Causal Statistics': '#6366f1',
            'Oceanography': '#06b6d4',
            'Firefighting Robotics': '#f97316',
            'Fire prevention': '#ef4444',
            'Verbal Architecture': '#8b5cf6',
        }
        concepts = ConceptNode.objects.select_related('domain').all()
        edge_counts = {}
        for edge in KnowledgeEdge.objects.all():
            edge_counts[edge.source_id] = edge_counts.get(edge.source_id, 0) + 1
            edge_counts[edge.target_id] = edge_counts.get(edge.target_id, 0) + 1

        for c in concepts:
            dom_name = c.domain.name if c.domain else "Uncategorized"
            clean_title = clean_human_title(c.title, c.slug)
            color = domain_colors.get(dom_name, '#a855f7')
            deg = edge_counts.get(c.id, 0)
            nodes.append({
                'id': f"c_{c.id}",
                'label': clean_title[:28] + ('...' if len(clean_title) > 28 else ''),
                'title': f"<b>{clean_title}</b><br>Domain: {dom_name}<br>Connections: {deg}",
                'group': dom_name,
                'slug': c.slug,
                'color': color,
                'value': max(10, 10 + deg * 3),
                'shape': 'dot',
            })
            clean_mermaid_label = re.sub(r'["\'\[\]\(\)\{\}<>]', '', clean_title)[:35]
            mermaid_lines.append(f'    c_{c.id}["{clean_mermaid_label}"]')

        for edge in KnowledgeEdge.objects.select_related('source', 'target').all():
            edges.append({
                'from': f"c_{edge.source_id}",
                'to': f"c_{edge.target_id}",
                'label': edge.relationship_type,
                'title': edge.justification or edge.relationship_type,
                'arrows': 'to',
                'color': {'color': '#64748b', 'highlight': '#a5b4fc'}
            })
            mermaid_lines.append(f'    c_{edge.source_id} -->|{edge.relationship_type}| c_{edge.target_id}')

    elif mode == 'citation':
        refs = Reference.objects.select_related('document').all()
        in_counts = {}
        for cit in Citation.objects.filter(target_reference__isnull=False):
            in_counts[cit.target_reference_id] = in_counts.get(cit.target_reference_id, 0) + 1

        for r in refs:
            is_full = bool(r.document)
            clean_title = clean_human_title(r.title or f"Reference {r.id}")
            in_deg = in_counts.get(r.id, 0)
            group = "Full Document" if is_full else "Cited Reference"
            color = '#10b981' if is_full else '#6366f1'
            shape = 'diamond' if is_full else 'dot'
            slug = f"doc-{r.document.id}" if is_full else f"ref-{r.id}"

            nodes.append({
                'id': f"r_{r.id}",
                'label': clean_title[:28] + ('...' if len(clean_title) > 28 else ''),
                'title': f"<b>{clean_title}</b><br>Year: {r.year or 'N/A'}<br>Status: {group}<br>Citations: {in_deg}",
                'group': group,
                'slug': slug,
                'color': color,
                'shape': shape,
                'value': max(12, 12 + in_deg * 4),
            })
            clean_mermaid_label = re.sub(r'["\'\[\]\(\)\{\}<>]', '', clean_title)[:35]
            mermaid_lines.append(f'    r_{r.id}["{clean_mermaid_label}"]')

        for cit in Citation.objects.filter(target_reference__isnull=False).select_related('source_reference', 'target_reference').all():
            edges.append({
                'from': f"r_{cit.source_reference_id}",
                'to': f"r_{cit.target_reference_id}",
                'arrows': 'to',
                'title': cit.context_text[:100] if cit.context_text else 'Citation',
                'color': {'color': '#475569', 'highlight': '#38bdf8'}
            })
            mermaid_lines.append(f'    r_{cit.source_reference_id} --> r_{cit.target_reference_id}')

    else:  # hybrid
        concepts = ConceptNode.objects.select_related('domain', 'source_chunk').all()
        doc_ids_seen = set()
        for c in concepts:
            clean_title = clean_human_title(c.title, c.slug)
            nodes.append({
                'id': f"c_{c.id}",
                'label': clean_title[:25] + ('...' if len(clean_title) > 25 else ''),
                'title': f"<b>Concept: {clean_title}</b>",
                'group': 'Concept',
                'color': '#8b5cf6',
                'shape': 'dot',
                'slug': c.slug,
            })
            if c.source_chunk and c.source_chunk.document:
                d = c.source_chunk.document
                d_node_id = f"d_{d.id}"
                if d.id not in doc_ids_seen:
                    doc_ids_seen.add(d.id)
                    nodes.append({
                        'id': d_node_id,
                        'label': clean_human_title(d.title)[:25] + ('...' if len(d.title) > 25 else ''),
                        'title': f"<b>Document: {d.title}</b>",
                        'group': 'Document',
                        'color': '#10b981',
                        'shape': 'square',
                        'slug': f"doc-{d.id}",
                    })
                edges.append({
                    'from': d_node_id,
                    'to': f"c_{c.id}",
                    'label': 'SOURCES',
                    'arrows': 'to',
                    'color': {'color': '#14b8a6'}
                })

    mermaid_code = "\n".join(mermaid_lines) if len(mermaid_lines) > 1 else "graph TD\n    A[No Data Available]"
    return {
        'mode': mode,
        'nodes': nodes,
        'edges': edges,
        'mermaid_code': mermaid_code,
        'node_count': len(nodes),
        'edge_count': len(edges),
    }


def get_reading_list_analytics() -> dict[str, Any]:
    """
    Computes literature citation centrality, missing PDF wishlist, and concept coverage gaps.
    """
    from django.db.models import Count
    from grobid_client.models import Reference, Citation
    from background_resources.models import Document
    from grips.models import ConceptNode

    # 1. Seminal Papers (most cited within library)
    seminal_refs = (
        Reference.objects.annotate(in_degree=Count('incoming_citations'))
        .filter(in_degree__gt=0)
        .order_by('-in_degree')[:25]
    )
    seminal_papers = []
    for r in seminal_refs:
        is_full = bool(r.document)
        slug = f"doc-{r.document.id}" if is_full else f"ref-{r.id}"
        seminal_papers.append({
            'id': r.id,
            'title': clean_human_title(r.title or 'Unknown Title'),
            'authors': r.authors or 'Unknown Authors',
            'year': r.year or 'Unknown',
            'citations_count': r.in_degree,
            'is_full_document': is_full,
            'pdf_url': r.document.file.url if is_full and r.document.file else None,
            'slug': slug,
        })

    # 2. Acquisition Wishlist (most cited papers that do not have a full PDF)
    wishlist_refs = (
        Reference.objects.filter(document__isnull=True)
        .annotate(in_degree=Count('incoming_citations'))
        .filter(in_degree__gt=0)
        .order_by('-in_degree')[:20]
    )
    wishlist = []
    for r in wishlist_refs:
        citing_docs = [
            clean_human_title(c.source_reference.title)
            for c in Citation.objects.filter(target_reference=r).select_related('source_reference')[:3]
        ]
        wishlist.append({
            'id': r.id,
            'title': clean_human_title(r.title or 'Unknown Title'),
            'authors': r.authors or 'Unknown Authors',
            'year': r.year or 'Unknown',
            'citations_count': r.in_degree,
            'doi': r.doi,
            'citing_sample': citing_docs,
            'slug': f"ref-{r.id}",
        })

    total_concepts = ConceptNode.objects.count()
    concepts_with_source = ConceptNode.objects.filter(source_chunk__isnull=False).count()
    coverage_percentage = round((concepts_with_source / total_concepts * 100), 1) if total_concepts else 0

    return {
        'seminal_papers': seminal_papers,
        'acquisition_wishlist': wishlist,
        'stats': {
            'total_documents': Document.objects.count(),
            'total_references': Reference.objects.count(),
            'full_documents': Reference.objects.filter(document__isnull=False).count(),
            'cited_only_references': Reference.objects.filter(document__isnull=True).count(),
            'total_citations': Citation.objects.count(),
            'total_concepts': total_concepts,
            'coverage_percentage': coverage_percentage,
        }
    }


def get_synthesis_matrix_data() -> dict[str, Any]:
    """
    Builds a literature synthesis cross-tabulation:
    Rows = Ingested Documents, Columns = Core Concepts.
    Cells = Relation / Mentions / Claims.
    """
    from background_resources.models import Document
    from grips.models import ConceptNode

    docs = Document.objects.all().order_by('id')[:10]
    concepts = ConceptNode.objects.select_related('domain', 'source_chunk').order_by('-id')[:12]

    concept_headers = [
        {'id': c.id, 'title': clean_human_title(c.title, c.slug), 'slug': c.slug, 'domain': c.domain.name if c.domain else ''}
        for c in concepts
    ]

    concept_doc_map = {}
    for c in concepts:
        if c.source_chunk and c.source_chunk.document:
            concept_doc_map[c.id] = c.source_chunk.document.id

    rows = []
    for d in docs:
        row_cells = []
        for c in concepts:
            is_sourced = (concept_doc_map.get(c.id) == d.id)
            has_mention = False
            if d.title and c.title.lower() in d.title.lower():
                has_mention = True

            status = 'sourced' if is_sourced else ('mentioned' if has_mention else 'none')
            row_cells.append({
                'concept_id': c.id,
                'status': status,
                'has_link': is_sourced or has_mention
            })
        rows.append({
            'document': {
                'id': d.id,
                'title': clean_human_title(d.title),
                'author': d.author or 'Unknown',
                'slug': f"doc-{d.id}",
                'file_url': d.file.url if d.file else None
            },
            'cells': row_cells
        })

    return {
        'concept_headers': concept_headers,
        'rows': rows,
    }
