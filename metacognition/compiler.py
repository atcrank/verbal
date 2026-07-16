import logging
import json
from typing import Dict, Any, List, Optional
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from langgraph.graph.state import CompiledStateGraph

from .models import CognitiveBlueprint, ReasoningStep, ToolDefinition
from .state import AgentState
from .checkpointer import DjangoCheckpointer
from .tool_executor import execute_tool
from .summarizer import summarize_if_needed

logger = logging.getLogger(__name__)


def _make_step_node(step: ReasoningStep):
    """
    Creates a node function closure for a given ReasoningStep.
    """
    def step_node(state: AgentState) -> dict:
        from llm_api.ai_service import AIService
        from llm_api.apps import service_registry
        
        # Track step count and retries — mirrors the old while-loop semantics:
        # First visit: initialize retries to max_retries, proceed.
        # Re-entry: decrement retries. If <= 0, abort immediately.
        step_count = state.get("step_count", 0) + 1
        retries_remaining = dict(state.get("retries_remaining", {}))
        step_key = str(step.id)
        
        # --- Token Budget & Summarization ---
        # Very rough proxy: 1 word ~ 1.3 tokens
        current_budget = state.get("token_budget_remaining")
        working_memory = list(state.get("working_memory", []))
        
        if current_budget is not None:
            # Estimate word count
            word_count = sum(len(str(m.content).split()) for m in working_memory if hasattr(m, 'content'))
            estimated_tokens = int(word_count * 1.3)
            current_budget = max(0, current_budget - estimated_tokens)
            
            # Update state dict to pass to summarizer if it triggers
            temp_state = dict(state)
            temp_state["token_budget_remaining"] = current_budget
            
            if current_budget < 500: # Threshold to trigger summarizer
                from metacognition.summarizer import summarize_if_needed
                summary_updates = summarize_if_needed(temp_state)
                if "working_memory" in summary_updates:
                    logger.warning("Summarizer triggered, but working_memory rewriting requires LangGraph RemoveMessage (not implemented in V1).")
        else:
            # Initialize if not set
            current_budget = 8000

        if step_key not in retries_remaining:
            # First visit to this step
            retries_remaining[step_key] = step.max_retries
        else:
            # Re-entry (looped back via SELF)
            retries_remaining[step_key] -= 1
            if retries_remaining[step_key] <= 0:
                # Abort: max retries exhausted — mirrors old while-loop `break`
                logger.warning(f"Max retries exhausted for step '{step.name}'. Aborting.")
                return {
                    "route_to": "END",  # Go straight to END, not FAILURE (avoids self-loop)
                    "step_count": step_count,
                    "retries_remaining": retries_remaining,
                    "internal_monologue": [{
                        "step_name": step.name,
                        "output": f"ABORTED: Max retries reached for step '{step.name}'.",
                        "failed": True,
                        "step_count": step_count
                    }],
                    "scratch": dict(state.get("scratch", {}))
                }
        
        # Fetch the system prompt and merge with RAG context
        system_prompt = step.system_prompt
        if state.get("rag_context"):
            system_prompt += f"\n\nContext provided:\n{state.get('rag_context')}"
            
        sys_msg = SystemMessage(content=system_prompt)
        
        # Ensure we don't duplicate the system prompt if it's already the first message
        working_memory = list(state.get("working_memory", []))
        messages = working_memory
        
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [sys_msg] + messages
        else:
            # Replace the first message (the old system prompt) with the new one for this step
            messages = [sys_msg] + messages[1:]
            
        # Call the LLM using the existing service registry
        ai_service = service_registry.ai_service
        
        # Determine schema format
        schema_format = None
        if step.output_schema:
            from .tasks import get_schema_object
            schema_format = get_schema_object(step.output_schema)
            
        log_kwargs = {
            "conversation_id": state.get("conversation_id"),
            "reasoning_step_id": step.id,
            "log_ids": []
        }
            
        def _map_role(m_type: str) -> str:
            if m_type == "human":
                return "user"
            if m_type == "ai":
                return "assistant"
            return m_type
            
        tools = list(step.available_tools.all())
        llm_result_message = None
        
        if tools:
            # 1. Create native dictionary schemas for the LLM
            tools_for_llm = []
            valid_tool_names = set()
            for t in tools:
                valid_tool_names.add(t.name)
                tool_dict = {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": json.loads(t.input_schema) if t.input_schema else {"type": "object", "properties": {}}
                    }
                }
                tools_for_llm.append(tool_dict)
                
            native_messages = [{"role": _map_role(m.type), "content": getattr(m, 'content', str(m))} for m in messages]
            
            # 2. Select strategy based on native tool support
            if ai_service.supports_native_tools(user=state.get("user")):
                # Native Tool Calling (OpenAI/Ollama)
                # Do not inject XML instructions; trust the API to return the tool array
                [raw_response] = ai_service.generate_response2(
                    native_messages, 
                    max_new_tokens=step.max_new_tokens,
                    tools=tools_for_llm,
                    log_kwargs=log_kwargs,
                    lora_adapter=step.lora_adapter
                )
                
                # ai_service.py has been updated to return the list of dicts directly
                if isinstance(raw_response, list):
                    result = raw_response
                elif isinstance(raw_response, dict):
                    result = [raw_response]
                else:
                    result = raw_response
            else:
                # Fallback: XML Injected Tool Calling (Local/Outlines)
                tool_instruction = "\n\nYou have access to the following tools:\n"
                for t in tools:
                    tool_instruction += f"- {t.name}: {t.description}\n"
                    
                tool_instruction += (
                    "\nTo use a tool, you MUST output a JSON array of tool calls "
                    "enclosed exactly in <tool_calls> and </tool_calls> tags. Example:\n"
                    "<tool_calls>\n"
                    '[\n  {"name": "tool_name", "args": {"arg1": "val1"}}\n]\n'
                    "</tool_calls>\n"
                    "If you want to use a tool, output ONLY the tool calls and no other text."
                )
                if native_messages and native_messages[0]["role"] == "system":
                    native_messages[0]["content"] += tool_instruction
                
                [raw_response] = ai_service.generate_response2(
                    native_messages, 
                    max_new_tokens=step.max_new_tokens,
                    tools=None, # DO NOT pass native tools to fallback
                    log_kwargs=log_kwargs,
                    lora_adapter=step.lora_adapter
                )
                
                import re
                tool_calls_match = re.search(r"<tool_calls>(.*?)</tool_calls>", raw_response, re.DOTALL)
                if tool_calls_match:
                    try:
                        result = json.loads(tool_calls_match.group(1))
                    except Exception as e:
                        logger.error(f"Failed to parse tool calls json: {e}")
                        result = raw_response
                else:
                    result = raw_response
                    
            # 3. Validate tool names
            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and "name" in result[0]:
                validated_result = []
                for tc in result:
                    if tc.get("name") in valid_tool_names:
                        validated_result.append(tc)
                    else:
                        logger.warning(f"LLM tried to call unauthorized/invented tool: {tc.get('name')}")
                        validated_result.append({"error": f"Tool '{tc.get('name')}' is not available."})
                result = validated_result
                if len(result) == 1 and "error" in result[0]:
                    result = result[0] # Convert to error dict if only one failed call
        elif schema_format:
            result = ai_service.generate_outline(
                messages=[{"role": _map_role(m.type), "content": m.content} for m in messages],
                response_schema=schema_format,
                log_kwargs=log_kwargs,
                lora_adapter=step.lora_adapter
            )
            if isinstance(result, list):
                result = result[0]
        else:
            [raw_response] = ai_service.generate_response2(
                [{"role": _map_role(m.type), "content": m.content} for m in messages], 
                max_new_tokens=step.max_new_tokens,
                log_kwargs=log_kwargs,
                lora_adapter=step.lora_adapter
            )
            result = ai_service.clean_response(raw_response)
        
        # Evaluate if criteria exists
        evaluation_passed = True
        resume_to = None
        if step.evaluation_criteria and not isinstance(result, dict) and not (isinstance(result, dict) and "error" in result) and not (isinstance(result, list) and len(result)>0 and isinstance(result[0], dict) and "name" in result[0]):
            # We must evaluate the combined context + result
            from pydantic import BaseModel, Field
            class EvaluationResult(BaseModel):
                passed: bool = Field(description="True if the evaluation criteria is met, False otherwise.")
                reasoning: str = Field(description="Reasoning for the evaluation result.")
            
            eval_messages = messages + [{"role": "assistant", "content": str(result)}]
            eval_sys = SystemMessage(content=f"Evaluate the conversation state and the latest assistant response against the following criteria: {step.evaluation_criteria}")
            eval_messages.insert(0, eval_sys)
            
            try:
                eval_result = ai_service.generate_outline(
                    messages=[{"role": _map_role(m.type), "content": getattr(m, 'content', str(m))} for m in eval_messages],
                    response_schema=EvaluationResult,
                    log_kwargs=log_kwargs
                )
                if isinstance(eval_result, list):
                    eval_result = eval_result[0]
                evaluation_passed = eval_result.passed
                logger.info(f"Evaluation for '{step.name}': passed={evaluation_passed}, reasoning={eval_result.reasoning}")
            except Exception as e:
                logger.error(f"Evaluation failed: {e}")
                evaluation_passed = False # Default to false if evaluation fails
        
        # Handle action hooks if defined
        route_to = None
        scratch_updates = dict(state.get("scratch", {}))
        additional_messages = []
        if llm_result_message and hasattr(llm_result_message, "tool_calls") and llm_result_message.tool_calls:
            formatted_output = json.dumps(llm_result_message.tool_calls, indent=4)
        elif hasattr(result, "model_dump_json"):
            formatted_output = result.model_dump_json(indent=4)
        elif hasattr(result, "json"):
            formatted_output = result.json(indent=4)
        else:
            formatted_output = str(result)
            
        monologue_entry = {
            "step_name": step.name,
            "output": formatted_output,
            "step_count": step_count,
            "system_prompt": sys_msg.content,
            "user_prompt": [{"role": _map_role(m.type), "content": m.content} for m in messages if m.type != "system"]
        }
        
        # Parse result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Error in step {step.name}: {result['error']}")
            monologue_entry["failed"] = True
            monologue_entry["output"] = f"Generation error in step '{step.name}': {result.get('error', 'Unknown')}"
            route_to = "SELF"  # Will decrement retries on next entry
        else:
            if getattr(result, "__class__", None).__name__ == "list" and len(result) > 0 and isinstance(result[0], dict) and "name" in result[0]:
                tool_results_str = []
                for tool_call in result:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    try:
                        tool_def = ToolDefinition.objects.get(name=tool_name)
                        hook_result = execute_tool(tool_def, state, params=tool_args)
                            
                        if isinstance(hook_result, dict):
                            if "route_to" in hook_result:
                                route_to = hook_result["route_to"]
                            if "working_prompt" in hook_result:
                                msg = hook_result["working_prompt"]
                                additional_messages.append(SystemMessage(content=msg))
                                tool_results_str.append(msg)
                                if route_to == "SUCCESS":
                                    monologue_entry["output"] += f"\n{msg}"
                            if "current_chunk_index" in hook_result:
                                scratch_updates["current_chunk_index"] = hook_result["current_chunk_index"]
                        else:
                            tool_results_str.append(str(hook_result))
                    except Exception as e:
                        logger.error(f"Error in tool {tool_name}: {e}")
                        tool_results_str.append(f"Error in tool {tool_name}: {e}")
                        route_to = "FAILURE"
                
                monologue_entry["tool_result"] = "\n".join(tool_results_str)
                if route_to is None:
                    route_to = "SELF"
            else:
                route_to = "SUCCESS" if evaluation_passed else "FAILURE"
                
            # Intercept FAILURE if it loops back to itself, change to USER_INPUT_REQUIRED unless autonomous
            if route_to == "FAILURE" and step.on_failure_step and step.on_failure_step.id == step.id:
                if step.blueprint.is_autonomous:
                    route_to = "SELF"
                else:
                    route_to = "USER_INPUT_REQUIRED"
                    resume_to = f"step_{step.id}"
                
        # Update state
        new_messages = [AIMessage(content=str(result))] + additional_messages
        
        # Telemetry: Update the PromptResponseLog with the step's final status
        if log_kwargs.get("log_ids"):
            from llm_api.models import PromptResponseLog
            for log_id in log_kwargs["log_ids"]:
                try:
                    log = PromptResponseLog.objects.get(id=log_id)
                    log.step_status = route_to
                    log.save(update_fields=['step_status'])
                except Exception as e:
                    logger.warning(f"Failed to update step_status telemetry for log {log_id}: {e}")
        
        
        return {
            "working_memory": new_messages,  # Reducer will append this
            "route_to": route_to,
            "resume_to": resume_to,
            "step_count": step_count,
            "retries_remaining": retries_remaining,
            "internal_monologue": [monologue_entry],
            "scratch": scratch_updates,
            "token_budget_remaining": current_budget
        }
        
    return step_node



