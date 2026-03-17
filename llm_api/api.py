import asyncio
import typing
import json
import re
import outlines
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from ninja import Router, Schema
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from ninja.security import SessionAuth

from .models import Conversation, PromptResponseLog

from llm_api.apps import service_registry
from background_resources.models import Document
router = Router(auth=SessionAuth())

def create_messages(conversation, system_prompt=None, user_prompt=None, rags=None):

    messages = conversation.as_messages()
    system_prompt = messages[0]
    if not system_prompt:
        system_prompt = "You are an expert experiment architect. Your task is to design a clear and efficient experiment design based on a user's description of what they want to find out. Output suggested factors in a list format."
    augmented_system_prompt = system_prompt + f"\n  These extracts from a local collection of authoritative documents may be be used to help guide your answer:\n {rags} "

    next_messages = [
        {
            "role": "system",
            "content": augmented_system_prompt,
        },
        {"role": "user", "content": user_prompt},
    ]
    print("Messages", messages)
    print(next_messages)
    return messages + next_messages

class GenerateIn(Schema):
    conversation_id: str = ""
    system_prompt: str = ""
    user_prompt: str = ""
    max_new_tokens: int = 1000

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

    # NB System prompt is ignored except at conversation creation time.
    rag_docs = service_registry.rag_service.get_context(payload.user_prompt)
    
    # Format RAG documents into a string for the LLM
    rag_text = "\n\n".join([f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs])
    
    messages = messages + conversation.as_messages() + [{"role": "user", "content": payload.user_prompt + "\n\nRelevant Context:\n" + rag_text}]
    max_new_tokens = payload.max_new_tokens
    print("Token Count:", service_registry.ai_service.count_conversation_tokens(messages))
    [response] = service_registry.ai_service.generate_response(messages=messages, max_new_tokens=max_new_tokens)
    cleaned_response = service_registry.ai_service.clean_response(response)
    system_prompt = messages[0]["content"]
    p = PromptResponseLog(system_prompt=system_prompt, user_prompt=payload.user_prompt,
                          rag_selections=rag_text, conversation_id=conversation_id,
                          generated_response=cleaned_response, user_id=request.auth.id)
    p.save()

    return JsonResponse({"conversation_id": conversation_id, "cleaned_response": cleaned_response})


@router.post("/get_rag_context/")
@ensure_csrf_cookie
def get_context(request, query:str ="", k:int =1):
    doc_segments = service_registry.rag_service.get_context(query, k=k)
    # Convert Langchain Documents to JSON-serializable dicts
    results = [{"page_content": d.page_content, "metadata": d.metadata} for d in doc_segments]
    return JsonResponse({"rag_context": results})

class OutlineQuery(Schema):
    user_query: str
    max_topics: int = 5

@dataclass
class Factor:
    name: str
    state1_name: str
    state2_name: str
    state3_name: str

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

from outlines.types import JsonSchema
schema_string = {
  "title": "Hydrant",
  "type": "object",
  "properties": {
    "location_name": {
      "type": "string",
      "description": "name of the hydrant"
    }
  },
  "required": [
    "location_name"
  ]
}

hydrant_json_def = JsonSchema(schema_string)

print(hydrant_json_def)

class OutlineIn(Schema):
    query: str
    schema_key: typing.Union[str, dict]

# Use with caution: The model needs some token space to step to correctness
extinguisher_types = typing.Literal["Foam", "Water", "Powder", "CO2"]

# superior approach allowing tokens of output that serve as thoughts
class CorrectExtinguishers(BaseModel):
    reasoning: str
    extinguisher_type: typing.Literal["Foam", "Water", "Powder", "CO2"]

# Alt approach: amplified Literal.  This was not effective - still chooses water
extinguisher_types2 = typing.Literal["Foam Extinguisher - for fat fires, oil and others", "Water Extinguisher - for wood and paper", "Powder Extinguisher - for intense fires", "CO2 Extinguisher - for extreme heat"]

OUTPUT_TYPES = {"Factor": Factor,
                "Hydrant": Hydrant,
                "Extinguisher": extinguisher_types,
                "CorrectExtinguisher": CorrectExtinguishers,
                "Extinguisher2": extinguisher_types2}

@router.post("/get_outline/")
@ensure_csrf_cookie
def get_outline(request, payload: OutlineIn):
    if type(payload.schema_key) == str:
        output_type = OUTPUT_TYPES.get(payload.schema_key)
        if output_type is None:
            return JsonResponse({"error": "Schema key not known."})
    elif type(payload.schema_key) == dict:
        try:
            output_type = JsonSchema(payload.schema_key)
        except ValidationError:
            return JsonResponse({"error": "Schema key not known and JsonSchema invalid."})
    print("Get Outline called: types -", type(payload.query), output_type)
    outline = service_registry.ai_service.generate_outline(payload.query, output_type)
    print("Outline", outline, type(outline))
    return JsonResponse({"outline": outline})

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
    
    try:
        # Use the standard LLM pipeline which has do_sample=True for diversity
        responses = service_registry.ai_service.generate_outline(messages=messages, response_schema=RegexCandidate, max_new_tokens=2048, num_return_sequences=10)
        
        candidates = []
        print(responses)
        for raw_response in responses:
            # Clean up markdown code blocks if present
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()
            try:
                parsed_candidate = RegexCandidate.model_validate_json(clean_json)
                candidates.append(parsed_candidate.pattern)
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
    print(candidates)
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
    print("scored candidates", scored_candidates)
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
        
        # Ensure models are loaded
        if service_registry.rag_service.summary_generator is None:
             service_registry.rag_service.load_models()

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
