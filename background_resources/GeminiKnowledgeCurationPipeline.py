def is_statement_a_reusable_heuristic(statement: str, llm_pipeline) -> bool:
    """
    Uses the LLM to determine if a user statement is a general principle
    or a project-specific detail.
    """
    judge_prompt = f"""
    You are a software architecture expert. Your task is to analyze a user's statement.
    Determine if the statement is a general, reusable design principle or a specific, project-only detail.

    - A general principle applies to many systems (e.g., "All public APIs should be versioned").
    - A specific detail applies only to the current project (e.g., "The 'users' table needs a 'last_login' column").

    User statement: "{statement}"

    Is this statement a general, reusable design principle? Answer with only "yes" or "no".
    """

    # Using a simple text-generation pipeline for classification
    # Note: In a real system, you'd use a more robust classification method.
    response = llm_pipeline(judge_prompt, max_new_tokens=3)

    # Extract the 'yes' or 'no' from the generated text
    answer = response[0]['generated_text'].splitlines()[-1].lower().strip()
    print(f"Judging statement: '{statement}' -> Is reusable? {answer}")
    return "yes" in answer

# --- Example Usage ---
# user_statement_1 = "I would never accept user input that was coming more frequently than 10 requests per sec, that might be a scraper or some kind of error."
# user_statement_2 = "For the blog project, the post title must be limited to 100 characters."
#
# is_statement_a_reusable_heuristic(user_statement_1, llm_pipeline) # Should return True
# is_statement_a_reusable_heuristic(user_statement_2, llm_pipeline) # Should return False

# PART 2=================================
def normalize_heuristic(statement: str, llm_pipeline) -> str:
    """
    Rewrites a conversational statement into a formal, context-free heuristic.
    """
    normalizer_prompt = f"""
    You are a technical writer. Convert the following user statement into a concise, formal, and reusable design heuristic. Write it as a clear rule or best practice.

    User statement: "{statement}"

    Normalized Heuristic:
    """
    response = llm_pipeline(normalizer_prompt, max_new_tokens=60)
    normalized_text = response[0]['generated_text'].split("Normalized Heuristic:")[-1].strip()
    return normalized_text

# --- Example Usage ---
user_statement = "I would never accept user input that was coming more frequently than 10 requests per sec, that might be a scraper or some kind of error."
normalized_rule = normalize_heuristic(user_statement, llm_pipeline)

print(normalized_rule)
# Expected Output: "Best Practice: Implement rate limiting on user input, typically around 10 requests per second, to prevent scraping and mitigate denial-of-service attacks."

jargon_context = FAISS.from_documents(chunks, embedding_model)

jargon_context = jargon_vector_store.similarity_search(query)
heuristics_context = heuristics_vector_store.similarity_search(query)
# Conceptual code for the combined context
final_context = ("--- Jargon Definitions ---\n" + jargon_context + "\n\n--- Relevant Design Principles ---\n" + heuristics_context)

# ... then use this final_context in your augmented prompt