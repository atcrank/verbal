# Note: Demo UI Aesthetic & Functional Refinements

- **Date**: 2026-09-02
- **Status**: completed
- **Workstream Spec**: [ws13_demo_ui_refinements.md](../ws13_demo_ui_refinements.md)

## Context & Key Objectives
Refinements based on review of `demo_ui`:
1. Make document ingestion badges compact, crisp, rectangular outlines (`Indexed`, `Ingesting`, `Queued`, `Failed`).
2. Eliminate sparkles and emojis in `grips_hierarchy.html`.
3. Fix Grips "Fill Stub" action (CSRF header, event bubbling, blueprint lookup).
4. Redesign generation progress indicator with an elapsed second timer (`Elapsed: 15s`) and non-terminating active state while waiting for LLM responses.
5. Restrict header `Broadcast`, `Admin`, and `Task Queue` controls to staff/superusers.
6. Replace all purple buttons with unified slate styling (`#334155` / `#1e293b`).
