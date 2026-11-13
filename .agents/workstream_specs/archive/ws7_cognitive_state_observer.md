# Workstream 5: Conversation State Observer

## Overview
As conversations with the AI extend over long periods and complex tasks, traditional flat linear contexts succumb to the "Lost in the Middle" phenomenon and consume excessive resources (VRAM/tokens). Human cognition relies on hierarchical, tree-like working memory where completed details are pruned and future goals are forward-projected. 

This workstream aims to implement a **Conversation State Observer**: a low-latency mechanism that continuously analyzes the conversation to build and maintain a dynamic, nestable tree structure representing the user's intent, tasks, and topics.

## 1. Data Model (`ConversationState`)

We will introduce a structured JSON schema (likely mapped to a Django JSONField on the `Conversation` model, or as an ongoing metadata payload in `PromptResponseLog`).

**Schema Requirements:**
- **Nestable Tree Dictionary:** Topics and tasks can contain child topics.
- **Unique Branch Keys:** Topic strings must be unique (e.g., appending version numbers like `v1`, `v2` if a topic is revisited after a tangent).
- **Path Separation:** The `active_branch` is tracked using a path notation (e.g., `Project Setup > Database Configuration v1 > Postgres Migration`).
- **Leaf Values:** Leaves can contain child task keys or directly reference a `PromptResponseLog` UUID to anchor the summary to a specific point in the conversation.

**Branch Status Modifiers:**
Every node in the tree requires a status flag:
- `active`: The current focus of the conversation.
- `resolved/completed`: The task was finished (detailed history can be pruned, leaving a summary).
- `projected/planned`: Forward-looking goals the user has stated but not started.
- `dormant - [condition]`: Blocked or paused until a condition is met (e.g., waiting for another branch to finish).
- `abandoned - [reason]`: Stopped intentionally (e.g., "was a tangent", "user changed mind").

## 2. Architecture & Execution Flow

### Addressing the "T-1 vs T-0" Problem (When does the Observer run?)
If the Observer only runs asynchronously *after* the heavy model responds, the heavy model won't realize the user just changed the topic in their latest prompt. 

**Proposed Solution (Pre-Processing Router):**
1. User submits a prompt.
2. A fast, low-cost "Observer" pipeline runs *synchronously*. It takes the `State at T-1` and the `New User Prompt`, and outputs `State at T-0`. 
3. The Observer updates the active branch path, flags abandoned/dormant tasks, and creates new projected tasks based *only* on the user's incoming prompt.
4. The heavy AI model (Responder) is then invoked. Its context window receives the pruned, updated `State at T-0` along with the prompt.
5. *(Optional)* After the heavy AI responds, an async cleanup task can summarize the completed turns into the state tree.

### Overlap with `ReasoningStep` (Prescriptive vs Descriptive)
Verbal currently uses `ReasoningStep` and nested `CognitiveBlueprints` to orchestrate multi-step LLM actions. 
- **ReasoningSteps** are *prescriptive* (they dictate how the AI *must* solve a problem).
- **The State Observer** is *descriptive* (it observes how the human and AI *actually* converse).
- **Synergy:** When the AI triggers a nested Blueprint to solve a problem, the Observer simply registers that execution as a new `active` sub-branch. When the Blueprint returns, the Observer marks it `resolved`. They work in tandem.

## 3. Context Pruning Strategy
Injecting the entire tree into the context window of every turn defeats the purpose of saving tokens. We need a pruning algorithm for the system prompt:
- **Root Level:** Show all root topics, but only their titles and status (no summaries).
- **Active Path:** Show the full breadcrumb trail to the active node.
- **Active Node Context:** Show the 1-sentence summaries of the immediate siblings and children of the active node.
- **Pruning:** Any node marked `resolved`, `abandoned`, or deep inside a `dormant` branch is collapsed into a single line (e.g., `[Resolved] Data Import v1 (Log ID: 1234)`). 

## 4. Implementation Steps
1. **Schema Definition:** Define the Pydantic `ConversationStateTree` model in `metacognition/models.py`.
2. **Observer Node:** Create a new `CognitiveBlueprint` or fast LLM callable designed specifically for structured JSON generation. It takes a State Tree and a Prompt, and returns the modified State Tree.
3. **Context Injector:** Modify `Conversation.as_messages()` in `llm_api/models.py` to compile the pruned tree into a `System` message.
4. **Integration:** Update `metacognition/tasks.py` to route the incoming prompt through the Observer before invoking the heavy LangGraph nodes.
