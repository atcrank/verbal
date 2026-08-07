import logging
logger = logging.getLogger(__name__)

import os
import stat
import subprocess
from typing import Literal, List, Optional, Union, Annotated, get_args, get_origin
from pydantic import BaseModel, Field
from django.conf import settings
from llm_api.apps import service_registry


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
        from background_resources.retrieval import unified_retrieve, format_context_block

        all_queries = list(set(
            (llm_output.rag_queries or []) + (llm_output.grips_queries or [])
        ))
        aggregated_context = ""

        for q in all_queries:
            results = unified_retrieve(
                query=q,
                rag_service=service_registry.rag_service,
                grips_service=service_registry.grips_service,
                rag_k=2,
                grips_k=2,
            )
            if results:
                aggregated_context += f"\n\n--- Results for '{q}' ---\n" + format_context_block(results)

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
        from background_resources.retrieval import unified_retrieve, format_context_block

        aggregated_context = ""
        logger.info(f'🪝 Running Action Hook: handle_difficult_prompt (KNOWLEDGE_SEARCH) with queries: {llm_output.search_queries}')

        for query in (llm_output.search_queries or []):
            results = unified_retrieve(
                query=query,
                rag_service=service_registry.rag_service,
                grips_service=service_registry.grips_service,
                rag_k=2,
                grips_k=2,
            )
            if results:
                aggregated_context += f"\n\n--- KNOWLEDGE SEARCH RESULTS FOR '{query}' ---\n"
                aggregated_context += format_context_block(results)

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
    quantitative_result: str = Field(
        description="Briefly restate the answer or summarize the conclusion that answers the original user prompt.")
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

class MakeDirArgs(BaseModel):
    directory: str = Field(description="A sensible directory name for a new folder to create because it is needed for this plan.")
    parent_directory: str = Field(description="The relative path of the folder the new folder should be in. Do NOT use absolute paths or directories.")

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

class MakeDirAction(BaseModel):
    tool: Literal["CREATE_FOLDER"]
    parameters: MakeDirArgs
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
        MakeDirAction,
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

def _tool_create_folder(params: MakeDirArgs, workspace_dir: str, conversation_id: str = None) -> str:
    safe_parent = params.parent_directory.lstrip("/\\") if params.parent_directory not in [".", ""] else ""
    safe_dir = params.directory.lstrip("/\\")
    target_path = os.path.abspath(os.path.join(workspace_dir, safe_parent, safe_dir))
    
    if not target_path.startswith(os.path.abspath(workspace_dir)):
        return f"\n\n[CREATE_FOLDER Error]\nCannot create directories outside of workspace."
        
    try:
        os.makedirs(target_path, exist_ok=True)
        commit_hash = _commit_workspace(workspace_dir, f"LLM created directory {os.path.join(safe_parent, safe_dir)}")
        
        if conversation_id:
            from llm_api.models import PromptResponseLog
            log = PromptResponseLog.objects.filter(conversation_id=conversation_id).first()
            if log:
                log.git_commit_hash = commit_hash
                log.save(update_fields=['git_commit_hash'])
                
        return f"\n\n[CREATE_FOLDER Success]\nDirectory '{safe_dir}' successfully created.\nWorkspace saved at commit: {commit_hash[:7]}"
    except Exception as e:
        return f"\n\n[CREATE_FOLDER Error]\nFailed to create directory: {str(e)}"

