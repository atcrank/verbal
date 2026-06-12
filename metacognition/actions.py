import os
import stat
import subprocess
from typing import Literal, List, Optional, Union, Annotated, get_args, get_origin
from pydantic import BaseModel, Field
from django.conf import settings
from llm_api.apps import service_registry
from background_resources.rag_service import ActiveReadingEvaluation



# ==========================================
# SKILL: UNIFIED RESEARCH & READING
# ==========================================

class ResearchEvaluation(BaseModel):
    """
    A unified cognitive loop that queries internal knowledge bases (RAG/Grips), evaluates the returned text, and decides whether further research is required.
    
    Step Prompt: You are a meticulous researcher. Analyze the current working context. If you lack information, formulate queries for our RAG (document) and Grips (concept) databases. CRITICAL: Do NOT guess API syntaxes or mathematical formulas. If the user asks for a specific library (e.g., pycid, numpy) or complex model, you MUST search the databases for documentation first. If the current information is SUFFICIENT to answer the user's overarching goal, output SUFFICIENT.
    Evaluation Prompt: Returns a ResearchEvaluation object. If status is SEARCHING, queries must be provided.
    Prior Nodes: DifficultPromptEvaluation
    Following Nodes: StrategicPlan
    """
    reasoning: str = Field(description="Analyze the currently retrieved context against the user's goal. What is missing?")
    status: Literal["SEARCHING", "SUFFICIENT", "UNRESOLVABLE"] = Field(description=(
        "SEARCHING: I need to run the queries below to gather more context. "
        "SUFFICIENT: I have enough context to proceed to planning/execution. "
        "UNRESOLVABLE: The databases do not contain the answer after multiple attempts."
    ))
    rag_queries: List[str] = Field(default_factory=list, description="Targeted queries for searching source documents.")
    grips_queries: List[str] = Field(default_factory=list, description="Targeted queries for searching the conceptual knowledge graph.")

def handle_research(state: dict, llm_output: ResearchEvaluation) -> dict:
    if llm_output.status == "SUFFICIENT":
        state["route_to"] = "SUCCESS"
    elif llm_output.status == "UNRESOLVABLE":
        state["working_prompt"] += "\n\n[SYSTEM: Research concluded without sufficient findings.]\n"
        state["route_to"] = "FAILURE"
    elif llm_output.status == "SEARCHING":
        rag_service = service_registry.rag_service
        grips_service = service_registry.grips_service
        aggregated_context = ""
        
        if rag_service and llm_output.rag_queries:
            for q in llm_output.rag_queries:
                docs = rag_service.get_context(q, k=2)
                if docs:
                    aggregated_context += f"\n\n--- RAG Results for '{q}' ---\n" + "\n".join([d.page_content for d in docs])
                    
        if grips_service and llm_output.grips_queries:
            for q in llm_output.grips_queries:
                docs = grips_service.get_grips_context(q, k=2)
                if docs:
                    aggregated_context += f"\n\n--- Grips Results for '{q}' ---\n" + "\n".join([d.page_content for d in docs])
                    
        if aggregated_context:
            state["working_prompt"] += f"\n\n[SYSTEM: Research Results]\n{aggregated_context}\n"
        else:
            state["working_prompt"] += "\n\n[SYSTEM: Searches returned no new information.]\n"
            
        state["route_to"] = "SELF" # Loop back to re-evaluate the new context
    return state


# ==========================================
# SKILL: DIFFICULT PROMPT / CLARIFICATION
# ==========================================

class DifficultPromptEvaluation(BaseModel):
    """
    Evaluates a user prompt for ambiguity or missing context, proactively querying the internal knowledge base or asking the user for clarification.
    
    Step Prompt: Consider the user's input. Evaluate the prompt for ambiguity or missing context. Decide whether to PROCEED, execute a KNOWLEDGE_SEARCH, or ASK_USER for clarification.
    Evaluation Prompt: Provides a valid DifficultPromptEvaluation with a clear action. If KNOWLEDGE_SEARCH, queries must be provided. If ASK_USER, a clarification question must be provided.
    Prior Nodes: None
    Following Nodes: None
    """
    reasoning: str = Field(description="Analyze the prompt for missing context, contradictions, or ambiguity.")
    action: Literal["PROCEED", "KNOWLEDGE_SEARCH", "ASK_USER"] = Field(description="Decide how to handle the prompt.")
    search_queries: Optional[List[str]] = Field(default_factory=list,
                                                description="If KNOWLEDGE_SEARCH, provide 1-3 targeted search queries to query the internal knowledge base.")
    clarification_question: Optional[str] = Field(default="",
                                                  description="If ASK_USER, provide the exact question to ask the user.")


