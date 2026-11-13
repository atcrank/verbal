# WS12: Demo UI Evolution & Experience Overhaul (Codename: "Reason")

## Goal

Transform the `demo_ui` application into a sleek, modern, highly interactive showcase interface under the local codename **"Reason"**.

This workstream improves visual aesthetics, fixes document ingestion tracking, adds DAG conversation branching, provides rich multi-step blueprint and reasoning progress visualization, improves context drag-and-drop mechanics with token estimation, and introduces a universal announcement / SSE broadcast bar across both the demo UI and Django Admin.

---

## Architectural Enhancements & Key Components

| Component / Feature | Current State | Target State |
| :--- | :--- | :--- |
| **Visual Design & Palette** | High-contrast orange/ivory scheme | Modern, polished slate & indigo theme with clean typography, rounded surfaces, glassmorphic accents, and micro-animations. |
| **Document Ingestion Status** | Shows "Processing" indefinitely due to incomplete status heuristics | Accurate multi-state status badges (`Indexed`, `Queued`, `Ingesting`, `Failed`) tied to `TaskRecord` & `RAGChunk` tables with 1-click retry. |
| **Conversation Branching** | Linear chat thread without branching affordance in UI | `🌿 Branch from here` button on every assistant bubble to fork the conversation DAG from that specific turn. |
| **Blueprint Progress & Steps** | Raw markdown dump or basic placeholder | Structured multi-step collapsible cards showing reasoning step titles, tool executions, and step-level token/duration telemetry. |
| **Progress Indicator** | Static spinner | Dynamic multi-stage animated thinking indicator (`Retrieving...` ➔ `Reasoning...` ➔ `Generating...`) with shimmering skeleton. |
| **Dropped Context Management** | Minimal chips without token preview or payload visibility | Rich context drawer with type icons (Doc, Concept, Chat, Chunk), live token estimation per item, payload preview modal, and instant deletion. |
| **Universal "Reason" Header & Broadcasts** | No common branding or broadcast banner | Sleek top & bottom header bars on both Demo UI and Django Admin with SSE/polling broadcast receiver for live announcements and redirects. |

---

## Key Files & Scope of Work

| App / Component | Files Affected | Scope & Responsibility |
| :--- | :--- | :--- |
| **Styles & Assets** | `static/demo_ui/css/styles.css`<br>`static/demo_ui/css/broadcast.css` | Define modern CSS variables, typography, chat bubble styling, stepper cards, progress loaders, and broadcast banner styles. |
| **Demo UI Views & Templates** | `demo_ui/views.py`<br>`demo_ui/urls.py`<br>`templates/demo_ui/index.html`<br>`templates/demo_ui/chat_history.html`<br>`templates/demo_ui/chat_message.html`<br>`templates/demo_ui/document_list.html`<br>`templates/demo_ui/context_preview_modal.html` | Implement branching endpoints, document status resolution, context token calculation, multi-step blueprint renderer, and context preview modals. |
| **Broadcast / SSE Engine** | `verbal_config/broadcast_service.py`<br>`verbal_config/views.py`<br>`templates/includes/reason_broadcast_bar.html`<br>`templates/admin/base_site.html` | Implement lightweight SSE broadcast channel (`/api/broadcasts/stream/` / `/api/broadcasts/current/`) and inject the "Reason" banner into Demo UI and Django Admin. |
| **Tests & Verification** | `demo_ui/tests.py`<br>`demo_ui/demo_ui_trials/` | Unit tests for branching, document status, context token counting, and broadcast endpoints. |

---

## Phased Implementation Plan

### Phase 1: Modern Visual Design & Palette Overhaul
1. **Design System & CSS Tokens**:
   - Refactor `static/demo_ui/css/styles.css` with a modern, curated palette:
     - Neutral base: Slate 50/100/200 (`--bg-main: #f8fafc`, `--bg-panel: #ffffff`, `--border: #e2e8f0`).
     - Typography: Slate 900 (`--text-main: #0f172a`), Slate 500 (`--text-muted: #64748b`).
     - Accents: Indigo 600 (`--primary: #4f46e5`, `--primary-hover: #4338ca`), Violet 500 (`--accent: #8b5cf6`), Emerald 600 (`--success: #10b981`).
     - Chat bubbles: Subtle slate/indigo tinted user bubbles (`--chat-user: #e0e7ff`) and elevated clean AI bubbles (`--chat-ai: #ffffff` with 1px border and soft shadow).
   - Add polished responsive scrollbars, smooth hover transitions, and pill badges.

