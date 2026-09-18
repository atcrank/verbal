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


def get_page_content_and_sha(rel_path: str) -> dict[str, Any]:
    """
    Reads a wiki markdown page, extracts frontmatter, markdown, and current Git SHA.
    """
    file_path = assert_safe_path(rel_path)
    wiki_root = get_wiki_root()

    if not file_path.is_file():
        return {
            'exists': False,
            'rel_path': rel_path,
            'title': Path(rel_path).stem.replace('-', ' ').title(),
            'frontmatter': {},
            'markdown': '',
            'body': '',
            'commit_sha': None,
            'last_modified': None,
            'backlinks': [],
        }

    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        markdown = f.read()

    frontmatter, body = parse_frontmatter_and_body(markdown)
    title = frontmatter.get('title')
    if not title:
        # Fallback to first H1
        h1_match = re.search(r'^#[ \t]+(.+)$', body, re.MULTILINE)
        title = h1_match.group(1).strip() if h1_match else file_path.stem.replace('-', ' ').title()

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

    slug = frontmatter.get('slug') or file_path.stem
    backlinks = get_backlinks_for_slug(slug)

    return {
        'exists': True,
        'rel_path': str(file_path.relative_to(wiki_root)),
        'slug': slug,
        'title': title,
        'frontmatter': frontmatter,
        'markdown': markdown,
        'body': body,
        'commit_sha': commit_sha,
        'last_modified': last_modified,
        'backlinks': backlinks,
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
            title = title_m.group(1) if title_m else slug.replace('-', ' ').title()
            if title.lower().startswith(q):
                score = max(score, 5)
            elif q in title.lower():
                score = max(score, 2)
        except Exception:
            title = slug.replace('-', ' ').title()

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
                title = fm.get('title') or path.stem.replace('-', ' ').title()

                backlinks.append({
                    'rel_path': rel,
                    'slug': path.stem,
                    'title': title,
                    'snippet': snippet,
                })
        except Exception as e:
            logger.debug("Could not inspect backlink in %s: %s", path, e)

    return backlinks