def handle_difficult_prompt(state: dict, llm_output: DifficultPromptEvaluation) -> dict:
    if llm_output.action == "PROCEED":
        state["route_to"] = "SUCCESS"
    elif llm_output.action == "KNOWLEDGE_SEARCH":
        rag_service = service_registry.rag_service
        grips_service = service_registry.grips_service
        
        aggregated_context = ""
        print(f"🪝 Running Action Hook: handle_difficult_prompt (KNOWLEDGE_SEARCH) with queries: {llm_output.search_queries}")
        
        for query in (llm_output.search_queries or []):
            context_parts = []
            
            if grips_service:
                grips_docs = grips_service.get_grips_context(query, k=2)
                if grips_docs:
                    context_parts.append("\n\n".join([f"Concept [{d.metadata.get('title', 'Unknown')}]:\n{d.page_content}" for d in grips_docs]))
                    
            if rag_service:
                rag_docs = rag_service.get_context(query, k=2)
                if rag_docs:
                     context_parts.append("\n\n".join([f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs]))
                     
            if context_parts:
                aggregated_context += f"\n\n--- KNOWLEDGE SEARCH RESULTS FOR '{query}' ---\n"
                aggregated_context += "\n---\n".join(context_parts)
                
        if aggregated_context:
            state["working_prompt"] += f"\n\n[SYSTEM: Executed Knowledge Search for clarification: {', '.join(llm_output.search_queries)}]{aggregated_context}\n"
        else:
            state["working_prompt"] += f"\n\n[SYSTEM: Knowledge Search yielded no results. Please formulate a clarification question to ASK_USER.]\n"
        
        state["route_to"] = "SELF"  # Loop back to re-evaluate with new info or to fall back to ASK_USER
    elif llm_output.action == "ASK_USER":
        state["working_prompt"] += f"\n\n--- SYSTEM PAUSE: WAITING FOR USER CLARIFICATION ---\nQuestion: {llm_output.clarification_question}\n"
        state["route_to"] = "USER_INPUT_REQUIRED"  # Halts the blueprint gracefully
    return state


# ==========================================
# SKILL: STRATEGIC PLANNING
# ==========================================

class StrategicPlan(BaseModel):
    """
    Drafts a step-by-step technical plan before executing any code.
    
    Step Prompt: Review the user request and gathered research. Formulate a highly specific, step-by-step strategy for how to solve the problem using code execution. Do not write the code yet; just write the logical plan.
    Evaluation Prompt: Returns a logical StrategicPlan.
    Prior Nodes: ResearchEvaluation
    Following Nodes: ExecutionPlan
    """
    context_summary: str = Field(description="Briefly summarize the current state and known constraints.")
    strategy_steps: List[str] = Field(min_length=1, description="A list of logical steps outlining how to solve the problem.")

# ==========================================
# SKILL: SCIENTIFIC CRITIQUE (THE OUTER LOOP)
# ==========================================

class ResultCritique(BaseModel):
    """
    Reviews the output of a completed execution plan to determine if the scientific/mathematical goal was actually achieved.
    
    Step Prompt: You are a senior data scientist. Review the final results provided by the execution agent. Does the data logically align with the theoretical expectations? Are the statistics robust? If the output is flawed, naive, or missing critical precision, REJECT it and provide feedback. If it is solid, ACCEPT it.
    Evaluation Prompt: Returns a ResultCritique object with an ACCEPT or REJECT decision.
    Prior Nodes: ExecutionPlan
    Following Nodes: None
    Failure Nodes: StrategicPlan
    """
    reasoning: str = Field(description="Analyze the quantitative results. Do they make logical sense in the context of the real-world problem?")
    decision: Literal["ACCEPT", "REJECT"] = Field(description="ACCEPT if the results are robust. REJECT if the model needs to be refined or re-calculated.")
    feedback_for_planner: Optional[str] = Field(default="", description="If REJECT, provide specific instructions on what the Strategic Planner must change in the next iteration.")

def handle_result_critique(state: dict, llm_output: ResultCritique) -> dict:
    if llm_output.decision == "ACCEPT":
        state["route_to"] = "SUCCESS"
    else:
        state["working_prompt"] += f"\n\n[SYSTEM: CRITIQUE REJECTED THE RESULTS]\nFeedback from Senior Data Scientist:\n{llm_output.feedback_for_planner}\n\nYou must now formulate a new StrategicPlan to fix these issues."
        state["route_to"] = "FAILURE" # Routes backwards via on_failure_step!
    return state