def _tool_write_file(params: WriteFileArgs, workspace_dir: str, conversation_id: str = None) -> str:
    safe_filepath = params.filepath.lstrip("/\\")
    file_path = os.path.abspath(os.path.join(workspace_dir, safe_filepath))
    # Prevent directory traversal attacks
    if not os.path.abspath(file_path).startswith(os.path.abspath(workspace_dir)):
        return f"\n\n[WRITE_FILE Error]\nCannot write outside of workspace directory."

    target_dir = os.path.dirname(file_path)

    # Strict Enforcement: If the target directory does not exist, fail immediately.
    if target_dir and not os.path.exists(target_dir):
        return f"""error: Action Failed: The directory '{target_dir}' does not exist. 
                You must use the CREATE_FOLDER action to build the directory structure 
                before attempting to write files into it."""

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(params.content)
    
    commit_hash = _commit_workspace(workspace_dir, f"LLM wrote {params.filepath}")
    
    if conversation_id:
        from llm_api.models import PromptResponseLog
        log = PromptResponseLog.objects.filter(conversation_id=conversation_id).first()
        if log:
            log.git_commit_hash = commit_hash
            log.save(update_fields=['git_commit_hash'])
            
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
        
        def _truncate_output(text: str, max_length: int = 1500) -> str:
            """Protects the LLM context window by truncating massive console outputs."""
            if not text or len(text) <= max_length:
                return text
            half = max_length // 2
            return text[:half] + f"\n\n... [TRUNCATED {len(text) - max_length} CHARACTERS TO SAVE TOKENS] ...\n\n" + text[-half:]

        if data.get('stdout'):
            output += f"\n--- STDOUT ---\n{_truncate_output(data.get('stdout').strip())}"
        if data.get('stderr'):
            output += f"\n--- STDERR ---\n{_truncate_output(data.get('stderr').strip())}"
            
        return output
    except Exception as e:
        return f"\n\n[EXECUTE_SCRIPT Alert]\nFailed to execute script. Sandbox error: {str(e)}"

def handle_execution_plan(state: dict, params: dict) -> dict:
    queue = params.get("queue", [])
    analysis = params.get("analysis", "")
    logger.info(f'🪝 Received Execution Plan with {len(queue)} steps.')
    logger.info(f'DEBUG QUEUE: {repr(queue)}')
    
    plan_results = ""
    workspace_dir = _get_workspace_dir(state.get("conversation_id"))
    
    from collections import namedtuple
    ActionParam = namedtuple('ActionParam', ['tool', 'parameters', 'expected_outcome'])
    
    # Parse queue elements if they are dicts
    parsed_queue = []
    for action in queue:
        if isinstance(action, dict):
            class DictToObject:
                def __init__(self, d):
                    for k, v in d.items():
                        if isinstance(v, dict):
                            setattr(self, k, DictToObject(v))
                        else:
                            setattr(self, k, v)
                def get(self, k, default=None):
                    return getattr(self, k, default)
            parsed_queue.append(DictToObject(action))
        else:
            parsed_queue.append(action)
            
    for action in parsed_queue:
        logger.info(f'  -> Executing tool: {action.tool}')
        try:
            if action.tool == "TASK_COMPLETE" and action.parameters.final_answer:
                state["working_prompt"] = state.get("working_prompt", "") + f"\n\n[TASK COMPLETE]\n{action.parameters.final_answer}\n"
                state["route_to"] = "SUCCESS"
                return state
                
            elif action.tool == "CHECK_PARSING":
                plan_results += _tool_check_parsing(action.parameters)
            elif action.tool == "CREATE_FOLDER":
                plan_results += _tool_create_folder(action.parameters, workspace_dir, state.get("conversation_id"))
            elif action.tool == "WRITE_FILE":
                plan_results += _tool_write_file(action.parameters, workspace_dir, state.get("conversation_id"))
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


# ==========================================
# EXPORTS
# ==========================================
class PromptVariant(BaseModel):
    """
    A proposed fine-tuning of an existing system prompt to address specific failures.
    """
    reasoning: str = Field(description="Analyze the provided failure logs and the original system prompt. Why did the LLM fail to follow the instructions or achieve the goal?")

def create_prompt_variant(state: dict, llm_output: PromptVariant) -> dict:
    state["working_prompt"] += f"\n\n[SYSTEM: NightManager proposed variant based on reasoning: {llm_output.reasoning}]\n"
    state["route_to"] = "SUCCESS"
    return state


class TaskItem(BaseModel):
    goal: str = Field(description="The goal of this task item.")
    delegated_blueprint: str | None = Field(None, description="Optional blueprint name to delegate this task to.")

class TaskQueue(BaseModel):
    queue: list[TaskItem] = Field(description="A list of task items to process sequentially.")