### Phase 2: Document Ingestion Status Accuracy & Action Controls
1. **Accurate Document State Resolver**:
   - Update `demo_ui/views.py` (`list_documents`):
     - Check `RAGChunk.objects.filter(metadata__document_id=doc.id)` or `doc.currently_indexed` for `Indexed` status.
     - Check `TaskRecord.objects.filter(task_path__contains='task_process_documents', status=TaskRecordStatus.RUNNING)` for `Ingesting`.
     - Check `TaskRecordStatus.READY` for `Queued`.
     - Check `TaskRecordStatus.FAILED` for `Failed`.
2. **Interactive Document Cards**:
   - Update `templates/demo_ui/document_list.html` to display exact chunk counts, extraction strategy tags, and an instant "⚡ Ingest Now" trigger button for pending/failed files.

### Phase 3: Conversation Branching from Any Assistant Message
1. **Branching Endpoint**:
   - Implement `demo_ui:branch_conversation` in `demo_ui/views.py` accepting `log_id`.
   - Creates a new `Conversation` instance titled `"Branch of {original_title} (from Turn N)"` and points the root message to `parent_log_id=log_id`.
2. **Chat Bubble Action**:
   - Add `🌿 Branch from here` button to `templates/demo_ui/chat_message.html` and `chat_history.html`.
   - When clicked, triggers HTMX swap to switch the active conversation and update the left sidebar list via OOB swap.

### Phase 4: Dropped Context Drawer, Token Preview & Deletion
1. **Context Management & Token Estimator**:
   - Enhance the drag-and-drop context store in `templates/demo_ui/index.html`:
     - Display rich chips with type-specific icons (`📄 Doc`, `🧠 Concept`, `💬 Chat`, `🧩 Chunk`).
     - Compute estimated token count per item (`~X tokens`) and display a cumulative token badge next to the Send button.
     - Provide an inspect button on each chip opening a lightweight preview popover/modal of the exact text.
     - Provide a delete button (`✖`) to remove items from the prompt before submission.

### Phase 5: Multi-Step Blueprints & Dynamic Thinking Progress Indicator
1. **Multi-Stage Animated Progress Loader**:
   - Implement an engaging multi-stage thinking indicator in the chat container during synchronous generation:
     - Stage 1: `🔍 Retrieving background context & concepts...`
     - Stage 2: `🧠 Analyzing reasoning constraints & blueprints...`
     - Stage 3: `✨ Generating synthesis & structured response...`
   - Incorporate shimmering skeletons for an authentic, premium feel.
2. **Multi-Step Blueprint Response Renderer**:
   - Structure blueprint step logs and tool execution outputs into collapsible accordion cards within `chat_message.html`.

### Phase 6: Universal "REASON" Header/Footer Bar & SSE Broadcast Channel
1. **Broadcast Service & Storage**:
   - Implement a lightweight, database or cache-backed broadcast model / store (`BroadcastMessage`) supporting active announcement text, urgency level (Info, Alert, Redirect), target exercise URL, and expiry.
   - Implement `/api/broadcasts/stream/` (SSE) and `/api/broadcasts/current/` (JSON fallback).
2. **Universal Banner Injection**:
   - Create `templates/includes/reason_broadcast_bar.html`.
   - Inject into `templates/demo_ui/index.html` and `templates/admin/base_site.html` (or admin base).
   - Display the **REASON** codename badge, live broadcast ticker, and handle client-side redirect events smoothly.

---

## Verification & Testing Plan

1. **Automated Tests**:
   - Run `pytest demo_ui/tests.py` testing document status resolution, branching view, context token estimation, and broadcast endpoints.
2. **Interactive UI Verification**:
   - Verify modern styling in browser across all three columns.
   - Drop documents, concepts, and prior conversations; verify token estimates, preview modal, and chip deletion.
   - Click "Branch from here" on an assistant message and verify conversation tree bifurcation.
   - Send a broadcast from admin / API and verify instant banner update in Demo UI.
