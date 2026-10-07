import os
import logging
logger = logging.getLogger(__name__)

import json
from django.shortcuts import render, HttpResponse, get_object_or_404
from django.http import JsonResponse, FileResponse, Http404, HttpResponseForbidden, StreamingHttpResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from llm_api.models import Conversation, PromptResponseLog
from metacognition.models import CognitiveBlueprint
from metacognition.governance import evaluate_blueprint_governance
from llm_api.apps import service_registry
from grips.models import ConceptNode, Domain, KnowledgeEdge
from llm_api.tasks import task_generate_response
from verbal_tasks.postgres_events import subscribe_pg_events_sync

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
                
    if ai_text.startswith(('<div id="blueprint-exec-', '<div id="gen-stream-')):
        log.html_response = mark_safe(ai_text)
    elif markdown:
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


def group_conversation_logs_for_display(logs):
    """
    Groups intermediate blueprint logs under their primary/final response turn.
    Extracts intermediate reasoning steps into `log.thinking_steps` and attaches
    the lightweight node visualizer (`log.visualizer_html`).
    """
    from metacognition.visualizer import render_blueprint_visualizer_html

    display_logs = []
    i = 0
    n = len(logs)

    while i < n:
        log = logs[i]

        # If this log is part of an async streaming session, pass it as-is
        resp = str(log.generated_response or "")
        if resp.startswith('<div id="blueprint-exec-'):
            _prepare_log_for_display(log)
            display_logs.append(log)
            i += 1
            continue

        # Check if this log is part of a blueprint run
        if log.blueprint_id:
            blueprint = log.blueprint
            run_logs = [log]
            j = i + 1

            # Collect consecutive logs that belong to the same blueprint run
            while j < n and logs[j].blueprint_id == log.blueprint_id:
                if logs[j].parent_log_id == run_logs[-1].id or logs[j].created_at >= run_logs[-1].created_at:
                    run_logs.append(logs[j])
                    j += 1
                else:
                    break

            if len(run_logs) > 1:
                initial_log = run_logs[0]
                leaf_log = run_logs[-1]

                intermediate_steps = []
                completed_step_names = []
                failed_step_names = []
                total_in = sum(l.input_tokens for l in run_logs)
                total_out = sum(l.output_tokens for l in run_logs)

                for step_idx, s_log in enumerate(run_logs[:-1], start=1):
                    step_name = s_log.reasoning_step.name if s_log.reasoning_step else f"Step {step_idx}"
                    is_failed = (s_log.step_status == "FAILURE")
                    if is_failed:
                        failed_step_names.append(step_name)
                    else:
                        completed_step_names.append(step_name)

                    step_text = str(s_log.generated_response or "").strip()
                    html_content = mark_safe(markdown.markdown(step_text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])) if markdown else step_text

                    intermediate_steps.append({
                        "step_badge": "❌" if is_failed else "✅",
                        "step_title": f"Step {step_idx}: {step_name}",
                        "html_content": html_content,
                    })

                leaf_step_name = leaf_log.reasoning_step.name if leaf_log.reasoning_step else f"Step {len(run_logs)}"
                if leaf_log.step_status == "FAILURE":
                    failed_step_names.append(leaf_step_name)
                else:
                    completed_step_names.append(leaf_step_name)

                leaf_log.user_prompt = initial_log.user_prompt
                leaf_log.thinking_steps = intermediate_steps
                leaf_log.input_tokens = total_in
                leaf_log.output_tokens = total_out
                leaf_log.visualizer_html = render_blueprint_visualizer_html(
                    blueprint,
                    completed_steps=completed_step_names,
                    active_step=None,
                    failed_steps=failed_step_names
                )
                _prepare_log_for_display(leaf_log)
                display_logs.append(leaf_log)
                i = j
                continue
            else:
                log.thinking_steps = []
                log.visualizer_html = render_blueprint_visualizer_html(blueprint)
                _prepare_log_for_display(log)
                display_logs.append(log)
                i += 1
                continue
        else:
            _prepare_log_for_display(log)
            display_logs.append(log)
            i += 1

    return display_logs


