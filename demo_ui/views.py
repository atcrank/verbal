import json
from django.shortcuts import render, HttpResponse, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
from llm_api.models import Conversation, PromptResponseLog
from metacognition.models import CognitiveBlueprint
from llm_api.apps import service_registry

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
    return log

@login_required
def index(request):
    """Renders the main Demo UI shell."""
    conversations = Conversation.objects.filter(user=request.user)
    blueprints = CognitiveBlueprint.objects.all()
    
    return render(request, 'demo_ui/index.html', {
        'conversations': conversations,
        'blueprints': blueprints,
    })

@login_required
def search_knowledge_base(request):
    """HTMX endpoint to perform a unified search across RAG and Grips."""
    query = request.GET.get('q', '').strip()
    
    if not query:
        return HttpResponse('<div class="conv-date" style="text-align: center; margin-top: 20px;">Search results will appear here.</div>')

    rag_service = service_registry.rag_service
    grips_service = service_registry.grips_service

    rag_results = []
    grips_results = []

    # Search Grips Knowledge Graph
    if grips_service:
        try:
            grips_results = grips_service.get_grips_context(query, k=3)
        except Exception as e:
            print(f"Grips Search error: {e}")

    # Search Document Chunks
    if rag_service:
        try:
            rag_results = rag_service.get_context(query, k=3)
        except Exception as e:
            print(f"RAG Search error: {e}")

    return render(request, 'demo_ui/search_results.html', {
        'rag_results': rag_results,
        'grips_results': grips_results,
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

    return render(request, 'demo_ui/chat_history.html', {
        'conversation': conversation,
        'logs': logs
    })

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
            
        log = PromptResponseLog.objects.create(
            system_prompt="[Blueprint Execution]", 
            user_prompt=user_prompt,
            conversation=conversation,
            generated_response=cleaned_response, 
            user=request.user
        )
    else:
        messages = conversation.as_messages()
        if not messages:
            messages.append({"role": "system", "content": "You are a helpful study design assistant."})
            
        rag_docs = service_registry.rag_service.get_context(user_prompt)
        rag_text = "\n\n".join([f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs])
        
        messages_for_llm = messages + [{"role": "user", "content": user_prompt + "\n\nRelevant Context:\n" + rag_text}]
        
        [response] = service_registry.ai_service.generate_response2(messages=messages_for_llm, max_new_tokens=1000, log_kwargs={"skip_log": True}, user=request.user)
        cleaned_response = service_registry.ai_service.clean_response(response)
        
        log = PromptResponseLog.objects.create(
            system_prompt=messages[0]["content"], user_prompt=user_prompt, rag_selections=rag_text, 
            conversation=conversation, generated_response=cleaned_response, user=request.user
        )
        
    # 3. Render formatting
    _prepare_log_for_display(log)

    return render(request, 'demo_ui/chat_message.html', {'log': log, 'conversation': conversation})