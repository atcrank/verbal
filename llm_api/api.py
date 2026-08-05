import logging
logger = logging.getLogger(__name__)

import asyncio
import typing
import json
import re
import outlines
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from ninja import Router, Schema
from django.http import HttpRequest, HttpResponse, JsonResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from ninja.security import SessionAuth

from .models import Conversation, PromptResponseLog, LocalAIModel, ExternalAIModel, UserActiveModel, UserAPIKey

from llm_api.apps import service_registry
from background_resources.models import Document
router = Router(auth=SessionAuth())


class GenerateIn(Schema):
    conversation_id: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    max_new_tokens: int = 1000
    skip_rag: bool = False
    skip_grips: bool = True
    parent_log_id: typing.Optional[str] = None
    uncertainty_mode: int = 1  # 1: ask, 2: search, 3: guess

@router.post("/generate_response/")
@ensure_csrf_cookie
def generate_response(request, payload: GenerateIn):
    # TODO: system prompt is supposed to appear only once and subsequent messages should not be system prompts
    conversation_id = payload.conversation_id
    messages = []
    if conversation_id:
        conversation = Conversation.objects.get(id=conversation_id)
        if conversation.user_id != request.auth.id:
            # I think I'd like to copy the conversation and allow the new user to take it over
            return JsonResponse({"error": "You are not authorized to access this conversation."})
    else:
        conversation = Conversation(user_id=request.auth.id)
        conversation.title = payload.user_prompt.split(".")[0]  # first sentence only
        conversation.save()
        conversation_id = conversation.id
        system_prompt = payload.system_prompt or "You are an expert experiment architect. Your task is to design a clear and efficient experiment design based on a user's description of what they want to find out. Output suggested factors in a list format."
        messages.append({"role": "system", "content": system_prompt})

    # Determine the parent log for the tree
    parent_log = None
    if payload.parent_log_id:
        parent_log = PromptResponseLog.objects.filter(id=payload.parent_log_id).first()
    elif conversation_id:
        parent_log = PromptResponseLog.objects.filter(conversation_id=conversation_id).order_by('-created_at').first()

    rag_text = ""
    rag_selections = []
    
    requires_research = True
    if not payload.skip_rag:
        try:
            from background_resources.nlp_service import NLPService
            requires_research = NLPService().requires_research(payload.user_prompt)
        except Exception as e:
            logger.error(f"Error checking requires_research: {e}")
            
    if not payload.skip_rag and requires_research:
        from background_resources.retrieval import get_deep_context_report
        from metacognition.models import CognitiveBlueprint
        from metacognition.tasks import run_blueprint

        deep_context = get_deep_context_report(
            query=payload.user_prompt,
            conversation_id=conversation_id,
            rag_service=service_registry.rag_service if not payload.skip_rag else None,
            grips_service=getattr(service_registry, 'grips_service', None) if not payload.skip_grips else None,
        )

        try:
            blueprint = CognitiveBlueprint.objects.get(name="NM_Deep_Reader")
            reader_prompt = f"User Query: {payload.user_prompt}\n\n{deep_context}"
            
            result = run_blueprint(
                blueprint_id=blueprint.id,
                user_prompt=reader_prompt,
                user_id=request.auth.id,
            )
            
            if "error" not in result and "final_response" in result:
                distilled = result["final_response"].strip()
                if distilled and distilled != "<SILENT_ABORT>":
                    rag_text = "\n\nRelevant Context (Synthesized):\n" + distilled
        except CognitiveBlueprint.DoesNotExist:
            logger.warning("NM_Deep_Reader blueprint not found. Proceeding without RAG context.")
    
    messages = messages + conversation.as_messages(leaf_log_id=payload.parent_log_id) + [{"role": "user", "content": payload.user_prompt + rag_text}]
    max_new_tokens = payload.max_new_tokens
    
    # Inject uncertainty handling into the system prompt (messages[0])
    if messages and messages[0].get("role") == "system":
        if payload.uncertainty_mode == 1:
            messages[0]["content"] += "\nIf you lack information to answer confidently based on the provided context, state what you know and explicitly ask the user if they would like you to search the knowledge base for specific concepts."
        elif payload.uncertainty_mode == 2:
            messages[0]["content"] += "\nIf you lack information, output <SEARCH: term> to halt and query the database."
        elif payload.uncertainty_mode == 3:
            messages[0]["content"] += "\nDo not ask for more information. Use your best judgment and infer the answer."
    
    input_tokens = service_registry.ai_service.count_conversation_tokens(messages)
    logger.info(" ".join([str(x) for x in ['Token Count:', input_tokens]]))
    
    [response] = service_registry.ai_service.generate_response2(messages=messages, max_new_tokens=max_new_tokens, log_kwargs={"skip_log": True}, user=request.auth)
    cleaned_response = service_registry.ai_service.clean_response(response)
    
    output_tokens = service_registry.ai_service.count_conversation_tokens([{"role": "assistant", "content": cleaned_response}])
    
    system_prompt = messages[0]["content"]
    p = PromptResponseLog(system_prompt=system_prompt, user_prompt=payload.user_prompt,
                          rag_selections=rag_selections, conversation_id=conversation_id,
                          generated_response=cleaned_response, user_id=request.auth.id,
                          parent_log=parent_log, input_tokens=input_tokens, output_tokens=output_tokens)
    p.save()

    return JsonResponse({"conversation_id": conversation_id, "cleaned_response": cleaned_response})