@login_required
def index(request):
    """Renders the main Demo UI shell."""
    conversations = Conversation.objects.filter(user=request.user).exclude(user__username="NightManager")
    try:
        blueprints = list(
            CognitiveBlueprint.objects.filter(category="REASONING")
            .exclude(name__startswith="NightManager")
            .exclude(name="The Architect")
            .exclude(name__startswith=":")
            .exclude(name__startswith="]")
            .exclude(name="CognitiveBlueprintProposal")
            .order_by("name")
        )
    except Exception:
        blueprints = list(
            CognitiveBlueprint.objects.exclude(name__startswith="NightManager")
            .exclude(name="The Architect")
            .exclude(name__icontains="Grips")
            .exclude(name__startswith="LintGrips")
            .exclude(name__in=["DigestDocumentChunk", "EvaluateConceptNeighbors", "EvaluateCrossDomain", "Propose Blueprint"])
            .exclude(name__startswith=":")
            .exclude(name__startswith="]")
            .exclude(name="CognitiveBlueprintProposal")
            .order_by("name")
        )
    for bp in blueprints:
        compat = evaluate_blueprint_governance(bp, request.user)
        bp.governance_status = compat["status"]
        bp.is_locked = compat["is_locked"]
        bp.is_degraded = compat["is_degraded"]
        bp.governance_badge = compat["badge_text"]
        bp.governance_tooltip = compat["tooltip"]
    
    active_conversation = None
    initial_logs = []
    initial_files = []
    
    conv_id = request.GET.get('conversation_id')
    if conv_id:
        active_conversation = Conversation.objects.filter(id=conv_id, user=request.user).first()
        if active_conversation:
            raw_logs = list(active_conversation.logs.order_by('created_at').select_related('blueprint', 'reasoning_step'))
            initial_logs = group_conversation_logs_for_display(raw_logs)
            initial_files = _get_workspace_files_list(active_conversation)

    return render(request, 'demo_ui/index.html', {
        'conversations': conversations,
        'blueprints': blueprints,
        'active_conversation': active_conversation,
        'initial_logs': initial_logs,
        'initial_files': initial_files,
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
    
    raw_logs = list(conversation.logs.order_by('created_at').select_related('blueprint', 'reasoning_step'))
    logs = group_conversation_logs_for_display(raw_logs)

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
    parent_log = conversation.logs.order_by('-created_at').first()
    if blueprint_id:
        target_bp = CognitiveBlueprint.objects.filter(id=blueprint_id).first()
        if target_bp:
            compat = evaluate_blueprint_governance(target_bp, request.user)
            if compat["is_locked"]:
                err_html = (
                    f'<div class="chat-bubble assistant-bubble error-bubble">'
                    f'<strong>Execution Blocked by Governance Policy:</strong><br>'
                    f'{compat["tooltip"]}'
                    f'</div>'
                )
                return HttpResponse(err_html, status=403)

        if request.POST.get('sync') == 'true' or request.GET.get('sync') == 'true':
            from metacognition.tasks import run_blueprint
            result = run_blueprint(
                blueprint_id=int(blueprint_id),
                user_prompt=user_prompt,
                conversation_id=str(conversation.id),
                user_id=request.user.id
            )
            log = conversation.logs.order_by('-created_at').first()
            if not log:
                log = PromptResponseLog.objects.create(
                    conversation=conversation,
                    parent_log=parent_log,
                    user=request.user,
                    user_prompt=user_prompt,
                    generated_response=result.get("final_response", ""),
                    model_name="blueprint",
                )
        else:
            from uuid import uuid4
            from metacognition.tasks import task_run_blueprint_async
            
            run_id = str(uuid4())
            log = PromptResponseLog.objects.create(
                system_prompt="[Async Blueprint Execution]", 
                user_prompt=user_prompt,
                conversation=conversation,
                parent_log=parent_log,
                blueprint_id=int(blueprint_id),
                generated_response="", 
                user=request.user,
                input_tokens=0,
                output_tokens=0
            )

            streaming_markup = f"""<div id="blueprint-exec-{run_id}" data-signals="{{isStreaming: true}}" data-on-load="@get('/api/meta/stream_blueprint/?run_id={run_id}&log_id={log.id}')">
<div id="blueprint-visualizer" class="blueprint-visualizer"></div>
<div id="blueprint-status" class="agent-step active">
    <span class="badge">Dispatched</span>
    <strong>Executing cognitive blueprint asynchronously...</strong>
</div>
<div id="tool-approval-container"></div>
<details id="blueprint-thinking-trace" class="blueprint-thinking-trace" open>
    <summary class="thinking-trace-summary" id="thinking-trace-summary">💭 Thinking Trace (0 steps)</summary>
    <div id="monologue-stream" class="thinking-trace-content"></div>
</details>
<div id="blueprint-final-response" class="blueprint-final-response"></div>
</div>"""
            log.generated_response = streaming_markup
            log.save(update_fields=["generated_response"])

            task_run_blueprint_async.enqueue(
                blueprint_id=int(blueprint_id),
                user_prompt=user_prompt,
                conversation_id=str(conversation.id),
                user_id=request.user.id,
                run_id=run_id,
                parent_log_id=str(parent_log.id) if parent_log else None,
                initial_log_id=str(log.id)
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
                from background_resources.models import RAGChunk
                docs = service_registry.rag_service.store.mget([item_id])
                if docs and docs[0]:
                    d = docs[0]
                    chunk_obj = RAGChunk.objects.filter(chunk_id=str(item_id)).first()
                    if chunk_obj:
                        citation = chunk_obj.get_citation()
                    else:
                        meta = d.metadata or {}
                        author_str = meta.get('authors') or meta.get('filename', 'Unknown Source')
                        year_str = f" ({meta.get('year')})" if meta.get('year') else ""
                        sec_str = f" — Section: {meta.get('section_title')}" if meta.get('section_title') else ""
                        citation = f"{author_str}{year_str}{sec_str}".strip()

                    rag_selections.append({
                        "model": "RAGChunk",
                        "id": item_id,
                        "citation": citation,
                        "preview": d.page_content[:150] + "..."
                    })
                    rag_text += f"\n[Reference: {citation}]\nExcerpt:\n{d.page_content}\n"
                    
            elif model_type == "ConceptNode":
                content = item.get("content", "Concept content unavailable")
                rag_selections.append({"model": "ConceptNode", "id": item_id, "preview": content[:150] + "..."})
                rag_text += f"\n[Concept Node: {item.get('preview', 'Concept')}]\n{content}\n"
                
            elif model_type == "Document" and service_registry.rag_service:
                from background_resources.models import Document
                try:
                    doc_obj = Document.objects.get(id=int(item_id))
                    doc_citation = doc_obj.get_citation()
                except Exception:
                    doc_citation = item.get("content", "Document dropped")
                rag_selections.append({"model": "Document", "id": item_id, "citation": doc_citation, "preview": doc_citation})
                rag_text += f"\n[Reference Document: {doc_citation}]\n"
                
            elif model_type == "Conversation":
                content = item.get("content", "Conversation dropped")
                rag_selections.append({"model": "Conversation", "id": item_id, "preview": content})
                rag_text += f"\n[Previous Conversation Reference: {content}]\n"
        
        if rag_text:
            context_prompt = (
                f"{user_prompt}\n\n"
                "--- Relevant Literature & Empirical Context ---\n"
                f"{rag_text}\n"
                "--- End of Context ---\n"
                "Ground your response in the provided reference excerpts where relevant. "
                "If an excerpt is not directly relevant to the user's specific experimental design question, do not focus on it."
            )
            messages_for_llm = messages + [{"role": "user", "content": context_prompt}]
        else:
            messages_for_llm = messages + [{"role": "user", "content": user_prompt}]
        
        from uuid import uuid4

        run_id = str(uuid4())
        max_new_tokens = int(request.POST.get('max_new_tokens', 1500))
        input_tokens = service_registry.ai_service.count_conversation_tokens(messages_for_llm)

        log = PromptResponseLog.objects.create(
            system_prompt=messages[0]["content"],
            user_prompt=user_prompt,
            rag_selections=rag_selections,
            conversation=conversation,
            parent_log=parent_log,
            generated_response="",
            user=request.user,
            input_tokens=input_tokens,
            output_tokens=0,
        )

        streaming_markup = (
            f'<div id="gen-stream-{run_id}" data-signals="{{isStreaming: true}}" '
            f'data-on-load="@get(\'/demo/stream_generation/?run_id={run_id}&log_id={log.id}\')">'
            '<div class="agent-step active">'
            '<span class="badge">Generating</span>'
            '<strong>Generating response...</strong>'
            '</div>'
            '</div>'
        )
        log.generated_response = streaming_markup
        log.save(update_fields=["generated_response"])

        task_generate_response.enqueue(
            messages=messages_for_llm,
            max_new_tokens=max_new_tokens,
            user_id=request.user.id if request.user.is_authenticated else None,
            run_id=run_id,
            log_id=str(log.id),
            parent_log_id=str(parent_log.id) if parent_log else None,
            conversation_id=str(conversation.id),
            rag_selections=rag_selections,
        )
        log.refresh_from_db()
        
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


@login_required
def stream_generation(request):
    """
    Server-Sent Events (SSE) streaming endpoint using Datastar protocol.
    Streams DOM fragment patches when background LLM generation completes.
    """
    from metacognition.datastar import DatastarSSE

    run_id = request.GET.get("run_id", "")
    log_id = request.GET.get("log_id", "")

    def event_generator():
        yield DatastarSSE.patch_signals({
            "isStreaming": True,
            "runId": run_id,
            "status": "generating"
        })

        # Fast path: check if the log already has a completed response
        if log_id:
            log = PromptResponseLog.objects.filter(id=log_id).first()
            if log and log.generated_response and not log.generated_response.startswith('<div id="gen-stream-'):
                html = markdown.markdown(log.generated_response, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if markdown else log.generated_response
                frag = f'<div id="gen-stream-{run_id}" class="markdown-body">{html}</div>'
                yield DatastarSSE.patch_elements(frag, selector=f"#gen-stream-{run_id}", mode="morph")
                if log.output_tokens:
                    out_frag = f'<span id="token-out-{log_id}" style="color: var(--text-main); font-weight: 600;">{log.output_tokens}</span>'
                    yield DatastarSSE.patch_elements(out_frag, selector=f"#token-out-{log_id}", mode="morph")
                if log.input_tokens:
                    in_frag = f'<span id="token-in-{log_id}" style="color: var(--text-main); font-weight: 600;">{log.input_tokens}</span>'
                    yield DatastarSSE.patch_elements(in_frag, selector=f"#token-in-{log_id}", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "completed"})
                return

        channel_name = f"verbal_events_{run_id}"
        for event in subscribe_pg_events_sync(channel_name):
            event_type = event.get("event")
            data = event.get("data", {})

            if event_type == "completed":
                final_text = data.get("final_response", "")
                output_tokens = data.get("output_tokens")
                input_tokens = data.get("input_tokens")
                if (output_tokens is None or input_tokens is None) and log_id:
                    fresh_log = PromptResponseLog.objects.filter(id=log_id).first()
                    if fresh_log:
                        output_tokens = fresh_log.output_tokens
                        input_tokens = fresh_log.input_tokens

                html = markdown.markdown(final_text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']) if markdown else final_text
                frag = f'<div id="gen-stream-{run_id}" class="markdown-body">{html}</div>'
                yield DatastarSSE.patch_elements(frag, selector=f"#gen-stream-{run_id}", mode="morph")
                if log_id and output_tokens is not None:
                    out_frag = f'<span id="token-out-{log_id}" style="color: var(--text-main); font-weight: 600;">{output_tokens}</span>'
                    yield DatastarSSE.patch_elements(out_frag, selector=f"#token-out-{log_id}", mode="morph")
                if log_id and input_tokens is not None:
                    in_frag = f'<span id="token-in-{log_id}" style="color: var(--text-main); font-weight: 600;">{input_tokens}</span>'
                    yield DatastarSSE.patch_elements(in_frag, selector=f"#token-in-{log_id}", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "completed"})
                break
            elif event_type == "error":
                err_msg = data.get("error", "Generation error")
                frag = f'<div id="gen-stream-{run_id}" class="agent-step error"><span class="badge badge-error">Error</span><strong>{err_msg}</strong></div>'
                yield DatastarSSE.patch_elements(frag, selector=f"#gen-stream-{run_id}", mode="morph")
                yield DatastarSSE.patch_signals({"isStreaming": False, "status": "error"})
                break

    return StreamingHttpResponse(event_generator(), content_type="text/event-stream")



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
    """HTMX endpoint to fork a conversation DAG from a specific assistant response, copying all content back to the start."""
    source_log = get_object_or_404(PromptResponseLog, id=log_id)
    original_conv = source_log.conversation

    # Trace ancestor path from root to source_log via parent_log DAG
    ancestor_chain = []
    curr = source_log
    while curr:
        ancestor_chain.append(curr)
        curr = curr.parent_log
    ancestor_chain.reverse()

    # For flat or unlinked historical logs in original_conv, ensure we copy all logs back to conversation start
    if original_conv:
        chronological_logs = list(original_conv.logs.filter(created_at__lte=source_log.created_at).order_by('created_at'))
        if len(chronological_logs) > len(ancestor_chain):
            ancestor_logs = chronological_logs
        else:
            ancestor_logs = ancestor_chain
    else:
        ancestor_logs = ancestor_chain

    turn_count = len(ancestor_logs)
    base_title = original_conv.title if original_conv else "Conversation"
    branch_title = f"Branch: {base_title[:28]} (Turn {turn_count})"

    new_conv = Conversation.objects.create(
        user=request.user,
        title=branch_title,
    )

    # Clone historical logs into new conversation to isolate the new branch, ensuring a continuous DAG
    log_map = {}
    last_cloned = None
    for old_log in ancestor_logs:
        parent_clone = log_map.get(old_log.parent_log_id) or last_cloned
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
            blueprint=old_log.blueprint,
        )
        log_map[old_log.id] = cloned_log
        last_cloned = cloned_log

    raw_logs = list(new_conv.logs.order_by('created_at').select_related('blueprint', 'reasoning_step'))
    logs = group_conversation_logs_for_display(raw_logs)

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
    """Triggers autonomous stub elaboration for a specific ConceptNode."""
    from grips.models import ConceptNode
    from django.conf import settings
    node = get_object_or_404(ConceptNode, id=concept_id)
    is_immediate = getattr(settings, 'TASKS', {}).get('default', {}).get('BACKEND') == 'django.tasks.backends.immediate.ImmediateBackend'
    sync = request.GET.get('sync') == 'true' or request.POST.get('sync') == 'true' or is_immediate

    try:
        if sync:
            from grips.tasks import generate_concept_narrative
            func = getattr(generate_concept_narrative, "func", generate_concept_narrative)
            _ = func(concept_id)
            node.refresh_from_db()
            return HttpResponse('<span style="color: #047857; font-size: 0.72rem; font-weight: 600;">Elaborated</span>')

        from metacognition.models import CognitiveBlueprint, bypass_canonical_lock
        from metacognition.tasks import task_run_blueprint_async

        bp = CognitiveBlueprint.objects.filter(name__icontains="Stub Filler").first()
        if not bp:
            from metacognition.seed import seed_cognitive_blueprints
            with bypass_canonical_lock():
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
        logger.exception("Failed to fill Grips stub")
        return HttpResponse(f'<span style="color: #b91c1c; font-size: 0.72rem; font-weight: 500;">Error: {str(e)}</span>', status=500)


@login_required
@require_POST
def set_active_provider(request):
    """
    HTMX endpoint to switch the user's active inference provider between local GPU and external models.
    """
    from llm_api.models import UserActiveModel, ExternalAIModel
    from metacognition.context_processors import active_model_context

    use_external_raw = request.POST.get("use_external", "false").lower()
    use_external = use_external_raw in ("true", "1", "yes")
    model_id = request.POST.get("model_id")

    active_external = None
    if use_external and model_id:
        try:
            active_external = ExternalAIModel.objects.get(id=model_id)
        except ExternalAIModel.DoesNotExist:
            use_external = False

    UserActiveModel.objects.update_or_create(
        user=request.user,
        defaults={
            "use_external": use_external,
            "active_external": active_external if use_external else None,
        }
    )

    context = active_model_context(request)
    return render(request, "includes/active_model_pill.html", context)


@login_required
def grips_blueprints_tab(request):
    """
    HTMX endpoint to render the Grips Knowledge Graph blueprints pathway.
    Presents the strategies clearly with step sequences, level-1 loop-back indicators,
    and admin access links.
    """
    from metacognition.models import CognitiveBlueprint
    try:
        blueprints = list(
            CognitiveBlueprint.objects.filter(category="GRIPS")
            .prefetch_related('steps__available_tools', 'steps__output_schema')
            .order_by('name')
        )
    except Exception:
        grips_names = [
            "Grips Stub Filler", "LintGripsEdge", "LintGripsNode",
            "DigestDocumentChunk", "EvaluateConceptNeighbors", "EvaluateCrossDomain"
        ]
        blueprints = list(
            CognitiveBlueprint.objects.filter(name__in=grips_names)
            .prefetch_related('steps__available_tools', 'steps__output_schema')
            .order_by('name')
        )

    for bp in blueprints:
        steps = list(bp.steps.all())
        has_loopback = any(
            (s.on_failure_step is not None) or (s.on_success_step == s)
            for s in steps
        )
        bp.has_loopback = has_loopback
        bp.ordered_steps = steps

    return render(request, 'demo_ui/grips_blueprints.html', {
        'blueprints': blueprints
    })