def process_task_queue(state: dict, llm_output) -> dict:
    task_queue = state.get("scratch", {}).get("queue", [])
    if not task_queue:
        state["route_to"] = "SUCCESS"
        return state
    
    current_task = task_queue.pop(0)
    state["scratch"]["queue"] = task_queue
    
    goal = current_task.get("goal")
    delegated = current_task.get("delegated_blueprint")
    
    state["working_prompt"] += f"\n\n--- NEXT TASK ---\nGoal: {goal}\n"
    if delegated:
        from .models import CognitiveBlueprint
        from .tasks import run_blueprint
        try:
            bp = CognitiveBlueprint.objects.get(name=delegated)
            res = run_blueprint(bp.id, goal, conversation_id=state.get("conversation_id"), user_id=state.get("user_id"))
            sub_resp = res.get("final_response", "")
            state["working_prompt"] += f"\n[SYSTEM: Delegated Blueprint '{delegated}' Completed. Output:\n{sub_resp}\n]\n"
        except Exception as e:
            state["working_prompt"] += f"\n[SYSTEM: Failed to delegate to '{delegated}': {e}]\n"
            
    state["route_to"] = "SELF"
    return state

def python_sandbox(state: dict, params: dict) -> dict:
    """
    Executes Python code in a secure sandbox.
    """
    import requests
    import os
    from django.conf import settings
    
    code = params.get("code", "")
    
    conversation_id = state.get("conversation_id")
    workspace_dir = _get_workspace_dir(conversation_id)
    safe_filepath = "sandbox_script.py"
    script_path = os.path.join(workspace_dir, safe_filepath)
    
    with open(script_path, "w") as f:
        f.write(code)
        
    cid_str = str(conversation_id) if conversation_id else "temp_workspace"
    sandbox_filepath = f"{cid_str}/{safe_filepath}"
    
    sandbox_url = getattr(settings, "SANDBOX_URL", "http://sandbox:8000/execute")
    try:
        resp = requests.post(sandbox_url, json={"filepath": sandbox_filepath, "timeout": 30}, timeout=35)
        if resp.status_code == 200:
            result = resp.json()
            out = result.get("stdout", "")
            err = result.get("stderr", "")
            rc = result.get("returncode", 0)
            if rc != 0 or err:
                return {
                    "working_prompt": f"\n\n[SYSTEM: Sandbox Execution Failed (RC={rc}).\nError:\n{err}\nOutput:\n{out}\n]\n",
                    "route_to": "SELF"
                }
            else:
                return {
                    "working_prompt": f"\n\n[SYSTEM: Sandbox Execution Succeeded.\nOutput:\n{out}\n]\n",
                    "route_to": "SUCCESS"
                }
        else:
            return {
                "working_prompt": f"\n\n[SYSTEM: Sandbox API returned status {resp.status_code}: {resp.text}]\n",
                "route_to": "SELF"
            }
    except Exception as e:
        return {
            "working_prompt": f"\n\n[SYSTEM: Sandbox API Request Failed: {e}]\n",
            "route_to": "SELF"
        }

class EdgeLintResult(BaseModel):
    """
    Evaluates and rewrites a KnowledgeEdge justification to be human-readable.
    
    Step Prompt: You are a meticulous editor. Review the existing relationship justification between two concepts. Rewrite it so that it NEVER uses placeholder terms like 'Concept A' or 'Concept B'. Instead, use the actual titles of the concepts. Ensure the justification sounds natural, professional, and clear for a human reader. You MUST also return the edge_id provided in the prompt.
    Evaluation Prompt: Returns an EdgeLintResult object containing the improved justification and the original edge_id.
    """
    edge_id: int = Field(description="The ID of the edge being linted, exactly as provided in the prompt.")
    improved_justification: str = Field(description="The rewritten, human-readable justification.")

def handle_edge_lint_tool(state: dict, params: dict) -> str:
    from grips.models import KnowledgeEdge
    
    edge_id = params.get("edge_id")
    improved_justification = params.get("improved_justification")
    
    if not edge_id or not improved_justification:
        return "Error - Missing edge_id or improved_justification."
        
    try:
        edge = KnowledgeEdge.objects.get(id=edge_id)
        edge.justification = improved_justification
        edge.save(update_fields=['justification'])
        return f"Successfully linted edge {edge_id}."
    except KnowledgeEdge.DoesNotExist:
        return f"Error - Edge {edge_id} not found."


