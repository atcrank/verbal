# Note

## Timestamp
2026-08-17 11:10:00

## User says
Workshop sessions should be organised as part of a workshop; each Workshop should be part of a project; each Project and Workshop should have a many-to-many set of associated Django Groups who will see and work on it, and be invisible to people outside those groups.
All work was recorded in subclasses of a BaseModel that overloaded methods like `get_queryset` so that the queryset would always by default check the user's groups and their relationship.
Access by users should be supported in several modes per workshop session:
- access restricted to certain users and input is user-tracked and labeled in the UI
- access restricted to certain users but input is anonymised in the UI
- access restricted to certain users but input is anonymised in the UI and the database
- access is not restricted and user ids are optional
Whiteboard material should balance flexibility and structure for storage, querying, reuse, and export (pastable tables, paragraphs, marked-up map images, diagrams for emailing/report-writing, multi-author documents for vision statements).

## Current context
Transitioning from Level 1 foundational stability fixes to Level 2 tasks (1 - SSE streaming, 2 - Multi-user Conversation & Workspace sharing, 3 - Whiteboard idea clustering and factor discovery endpoints). Sketching the overarching domain model for assisted experiment whiteboarding and work organisation.

## What needs to be done
Document the extended architectural vision for "Work Organisation" (Demo UI No. 2):
1. **Deferred Drawing & Vector Map Rendering**: Canvas drawing objects, connector graphs, marked-up map images, and vector stroke serialization.
2. **Deferred Multi-Author Document Editor**: Rich multi-author drafting space (mission statements, vision drafts, collaborative prose).
3. **Deferred Group Admin Management UI**: Fine-grained administrative controls for group creation, membership invitations, and project ownership transfers.
4. **Integration Affordance**: Level 2 implementation will build the core data models (`Project`, `Workshop`, `WorkshopSession`, `WhiteboardCard`, `WhiteboardCluster`, `ConversationMember`), group filtering queryset mixins, the 4 access/anonymity modes, SSE synchronization, and LLM clustering/factor extraction endpoints so these deferred features can plug in cleanly.

## Tags
demo_ui, whiteboard, work_organisation, permissions, sse, feature, architecture, keep_in_mind

## Status
pending