# ==========================================
# SKILL: HELPERS
# ==========================================

def _get_schema_description(schema) -> str:
    """Introspects a Pydantic model or Union to generate a markdown-like description."""
    text = ""
    
    # Handle Union types like our ActionItemType
    if get_origin(schema) is Union:
        options = get_args(schema)
        text += "The `queue` must contain a list of the following tools:\n\n"
        for option in options:
            if hasattr(option, 'model_fields'):
                tool_name = option.model_fields['tool'].annotation.__args__[0]
                text += f"#### Tool: `{tool_name}`\n"
                for name, field in option.model_fields.items():
                    if name != 'tool':
                        text += f"- **{name}**: {field.description}\n"
                text += "\n"
    return text

# ==========================================
# SKILL: STATEFUL EXECUTION PLANNING
# ==========================================

class CheckParsingArgs(BaseModel):
    code: str = Field(description="The Python code to syntax-check.")

class WriteFileArgs(BaseModel):
    filepath: str = Field(description="A simple filename (e.g., 'script.py'). Do NOT use absolute paths or directories.")
    content: str = Field(description="The complete Python file content to write. CRITICAL: Use simple print() statements to output results. Avoid multi-line strings and escaped newlines (\\n).")

class ReadFileArgs(BaseModel):
    filepath: str = Field(description="A simple filename of the file to read.")

class ListFilesArgs(BaseModel):
    directory: str = Field(default=".", description="Relative directory path to list (use '.' for workspace root).")

class ExecuteScriptArgs(BaseModel):
    filepath: str = Field(description="A simple filename of the Python script to run.")

class TaskCompleteArgs(BaseModel):
    final_answer: str = Field(description="The final output, code, or result to return to the parent cognitive process.")

class CheckParsingAction(BaseModel):
    tool: Literal["CHECK_PARSING"]
    parameters: CheckParsingArgs
    expected_outcome: str = Field(description="What this tool MUST achieve.")

class WriteFileAction(BaseModel):
    tool: Literal["WRITE_FILE"]
    parameters: WriteFileArgs
    expected_outcome: str = Field(description="What this tool MUST achieve.")

class ReadFileAction(BaseModel):
    tool: Literal["READ_FILE"]
    parameters: ReadFileArgs
    expected_outcome: str = Field(description="What this tool MUST achieve.")

class ListFilesAction(BaseModel):
    tool: Literal["LIST_FILES"]
    parameters: ListFilesArgs
    expected_outcome: str = Field(description="What this tool MUST achieve.")

class ExecuteScriptAction(BaseModel):
    tool: Literal["EXECUTE_SCRIPT"]
    parameters: ExecuteScriptArgs
    expected_outcome: str = Field(description="What this tool MUST achieve.")

class TaskCompleteAction(BaseModel):
    tool: Literal["TASK_COMPLETE"]
    parameters: TaskCompleteArgs
    expected_outcome: str = Field(default="Terminate the planning loop.")

ActionItemType = Annotated[
    Union[
        CheckParsingAction,
        WriteFileAction,
        ReadFileAction,
        ListFilesAction,
        ExecuteScriptAction,
        TaskCompleteAction
    ],
    Field(discriminator="tool")
]

class ExecutionPlan(BaseModel):
    """
    A loop-based executor designed strictly for I/O tasks like code generation, testing, and file manipulation.

    Step Prompt: You are a code execution agent. Your goal is to use a sequence of tools to accomplish the user's request.
    Review the user's goal and the results of previous tool executions.
    Formulate a plan by creating a queue of tool actions.
    CRITICAL: If the user's request has been fully satisfied by the execution results, your response MUST be a single `TASK_COMPLETE` action to terminate the loop.
    Evaluation Prompt: Returns an ExecutionPlan containing a valid queue of ActionItems.
    Prior Nodes: None
    Following Nodes: ResultCritique
    Failure Nodes: None
    """
    analysis: str = Field(description="System 2 reasoning: Analyze the situation. If the final answer is already in the previous execution output, explicitly state that it's time to terminate using TASK_COMPLETE.")
    queue: List[ActionItemType] = Field(min_length=1, description="The sequential list of actions to execute. If you know the final answer, this MUST contain exactly one action: TASK_COMPLETE.")

def _tool_check_parsing(params: CheckParsingArgs) -> str:
    import ast
    try:
        ast.parse(params.code)
        return "\n\n[CHECK_PARSING]\nSuccess: The provided Python code is syntactically valid."
    except SyntaxError as e:
        return f"\n\n[CHECK_PARSING Error]\nSyntaxError: {e}\nLine {e.lineno}: {e.text}"

