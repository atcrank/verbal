LLM API
=======

This app is home for the LLM service API and the LLM ai_service.

App achievements
----------------

* **Tri-Backend Routing:** Safely isolates heavy local VRAM inference from lightweight web/worker clients, routing traffic fluidly between Native PyTorch, local Ollama containers, or high-throughput vLLM services.
* **Structured JSON Generation:** Deep integration with ``outlines`` to enforce strict Pydantic/JSON Schema adherence, completely eliminating JSON parsing errors.
* **Standardized Endpoints:** Exposes an OpenAI-compatible ``/v1/chat/completions`` interface, making it universally compatible with standard API clients.
* **External Model Routing:** Allows dynamic, per-user routing to fallback onto massive external APIs (like OpenAI or Anthropic) seamlessly.
* **Dynamic LoRA Swapping:** Supports loading and unloading PEFT LoRA adapters on the fly directly in PyTorch VRAM to give the base model specialized skills for different cognitive steps.
* **Thread Safety:** Implements reentrant locking (RLock) to queue and interleave rapid UI requests with heavy background processing.

App enhancement
---------------

* **Semantic Cache / Conversation Search:** Rather than basic prompt caching, explore surfacing relevant historic conversation turns alongside RAG retrievals to prevent duplicating complex systemic workflows.


Database models
---------------

.. automodule:: llm_api.models
   :members:
   :undoc-members:
   :show-inheritance:

API Reference
-------------

.. automodule:: llm_api.api
   :members:
   :undoc-members:
   :show-inheritance:

Background Tasks
----------------

.. automodule:: llm_api.ai_service
   :members:
   :undoc-members:
   :show-inheritance:

