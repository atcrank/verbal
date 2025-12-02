This project uses a Django backend with `django-ninja` to provide a RAG (Retrieval-Augmented Generation) API. The core logic is split between an `llm_api` app and a `rag_service`.

### 1. RAG Service & Document Handling
The `rag_service` is responsible for managing and querying a knowledge base.

* **`rag_service.models.Document`**: A Django model that tracks uploaded files (`.pdf`, `.txt`, `.docx`, etc.) and stores them.
* **Hashing**: When a `Document` is saved, its contents are hashed (SHA256).
* **Vector Store**: The system uses a **FAISS** vector store for efficient similarity search.
* **Indexing**: The `Document.fill_vector_store()` method finds documents in the database that are not yet in the FAISS index (by checking hashes), splits them into chunks, and adds them to the store.

### 2. LLM Service
The `llm_api` is responsible for handling prompts and generating responses.

* **`llm_api.ai_service.AIService`**: This class loads the Hugging Face model (e.g., `Phi-3-mini`) and tokenizer into memory.
* **`llm_api.models.PromptResponseLog`**: A model to log all user interactions, system prompts, and final responses in the database.

### 3. Request Flow
A typical user request flows through the system as follows:

1.  A user sends a prompt to an endpoint in `llm_api/api.py`.
2.  The API view calls `rag_service.get_context(prompt_text)` to find relevant documents from the FAISS vector store.
3.  The API view constructs a `messages` list, inserting the retrieved context into the system prompt.
4.  It then calls `ai_service.generate_response(messages, ...)`.
5.  The `AIService` passes the full prompt to the LLM.
6.  The final, generated text is returned to the user.
7.  The entire interaction (prompts, RAG context, and response) is saved to the `PromptResponseLog` for auditing.