def _get_workspace_dir(conversation_id: str) -> str:
    """Ensures a Git-tracked workspace exists for the conversation."""
    cid_str = str(conversation_id) if conversation_id else "temp_workspace"
    workspace_dir = os.path.join(settings.BASE_DIR, 'workspaces', cid_str)
    
    if not os.path.exists(workspace_dir):
        os.makedirs(workspace_dir, exist_ok=True)
        # Initialize git tracking
        subprocess.run(["git", "init"], cwd=workspace_dir, capture_output=True)
        
    return workspace_dir

def _commit_workspace(workspace_dir: str, message: str) -> str:
    """Commits changes to git and returns the commit hash."""
    subprocess.run(["git", "add", "."], cwd=workspace_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=workspace_dir, capture_output=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace_dir, capture_output=True, text=True)
    return result.stdout.strip()

def _tool_write_file(params: WriteFileArgs, workspace_dir: str) -> str:
    safe_filepath = params.filepath.lstrip("/\\")
    file_path = os.path.abspath(os.path.join(workspace_dir, safe_filepath))
    # Prevent directory traversal attacks
    if not os.path.abspath(file_path).startswith(os.path.abspath(workspace_dir)):
        return f"\n\n[WRITE_FILE Error]\nCannot write outside of workspace directory."
        
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(params.content)
    
    commit_hash = _commit_workspace(workspace_dir, f"LLM wrote {params.filepath}")
    return f"\n\n[WRITE_FILE Success]\nWrote {len(params.content)} characters to {params.filepath}.\nWorkspace saved at commit: {commit_hash[:7]}"

def _tool_read_file(params: ReadFileArgs, workspace_dir: str) -> str:
    safe_filepath = params.filepath.lstrip("/\\")
    file_path = os.path.abspath(os.path.join(workspace_dir, safe_filepath))
    if not os.path.abspath(file_path).startswith(os.path.abspath(workspace_dir)):
        return f"\n\n[READ_FILE Error]\nCannot read outside of workspace directory."

    if ".git" in file_path.split(os.sep):
        return f"\n\n[READ_FILE Error]\nAccess to version control internals (.git/) is strictly prohibited."

    if not os.path.exists(file_path):
        return f"\n\n[READ_FILE Error]\nFile '{params.filepath}' does not exist."
        
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    return f"\n\n[READ_FILE Result: {params.filepath}]\n```\n{content}\n```"

def _tool_list_files(params: ListFilesArgs, workspace_dir: str) -> str:
    safe_dir = params.directory.lstrip("/\\") if params.directory != "." else ""
    target_dir = os.path.abspath(os.path.join(workspace_dir, safe_dir))
    
    if not target_dir.startswith(os.path.abspath(workspace_dir)):
        return f"\n\n[LIST_FILES Error]\nCannot list directories outside of workspace."
        
    if not os.path.isdir(target_dir):
        return f"\n\n[LIST_FILES Error]\nDirectory '{params.directory}' does not exist."
        
    try:
        entries = []
        for entry in os.scandir(target_dir):
            if entry.name == '.git':
                continue
            info = entry.stat()
            mode = stat.filemode(info.st_mode)
            size = info.st_size
            entries.append(f"{mode} {size:>8} bytes  {entry.name}{'/' if entry.is_dir() else ''}")
        
        listing = "\n".join(entries) if entries else "(empty directory)"
        return f"\n\n[LIST_FILES Result: {params.directory}]\n```\n{listing}\n```"
    except Exception as e:
        return f"\n\n[LIST_FILES Error]\nFailed to list directory: {str(e)}"

def _tool_execute_script(params: ExecuteScriptArgs, workspace_dir: str) -> str:
    import requests
    
    # We pass only the relative path (conversation_id/script.py) to the sandbox
    safe_filepath = params.filepath.lstrip("/\\")
    cid_str = os.path.basename(workspace_dir)
    sandbox_filepath = f"{cid_str}/{safe_filepath}"
    
    sandbox_url = getattr(settings, "SANDBOX_URL", "http://sandbox:8000/execute")
    
    try:
        resp = requests.post(
            sandbox_url,
            json={"filepath": sandbox_filepath, "timeout": 30},
            timeout=35 # Generous outer timeout in case of network latency
        )
        
        # If the sandbox throws a 400/404/500, extract the helpful FastAPI error message
        if not resp.ok:
            try:
                error_detail = resp.json().get("detail", resp.text)
            except Exception:
                error_detail = resp.text
            return f"\n\n[EXECUTE_SCRIPT Alert]\nSandbox rejected the request (Status {resp.status_code}): {error_detail}\nDid you remember to use WRITE_FILE first?"
            
        data = resp.json()
        
        output = f"\n\n[EXECUTE_SCRIPT Result]\nStatus: {data.get('status')}\nReturn Code: {data.get('returncode')}\n"
        if data.get('stdout'):
            output += f"\n--- STDOUT ---\n{data.get('stdout').strip()}"
        if data.get('stderr'):
            output += f"\n--- STDERR ---\n{data.get('stderr').strip()}"
            
        return output
    except Exception as e:
        return f"\n\n[EXECUTE_SCRIPT Alert]\nFailed to execute script. Sandbox error: {str(e)}"