@router.get("/get_rag_context/")
@ensure_csrf_cookie
def get_rag_context(request, query: str = "", k: int = 4):
    if not service_registry.rag_service:
        return JsonResponse({"error": "RAG Service offline."})
    doc_segments = service_registry.rag_service.get_context(query, k=k)
    # Convert Langchain Documents to JSON-serializable dicts
    results = [{"page_content": d.page_content, "metadata": d.metadata} for d in doc_segments]
    return JsonResponse({"rag_context": results})

@router.get("/get_grips_context/")
@ensure_csrf_cookie
def get_grips_context(request, query: str = "", k: int = 4):
    if not getattr(service_registry, 'grips_service', None):
        return JsonResponse({"error": "Grips Service offline."})
    doc_segments = service_registry.grips_service.get_grips_context(query, k=k)
    results = [{"page_content": d.page_content, "metadata": d.metadata} for d in doc_segments]
    return JsonResponse({"grips_context": results})

@router.get("/conversation/{conversation_id}/workspace/")
@ensure_csrf_cookie
def get_workspace_info(request, conversation_id: str):
    """Returns the file listing and git history for a conversation's workspace."""
    try:
        conversation = Conversation.objects.get(id=conversation_id)
        if conversation.user_id != request.auth.id:
            return JsonResponse({"error": "Not authorized to access this workspace."}, status=403)
        
        return JsonResponse({
            "files": conversation.get_workspace_files(),
            "git_history": conversation.get_git_history()
        })
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Conversation not found."}, status=404)

@router.get("/conversation/{conversation_id}/workspace/file/")
@ensure_csrf_cookie
def get_workspace_file(request, conversation_id: str, filename: str, commit_hash: str = "HEAD"):
    """Extracts the content of a file from the workspace at a specific git commit."""
    try:
        conversation = Conversation.objects.get(id=conversation_id)
        if conversation.user_id != request.auth.id:
            return JsonResponse({"error": "Not authorized to access this workspace."}, status=403)
        
        content = conversation.get_file_at_commit(filename, commit_hash)
        if content is None:
            return JsonResponse({"error": "File or commit not found in workspace."}, status=404)
            
        return JsonResponse({
            "filename": filename,
            "commit_hash": commit_hash,
            "content": content
        })
    except Conversation.DoesNotExist:
        return JsonResponse({"error": "Conversation not found."}, status=404)

class OutlineQuery(Schema):
    user_query: str
    max_topics: int = 5

class StateValue(BaseModel):
    name: str = ""
    definition: str = ""

class StateSet(BaseModel):
    state_values: typing.List[StateValue] = []

class Factor(BaseModel):
    name: str
    state_options: StateSet


