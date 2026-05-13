LLM API
=======

This app is home for the LLM service API and the LLM ai_service.

App achievements
----------------

* **Dual-Role Architecture:** Safely isolates heavy local VRAM inference from lightweight web/worker clients via an internal HTTP proxy.
* **Structured JSON Generation:** Deep integration with ``outlines`` to enforce strict Pydantic/JSON Schema adherence, completely eliminating JSON parsing errors.
* **Standardized Endpoints:** Exposes an OpenAI-compatible ``/v1/chat/completions`` interface, making it universally compatible with standard API clients.
* **External Model Routing:** Allows dynamic, per-user routing to fallback onto massive external APIs (like OpenAI or Anthropic) seamlessly.
* **Thread Safety:** Implements reentrant locking (RLock) to queue and interleave rapid UI requests with heavy background processing.

App enhancement
---------------

* **Secure Credential Storage:** Migrate the ``UserAPIKey`` model to use encrypted storage (e.g., ``django-fernet-fields``) instead of plaintext.
* **Streaming Support:** Implement Server-Sent Events (SSE) to stream tokens to the frontend in real-time, significantly improving UX on long generations.
* **Advanced KV-Cache Management:** Investigate switching the local inference backend to vLLM (if VRAM allows) to natively support continuous batching.
* **Prompt Caching:** Implement a caching layer for heavily repeated semantic queries to save compute cycles.


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