def handle_execution_plan(state: dict, llm_output: ExecutionPlan) -> dict:
    print(f"🪝 Received Execution Plan with {len(llm_output.queue)} steps.")
    
    plan_results = ""
    workspace_dir = _get_workspace_dir(state.get("conversation_id"))
    
    for action in llm_output.queue:
        print(f"  -> Executing tool: {action.tool}")
        try:
            if action.tool == "TASK_COMPLETE" and action.parameters.final_answer:
                state["working_prompt"] += f"\n\n[TASK COMPLETE]\n{action.parameters.final_answer}\n"
                state["route_to"] = "SUCCESS"
                return state
                
            elif action.tool == "CHECK_PARSING":
                plan_results += _tool_check_parsing(action.parameters)
            elif action.tool == "WRITE_FILE":
                plan_results += _tool_write_file(action.parameters, workspace_dir)
            elif action.tool == "READ_FILE":
                plan_results += _tool_read_file(action.parameters, workspace_dir)
            elif action.tool == "LIST_FILES":
                plan_results += _tool_list_files(action.parameters, workspace_dir)
            elif action.tool == "EXECUTE_SCRIPT":
                plan_results += _tool_execute_script(action.parameters, workspace_dir)
            else:
                plan_results += f"\n\n[TOOL FAILURE: '{action.tool}' is not a recognized tool.]\n"
                break
                
        except Exception as e:
            error_msg = f"\n\n[TOOL BREAKDOWN] The tool '{action.tool}' failed with error: {str(e)}\n"
            error_msg += f"Expected Outcome: {action.expected_outcome}\n"
            error_msg += "Please analyze this failure and re-plan your next steps."
            plan_results += error_msg
            break
            
    state["working_prompt"] += f"\n\n--- EXECUTION PLAN RESULTS ---\n{plan_results}\n"
    state["working_prompt"] += "\n[SYSTEM INSTRUCTION]: Read the results above. If the execution succeeded and you have the final answer, your NEXT action MUST be TASK_COMPLETE. Do NOT repeat previous actions. If you encountered an error, write a corrected script and try again."
    state["route_to"] = "SELF" # Loop back to LLM to re-evaluate or execute more steps
    return state

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
# EXPORTS
# ==========================================
ACTION_REGISTRY = {
    "handle_active_reading": handle_active_reading,
    "handle_difficult_prompt": handle_difficult_prompt,
    "handle_research": handle_research,
    "handle_execution_plan": handle_execution_plan,
    "handle_result_critique": handle_result_critique,
}
OUTPUT_TYPES = {"ResearchEvaluation": ResearchEvaluation,
                "DifficultPromptEvaluation": DifficultPromptEvaluation,
                "StrategicPlan": StrategicPlan,
                "ExecutionPlan": ExecutionPlan,
                "ResultCritique": ResultCritique}

# Inject the detailed schema description into the system prompt for the ExecutionPlan
enhanced_prompt = (
    f"Step Prompt:\n\n{_get_schema_description(ActionItemType)}\n\n"
    "CRITICAL INSTRUCTIONS:\n"
    "1. The sandbox can only execute files that you have already written using WRITE_FILE.\n"
    "2. Scripts must use simple print() statements to output results. Avoid multi-line strings or escaped newlines (\\n).\n"
    "3. Do NOT repeat actions that have already succeeded.\n"
    "4. If the results of a previous EXECUTE_SCRIPT give you the answer to the user's request, your NEXT queue MUST contain ONLY the `TASK_COMPLETE` tool with the final answer.\n"
    "5. If a script fails due to memory limits, reduce array sizes or iterations, or avoid heavy libraries like numpy for simple tasks."
)
ExecutionPlan.__doc__ = ExecutionPlan.__doc__.replace("Step Prompt:", enhanced_prompt)