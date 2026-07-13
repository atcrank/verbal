import os
import logging
logger = logging.getLogger(__name__)

import json
from django.shortcuts import render, HttpResponse, get_object_or_404
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
        if blueprint_id:
            try:
                bp = CognitiveBlueprint.objects.get(id=blueprint_id)
                conversation.blueprint = bp
                conversation.save()
            except CognitiveBlueprint.DoesNotExist:
                pass

    # 2. Execute Generation (Blueprint or Native)
    if blueprint_id:
        from metacognition.tasks import run_blueprint
        result = run_blueprint(blueprint_id, user_prompt, user_id=request.user.id)
        cleaned_response = result.get('final_response', '')
        if not cleaned_response and 'error' in result:
            cleaned_response = f"**Blueprint Error:** {result['error']}"
            
        input_tokens = service_registry.ai_service.count_conversation_tokens([{"role": "user", "content": user_prompt}])
        output_tokens = service_registry.ai_service.count_conversation_tokens([{"role": "assistant", "content": cleaned_response}])
        
        log = PromptResponseLog.objects.create(
            system_prompt="[Blueprint Execution]", 
            user_prompt=user_prompt,
            conversation=conversation,
            generated_response=cleaned_response, 
            user=request.user,
            input_tokens=input_tokens,
            output_tokens=output_tokens
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
        
        # Trigger Celery task asynchronously
        task_process_documents.delay([doc.id])
        
        return HttpResponse('<span style="color: #2ecc71; font-weight: 500;">✓ Ingestion started.</span>')
    except Exception as e:
        logger.exception("Failed to upload document")
        return HttpResponse(f'<span style="color: #ea5322; font-weight: 500;">Upload failed: {str(e)}</span>', status=500)


@login_required
def list_documents(request):
    """HTMX endpoint to render the recent uploaded documents list."""
    documents = list(Document.objects.all().order_by('-uploaded_at')[:15])
    
    # Workaround: since backend ingestion never sets currently_indexed to True,
    # we dynamically check if any reading strategies have chunk usages in the DB.
    for doc in documents:
        doc.currently_indexed = doc.readingstrategy_set.filter(usages__isnull=False).exists()
        
    return render(request, 'demo_ui/document_list.html', {'documents': documents})


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