def _make_router(step: ReasoningStep, all_steps: Dict[int, ReasoningStep]):
    """
    Creates a router function for conditional edges based on state["route_to"].
    """
    def router(state: AgentState) -> List[str] | str:
        route = state.get("route_to")
        
        if route == "SELF":
            return f"step_{step.id}"
            
        elif route == "USER_INPUT_REQUIRED":
            return "interrupt_node"
            
        elif route == "SUCCESS":
            # Handle Fan-Out
            parallel_targets = step.parallel_steps.all()
            if parallel_targets.exists():
                return [Send(f"step_{p.id}", state) for p in parallel_targets]
                
            if step.on_success_step:
                return f"step_{step.on_success_step.id}"
            return END
            
        elif route == "FAILURE":
            if step.on_failure_step:
                return f"step_{step.on_failure_step.id}"
            return END
            
        # Default fallback
        return END
        
    return router


def compile_graph_from_blueprint(blueprint: CognitiveBlueprint) -> CompiledStateGraph:
    """
    Reads Django models and emits a LangGraph StateGraph.
    """
    steps = {s.id: s for s in blueprint.steps.all()}
    if not steps:
        raise ValueError(f"Blueprint {blueprint.name} has no steps.")
        
    builder = StateGraph(AgentState)
    
    # Add nodes
    for step_id, step in steps.items():
        node_name = f"step_{step_id}"
        builder.add_node(node_name, _make_step_node(step))
        
    # Add summarizer as a general pre-processing node
    # Since LangGraph edges go from node to node, we could inject the summarizer
    # before every step. For simplicity, we'll just add it as a node that the router
    # could potentially route through, or we can just call it inside _make_step_node.
    # To keep the graph pure, calling it inside the step_node is actually cleaner for V1.
    
    # Add dummy interrupt node
    def interrupt_node_func(state: AgentState) -> dict:
        # When graph resumes, we route to wherever resume_to pointed.
        return {"route_to": state.get("resume_to")}
        
    builder.add_node("interrupt_node", interrupt_node_func)
    
    # Set entry point
    start_step = blueprint.steps.filter(is_start_node=True).first()
    if not start_step:
        # Fallback to the first created step
        start_step = blueprint.steps.order_by('id').first()
        
    builder.set_entry_point(f"step_{start_step.id}")
    
    # Add edges
    for step_id, step in steps.items():
        node_name = f"step_{step_id}"
        
        builder.add_conditional_edges(
            node_name,
            _make_router(step, steps),
        )
        
    builder.add_conditional_edges(
        "interrupt_node",
        lambda state: state.get("route_to") or END
    )
        
    # Compile with Django Checkpointer
    checkpointer = DjangoCheckpointer()
    graph = builder.compile(checkpointer=checkpointer, interrupt_before=["interrupt_node"])
    
    return graph
