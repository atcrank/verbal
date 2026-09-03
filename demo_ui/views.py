import os
import logging
logger = logging.getLogger(__name__)

import json
from django.shortcuts import render, HttpResponse, get_object_or_404
from django.http import JsonResponse, FileResponse, Http404, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from llm_api.models import Conversation, PromptResponseLog
from metacognition.models import CognitiveBlueprint
from llm_api.apps import service_registry
from grips.models import ConceptNode, Domain, KnowledgeEdge

try:
    import markdown
except ImportError:
    markdown = None

def _prepare_log_for_display(log):
    """Helper to cleanly format AI responses and User Prompts for HTML rendering."""
    # 1. Format AI Response (Auto-detect raw JSON)
    ai_text = str(log.generated_response or "").strip()
    if (ai_text.startswith('{') and ai_text.endswith('}')) or (ai_text.startswith('[') and ai_text.endswith(']')):
        try:
            parsed = json.loads(ai_text)
            ai_text = f"```json\n{json.dumps(parsed, indent=2)}\n```"
        except Exception:
            pass
                
    if markdown:
        log.html_response = mark_safe(markdown.markdown(ai_text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']))
    else:
        from django.utils.html import linebreaks
        log.html_response = linebreaks(ai_text)
        
    # 2. Format User Prompt (Add linebreaks so paragraphs display correctly)
    from django.utils.html import linebreaks
    log.html_user_prompt = mark_safe(linebreaks(log.user_prompt or ""))
    
    # 3. Format RAG selections if they are JSON
    if log.rag_selections and isinstance(log.rag_selections, list):
        log.html_rag_selections = mark_safe(f"<pre style='font-size: 0.75rem; white-space: pre-wrap;'>{json.dumps(log.rag_selections, indent=2)}</pre>")
    else:
        log.html_rag_selections = log.rag_selections
        
    return log

@login_required
def index(request):
    """Renders the main Demo UI shell."""
    conversations = Conversation.objects.filter(user=request.user).exclude(user__username="NightManager")
    blueprints = CognitiveBlueprint.objects.exclude(name__startswith="NightManager").exclude(name="The Architect")
    
    return render(request, 'demo_ui/index.html', {
        'conversations': conversations,
        'blueprints': blueprints,
    })

@login_required
def search_knowledge_base(request):
    """HTMX endpoint to perform a unified search across RAG and Grips."""
    query = request.GET.get('q', request.GET.get('user_prompt', '')).strip()
    
    if not query:
        return HttpResponse('<div class="conv-date" style="text-align: center; margin-top: 20px;">Search results will appear here.</div>')

    from background_resources.retrieval import unified_retrieve
    
    retrieval_results = unified_retrieve(
        query=query,
        rag_service=service_registry.rag_service,
        grips_service=service_registry.grips_service,
        rag_k=5,
        grips_k=4,
    )

    unified_results = []
    
    for r in retrieval_results:
        if r.source == "grips":
            source_doc_name = None
            if r.source_chunk_id:
                try:
                    from background_resources.models import RAGChunk
                    chunk = RAGChunk.objects.get(id=r.source_chunk_id)
                    source_doc_name = chunk.metadata.get('filename', 'Unknown Document')
                except Exception:
                    pass
            unified_results.append({
                'type': 'grip',
                'doc': r.doc,
                'source_doc_name': source_doc_name
            })
        else:
            unified_results.append({
                'type': 'rag',
                'doc': r.doc
            })

    return render(request, 'demo_ui/search_results.html', {
        'unified_results': unified_results,
        'query': query
    })

@login_required
def get_conversation(request, conversation_id):
    """HTMX endpoint to load an existing conversation's history."""
    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    
    # Logs are ordered by -created_at, so we reverse them for top-to-bottom chat flow
    logs = list(conversation.logs.all())[::-1]
    
    for log in logs:
        _prepare_log_for_display(log)

    response_html = render(request, 'demo_ui/chat_history.html', {
        'conversation': conversation,
        'logs': logs
    }).content.decode('utf-8')
    
    # Append workspace files OOB swap
    files = _get_workspace_files_list(conversation)
    files_html = render(request, 'demo_ui/workspace_files.html', {
        'files': files,
        'conversation_id': str(conversation.id)
    }).content.decode('utf-8')

    return HttpResponse(response_html + "\n" + files_html)

@login_required
def send_message(request):
    """HTMX endpoint to process a prompt, run AI/Blueprint, and return the new bubbles."""
    user_prompt = request.POST.get('user_prompt', '').strip()
    conversation_id = request.POST.get('conversation_id', '')
    blueprint_id = request.POST.get('blueprint_id', '')
    
    if not user_prompt:
        return HttpResponse("")
        
    # 1. Resolve or Create Conversation
    if conversation_id:
        conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    else:
        conversation = Conversation.objects.create(
            user=request.user, 
            title=user_prompt[:50] + ("..." if len(user_prompt) > 50 else "")
        )

    # 2. Execute Generation (Blueprint or Native)
    if blueprint_id:
        from uuid import uuid4
        from metacognition.tasks import task_run_blueprint_async
        
        run_id = str(uuid4())
        task_run_blueprint_async.enqueue(
            blueprint_id=int(blueprint_id),
            user_prompt=user_prompt,
            conversation_id=str(conversation.id),
            user_id=request.user.id,
            run_id=run_id
        )
        
        streaming_markup = f"""<div id="blueprint-exec-{run_id}" data-signals="{{isStreaming: true}}" data-on-load="@get('/api/meta/stream_blueprint/?run_id={run_id}')">
<div id="blueprint-status" class="agent-step active">
    <span class="badge">Dispatched</span>
    <strong>Executing cognitive blueprint asynchronously...</strong>
</div>
<div id="tool-approval-container"></div>
<div id="monologue-stream"></div>
<div id="blueprint-final-response"></div>
</div>"""
        
        log = PromptResponseLog.objects.create(
            system_prompt="[Async Blueprint Execution]", 
            user_prompt=user_prompt,
            conversation=conversation,
            generated_response=streaming_markup, 
            user=request.user,
            input_tokens=0,
            output_tokens=0
        )
    else:
        messages = conversation.as_messages()
        if not messages:
            messages.append({"role": "system", "content": "You are a helpful study design assistant."})
            
        included_context_json = request.POST.get('included_context', '[]')
        try:
            included_context = json.loads(included_context_json)
        except Exception:
            included_context = []
            
        rag_selections = []
        rag_text = ""
        
        for item in included_context:
            model_type = item.get("model")
            item_id = item.get("id")
            
            if model_type == "RAGChunk" and service_registry.rag_service:
                docs = service_registry.rag_service.store.mget([item_id])
                if docs and docs[0]:
                    d = docs[0]
                    rag_selections.append({"model": "RAGChunk", "id": item_id, "preview": d.page_content[:150] + "..."})
                    rag_text += f"\nSource: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}\n"
                    
            elif model_type == "ConceptNode":
                content = item.get("content", "Concept content unavailable")
                rag_selections.append({"model": "ConceptNode", "id": item_id, "preview": content[:150] + "..."})
                rag_text += f"\nConcept:\n{content}\n"
                
            elif model_type == "Document" and service_registry.rag_service:
                # If they dropped a whole document, maybe just add a reference to it
                content = item.get("content", "Document dropped")
                rag_selections.append({"model": "Document", "id": item_id, "preview": content})
                rag_text += f"\nReference Document: {content}\n"
                
            elif model_type == "Conversation":
                content = item.get("content", "Conversation dropped")
                rag_selections.append({"model": "Conversation", "id": item_id, "preview": content})
                rag_text += f"\nPrevious Conversation Reference: {content}\n"
        
        if rag_text:
            messages_for_llm = messages + [{"role": "user", "content": user_prompt + "\n\nRelevant Context:\n" + rag_text}]
        else:
            messages_for_llm = messages + [{"role": "user", "content": user_prompt}]
        
        input_tokens = service_registry.ai_service.count_conversation_tokens(messages_for_llm)
        [response] = service_registry.ai_service.generate_response2(messages=messages_for_llm, max_new_tokens=1000, log_kwargs={"skip_log": True}, user=request.user)
        cleaned_response = service_registry.ai_service.clean_response(response)
        
        output_tokens = service_registry.ai_service.count_conversation_tokens([{"role": "assistant", "content": cleaned_response}])
        
        log = PromptResponseLog.objects.create(
            system_prompt=messages[0]["content"], user_prompt=user_prompt, rag_selections=rag_selections, 
            conversation=conversation, generated_response=cleaned_response, user=request.user,
            input_tokens=input_tokens, output_tokens=output_tokens
        )
        
    # 3. Render formatting
    _prepare_log_for_display(log)

    response_html = render(request, 'demo_ui/chat_message.html', {'log': log, 'conversation': conversation}).content.decode('utf-8')
    
    # Append workspace files OOB swap
    files = _get_workspace_files_list(conversation)
    files_html = render(request, 'demo_ui/workspace_files.html', {
        'files': files,
        'conversation_id': str(conversation.id)
    }).content.decode('utf-8')

    return HttpResponse(response_html + "\n" + files_html)


from django.views.decorators.http import require_POST
from background_resources.models import Document
from background_resources.tasks import task_process_documents

@login_required
@require_POST
def upload_document(request):
    """HTMX endpoint to upload a document and trigger background RAG processing."""
    title = request.POST.get('title', '').strip()
    author = request.POST.get('author', '').strip() or None
    uploaded_file = request.FILES.get('file')

    if not title or not uploaded_file:
        return HttpResponse('<span style="color: #ea5322; font-weight: 500;">Title and file are required.</span>', status=400)

    try:
        # Create Document instance
        doc = Document.objects.create(
            title=title,
            author=author,
            file=uploaded_file
        )
        
        # Trigger task asynchronously
        task_process_documents.enqueue([doc.id])
        
        return HttpResponse('<span style="color: #059669; font-weight: 500;">Ingestion started.</span>')
    except Exception as e:
        logger.exception("Failed to upload document")
        return HttpResponse(f'<span style="color: #ea5322; font-weight: 500;">Upload failed: {str(e)}</span>', status=500)


@login_required
def list_documents(request):
    """HTMX endpoint to render the recent uploaded documents list with accurate status."""
    from background_resources.models import Document, RAGChunk
    from verbal_tasks.models import TaskRecord, TaskRecordStatus

    documents = list(Document.objects.all().order_by('-uploaded_at')[:20])

    for doc in documents:
        # Check if chunks exist in database
        chunk_count = RAGChunk.objects.filter(metadata__document_id=str(doc.id)).count()
        if chunk_count == 0:
            chunk_count = doc.readingstrategy_set.filter(usages__isnull=False).count()

        doc.chunk_count = chunk_count

        if doc.currently_indexed or chunk_count > 0:
            doc.ingestion_status = "INDEXED"
            doc.status_badge_class = "badge-indexed"
            doc.status_label = f"Indexed ({chunk_count})" if chunk_count > 0 else "Indexed"
        else:
            # Check TaskRecord for active or queued jobs
            task = TaskRecord.objects.filter(
                task_path__contains='task_process_documents',
                args_json__contains=doc.id
            ).order_by('-enqueued_at').first()

            if task:
                if task.status == TaskRecordStatus.RUNNING:
                    doc.ingestion_status = "INGESTING"
                    doc.status_badge_class = "badge-ingesting"
                    doc.status_label = "Ingesting"
                elif task.status == TaskRecordStatus.READY:
                    doc.ingestion_status = "QUEUED"
                    doc.status_badge_class = "badge-queued"
                    doc.status_label = "Queued"
                elif task.status == TaskRecordStatus.FAILED:
                    doc.ingestion_status = "FAILED"
                    doc.status_badge_class = "badge-failed"
                    doc.status_label = "Failed"
                else:
                    doc.ingestion_status = "INDEXED"
                    doc.status_badge_class = "badge-indexed"
                    doc.status_label = f"Indexed ({chunk_count})" if chunk_count > 0 else "Indexed"
            else:
                doc.ingestion_status = "PENDING"
                doc.status_badge_class = "badge-queued"
                doc.status_label = "Pending"

    return render(request, 'demo_ui/document_list.html', {'documents': documents})


@login_required
@require_POST
def trigger_document_ingestion(request, document_id):
    """HTMX endpoint to manually trigger or re-enqueue ingestion for a document."""
    from background_resources.models import Document

    doc = get_object_or_404(Document, id=document_id)
    task_process_documents.enqueue([doc.id])
    return list_documents(request)


@login_required
@require_POST
def branch_conversation(request, log_id):
    """HTMX endpoint to fork a conversation DAG from a specific assistant response."""
    source_log = get_object_or_404(PromptResponseLog, id=log_id)
    original_conv = source_log.conversation

    # Trace ancestor path from root to source_log
    ancestor_logs = []
    curr = source_log
    while curr:
        ancestor_logs.append(curr)
        curr = curr.parent_log
    ancestor_logs.reverse()

    turn_count = len(ancestor_logs)
    base_title = original_conv.title if original_conv else "Conversation"
    branch_title = f"Branch: {base_title[:28]} (Turn {turn_count})"

    new_conv = Conversation.objects.create(
        user=request.user,
        title=branch_title,
    )

    # Clone historical logs into new conversation to isolate the new branch
    log_map = {}
    for old_log in ancestor_logs:
        parent_clone = log_map.get(old_log.parent_log_id)
        cloned_log = PromptResponseLog.objects.create(
            user=request.user,
            conversation=new_conv,
            parent_log=parent_clone,
            system_prompt=old_log.system_prompt,
            user_prompt=old_log.user_prompt,
            generated_response=old_log.generated_response,
            rag_selections=old_log.rag_selections,
            input_tokens=old_log.input_tokens,
            output_tokens=old_log.output_tokens,
            model_name=old_log.model_name,
            reasoning_step=old_log.reasoning_step,
            step_status=old_log.step_status,
        )
        log_map[old_log.id] = cloned_log

    logs = list(new_conv.logs.order_by('created_at'))
    for log in logs:
        _prepare_log_for_display(log)

    chat_html = render(request, 'demo_ui/chat_history.html', {
        'conversation': new_conv,
        'logs': logs,
    }).content.decode('utf-8')

    # Update conversation list in left sidebar via OOB swap
    user_conversations = Conversation.objects.filter(user=request.user).exclude(user__username="NightManager")
    sidebar_items_html = ""
    for conv in user_conversations:
        active_cls = " active" if conv.id == new_conv.id else ""
        sidebar_items_html += f"""
        <div class="conv-item{active_cls}" draggable="true"
            ondragstart="event.dataTransfer.setData('application/json', JSON.stringify({{model: 'Conversation', id: '{conv.id}', content: 'Conversation id {conv.id}', preview: '{conv.title}'}}))"
            hx-get="/demo/conversation/{conv.id}/" hx-target="#chat-history" hx-swap="innerHTML">
            <div class="conv-title">{conv.title}</div>
            <div class="conv-date">{conv.start_time.strftime('%b %d, %Y - %I:%M %p')}</div>
        </div>
        """

    sidebar_oob = f'<div class="sidebar-content" id="conversation-list" hx-swap-oob="innerHTML">{sidebar_items_html}</div>'

    # Workspace files OOB swap
    files = _get_workspace_files_list(new_conv)
    files_html = render(request, 'demo_ui/workspace_files.html', {
        'files': files,
        'conversation_id': str(new_conv.id)
    }).content.decode('utf-8')

    return HttpResponse(chat_html + "\n" + sidebar_oob + "\n" + files_html)


@login_required
def preview_context_item(request):
    """HTMX endpoint returning content preview and estimated token count for a dropped item."""
    model_type = request.GET.get('model', '')
    item_id = request.GET.get('id', '')

    title = f"{model_type} ({item_id})"
    content = ""

    if model_type == "RAGChunk":
        from background_resources.models import RAGChunk
        chunk = RAGChunk.objects.filter(id=item_id).first()
        if chunk:
            title = f"RAG Chunk: {chunk.metadata.get('filename', 'Unknown Document')}"
            content = chunk.page_content
    elif model_type == "ConceptNode":
        from grips.models import ConceptNode
        node = ConceptNode.objects.filter(id=item_id).first()
        if node:
            title = f"Concept: {node.title}"
            content = f"Title: {node.title}\nDomain: {node.domain.name}\n\nNarrative:\n{node.narrative_content or 'No narrative generated yet.'}\n\nStructured Claims:\n{json.dumps(node.structured_claims or [], indent=2)}"
    elif model_type == "Document":
        from background_resources.models import Document
        doc = Document.objects.filter(id=item_id).first()
        if doc:
            title = f"Document: {doc.title}"
            content = f"Title: {doc.title}\nAuthor: {doc.author or 'Unknown'}\nFile: {doc.file.name}\nUploaded: {doc.uploaded_at.strftime('%Y-%m-%d %H:%M')}"
    elif model_type == "Conversation":
        conv = Conversation.objects.filter(id=item_id).first()
        if conv:
            title = f"Conversation: {conv.title}"
            logs = list(conv.logs.order_by('created_at')[:5])
            content = "\n\n".join([f"User: {l.user_prompt}\nAssistant: {str(l.generated_response)[:300]}..." for l in logs])

    # Count tokens
    try:
        from llm_api.apps import service_registry
        token_count = service_registry.ai_service.count_conversation_tokens([{"role": "user", "content": content}])
    except Exception:
        token_count = max(1, len(content.split()))

    return render(request, 'demo_ui/context_preview_modal.html', {
        'title': title,
        'model_type': model_type,
        'item_id': item_id,
        'content': content,
        'token_count': token_count,
    })


@login_required
def calculate_context_tokens(request):
    """JSON helper endpoint to calculate token counts for an array of dropped context items."""
    try:
        items = json.loads(request.POST.get('included_context', '[]'))
    except Exception:
        items = []

    total_tokens = 0
    item_tokens = {}

    for item in items:
        m = item.get('model')
        i_id = item.get('id')
        txt = item.get('content', '') or item.get('preview', '')
        try:
            from llm_api.apps import service_registry
            t = service_registry.ai_service.count_conversation_tokens([{"role": "user", "content": txt}])
        except Exception:
            t = max(1, len(txt.split()))
        key = f"{m}_{i_id}"
        item_tokens[key] = t
        total_tokens += t

    return JsonResponse({'total_tokens': total_tokens, 'item_tokens': item_tokens})


from django.http import FileResponse, Http404, HttpResponseForbidden
from django.utils import timezone

def _get_workspace_files_list(conversation):
    """Retrieves file details (names, sizes, mtimes) inside conversation workspace."""
    workspace_dir = conversation.get_workspace_dir()
    if not os.path.exists(workspace_dir):
        return []
    
    file_list = []
    for root, dirs, files in os.walk(workspace_dir):
        if '.git' in dirs:
            dirs.remove('.git')  # Hide version control internals
        for name in files:
            file_path = os.path.join(root, name)
            rel_path = os.path.relpath(file_path, workspace_dir)
            try:
                info = os.stat(file_path)
                size_kb = round(info.st_size / 1024, 2)
                file_list.append({
                    'name': rel_path,
                    'size': f"{size_kb} KB" if size_kb > 0 else f"{info.st_size} bytes",
                    'modified': timezone.datetime.fromtimestamp(info.st_mtime, tz=timezone.get_current_timezone())
                })
            except OSError:
                pass
    # Sort files alphabetically by name
    file_list.sort(key=lambda x: x['name'])
    return file_list


@login_required
def download_file(request, conversation_id, filename):
    """Secure endpoint to download a generated file from a conversation workspace."""
    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    workspace_dir = os.path.abspath(conversation.get_workspace_dir())
    
    if not os.path.exists(workspace_dir):
        raise Http404("Workspace directory not found")
        
    # Safe path resolution to prevent directory traversal
    file_path = os.path.abspath(os.path.join(workspace_dir, filename))
    if not file_path.startswith(workspace_dir):
        return HttpResponseForbidden("Access denied: path traversal attempt detected.")
        
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        raise Http404("Requested file not found")
        
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=os.path.basename(file_path))


@login_required
def grips_explorer_tab(request):
    """HTMX endpoint to render the Grips Explorer."""
    query = request.GET.get('q', '').strip()
    
    if query:
        # Search ConceptNodes
        # Simplistic substring search on title or narrative_content
        concepts = ConceptNode.objects.filter(title__icontains=query) | ConceptNode.objects.filter(narrative_content__icontains=query)
        concepts = concepts.select_related('domain').order_by('domain__name', 'title')[:50]
        
        return render(request, 'demo_ui/grips_search_results.html', {
            'concepts': concepts,
            'query': query
        })
    else:
        # Show Hierarchy Overview
        domains = Domain.objects.prefetch_related('concepts').all()
        # For a true hierarchy we might just show domains, and then root concepts
        domain_data = []
        for d in domains:
            # We want true root nodes: concepts with no incoming INCLUDES edges.
            roots = d.concepts.filter(incoming_edges__isnull=True).order_by('title')
            domain_data.append({
                'domain': d,
                'concepts': roots
            })
            
        return render(request, 'demo_ui/grips_hierarchy.html', {
            'domain_data': domain_data
        })


@login_required
def grips_concept_children(request, concept_id):
    """HTMX endpoint to lazily load children of a ConceptNode in the hierarchy."""
    node = get_object_or_404(ConceptNode, id=concept_id)
    # Find all children where this node is the source of an INCLUDES edge
    children_edges = node.outgoing_edges.filter(relationship_type='INCLUDES').select_related('target')
    children = [edge.target for edge in children_edges]
    
    return render(request, 'demo_ui/grips_hierarchy_children.html', {
        'children': children
    })


@login_required
@require_POST
def fill_grips_stub(request, concept_id):
    """Triggers the Grips Stub Filler blueprint for a specific ConceptNode."""
    from metacognition.models import CognitiveBlueprint
    from metacognition.tasks import task_run_blueprint_async
    try:
        bp = CognitiveBlueprint.objects.filter(name__icontains="Stub Filler").first()
        if not bp:
            from metacognition.seed import seed_cognitive_blueprints
            seed_cognitive_blueprints()
            bp = CognitiveBlueprint.objects.filter(name="Grips Stub Filler").first()

        if not bp:
            return HttpResponse('<span style="color: #b91c1c; font-size: 0.72rem; font-weight: 500;">Blueprint missing.</span>', status=404)

        task_run_blueprint_async.enqueue(
            blueprint_id=bp.id,
            user_prompt=str(concept_id),
            user_id=request.user.id
        )
        return HttpResponse('<span style="color: #047857; font-size: 0.72rem; font-weight: 600;">Filler queued.</span>')
    except Exception as e:
        logger.exception("Failed to queue Grips Stub Filler")
        return HttpResponse(f'<span style="color: #b91c1c; font-size: 0.72rem; font-weight: 500;">Error: {str(e)}</span>', status=500)