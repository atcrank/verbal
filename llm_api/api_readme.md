## 🤖 API Endpoints

All API endpoints are located under `/api/` and require session-based authentication.

### `/api/llm/generate_response/`

**Method:** `POST`

Generates a response from the LLM, augmented with context from the local RAG vector store.

**Request Body (JSON):**
* `user_prompt` (str): The user's question or prompt.
* `system_prompt` (str, optional): A custom system prompt. If not provided, a default "expert experiment architect" prompt is used.
* `max_new_tokens` (int, optional): The maximum number of tokens to generate. Defaults to `1000`.

**Process:**
1.  The `user_prompt` is used to query the `RAGService`.
2.  Retrieved RAG context is appended to the `system_prompt` (or the default prompt).
3.  The full prompt (system + RAG + user) is sent to the `AIService`.
4.  The entire interaction and the final, cleaned response are logged to the `PromptResponseLog`.

**Success Response (200):**
* **Content-Type:** `text/plain`
* **Body:** The raw, cleaned string of the generated response.

### `/api/llm/get_rag_context/`

**Method:** `POST`

A utility endpoint to directly test the RAG service and see what context is being retrieved for a given query.

**Request Body (JSON):**
* `query` (str): The query string to search for.

**Success Response (200):**
* **Content-Type:** `text/plain`
* **Body:** A string containing the retrieved document segments, separated by newlines.