import asyncio
import typing
import json
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from ninja import Router, Schema
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from ninja.security import SessionAuth

from .models import Conversation, PromptResponseLog

from llm_api.apps import service_registry
ai_service = service_registry.get('ai_service')
rag_service = service_registry.get('rag_service')
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
    rags = rag_service.get_context(payload.user_prompt)
    messages = messages + conversation.as_messages() + [{"role": "user", "content": payload.user_prompt + "\n" + rags}]
    max_new_tokens = payload.max_new_tokens
    print("Token Count:", ai_service.count_conversation_tokens(messages))
    response = ai_service.generate_response(messages=messages, max_new_tokens=max_new_tokens)
    cleaned_response = ai_service.clean_response(response)
    system_prompt = messages[0]["content"]
    p = PromptResponseLog(system_prompt=system_prompt, user_prompt=payload.user_prompt,
                          rag_selections=rags, conversation_id=conversation_id,
                          generated_response=cleaned_response, user_id=request.auth.id)
    p.save()

    return JsonResponse({"conversation_id": conversation_id, "cleaned_response": cleaned_response})


@router.post("/get_rag_context/")
@ensure_csrf_cookie
def get_context(request, query:str ="", k:int =1):
    doc_segments = rag_service.get_context(query, k=k)
    print("RAG Response", doc_segments)
    return JsonResponse({"rag_context": doc_segments})

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
    outline = ai_service.generate_outline(payload.query, output_type)
    print("Outline", outline, type(outline))
    return JsonResponse({"outline": outline})

