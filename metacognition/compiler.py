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


def resolve_active_steps(blueprint: CognitiveBlueprint) -> tuple[Dict[int, ReasoningStep], Dict[int, int]]:
    """
    Selects the active variant for each step lineage in a blueprint.
    Returns (resolved_steps, root_mapping).
    """
    import random
    lineage_groups = ReasoningStep.objects.active_for_blueprint(blueprint)
    
    # We also need a fast lookup of step.id -> canonical_root.id for the router
    all_steps = list(blueprint.steps.all())
    step_map = {s.id: s for s in all_steps}
    root_mapping = {}
    
    def get_root_id(step):
        curr = step
        seen = set()
        while curr.parent_step_id and curr.parent_step_id not in seen:
            seen.add(curr.id)
            if curr.parent_step_id in step_map:
                curr = step_map[curr.parent_step_id]
            else:
                curr = curr.parent_step
        return curr.id
        
    for step in all_steps:
        root_mapping[step.id] = get_root_id(step)

    resolved = {}
    for root_id, variants in lineage_groups.items():
        if len(variants) == 1 and variants[0].id == root_id and not variants[0].variants.filter(is_active=True).exists():
            resolved[root_id] = variants[0]
        else:
            weights = [max(v.selection_weight, 0.01) for v in variants]
            [selected] = random.choices(variants, weights=weights, k=1)
            resolved[root_id] = selected
            logger.info(f"Variant selection for lineage {root_id}: selected '{selected.name}' "
                        f"(weight={selected.selection_weight}, id={selected.id})")
                        
    return resolved, root_mapping