class NodeLintResult(BaseModel):
    """
    Evaluates and rewrites a ConceptNode narrative to fix style violations, missing references, or clarify meaning.
    
    Step Prompt: You are a meticulous editor. Review the existing ConceptNode narrative and apply the suggested fixes. Make sure to adhere to the Domain Style Guide and explicitly incorporate any exceptions or limitations (determinate negations). Return the improved narrative and the node_id.
    Evaluation Prompt: Returns a NodeLintResult object containing the rewritten narrative and the original node_id.
    """
    node_id: int = Field(description="The ID of the ConceptNode being linted, exactly as provided in the prompt.")
    improved_narrative: str = Field(description="The rewritten narrative for the ConceptNode.")

def handle_node_lint_tool(state: dict, params: dict) -> str:
    from grips.models import ConceptNode
    
    node_id = params.get("node_id")
    improved_narrative = params.get("improved_narrative")
    
    if not node_id or not improved_narrative:
        return "Error - Missing node_id or improved_narrative."
        
    try:
        node = ConceptNode.objects.get(id=node_id)
        node.narrative_content = improved_narrative
        # By saving the node, it resets needs_linting in the normal workflow (if any post_save exists). 
        # But we will let the blueprint clear the issue_flags if it passes the next re-evaluation step.
        node.save(update_fields=['narrative_content'])
        return f"Successfully updated narrative for node {node_id}."
    except ConceptNode.DoesNotExist:
        return f"Error - Node {node_id} not found."


class StructuredClaim(BaseModel):
    subject: str = Field(description="The subject of the claim (e.g., 'Foam Dispenser X').")
    predicate: str = Field(description="ENUM: [REQUIRES, CAPABLE_OF, INCOMPATIBLE_WITH, HAS_PROPERTY, IS_A, PART_OF]")
    object: str = Field(description="The object of the claim (e.g., 'PPE Level A').")
    qualifier: str = Field(default="", description="Optional context or condition for the claim.")

class ConceptExtraction(BaseModel):
    """
    Extracted concept and its operational logic claims.
    """
    title: str = Field(description="Clear, concise title of the concept.")
    focus_hint: str = Field(description="A short phrase explaining the context of this concept in the domain.")
    narrative_content: str = Field(description="Encyclopedic explanation of the concept.")
    claims: list[StructuredClaim] = Field(default_factory=list, description="Operational logic claims for symbolic computation.")

def handle_create_concept_nodes_tool(state: dict, params: dict) -> str:
    from grips.models import ConceptNode, Domain
    from document_storage.models import Document
    import re
    
    domain_id = params.get("domain_id")
    document_id = params.get("document_id")
    concepts = params.get("concepts", [])
    
    if not domain_id or not concepts:
        return "Error - Missing domain_id or concepts."
        
    try:
        domain = Domain.objects.get(id=domain_id)
    except Domain.DoesNotExist:
        return f"Error - Domain {domain_id} not found."
        
    root_node = None
    if document_id:
        try:
            from background_resources.models import Document
            doc = Document.objects.get(id=document_id)
            safe_doc_title = re.sub(r'[^a-zA-Z0-9]', '-', doc.title.lower())[:50]
            root_node, _ = ConceptNode.objects.get_or_create(
                domain=domain,
                slug=f"doc-{doc.id}-{safe_doc_title}",
                defaults={
                    "title": f"Doc: {doc.title[:200]}",
                    "focus_hint": "Document Root Node",
                    "narrative_content": "Extracted from document.",
                    "needs_linting": True
                }
            )
        except Exception:
            pass

    created_count = 0
    from grips.models import KnowledgeEdge
    
    for c in concepts:
        title = c.get('title', 'Unknown Concept')
        safe_title = re.sub(r'[^a-zA-Z0-9]', '-', title.lower())[:50]
        
        # If part of a document, prefix the slug so chunks don't clash identically named concepts from other docs easily
        slug = f"doc-{document_id}-{safe_title}" if document_id else safe_title
        
        node, created = ConceptNode.objects.get_or_create(
            domain=domain,
            slug=slug,
            defaults={
                "title": title[:200],
                "focus_hint": c.get('focus_hint', ''),
                "narrative_content": c.get('narrative_content', ''),
                "structured_claims": c.get('claims', []),
                "needs_linting": True
            }
        )
        if created:
            created_count += 1
            
        if root_node and node != root_node:
            KnowledgeEdge.objects.get_or_create(
                source=root_node,
                target=node,
                relationship_type='INCLUDES',
                defaults={"justification": "Extracted Sub-Concept"}
            )
            
    # Trigger Level 2 digestion incrementally for newly created concepts?
    # This could be handled by a queue, but for now we just return.
    return f"Successfully created {created_count} ConceptNodes."

