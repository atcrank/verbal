# WS17: Layered Tool Governance & Lockdown Architecture

## 1. Executive Summary

This specification establishes a defense-in-depth governance architecture for tool execution in Verbal/Reason. It addresses three primary organizational security concerns:
1. **Model-written code execution**: Mitigating escape risks from home-grown sandboxes.
2. **Model network access**: Preventing SSRF, unapproved external API communication, and data exfiltration.
3. **Privilege escalation & self-modification**: Preventing models from mutating permissions, modifying tool definitions, or manipulating blueprint graphs.

The architecture enforces a **Zero-Trust Model Boundary**: prompts are strictly operational context, while all security controls are enforced programmatically across three hierarchical tiers:
- **Tier 1 (Environment Ceiling)**: Immutable `.env` / `settings.py` clamps that define absolute system boundaries.
- **Tier 2 (Database RBAC & Tool Classification)**: Fine-grained categorization on [ToolDefinition](file:///home/crank/coding/antigrav/verbal/metacognition/models.py#L276) managed exclusively through authenticated Django Admin controls, combined with strict allowlists on generic write tools.
- **Tier 3 (User / Session Clearance)**: Role-based clearance (`STANDARD`, `TRUSTED`, `ADMIN`) that resolves the active tool catalog per user session, bounded by Tier 1.

The system provides complete observability via **Lockdown Badges in UI and HTTP headers** and **Visual Blueprint Compatibility Filtering**.

---

## 2. The 3-Tier Layered Architecture

```mermaid
graph TD
    subgraph Tier 1: Host Environment Ceiling [Tier 1: Host Environment Ceiling (settings.py / .env)]
        E[VERBAL_LOCKDOWN_LEVEL: AIR_GAPPED | RESTRICTED | CONTROLLED | PERMISSIVE]
        C1[ALLOW_MODEL_CODE_EXECUTION: False]
        C2[ALLOW_TOOL_NETWORK_ACCESS: False]
        C3[ALLOW_AGENT_SELF_MODIFICATION: False]
    end

    subgraph Tier 2: Database Registry & RBAC [Tier 2: Database Registry & RBAC (Django Admin Only)]
        T[ToolDefinition Metadata]
        TC[Capability: DOMAIN_READ | DOMAIN_WRITE | CODE_EXECUTION | NETWORK_CALL | META_GOVERNANCE]
        TR[Required Clearance: STANDARD | TRUSTED | ADMIN]
        W[write_django_model Strict Allowlist: grips.*, work_organisation.*, notes.*]
    end

    subgraph Tier 3: Session & User Clearance [Tier 3: Session & User Clearance]
        U[User Group / Clearance: Standard | Trusted Analyst | Admin]
        RES[Effective Tool Resolution = Step Tools ∩ Tier 1 Ceiling ∩ User Clearance]
    end

    subgraph Enforcement & Runtime [Compiler & Execution Gateway]
        COMP[compiler.py: Filter Tool Schemas Injected into Prompt/LLM]
        EXEC[tool_executor.py: Non-Bypassable Hard Gate Validation]
        PR[Prompt Grounding: Informational Governance Header]
    end

    E --> RES
    C1 --> RES
    C2 --> RES
    C3 --> RES
    T --> RES
    TC --> RES
    TR --> RES
    U --> RES
    W --> EXEC
    RES --> COMP
    RES --> EXEC
    E --> PR
```

### 2.1 Tier 1: The Environment Ceiling (The Iron Dome)

The top-level configuration resides in `verbal_config/settings.py` and is configured via environment variables. It defines the absolute ceiling of capabilities across the deployment.

#### Lockdown Modes

| Mode | Level | Model Code Execution | Meta-Tools (Self-Mod) | Permitted Capabilities & Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **`AIR_GAPPED`**<br>*(or `TEXT_ONLY` / `LOCKED`)* | 3 | **BLOCKED** | **BLOCKED** | **Deterministic inspection & domain state mutation only**: Safe queries (`READ_ONLY`), deterministic domain writes (`STATE_MUTATION`), graph traversal, RAG lookup. Zero model-written code evaluation. *(Note: Platform microservice traffic such as web ↔ inference or web ↔ grobid is platform plumbing and is never blocked).* |
| **`RESTRICTED`** | 2 | **BLOCKED** | **BLOCKED** | Standard operational mode: read-only queries, domain writes, and internal microservice integrations (e.g. Grobid, local vector embeddings). Code execution and self-modification blocked. |
| **`CONTROLLED`** | 1 | Permitted for `TRUSTED` users | **BLOCKED** | Allows sandboxed Python script execution for verified analysts. Agent self-modification remains blocked. |
| **`DEVELOPMENT`** | 0 | Permitted | **PERMITTED** | **Active Research Mode**: Unlocks all capabilities, including `create_tool`, `manage_dynamic_tools`, and blueprint modification for research into autonomous self-modifying agents. |

#### Settings Implementation (`verbal_config/settings.py`)
```python
# Lockdown Levels: 'AIR_GAPPED', 'RESTRICTED', 'CONTROLLED', 'DEVELOPMENT'
VERBAL_LOCKDOWN_LEVEL = os.getenv('VERBAL_LOCKDOWN_LEVEL', 'AIR_GAPPED').upper()

# Granular override clamps (default to the lockdown level preset)
ALLOW_MODEL_CODE_EXECUTION = os.getenv('ALLOW_MODEL_CODE_EXECUTION', 'False').lower() == 'true'
ALLOW_AGENT_SELF_MODIFICATION = (VERBAL_LOCKDOWN_LEVEL == 'DEVELOPMENT')
```

> [!NOTE]
> **Network Clarification**: In Verbal, the LLM has **no external web-access tools**. Internal microservice communication (e.g. Grobid PDF extraction, Postgres queries, local LLM inference) is handled by the application runtime, not by model-facing tools. Network concerns from the organization are therefore fully addressed: the model cannot make arbitrary outbound HTTP calls.

---

### 2.2 Tier 2: Database Registry & Control Plane Separation

To prevent privilege escalation while preserving research flexibility in development, the system cleanly categorizes every tool by its functional capability.

#### 1. Concrete Tool Capability Categories
Instead of vague labels, tools are classified into 4 distinct categories based on what they actually do:

1. **`READ_ONLY` (Knowledge & Database Inspection)**:
   - Pure read operations that inspect existing state without side-effects.
   - Examples: querying the database ([read_django_models](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L657)), fetching RAG context ([document_reader](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L275), [search_rag_chunks](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L1066)), exploring the knowledge graph ([get_grips_metrics](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L1000), [search_grips_nodes](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L1082)), and reading logs ([fetch_log_details](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L954)).
   - **Risk**: None. Safe in all environments.

2. **`STATE_MUTATION` (Deterministic State & Domain Writes)**:
   - Operations that write or mutate application data using strictly parameterized Django ORM logic (no code execution).
   - Examples: updating working memory ([update_conversation_state](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L805)), creating/updating allowed domain objects via [write_django_model](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L694) (e.g. ConceptNodes, WhiteboardCards, Notes), recording telemetry ([record_signal](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L1120)), or completing tasks ([TASK_COMPLETE](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L597)).
   - **Risk**: Low. Safe when bounded by model allowlists.

3. **`CODE_EXECUTION` (Model-Written Script Execution)**:
   - Tools that take raw code strings generated by an LLM and execute them in a runtime ([django_shell_script](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L410), [python_sandbox](file:///home/crank/coding/antigrav/verbal/metacognition/actions.py#L745)).
   - **Risk**: High (sandbox escape, resource exhaustion). Gated strictly by Tier 1 and restricted to `TRUSTED` users in `CONTROLLED` mode.

4. **`META_GOVERNANCE` (Self-Modification & Dynamic Tools)**:
   - Tools that modify the agent's own execution graph, create new tools on disk, or promote artifacts ([create_tool](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L21), [manage_dynamic_tools](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L746), [promote_artifact](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L241), [deprecate_tool](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L230), [create_blueprint](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L61)).
   - **Preserved for Development Research**: These tools remain active and available in `DEVELOPMENT` mode to support ongoing research into autonomous self-modifying agents and dynamic tool synthesis. In production/air-gapped deployments, they are strictly blocked by Tier 1 (`ALLOW_AGENT_SELF_MODIFICATION = False`).

---

### 2.3 Inventory of Existing Verbal Tools

| Tool Name | Capability Category | Default Clearance | Operational Description |
| :--- | :--- | :--- | :--- |
| `read_django_models` | `READ_ONLY` | `STANDARD` | Inspects database records safely via read-only queries. |
| `discover_django_models` | `READ_ONLY` | `STANDARD` | Returns field schemas and model relationships. |
| `document_reader` | `READ_ONLY` | `STANDARD` | Reads and navigates documents in the RAG repository. |
| `search_rag_chunks` | `READ_ONLY` | `STANDARD` | Performs vector and keyword retrieval over indexed documents. |
| `search_grips_nodes` | `READ_ONLY` | `STANDARD` | Semantic and keyword search across GRIPS knowledge graph. |
| `get_grips_metrics` | `READ_ONLY` | `STANDARD` | Reads aggregate metrics on concept nodes and failure flags. |
| `get_empty_grips_stubs`| `READ_ONLY` | `STANDARD` | Identifies concept nodes that require enrichment. |
| `search_past_conversations` | `READ_ONLY` | `STANDARD` | Queries historical conversation logs for RAG context. |
| `fetch_log_details` | `READ_ONLY` | `STANDARD` | Deep reads of specific prompt and response logs. |
| `get_conversation_metrics` | `READ_ONLY` | `STANDARD` | Summarizes reasoning step pass/fail statistics. |
| `get_benchmark_stats` | `READ_ONLY` | `STANDARD` | Reads benchmark summary statistics. |
| `read_benchmark_topic` | `READ_ONLY` | `STANDARD` | Reads detailed benchmark logs for specific investigations. |
| `review_benchmark_results`| `READ_ONLY` | `STANDARD` | Fetches and aggregates benchmark outcomes. |
| `inspect_nightmanager_performance` | `READ_ONLY` | `STANDARD` | Reads health metrics and telemetry for background tasks. |
| `list_available_tools` | `READ_ONLY` | `STANDARD` | Informational listing of active tools for reasoning steps. |
| `list_blueprints` | `READ_ONLY` | `STANDARD` | Informational summary of blueprints and step topology. |
| `update_conversation_state` | `STATE_MUTATION` | `STANDARD` | Updates the active `Conversation.state_tree` working memory. |
| `record_signal` | `STATE_MUTATION` | `STANDARD` | Writes structured signals/telemetry to database. |
| `TASK_COMPLETE` | `STATE_MUTATION` | `STANDARD` | Signals task completion and updates working prompt. |
| `delegate_task` | `STATE_MUTATION` | `STANDARD` | Enqueues a sub-task for another blueprint to process. |
| `run_sub_blueprint` | `STATE_MUTATION` | `STANDARD` | Runs a sub-blueprint synchronously and returns output. |
| `write_django_model` | `STATE_MUTATION` | `STANDARD` | Parameterized CRUD for allowlisted domain models only. |
| `system_janitor` | `STATE_MUTATION` | `TRUSTED` | Cleans up empty workspace directories on disk. |
| `database_backup` | `STATE_MUTATION` | `ADMIN` | Takes a JSON backup dump of the database. |
| `django_shell_script` | `CODE_EXECUTION` | `TRUSTED` | Executes model-generated Python code in Docker sandbox. |
| `python_sandbox` | `CODE_EXECUTION` | `TRUSTED` | Dispatches script execution to sandbox HTTP daemon. |
| `create_tool` | `META_GOVERNANCE` | `ADMIN` | Creates new `ToolDefinition` (Dev research mode only). |
| `manage_dynamic_tools` | `META_GOVERNANCE` | `ADMIN` | Writes dynamic Python tools to disk (Dev research mode only). |
| `promote_artifact` | `META_GOVERNANCE` | `ADMIN` | Promotes tools or blueprints (Dev research mode only). |
| `deprecate_tool` | `META_GOVERNANCE` | `ADMIN` | Deactivates tools (Dev research mode only). |
| `create_blueprint` | `META_GOVERNANCE` | `ADMIN` | Synthesizes new cognitive blueprints (Dev research mode only). |
| `clone_and_modify_blueprint` | `META_GOVERNANCE` | `ADMIN` | Clones and alters blueprint steps (Dev research mode only). |

---

### 2.4 Hardening `write_django_model`
[write_django_model](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L694) is restricted with an explicit model **allowlist**:
```python
ALLOWED_DOMAIN_MODELS = {
    'grips.conceptnode',
    'grips.knowledgeedge',
    'work_organisation.whiteboardcard',
    'work_organisation.whiteboardcluster',
    'notes.investigationnote',
}
```
Any attempt to target `metacognition.*`, `auth.*`, `admin.*`, `contenttypes.*`, or `verbal_tasks.*` immediately raises a security error and logs an alert.

---

### 2.3 Tier 3: User & Session Clearance

Clearance is evaluated dynamically at session runtime:
1. **User Classification**:
   - `Standard User`: Clearance `STANDARD`.
   - `Trusted Analyst` (member of `Trusted Operators` group or `user.profile.is_trusted_operator = True`): Clearance `TRUSTED`.
   - `Superuser / Staff`: Clearance `ADMIN`.
2. **Effective Tool Resolution**:
   When resolving tools for a [ReasoningStep](file:///home/crank/coding/antigrav/verbal/metacognition/models.py#L137):
   ```python
   def is_tool_permitted(tool: ToolDefinition, user: User) -> bool:
       # 1. Tier 1 Environment Clamps
       if tool.capability_category == 'CODE_EXECUTION' and not settings.ALLOW_MODEL_CODE_EXECUTION:
           return False
       if tool.capability_category == 'NETWORK_CALL' and not settings.ALLOW_TOOL_NETWORK_ACCESS:
           return False
       if tool.capability_category == 'META_GOVERNANCE' and not settings.ALLOW_AGENT_SELF_MODIFICATION:
           return False

       # 2. Tier 3 User Clearance Clamps
       user_clearance = get_user_clearance(user)
       if CLEARANCE_RANKS[tool.required_clearance] > CLEARANCE_RANKS[user_clearance]:
           return False

       return tool.is_active
   ```

---

## 3. Enforcement: Compiler & Execution Gateway

Security is enforced at two non-bypassable architectural choke points:

1. **Schema Injection (Compiler Level)**:
   In [compiler.py](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py#L406):
   - `step.available_tools.all()` is filtered through `is_tool_permitted(tool, user)`.
   - Blocked tools are **never injected** into the LLM's system prompt or native JSON tool calling schema (`tools_for_llm`).
   - The model has no syntactic awareness of prohibited tools.

2. **Execution Gateway (Executor Level)**:
   In [execute_tool](file:///home/crank/coding/antigrav/verbal/metacognition/tool_executor.py#L16):
   - Before dispatching to any callable or API, `execute_tool` performs an invariant validation:
     ```python
     if not is_tool_permitted(tool_def, user):
         raise PermissionDenied(f"Execution of tool '{tool_def.name}' is blocked by active governance policy.")
     ```
   - Even if an adversarial prompt convinces a model to hallucinate or invent a tool call, the gateway rejects it unconditionally.

---

## 4. UI Transparency & Observability

### 4.1 Header Lockdown Badge
A prominent, clean status pill is added to the Demo UI navigation bar and all view templates:
- **`AIR-GAPPED`**: Red status pill (`bg-red-500/10 text-red-400 border-red-500/30`) with a shield icon.
- **`RESTRICTED`**: Amber status pill (`bg-amber-500/10 text-amber-400 border-amber-500/30`).
- **`CONTROLLED`**: Blue status pill (`bg-blue-500/10 text-blue-400 border-blue-500/30`).
- **`PERMISSIVE`**: Emerald status pill (`bg-emerald-500/10 text-emerald-400 border-emerald-500/30`).

Hovering or clicking the pill opens a **Security Governance Dropdown** showing:
- Active Lockdown Mode: `AIR-GAPPED`
- Code Execution: `BLOCKED` (Ceiling)
- Network Access: `BLOCKED` (Ceiling)
- Agent Self-Mod: `DISABLED` (Immutable)
- User Clearance: `TRUSTED` (Effective: `AIR-GAPPED`)

Additionally, all API and SSE responses include the header:
`X-Verbal-Lockdown-Level: AIR_GAPPED`

### 4.2 Blueprint Compatibility Filtering
In the Blueprint selection menu (`demo_ui/views.py`):
1. **Compatible Blueprints**: Render normally with an active status.
2. **Degraded Blueprints**: Blueprints where non-essential steps can skip elevated tools; tagged with a warning icon `⚠️ Degraded (Python sandbox bypassed)`.
3. **Incompatible / Locked Blueprints**: Blueprints that strictly depend on blocked capabilities (e.g. an autonomous code synthesis blueprint); rendered as dimmed, unselectable, with a lock badge and explanatory tooltip:
   *"Locked: Requires Code Execution clearance which is blocked by system policy (AIR-GAPPED)."*

---

## 5. Model Context Grounding (Zero-Trust Context Injection)

While prompt rules cannot enforce security boundaries, providing clear operational grounding prevents the model from attempting blocked operations, hallucinating permissions, or entering unhelpful failure loops.

The compiler prepends a standardized, concise governance declaration to the system prompt:
```markdown
[SYSTEM GOVERNANCE DIRECTIVE]
Environment Policy: AIR-GAPPED (Strict Local Determinism).
Permitted Capabilities: Database queries, RAG retrieval, knowledge graph navigation.
Prohibited Capabilities: Arbitrary code execution (Python sandbox disabled), external network calls.
If a user requests code execution or web requests, inform them politely that the system is operating in an air-gapped security mode and offer to solve the problem using analytical reasoning or available database tools.
```

---

## 6. Implementation Plan & Workstreams

| Phase | Milestone | Scope |
| :--- | :--- | :--- |
| **Phase 1** | **Settings & Core Engine** | - Add `VERBAL_LOCKDOWN_LEVEL`, `ALLOW_MODEL_CODE_EXECUTION`, `ALLOW_TOOL_NETWORK_ACCESS` to `settings.py`.<br>- Implement `metacognition/governance.py` with `is_tool_permitted()` and `ALLOWED_DOMAIN_MODELS`.<br>- Add allowlist enforcement to [write_django_model](file:///home/crank/coding/antigrav/verbal/metacognition/meta_tools.py#L694). |
| **Phase 2** | **Tool Classification & Seed Updates** | - Add `capability_category` and `required_clearance` to [ToolDefinition](file:///home/crank/coding/antigrav/verbal/metacognition/models.py#L276).<br>- Update [seed_tools](file:///home/crank/coding/antigrav/verbal/metacognition/seed.py#L220) with capability tags for all 25+ tools.<br>- Remove dynamic tools / meta-tools (`create_tool`, `manage_dynamic_tools`, etc.) from agent blueprints. |
| **Phase 3** | **Compiler & Gateway Enforcement** | - Update [compiler.py](file:///home/crank/coding/antigrav/verbal/metacognition/compiler.py) to filter available tools and inject governance system prompt header.<br>- Update [tool_executor.py](file:///home/crank/coding/antigrav/verbal/metacognition/tool_executor.py) to abort disallowed executions with `PermissionDenied`. |
| **Phase 4** | **UI Observability & Blueprint Filtering** | - Update `demo_ui` templates and `base.html` to display the header lockdown badge and governance modal.<br>- Add blueprint compatibility resolver in `demo_ui/views.py` to dim/lock incompatible blueprints. |
| **Phase 5** | **Test Suite Verification** | - Unit tests in `metacognition/tests.py` testing each lockdown level.<br>- Tests verifying `write_django_model` blocks attacks on `ToolDefinition`, `User`, etc.<br>- Integration tests verifying blocked tools are never in LLM schemas and fail hard if called. |

---

## 7. Verification Matrix

| Test Case | Expected Behavior |
| :--- | :--- |
| `AIR_GAPPED` mode + Step with `django_shell_script` | Tool is omitted from LLM prompt and schema; direct invocation in executor raises `PermissionDenied`. |
| `AIR_GAPPED` mode + `write_django_model` targeting `ToolDefinition` | Call rejected with error message: `"Model metacognition.tooldefinition is forbidden by governance allowlist."` |
| `AIR_GAPPED` mode + `write_django_model` targeting `ConceptNode` | Successfully creates/updates the concept node. |
| `CONTROLLED` mode + Standard user calling `python_sandbox` | Rejected due to insufficient user clearance (`STANDARD` < `TRUSTED`). |
| `CONTROLLED` mode + Trusted user calling `python_sandbox` | Execution proceeds through sandbox container. |
| UI Header in `AIR_GAPPED` | Displays Red `🛡️ AIR-GAPPED` badge with tooltip detailing blocked code execution & network. |
| Blueprint Catalog in `AIR_GAPPED` | Blueprints requiring code execution are displayed with lock icon and disabled selection. |