# Pydantic example
class Hydrant(BaseModel):
    location_name: str = Field(description="name of the hydrant")
    water_static_pressure: int = Field(ge=100, le=650, description="kPa - hydrant static pressure")
    water_residual_pressure: int = Field(ge=100, le=550, description="kPa - hydrant residual pressure")
    peak_flow: int = Field(ge=0, le=1000, description="L / minute - max flow of water from the hydrant")

    @field_validator('location_name')
    @classmethod
    def validate_residual_pressure(cls, v):
        if v == 0:
            raise ValueError("Infants must be defined as months, not 0 years.")
        return v

    @model_validator(mode='after')
    def check_physics(self):
        # The LLM might try to generate a residual higher than static
        if self.residual_pressure >= self.static_pressure:
            raise ValueError(
                f"Physics violation: Residual pressure ({self.residual_pressure}) "
                f"cannot be higher than Static pressure ({self.static_pressure})"
            )
        return self

class OutlineIn(Schema):
    query: str
    schema_key: typing.Union[str, dict]


@router.post("/get_outline/")
@ensure_csrf_cookie
def get_outline(request, payload: OutlineIn):
    if type(payload.schema_key) == str:
        output_type = OUTPUT_TYPES.get(payload.schema_key)
        if output_type is None:
            return JsonResponse({"error": "Schema key not known."})
    elif type(payload.schema_key) == dict:
        output_type = payload.schema_key
    logger.info(" ".join([str(x) for x in ['Get Outline called: types -', type(payload.query), output_type]]))
    outline = service_registry.ai_service.generate_outline(payload.query, output_type, user=request.auth)
    logger.info(" ".join([str(x) for x in ['Outline', outline, type(outline)]]))
    
    outline_data = outline.model_dump() if hasattr(outline, 'model_dump') else outline
    return JsonResponse({"outline": outline_data})


# --- Standard OpenAI API Proxy Endpoints (No Auth) ---

class OpenAIChatMessage(Schema):
    role: str
    content: str

class OpenAIJsonSchema(Schema):
    name: str
    schema_dict: dict = Field(..., alias="schema")
    strict: typing.Optional[bool] = False

class OpenAIResponseFormat(Schema):
    type: str
    json_schema: typing.Optional[OpenAIJsonSchema] = None

class OpenAIChatCompletionIn(Schema):
    model: typing.Optional[str] = "local-model"
    messages: typing.List[OpenAIChatMessage]
    temperature: typing.Optional[float] = 0.7
    max_tokens: typing.Optional[int] = 1024
    n: typing.Optional[int] = 1
    response_format: typing.Optional[OpenAIResponseFormat] = None

class RegexCandidate(BaseModel):
    reasoning: str = Field(description="Brief analysis of the text structure and strategy")
    pattern: str = Field(..., min_length=1, description="The Python regex pattern")

OUTPUT_TYPES = {
    "Factor": Factor,
    "RegexCandidate": RegexCandidate,
}

@router.get("/internal/ping/", auth=None)
@csrf_exempt
def internal_ping(request):
    from django.conf import settings
    return JsonResponse({"status": "ok", "role": getattr(settings, "VERBAL_ROLE", "unknown")})

@router.post("/v1/chat/completions", auth=None)
@csrf_exempt
def openai_chat_completions(request, payload: OpenAIChatCompletionIn):
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    max_tokens = payload.max_tokens or 1024
    
    # Route structured output vs standard generation
    if payload.response_format and payload.response_format.type == "json_schema":
        schema_dict = payload.response_format.json_schema.schema_dict
        schema_name = payload.response_format.json_schema.name
        
        response_schema = schema_dict
        
        # Attempt to resolve the Pydantic class by name to enable native validation & FSM setup
        if schema_name in OUTPUT_TYPES:
            response_schema = OUTPUT_TYPES[schema_name]
        else:
            try:
                from background_resources.rag_service import OUTPUT_TYPES as RAG_OUTPUT_TYPES
                if schema_name in RAG_OUTPUT_TYPES:
                    response_schema = RAG_OUTPUT_TYPES[schema_name]
            except ImportError:
                pass
            
            if response_schema == schema_dict:
                try:
                    import benchmarking.generators as bg
                    if hasattr(bg, schema_name):
                        response_schema = getattr(bg, schema_name)
                except ImportError:
                    pass
            
            if response_schema == schema_dict:
                try:
                    import metacognition.actions as ma
                    if hasattr(ma, schema_name):
                        response_schema = getattr(ma, schema_name)
                except ImportError:
                    pass
        
        result = service_registry.ai_service.generate_outline(
            messages=messages,
            response_schema=response_schema,
            max_new_tokens=max_tokens,
            temperature=payload.temperature,
            num_return_sequences=payload.n,
            log_kwargs={"skip_log": True}
        )
        
        results_list = result if isinstance(result, list) else [result]
        choices = []
        for r in results_list:
            if hasattr(r, 'model_dump'):
                content = json.dumps(r.model_dump())
            elif isinstance(r, dict):
                content = json.dumps(r)
            else:
                # JsonSchema generations return strings directly
                content = str(r)
            choices.append({"message": {"role": "assistant", "content": content}})
            
        return JsonResponse({"choices": choices})
    else:
        results = service_registry.ai_service.generate_response2(
            messages=messages,
            max_new_tokens=max_tokens,
            temperature=payload.temperature,
            num_return_sequences=payload.n,
            log_kwargs={"skip_log": True}
        )
        choices = [{"message": {"role": "assistant", "content": str(r)}} for r in results]
        return JsonResponse({"choices": choices})