def _make_action_node(step: ReasoningStep, root_mapping: Dict[int, int]):
    """
    Creates an action node function closure for a given ReasoningStep.
    """
    def action_node(state: AgentState) -> dict:
        from llm_api.ai_service import AIService
        from llm_api.apps import service_registry
        
        # Track step count and retries — mirrors the old while-loop semantics:
        # First visit: initialize retries to max_retries, proceed.
        # Re-entry: decrement retries. If <= 0, abort immediately.
        step_count = state.get("step_count", 0) + 1
        retries_remaining = dict(state.get("retries_remaining", {}))
        step_key = str(step.id)
        
        # --- max_steps Safety Brake ---
        # step_count = total node visits across the entire graph (including loop re-visits).
        # max_steps = hard cap on total node visits to prevent runaway execution.
        max_steps = state.get("max_steps", 50)
        if step_count > max_steps:
            logger.warning(f"max_steps ({max_steps}) exceeded at step_count={step_count}. Halting graph.")
            return {
                "route_to": "END",
                "step_count": step_count,
                "retries_remaining": retries_remaining,
                "internal_monologue": [{
                    "step_name": step.name,
                    "output": f"ABORTED: max_steps limit ({max_steps}) exceeded after {step_count} node visits.",
                    "failed": True,
                    "step_count": step_count
                }],
                "scratch": dict(state.get("scratch", {}))
            }
        
        # --- Token Budget & Summarization ---
        current_budget = state.get("token_budget_remaining")
        if current_budget is None:
            current_budget = 8000
        working_memory = list(state.get("working_memory", []))
        
        summary_remove_msgs = []
        if current_budget < 500: # Threshold to trigger summarizer
            from metacognition.summarizer import summarize_if_needed
            temp_state = dict(state)
            temp_state["token_budget_remaining"] = current_budget
            temp_state["working_memory"] = working_memory
            
            summary_updates = summarize_if_needed(temp_state)
            if "working_memory" in summary_updates:
                summary_remove_msgs = summary_updates["working_memory"]
                remove_ids = {m.id for m in summary_remove_msgs if getattr(m, 'id', None)}
                working_memory = [m for m in working_memory if getattr(m, 'id', None) not in remove_ids]
                
                # Reset budget based on remaining memory
                word_count = sum(len(str(getattr(m, 'content', '')).split()) for m in working_memory)
                current_budget = 8000 - int(word_count * 1.3)

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
        
        # 1. Native Sub-Blueprint Execution Bypass
        if step.sub_blueprint_id:
            from .tasks import run_blueprint
            from llm_api.models import Conversation
            try:
                # Serialize accumulated working_memory into the sub-blueprint prompt
                prior_context = "\n".join(
                    getattr(m, 'content', str(m))
                    for m in state.get('working_memory', [])
                    if hasattr(m, 'content')
                )
                task_prompt = f"Macro Goal: {step.system_prompt}\n\nWorking Context (Prior Blueprint Conclusions):\n{prior_context}"
                logger.info(f"Step '{step.name}' bypassing LLM to execute sub-blueprint: {step.sub_blueprint.name}")
                
                # Use a stable, reusable Conversation for each sub-blueprint.
                # This limits proliferation to exactly 1 Conversation per sub-blueprint,
                # reused across nightly runs.
                sub_conv_title = f"NightManager: {step.sub_blueprint.name}"
                sub_conv, _ = Conversation.objects.get_or_create(
                    title=sub_conv_title,
                    user_id=state.get("user_id"),
                    defaults={}
                )
                
                res = run_blueprint(
                    blueprint_id=step.sub_blueprint_id, 
                    user_prompt=task_prompt, 
                    conversation_id=str(sub_conv.id),
                    user_id=state.get("user_id"),
                    max_steps=150  # Sub-blueprint step budget
                )
                
                final_response = res.get("final_response", "")
                monologue = res.get("internal_monologue", [])
                
                # Evaluate success/failure of the sub-blueprint
                sub_failed = False
                if not monologue:
                    # Sub-blueprint returned no execution trace at all
                    sub_failed = True
                    final_response = f"Sub-Blueprint '{step.sub_blueprint.name}' returned no execution trace."
                elif monologue[-1].get("failed"):
                    sub_failed = True
                    
                working_prompt = f"\n[SYSTEM: Sub-Blueprint '{step.sub_blueprint.name}' Completed.\nSuccess: {not sub_failed}\nOutput:\n{final_response}\n]\n"
                
                monologue_entry = {
                    "step_name": step.name,
                    "output": working_prompt,
                    "failed": sub_failed,
                    "step_count": step_count,
                    "system_prompt": f"Native Sub-Blueprint Executor: {step.sub_blueprint.name}",
                    "user_prompt": [],
                    "sub_monologue": monologue
                }
                
                return {
                    "working_memory": summary_remove_msgs + [SystemMessage(content=working_prompt)],
                    "route_to": "FAILURE" if sub_failed else "SUCCESS",
                    "resume_to": None,
                    "step_count": step_count,
                    "retries_remaining": retries_remaining,
                    "internal_monologue": [monologue_entry],
                    "scratch": dict(state.get("scratch", {})),
                    "token_budget_remaining": current_budget
                }
                
            except Exception as e:
                logger.error(f"Error executing sub-blueprint {step.sub_blueprint_id}: {e}")
                working_prompt = f"\n[SYSTEM: Failed to execute Sub-Blueprint '{step.sub_blueprint.name}': {e}]\n"
                monologue_entry = {
                    "step_name": step.name,
                    "output": working_prompt,
                    "failed": True,
                    "step_count": step_count,
                    "system_prompt": f"Native Sub-Blueprint Executor: {step.sub_blueprint.name}",
                    "user_prompt": []
                }
                return {
                    "working_memory": summary_remove_msgs + [SystemMessage(content=working_prompt)],
                    "route_to": "FAILURE",
                    "resume_to": None,
                    "step_count": step_count,
                    "retries_remaining": retries_remaining,
                    "internal_monologue": [monologue_entry],
                    "scratch": dict(state.get("scratch", {})),
                    "token_budget_remaining": current_budget
                }
        
        system_prompt = step.system_prompt
        if state.get("rag_context"):
            system_prompt += f"\n\nContext provided:\n{state.get('rag_context')}"
        if state.get("scratch"):
            system_prompt += f"\n\nScratchpad Variables:\n{json.dumps(state.get('scratch'), indent=2)}"
        
        # Inject Conversation.state_tree into the prompt so the LLM can see
        # queued tasks, resolved tasks, and accumulated findings.
        if state.get("conversation_id"):
            try:
                from llm_api.models import Conversation
                conv = Conversation.objects.get(id=state["conversation_id"])
                if conv.state_tree:
                    system_prompt += f"\n\nConversation State Tree:\n{json.dumps(conv.state_tree, indent=2)}"
            except Exception:
                pass
            
        sys_msg = SystemMessage(content=system_prompt)
        
        # Ensure we don't duplicate the system prompt if it's already the first message
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
                # Fallback: JSON-Schema Guided Generation via Outlines
                # Build a dynamic JSON Schema for the available tools
                tool_schemas = []
                for t in tools:
                    t_args = json.loads(t.input_schema) if t.input_schema else {"type": "object"}
                    tool_schemas.append({
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "const": t.name},
                            "args": t_args
                        },
                        "required": ["name", "args"],
                        "additionalProperties": False
                    })
                
                tool_calls_schema = {
                    "type": "object",
                    "properties": {
                        "tool_calls": {
                            "type": "array",
                            "items": {
                                "anyOf": tool_schemas
                            }
                        }
                    },
                    "required": ["tool_calls"],
                    "additionalProperties": False
                }
                
                # We still provide tool descriptions in the prompt so the LLM understands semantics
                tool_instruction = "\n\n--- TOOL INSTRUCTIONS ---\n"
                tool_instruction += "You have access to the following tools:\n\n"
                for t in tools:
                    schema = json.loads(t.input_schema) if t.input_schema else {}
                    props = schema.get("properties", {})
                    param_desc = ", ".join(f'"{k}": "{v.get("description", v.get("type", "string"))}"' for k, v in props.items()) if props else ""
                    tool_instruction += f"Tool: {t.name}\n  Description: {t.description}\n"
                    if param_desc:
                        tool_instruction += f"  Parameters: {{{param_desc}}}\n"
                    tool_instruction += "\n"
                    
                tool_instruction += "You must respond with a JSON object containing a 'tool_calls' array. Each item must specify 'name' and 'args'. Do not add any text before or after the JSON."
                
                if native_messages and native_messages[0]["role"] == "system":
                    native_messages[0]["content"] += tool_instruction
                
                raw_response = ai_service.generate_outline(
                    native_messages, 
                    response_schema=tool_calls_schema,
                    max_new_tokens=step.max_new_tokens,
                    log_kwargs=log_kwargs,
                    lora_adapter=step.lora_adapter
                )
                
                if isinstance(raw_response, list):
                    raw_response = raw_response[0]
                    
                # Since we used a raw JSON schema dict, Outlines returns a JSON string
                if isinstance(raw_response, str):
                    try:
                        parsed = json.loads(raw_response)
                        result = parsed.get("tool_calls", [])
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse tool calls json from outlines: {e} - Raw: {raw_response[:500]}")
                        result = raw_response
                elif isinstance(raw_response, dict):
                    if "error" in raw_response:
                        # generate_outline returned an error (e.g. connection refused)
                        # Propagate as an error dict so the step fails properly
                        result = raw_response
                    else:
                        result = raw_response.get("tool_calls", [])
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
            elif isinstance(result, str):
                # The model produced raw text instead of tool calls.
                # This means it "simulated" tool usage in prose rather than actually calling them.
                logger.warning(f"Step '{step.name}' has tools but model returned raw text instead of tool calls. Treating as failure.")
                result = {"error": "ToolCallMissing", "details": f"Model failed to produce tool calls. Raw output: {result[:500]}"}
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
            route_to = "FAILURE" if "ToolCallMissing" in result.get('error', '') else "SELF" 
            # If generation fails parsing, ReAct loops back (SELF).
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
                                    monologue_entry["output"] += f"\\n{msg}"
                            if "current_chunk_index" in hook_result:
                                scratch_updates["current_chunk_index"] = hook_result["current_chunk_index"]
                        else:
                            res_str = str(hook_result)
                            tool_results_str.append(res_str)
                            
                            # Add a loud success signal to help the model understand the tool worked
                            success_prefix = ""
                            if not res_str.lower().startswith("error"):
                                success_prefix = f"✅ THE TOOL '{tool_name}' EXECUTED SUCCESSFULLY.\n"
                                
                            reminder = f"\n\nIMPORTANT: If this result satisfies your goal, you MUST output the TASK_COMPLETE tool now. Do not call {tool_name} again unless you need to execute a DIFFERENT action." if "TASK_COMPLETE" in [t.name for t in tools] else ""
                            additional_messages.append(SystemMessage(content=f"[Tool '{tool_name}' Result]:\n{success_prefix}Output:\n{res_str}{reminder}"))
                    except Exception as e:
                        logger.error(f"Error in tool {tool_name}: {e}")
                        tool_results_str.append(f"Error in tool {tool_name}: {e}")
                        additional_messages.append(SystemMessage(content=f"[Tool '{tool_name}' Error]: {e}"))
                        route_to = "FAILURE"
                
                if any(not msg.content.startswith("[Tool 'TASK_COMPLETE'") for msg in additional_messages) and "TASK_COMPLETE" in [t.name for t in tools]:
                    additional_messages.append(HumanMessage(content="Tool execution completed. Review the results above. If you have achieved your goal, you MUST output the `TASK_COMPLETE` tool now. Do not call the same tools again with the same arguments."))
                
                monologue_entry["tool_result"] = "\n".join(tool_results_str)
                if route_to is None:
                    route_to = "SELF"
            else:
                route_to = "SUCCESS" 
                # Text output means we successfully generated an answer, subject to eval
                
        # Automatically merge any top-level Pydantic schema fields into scratch
        if hasattr(result, "model_dump"):
            try:
                scratch_updates.update(result.model_dump())
            except Exception as e:
                logger.warning(f"Failed to merge Pydantic result into scratch: {e}")
        elif hasattr(result, "dict"):
            try:
                scratch_updates.update(result.dict())
            except Exception as e:
                logger.warning(f"Failed to merge Pydantic v1 result into scratch: {e}")
                
        # Update state
        new_messages = [AIMessage(content=str(result))] + additional_messages
        final_memory = summary_remove_msgs + new_messages
        
        new_words = sum(len(str(getattr(m, 'content', '')).split()) for m in new_messages)
        new_tokens = int(new_words * 1.3)
        final_budget = max(0, current_budget - new_tokens)
        
        # Telemetry: Update the PromptResponseLog with the step's final status
        if log_kwargs.get("log_ids"):
            from llm_api.models import PromptResponseLog
            for log_id in log_kwargs["log_ids"]:
                try:
                    log = PromptResponseLog.objects.get(id=log_id)
                    log.step_status = route_to
                    log.save(update_fields=['step_status'])
                    monologue_entry["model_name"] = log.model_name
                except Exception as e:
                    logger.warning(f"Failed to update step_status telemetry for log {log_id}: {e}")
        
        # Fallback: if no log_ids were populated, query SystemConfiguration directly
        if "model_name" not in monologue_entry:
            try:
                from llm_api.models import SystemConfiguration
                config = SystemConfiguration.get_solo()
                if config:
                    if config.hosting_backend == 'vllm' and config.active_vllm_model:
                        monologue_entry["model_name"] = config.active_vllm_model.name
                    elif config.hosting_backend == 'ollama' and config.active_ollama_model:
                        monologue_entry["model_name"] = config.active_ollama_model.name
                    elif config.hosting_backend == 'pytorch' and config.active_local_model:
                        monologue_entry["model_name"] = config.active_local_model.name
            except Exception:
                pass
        
        return {
            "working_memory": final_memory,  # Reducer will append or overwrite
            "route_to": route_to,
            "resume_to": None,
            "step_count": step_count,
            "retries_remaining": retries_remaining,
            "internal_monologue": [monologue_entry],
            "scratch": scratch_updates,
            "token_budget_remaining": final_budget
        }
        
    return action_node


