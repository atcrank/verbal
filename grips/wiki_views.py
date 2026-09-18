import os
import json
import re
from pathlib import Path
from django.shortcuts import render, redirect
from django.http import JsonResponse, Http404, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
import markdown as md_lib

from grips.wiki_service import (
    get_wiki_root,
    assert_safe_path,
    resolve_wiki_rel_path,
    get_page_content_and_sha,
    save_and_commit_page,
    get_page_history,
    get_page_diff,
    get_wiki_reflog,
    search_wiki_links,
    WIKILINK_PATTERN,
    get_graph_data,
    get_reading_list_analytics,
    get_synthesis_matrix_data,
)

_resolve_wiki_rel_path = resolve_wiki_rel_path


def _render_wikilinks_html(markdown_text: str) -> str:
    """
    Converts [[slug]] or [[slug|Display Text]] into clickable wiki links,
    then renders the markdown into safe HTML.
    """
    def replace_link(match):
        target = match.group(1).strip()
        label = (match.group(2) or target).strip()
        url = f"/wiki/{target}/"
        return f'<a href="{url}" class="wiki-link text-indigo-600 dark:text-indigo-400 hover:underline font-medium">[[{label}]]</a>'

    processed_md = WIKILINK_PATTERN.sub(replace_link, markdown_text)
    html = md_lib.markdown(
        processed_md,
        extensions=['extra', 'codehilite', 'tables', 'toc']
    )
    return html


def wiki_index(request):
    """
    Landing page for the Grips OKF Wiki. Shows domain tree, statistics,
    and recent activity stream.
    """
    wiki_root = get_wiki_root()
    domains = {}

    for concept_file in (wiki_root / 'concepts').rglob('*.md'):
        parts = concept_file.relative_to(wiki_root / 'concepts').parts
        if parts:
            domain_name = parts[0].replace('-', ' ').title()
            domains.setdefault(domain_name, []).append({
                'slug': concept_file.stem,
                'title': concept_file.stem.replace('-', ' ').title(),
                'rel_path': str(concept_file.relative_to(wiki_root)),
            })

    recent_activity = get_wiki_reflog(limit=10)

    context = {
        'domains': sorted(domains.items()),
        'total_concepts': sum(len(c) for c in domains.values()),
        'recent_activity': recent_activity,
    }
    return render(request, 'grips/wiki/index.html', context)


def wiki_page(request, path=""):
    """
    Displays a wiki page in read-only mode with breadcrumbs, rendered markdown,
    incoming backlinks, and a toggle button to enter Milkdown edit mode.
    """
    if not path or path.strip('/') == '':
        return redirect('grips:wiki_index')

    try:
        rel_path = _resolve_wiki_rel_path(path)
        assert_safe_path(rel_path)
    except PermissionError as e:
        raise Http404(str(e))

    page_data = get_page_content_and_sha(rel_path)
    rendered_body_html = mark_safe(_render_wikilinks_html(page_data['body']))

    context = {
        'page': page_data,
        'rendered_body_html': rendered_body_html,
        'rel_path': rel_path,
        'slug': page_data.get('slug') or Path(rel_path).stem,
    }
    return render(request, 'grips/wiki/page.html', context)


@require_http_methods(["POST"])
def wiki_save(request, path=""):
    """
    Saves updated markdown content, commits to Git, and syncs to PostgreSQL models.
    Accepts JSON: { markdown: str, message: str, base_sha: str }
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON body.")

    markdown_content = data.get('markdown', '')
    commit_message = data.get('message', '').strip()
    base_sha = data.get('base_sha')

    if not markdown_content:
        return HttpResponseBadRequest("Markdown content cannot be empty.")

    try:
        rel_path = _resolve_wiki_rel_path(path)
        assert_safe_path(rel_path)
    except PermissionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=403)

    user_name = request.user.get_full_name() or request.user.username if request.user.is_authenticated else "Wiki Contributor"
    user_email = request.user.email if request.user.is_authenticated and request.user.email else "wiki@verbal.local"

    result = save_and_commit_page(
        rel_path=rel_path,
        content=markdown_content,
        author_name=user_name,
        author_email=user_email,
        message=commit_message,
        base_sha=base_sha
    )

    return JsonResponse(result)


def wiki_query_links(request):
    """
    JSON API for debounced autocomplete when a user types [[ in Milkdown.
    Query parameter: ?q=<search term>
    """
    q = request.GET.get('q', '').strip()
    results = search_wiki_links(q, limit=15)
    return JsonResponse({'results': results})


def wiki_history(request, path=""):
    """
    Displays the commit history log and diffs for a specific wiki file.
    """
    try:
        rel_path = _resolve_wiki_rel_path(path)
        assert_safe_path(rel_path)
    except PermissionError as e:
        raise Http404(str(e))

    history = get_page_history(rel_path, limit=25)
    selected_sha = request.GET.get('sha')
    diff_output = ""

    if selected_sha:
        diff_output = get_page_diff(rel_path, selected_sha)

    context = {
        'rel_path': rel_path,
        'title': Path(rel_path).stem.replace('-', ' ').title(),
        'history': history,
        'selected_sha': selected_sha,
        'diff_output': diff_output,
    }
    return render(request, 'grips/wiki/history.html', context)


def wiki_activity(request):
    """
    Global activity log showing commits and affected files across grips_okf.
    """
    limit = int(request.GET.get('limit', 50))
    reflog = get_wiki_reflog(limit=limit)

    selected_sha = request.GET.get('sha')
    selected_file = request.GET.get('file')
    diff_output = ""
    if selected_sha and selected_file:
        try:
            assert_safe_path(selected_file)
            diff_output = get_page_diff(selected_file, selected_sha)
        except Exception:
            diff_output = ""

    context = {
        'reflog': reflog,
        'selected_sha': selected_sha,
        'selected_file': selected_file,
        'diff_output': diff_output,
    }
    return render(request, 'grips/wiki/activity.html', context)


def wiki_graph(request):
    """
    Interactive network graph visualization view supporting Knowledge, Citation,
    and Hybrid modes. Renders vis-network.js canvas and Mermaid code toggle.
    """
    mode = request.GET.get('mode', 'knowledge').lower()
    if mode not in ('knowledge', 'citation', 'hybrid'):
        mode = 'knowledge'

    graph_data = get_graph_data(mode)
    context = {
        'mode': mode,
        'graph_data': graph_data,
        'graph_data_json': mark_safe(json.dumps(graph_data)),
    }
    return render(request, 'grips/wiki/graph.html', context)


def wiki_graph_data(request):
    """
    JSON API for dynamic, client-side switching of graph visualization modes.
    """
    mode = request.GET.get('mode', 'knowledge').lower()
    if mode not in ('knowledge', 'citation', 'hybrid'):
        mode = 'knowledge'

    graph_data = get_graph_data(mode)
    return JsonResponse(graph_data)


def wiki_reading_list(request):
    """
    Academic research analytics view: seminal papers ranked by citation in-degree,
    acquisition wishlist for missing PDFs, and concept coverage statistics.
    """
    data = get_reading_list_analytics()
    return render(request, 'grips/wiki/research_reading.html', data)


def wiki_synthesis_matrix(request):
    """
    Literature synthesis cross-tabulation: documents vs core concepts coverage grid.
    """
    data = get_synthesis_matrix_data()
    return render(request, 'grips/wiki/synthesis_matrix.html', data)