# --- PDF Viewer Endpoint ---

@router.get("/view_document/{doc_id}/", auth=None)
def view_document_pdf(request, doc_id: int):
    """
    Serves a document's PDF file with the X-Frame-Options header set to SAMEORIGIN,
    allowing it to be embedded in an iframe within the Django admin.
    """
    try:
        doc = Document.objects.get(id=doc_id)
        if doc.file and doc.file.name.lower().endswith('.pdf'):
            response = FileResponse(doc.file.open('rb'), content_type='application/pdf')
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
        else:
            return HttpResponse("Document has no associated PDF file.", status=404)
    except Document.DoesNotExist:
        return HttpResponse("Document not found.", status=404)

# --- Admin Helper Endpoints ---

class RegexSuggestIn(Schema):
    description: str
    term_description: str = ""
    definition_description: str = ""
    examples: str = ""
    document_id: typing.Optional[int] = None

class RegexCandidate(BaseModel):
    reasoning: str = Field(description="Brief analysis of the text structure and strategy")
    pattern: str = Field(..., min_length=1, description="The Python regex pattern")

@router.post("/admin/suggest_regex/")
@ensure_csrf_cookie
def suggest_regex(request, payload: RegexSuggestIn):
    """
    Asks the AI to generate a regex based on description and examples.
    """
    # 1. Fetch Sample Text for "Genetic" Evaluation
    sample_text = ""
    if payload.document_id:
        try:
            doc = Document.objects.get(id=payload.document_id)
            chunks, chunk_ids = service_registry.rag_service.convert_chunk_store_document(doc)
            if not chunks and chunk_ids:
                chunks = service_registry.rag_service.store.mget(chunk_ids)
                chunks = [c for c in chunks if c]
            
            # Use first 5 chunks as test ground
            if chunks:
                sample_text = "\n".join([c.page_content for c in chunks[:5]])
        except Document.DoesNotExist:
            pass

    prompt = f"""
    You are a regular expression expert. 
    Task: Create simple, logical Python regex pattern (compatible with re.findall) to extract structured data from a text document.
    
    CRITICAL REQUIREMENT: Each regex MUST capture exactly two groups.
    Group 1: {payload.term_description if payload.term_description else "The term or key being defined"}
    Group 2: {payload.definition_description if payload.definition_description else "The definition or value"}
    
    Context/Description: {payload.description}

    Positive Examples (lines that should match):
    {payload.examples if payload.examples else "No examples provided. Rely strictly on the description."}
    
    GUIDELINES:
    1. GENERALIZE: Do not overfit to the specific words in the examples. Match the structure (e.g. "Word: Definition"). 
    2. LOOK TO PUNCTUATION: Significant structure is usually expressed in punctuation and whitespace characters.
    3. COMPATIBILITY: Python's re module DOES NOT support variable-width look-behind assertions. Do not use them.
    4. TWO GROUPS PER MATCH: Use non-capturing groups (?:...) for any grouping that is not the Term (Group 1) or Definition (Group 2). Each match must be a 2-tuple because they are to be stored as a Key and Value.  
    5. NO NESTING: Do not nest groups.
    
    OUTPUT FORMAT:
    Your response MUST consist of ONLY THE REGEX. Don't include any labels, introductions or preambles.
    
    """
    
    messages = [{"role": "user", "content": prompt}]
    logger.info(" ".join([str(x) for x in ['MESSAGES FOR REGEX:', messages]]))
    try:
        # Use the standard LLM pipeline which has do_sample=True for diversity
        responses = service_registry.ai_service.generate_outline(messages=messages, response_schema=RegexCandidate, max_new_tokens=2048, num_return_sequences=10)
        
        candidates = []
        logger.info(responses)
        for raw_response in responses:
            try:
                if isinstance(raw_response, dict) and "error" in raw_response:
                    continue
                if isinstance(raw_response, RegexCandidate):
                    candidates.append(raw_response.pattern)
                elif isinstance(raw_response, dict):
                    candidates.append(RegexCandidate.model_validate(raw_response).pattern)
                elif isinstance(raw_response, str):
                    # Clean up markdown code blocks if present
                    clean_json = raw_response.replace("```json", "").replace("```", "").strip()
                    candidates.append(RegexCandidate.model_validate_json(clean_json).pattern)
            except Exception:
                continue
        
        # Deduplicate candidates preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            c_clean = c.strip()
            if c_clean and c_clean not in seen:
                unique_candidates.append(c_clean)
                seen.add(c_clean)
        candidates = unique_candidates
    except Exception as e:
        return JsonResponse({"error": f"AI generation failed: {str(e)}"})

    if not candidates:
        return JsonResponse({"error": "AI returned no candidates."})

    # --- Genetic Selection / Ranking ---
    best_regex = None
    best_score = -1
    
    scored_candidates = []
    logger.info(candidates)
    for pattern in candidates:
        try:
            # Compile with MULTILINE automatically to be forgiving
            c_regex = re.compile(pattern, re.MULTILINE)
            if c_regex.groups != 2:
                continue
            
            matches = c_regex.findall(sample_text)
            match_count = len(matches)
            
            # Heuristics for Quality
            # 1. Term Length Sanity: Terms shouldn't be massive (e.g. > 100 chars)
            #    If they are, the regex is likely too greedy (e.g. matching whole lines as terms)
            avg_term_len = sum(len(m[0]) for m in matches) / match_count if match_count > 0 else 0
            
            score = match_count
            
            # Penalize greedy terms
            if avg_term_len > 100:
                score = 0
            
            scored_candidates.append({"regex": pattern, "score": score, "matches": match_count})
            
            if score > best_score:
                best_score = score
                best_regex = pattern
                
        except re.error:
            continue
    logger.info(" ".join([str(x) for x in ['scored candidates', scored_candidates]]))
    if best_regex:
        return JsonResponse({"regex": best_regex, "candidates_evaluated": len(candidates), "best_match_count": best_score})
    
    return JsonResponse({"error": f"Generated {len(candidates)} candidates but none were valid or found matches."})