def _make_eval_node(step: ReasoningStep, root_mapping: Dict[int, int]):
    """
    Creates an evaluation node function closure for a given ReasoningStep.
    """
    def eval_node(state: AgentState) -> dict:
        route_to = state.get("route_to")
        resume_to = state.get("resume_to")
        
        # 1. Do not evaluate if the graph is halting or awaiting input
        if route_to in ("END", "USER_INPUT_REQUIRED"):
            return {}
            
        # 2. Do not evaluate if this was a sub-blueprint (it handles its own success/failure)
        if step.sub_blueprint_id:
            return _intercept_failure(route_to, resume_to, state, step, root_mapping)
            
        # 3. Determine if this is an interactive step (requires TASK_COMPLETE to finish)
        has_task_complete = "TASK_COMPLETE" in [t.name for t in step.available_tools.all()]
        
        # We only evaluate if:
        # A) It's a deterministic step (no TASK_COMPLETE), so we evaluate immediately after the tool run (route_to == SELF or SUCCESS)
        # B) It's an interactive step, and the model explicitly finished (route_to == SUCCESS)
        should_evaluate = step.evaluation_criteria and (
            (not has_task_complete) or 
            (has_task_complete and route_to == "SUCCESS")
        )
        
        if should_evaluate:
            from llm_api.apps import service_registry
            ai_service = service_registry.ai_service
            
            from pydantic import BaseModel, Field
            class EvaluationResult(BaseModel):
                passed: bool = Field(description="True if the evaluation criteria is met, False otherwise.")
                reasoning: str = Field(description="Reasoning for the evaluation result.")
            
            import copy
            messages = []
            for m in state.get("working_memory", []):
                m_copy = copy.copy(m)
                if m_copy.type == "human" and hasattr(m_copy, "content") and "Working Context (Prior Blueprint Conclusions):" in str(m_copy.content):
                    m_copy.content = str(m_copy.content).split("Working Context (Prior Blueprint Conclusions):")[0].strip()
                messages.append(m_copy)
                
            eval_sys = SystemMessage(content=f"Evaluate the conversation state and the latest assistant response (and tool execution result if any) against the following criteria: {step.evaluation_criteria}\nDO NOT copy past evaluation results; you must evaluate the CURRENT assistant response.")
            eval_messages = [eval_sys] + messages
            
            def _map_role(m_type: str) -> str:
                if m_type == "human": return "user"
                if m_type == "ai": return "assistant"
                return m_type
                
            try:
                eval_result = ai_service.generate_outline(
                    messages=[{"role": _map_role(m.type), "content": getattr(m, 'content', str(m))} for m in eval_messages],
                    response_schema=EvaluationResult,
                    log_kwargs={"conversation_id": state.get("conversation_id"), "reasoning_step_id": step.id}
                )
                if isinstance(eval_result, list):
                    eval_result = eval_result[0]
                    
                evaluation_passed = eval_result.passed
                logger.info(f"Evaluation for '{step.name}': passed={evaluation_passed}, reasoning={eval_result.reasoning}")
                
                # Update route based on explicit evaluation
                route_to = "SUCCESS" if evaluation_passed else "FAILURE"
                
                # Append the evaluator's reasoning to the monologue
                if state.get("internal_monologue"):
                    last_entry = dict(state["internal_monologue"][-1])
                    last_entry["evaluator_reasoning"] = eval_result.reasoning
                    return _intercept_failure(route_to, resume_to, state, step, root_mapping, updated_monologue=last_entry)
                    
            except Exception as e:
                logger.error(f"Evaluation failed for '{step.name}': {e}")
                route_to = "FAILURE" # Default to false if evaluation fails
                
        return _intercept_failure(route_to, resume_to, state, step, root_mapping)

    def _intercept_failure(route_to, resume_to, state, step, root_mapping, updated_monologue=None):
        # Intercept FAILURE if it loops back to itself, change to USER_INPUT_REQUIRED unless autonomous
        if route_to == "FAILURE" and step.on_failure_step and root_mapping.get(step.on_failure_step.id) == root_mapping.get(step.id):
            if not step.blueprint.is_autonomous:
                route_to = "USER_INPUT_REQUIRED"
                resume_to = f"step_{root_mapping.get(step.id)}"
        
        updates = {"route_to": route_to, "resume_to": resume_to}
        if updated_monologue:
            eval_entry = {
                "step_name": f"{step.name} (Evaluation)",
                "output": f"Evaluation Result: {route_to}\nReasoning: {updated_monologue.get('evaluator_reasoning', '')}",
                "failed": route_to == "FAILURE",
                "step_count": state.get("step_count", 0)
            }
            updates["internal_monologue"] = [eval_entry]
            
        return updates

    return eval_node


