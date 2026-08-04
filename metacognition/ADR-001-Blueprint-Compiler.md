# Architecture Decision Record: CognitiveBlueprint & LangGraph

**Context & Problem:**
The system uses `CognitiveBlueprint` and `ReasoningStep` to define agentic workflows, which are dynamically compiled into LangGraph graphs by `compiler.py`. A key challenge was handling sub-blueprints (Blueprints invoking other Blueprints as steps) while maintaining context efficiency and avoiding parallel execution issues on resource-constrained local models (e.g. 2B/7B open-weight models). 

**Decisions:**
1. **Compilation to LangGraph:** 
   - `compiler.py` dynamically builds a `StateGraph`. Each `ReasoningStep` is a node. 
   - Edges are created dynamically: `next_step` on success, `on_failure_step` on failure (typically defined by evaluating `evaluation_criteria` or catching LLM errors).
   - If a step is a `sub_blueprint`, `compiler.py` constructs a nested graph and attaches it as a node in the parent graph, executing the child Blueprint synchronously.
   
2. **Tool Calling & Fallbacks:**
   - Proxied models (OpenAI, Anthropic) use native tool-calling APIs.
   - Local PyTorch/vLLM/Ollama models fall back to XML-injected prompt instructions (handled automatically during compilation) because local models often lack native tool-calling chat templates. The compiler parses `<tool_calls>` XML blocks from the output.
   - We removed system roles for models that don't support them (e.g., Gemma 2) by sanitizing messages and folding system prompts into user messages before applying the chat template.

3. **Sequential over Parallel:**
   - Due to limited local inference resources, we strictly prefer sequential graph execution over parallel execution.
   - "Parallel" logic is achieved sequentially by pointing `on_failure_step` or multiple sequential steps to process items iteratively.
   
4. **Context Window Management (The Clean Slate Principle):**
   - When a `sub_blueprint` executes, it receives a truncated context.
   - To prevent context overflow in long-running jobs (e.g., NightManager), Blueprints are designed to start with a "clean slate", passing only a condensed summary of the previous Blueprint's conclusions into the state.

**Guiding Principles for Blueprint Designers:**
- **Keep ReasoningSteps Small:** Local models (like Gemma 2B) struggle with massive multi-step reasoning. Break logic into granular `ReasoningStep`s.
- **Strict Self-Evaluation:** Use `evaluation_criteria` with explicit Pydantic schemas. The compiler will validate the LLM's output against the schema and retry on failure.
- **Sequential Chains:** Do not design DAGs with parallel fan-out. Use linear flows with conditional branching on failure.
- **Tool Coercion for Text Nodes:** If `available_tools` are assigned to a `ReasoningStep`, the system may coerce the LLM into strict JSON/tool-calling output mode. For nodes intended for free-form analysis or contemplation, explicitly leave `available_tools` empty.