class RegexPreviewIn(Schema):
    document_id: typing.Optional[int] = None
    chunk_id: typing.Optional[str] = None
    regex: str

@router.post("/admin/preview_regex/")
@ensure_csrf_cookie
def preview_regex(request, payload: RegexPreviewIn):
    """
    Tests a regex against a specific chunk or the first 5 chunks of a document.
    """
    try:
        target_chunks = []
        
        # 1. Try to get specific chunk if selected
        if payload.chunk_id:
            chunks = service_registry.rag_service.store.mget([payload.chunk_id])
            if chunks and chunks[0]:
                target_chunks = [chunks[0]]

        # 2. Fallback to document chunks
        if not target_chunks and payload.document_id:
            doc = Document.objects.get(id=payload.document_id)
            # We grab chunks. If not indexed, we generate them in memory.
            chunks, chunk_ids = service_registry.rag_service.convert_chunk_store_document(doc)
            
            # If chunks are reused (already indexed), fetch them from store so we can process them
            if not chunks and chunk_ids:
                chunks = service_registry.rag_service.store.mget(chunk_ids)
                chunks = [c for c in chunks if c] # Filter Nones
            
            if chunks:
                # Test against the first 5 chunks to ensure we catch patterns that might start later
                target_chunks = chunks[:5]
        
        if not target_chunks:
            return JsonResponse({"matches": [], "sample_text": "No text found."})

        sample_text = "\n\n--- CHUNK BREAK ---\n\n".join([c.page_content for c in target_chunks])
        
        # Use the RAG service's extraction logic to ensure consistency
        # We reuse get_glossary_terms logic but generic
        try:
            pattern = re.compile(payload.regex)
            matches = pattern.findall(sample_text)
            # Limit matches to top 10 to avoid huge payloads
            return JsonResponse({
                "matches": [str(m) for m in matches[:10]], 
                "count": len(matches),
                "sample_text": sample_text  # Return full text of the 5 chunks for inspection
            })
        except re.error as e:
            return JsonResponse({"error": f"Invalid Regex: {e}"})
            
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found."})