class EvaluateConceptNeighborsResult(BaseModel):
    decision: str = Field(description="ENUM: [MERGE, EDGE, DISTINCT]")
    justification: str = Field(description="Explanation for the relationship or merge decision.")

def handle_evaluate_concept_neighbors_tool(state: dict, params: dict) -> str:
    from grips.models import ConceptNode, KnowledgeEdge
    
    source_id = params.get("source_id")
    target_id = params.get("target_id")
    decision = params.get("decision")
    justification = params.get("justification", "")
    
    if not source_id or not target_id or not decision:
        return "Error - Missing required parameters."
        
    try:
        source = ConceptNode.objects.get(id=source_id)
        target = ConceptNode.objects.get(id=target_id)
    except ConceptNode.DoesNotExist:
        return "Error - Source or Target node not found."
        
    if decision == "MERGE":
        # For simplicity, we just mark a RELATED_TO edge with 'MERGE' in justification, 
        # as actual node merging is complex (re-wiring edges, vectors, etc).
        KnowledgeEdge.objects.get_or_create(
            source=source, target=target, relationship_type='RELATED_TO',
            defaults={"justification": f"[MERGE SUGGESTED] {justification}"}
        )
        return "Successfully evaluated. Merge suggested via RELATED_TO edge."
    elif decision == "EDGE":
        KnowledgeEdge.objects.get_or_create(
            source=source, target=target, relationship_type='RELATED_TO',
            defaults={"justification": justification}
        )
        return "Successfully created EDGE."
    elif decision == "DISTINCT":
        return "Evaluated as DISTINCT. No edge created."
    else:
        return f"Unknown decision: {decision}"

class EvaluateCrossDomainResult(BaseModel):
    is_related: bool = Field(description="True if they share underlying principles or are analogous.")
    justification: str = Field(description="Explanation of the cross-domain analogy.")

def handle_evaluate_cross_domain_tool(state: dict, params: dict) -> str:
    from grips.models import ConceptNode, KnowledgeEdge
    
    source_id = params.get("source_id")
    target_id = params.get("target_id")
    is_related = params.get("is_related")
    justification = params.get("justification", "")
    
    if not source_id or not target_id or is_related is None:
        return "Error - Missing required parameters."
        
    if is_related:
        try:
            source = ConceptNode.objects.get(id=source_id)
            target = ConceptNode.objects.get(id=target_id)
            
            KnowledgeEdge.objects.get_or_create(
                source=source, target=target, relationship_type='RELATED_TO',
                defaults={"justification": f"[Cross-Domain Link] {justification}"}
            )
            return "Successfully created Cross-Domain EDGE."
        except ConceptNode.DoesNotExist:
            return "Error - Source or Target node not found."
            
    return "Evaluated as not related. No edge created."

# ACTION_REGISTRY has been deprecated and replaced by ToolDefinition.
OUTPUT_TYPES = {"ResearchEvaluation": ResearchEvaluation,
                "DifficultPromptEvaluation": DifficultPromptEvaluation,
                "StrategicPlan": StrategicPlan,
                "ExecutionPlan": ExecutionPlan,
                "ResultCritique": ResultCritique,
                "PromptVariant": PromptVariant,
                "TaskQueue": TaskQueue,
                "EdgeLintResult": EdgeLintResult,
                "NodeLintResult": NodeLintResult,
                "ConceptExtraction": ConceptExtraction,
                "EvaluateConceptNeighborsResult": EvaluateConceptNeighborsResult,
                "EvaluateCrossDomainResult": EvaluateCrossDomainResult}

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