def _make_router(step: ReasoningStep, root_mapping: Dict[int, int]):
    """
    Creates a router function for conditional edges based on state["route_to"].
    """
    def router(state: AgentState) -> List[str] | str:
        route = state.get("route_to")
        
        canonical_self_id = root_mapping[step.id]
        
        if route == "SELF":
            return f"step_{canonical_self_id}_action"
            
        elif route == "USER_INPUT_REQUIRED":
            return "interrupt_node"
            
        elif route == "SUCCESS":
            # Handle Fan-Out
            parallel_targets = step.parallel_steps.all()
            if parallel_targets.exists():
                return [Send(f"step_{root_mapping[p.id]}_action", state) for p in parallel_targets if p.id in root_mapping]
                
            try:
                target_id = step.on_success_step_id
                if target_id and target_id in root_mapping:
                    return f"step_{root_mapping[target_id]}_action"
            except Exception:
                pass
            return END
            
        elif route == "FAILURE":
            try:
                target_id = step.on_failure_step_id
                if target_id and target_id in root_mapping:
                    return f"step_{root_mapping[target_id]}_action"
            except Exception:
                pass
            return END
            
        # Default fallback
        return END
        
    return router


def compile_graph_from_blueprint(blueprint: CognitiveBlueprint) -> CompiledStateGraph:
    """
    Reads Django models and emits a LangGraph StateGraph.
    """
    resolved, root_mapping = resolve_active_steps(blueprint)
    steps = resolved  # {canonical_id: selected_variant}
    if not steps:
        raise ValueError(f"Blueprint {blueprint.name} has no steps.")
        
    builder = StateGraph(AgentState)
    
    # Add nodes
    for canonical_id, step in steps.items():
        action_node_name = f"step_{canonical_id}_action"
        eval_node_name = f"step_{canonical_id}_eval"
        
        builder.add_node(action_node_name, _make_action_node(step, root_mapping))
        builder.add_node(eval_node_name, _make_eval_node(step, root_mapping))
        
        # Unconditional edge from action to eval
        builder.add_edge(action_node_name, eval_node_name)
        
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
        
    start_canonical_id = root_mapping[start_step.id]
    builder.set_entry_point(f"step_{start_canonical_id}_action")
    
    # Add edges
    for canonical_id, step in steps.items():
        eval_node_name = f"step_{canonical_id}_eval"
        
        # Router is attached to the eval node
        builder.add_conditional_edges(
            eval_node_name,
            _make_router(step, root_mapping),
        )
        
    builder.add_conditional_edges(
        "interrupt_node",
        lambda state: state.get("route_to") or END
    )
        
    # Compile with Django Checkpointer
    checkpointer = DjangoCheckpointer()
    graph = builder.compile(checkpointer=checkpointer, interrupt_before=["interrupt_node"])
    
    return graph