class AbbreviationPreviewIn(Schema):
    document_id: typing.Optional[int] = None
    chunk_id: typing.Optional[str] = None

@router.post("/admin/preview_abbreviations/")
@ensure_csrf_cookie
def preview_abbreviations(request, payload: AbbreviationPreviewIn):
    """
    Tests abbreviation extraction against a specific chunk or the first 5 chunks.
    """
    try:
        target_chunks = []
        
        if payload.chunk_id:
            chunks = service_registry.rag_service.store.mget([payload.chunk_id])
            if chunks and chunks[0]:
                target_chunks = [chunks[0]]

        if not target_chunks and payload.document_id:
            doc = Document.objects.get(id=payload.document_id)
            chunks, chunk_ids = service_registry.rag_service.convert_chunk_store_document(doc)
            if not chunks and chunk_ids:
                chunks = service_registry.rag_service.store.mget(chunk_ids)
                chunks = [c for c in chunks if c]
            if chunks:
                target_chunks = chunks[:5]

        if not target_chunks:
            return JsonResponse({"abbreviations": [], "sample_text": "No text found."})

        sample_text = "\n\n".join([c.page_content for c in target_chunks])
        
        nlp_service = service_registry.nlp_service
        model = nlp_service.get_abbreviation_model()
        doc = model(sample_text)
        
        abbreviations = []
        if doc._.abbreviations:
            for abrv in doc._.abbreviations:
                abbreviations.append(f"{abrv.text}: {abrv._.long_form.text}")
        
        return JsonResponse({"abbreviations": abbreviations, "count": len(abbreviations), "sample_text": sample_text[:1000] + "..."})

    except Exception as e:
        return JsonResponse({"error": str(e)})

class PromptPreviewIn(Schema):
    document_id: typing.Optional[int] = None
    chunk_id: typing.Optional[str] = None
    prompt: str

@router.post("/admin/preview_prompt/")
@ensure_csrf_cookie
def preview_prompt(request, payload: PromptPreviewIn):
    """
    Tests a prompt against a specific chunk or the first chunk of a document.
    """
    try:
        target_chunk = None
        
        # 1. Try to get specific chunk if selected
        if payload.chunk_id:
            chunks = service_registry.rag_service.store.mget([payload.chunk_id])
            if chunks and chunks[0]:
                target_chunk = chunks[0]
        
        # 2. Fallback to first chunk of document
        if not target_chunk and payload.document_id:
            doc = Document.objects.get(id=payload.document_id)
            # We grab chunks. If not indexed, we generate them in memory.
            chunks, chunk_ids = service_registry.rag_service.convert_chunk_store_document(doc)
            
            # If chunks are reused (already indexed), fetch them from store
            if not chunks and chunk_ids:
                chunks = service_registry.rag_service.store.mget(chunk_ids)
                chunks = [c for c in chunks if c] # Filter Nones
            
            if chunks:
                target_chunk = chunks[0]

        if not target_chunk:
            return JsonResponse({"error": "No text found. Select a document or a specific chunk."})
        
        result = service_registry.rag_service.get_chunk_summary(target_chunk.page_content, custom_prompt=payload.prompt)
        
        return JsonResponse({
            "long_form": result.long_form,
            "short_form": result.short_form,
            "keywords": result.keywords,
            "sample_text": target_chunk.page_content[:1000] + "..."
        })
            
    except Document.DoesNotExist:
        return JsonResponse({"error": "Document not found."})
    except Exception as e:
        return JsonResponse({"error": f"Error generating summary: {str(e)}"})
