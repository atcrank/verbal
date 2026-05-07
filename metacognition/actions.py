from typing import Literal, List, Optional
from pydantic import BaseModel, Field
from llm_api.apps import service_registry


# ==========================================
# SKILL: ACTIVE READING
# ==========================================

class ActiveReadingEvaluation(BaseModel):
    reasoning: str = Field(description="Analyze if the provided context chunks fully answer the user's query.")
    context_status: Literal["SUFFICIENT", "NEED_PREVIOUS_CHUNK", "NEED_NEXT_CHUNK", "IRRELEVANT"] = Field(description=(
        "Action to take. "
        "Pick 'SUFFICIENT' if the answer is found. "
        "Pick 'NEED_PREVIOUS_CHUNK' or 'NEED_NEXT_CHUNK' if the context cuts off mid-sentence or lacks a referenced definition."
        "Pick 'IRRELEVANT' if the provided context is completely unrelated to the query."
    ))
    draft_answer: str = Field(description="If SUFFICIENT, provide the answer. Otherwise, leave blank.")


def handle_active_reading(state: dict, llm_output: ActiveReadingEvaluation) -> dict:
    context_status = getattr(llm_output, 'context_status', None)

    if context_status in ["NEED_PREVIOUS_CHUNK", "NEED_NEXT_CHUNK"]:
        rag_service = service_registry.rag_service
        rag_docs_meta = state.get("primary_rag_doc_meta", {})
        indexed_hash = rag_docs_meta.get("indexed_hash")

        if indexed_hash:
            current_index = state.get("current_chunk_index", rag_docs_meta.get("chunk_index", 0))
            target_index = current_index - 1 if context_status == "NEED_PREVIOUS_CHUNK" else current_index + 1

            chunk_ids = rag_service.hashes_indexed.get(indexed_hash, [])
            all_chunks = rag_service.store.mget(chunk_ids)
            target_chunk = next((c for c in all_chunks if c and c.metadata.get("chunk_index") == target_index), None)

            if target_chunk:
                new_context = f"\n\n--- ADDITIONAL FETCHED CONTEXT (Chunk {target_index}) ---\nSource: {target_chunk.metadata.get('filename', 'Unknown')}\nContent: {target_chunk.page_content}\n"
                state["working_prompt"] += new_context
                state["current_chunk_index"] = target_index
                state["route_to"] = "SELF"
                return state

        state["route_to"] = "FAILURE"
    elif context_status == "SUFFICIENT":
        if llm_output.draft_answer:
            state["working_prompt"] += f"\n\n--- DRAFT ANSWER ---\n{llm_output.draft_answer}\n"
        state["route_to"] = "SUCCESS"
    else:
        state["route_to"] = "FAILURE"
    return state


# ==========================================
# SKILL: DIFFICULT PROMPT / CLARIFICATION
# ==========================================

class DifficultPromptEvaluation(BaseModel):
    reasoning: str = Field(description="Analyze the prompt for missing context, contradictions, or ambiguity.")
    action: Literal["PROCEED", "WEB_SEARCH", "ASK_USER"] = Field(description="Decide how to handle the prompt.")
    search_queries: Optional[List[str]] = Field(default_factory=list,
                                                description="If WEB_SEARCH, provide 1-3 targeted search queries.")
    clarification_question: Optional[str] = Field(default="",
                                                  description="If ASK_USER, provide the exact question to ask the user.")


def handle_difficult_prompt(state: dict, llm_output: DifficultPromptEvaluation) -> dict:
    if llm_output.action == "PROCEED":
        state["route_to"] = "SUCCESS"
    elif llm_output.action == "WEB_SEARCH":
        # Placeholder for actual web search tool
        search_results = f"\n\n--- WEB SEARCH RESULTS for {llm_output.search_queries} ---\n[Simulated search results...]\n"
        state["working_prompt"] += search_results
        state["route_to"] = "SELF"  # Loop back to re-evaluate with new info
    elif llm_output.action == "ASK_USER":
        state[
            "working_prompt"] += f"\n\n--- SYSTEM PAUSE: WAITING FOR USER CLARIFICATION ---\nQuestion: {llm_output.clarification_question}\n"
        state["route_to"] = "USER_INPUT_REQUIRED"  # Halts the blueprint gracefully
    return state


# ==========================================
# EXPORTS
# ==========================================
ACTION_REGISTRY = {
    "handle_active_reading": handle_active_reading,
    "handle_difficult_prompt": handle_difficult_prompt
}
OUTPUT_TYPES = {"ActiveReadingEvaluation": ActiveReadingEvaluation,
                "DifficultPromptEvaluation": DifficultPromptEvaluation}