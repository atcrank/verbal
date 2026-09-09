# Note: Demo UI Evolution & Experience Overhaul (Codename "Reason")

- **Date**: 2026-09-01
- **Status**: completed
- **Workstream Spec**: [ws12_demo_ui_evolution.md](../ws12_demo_ui_evolution.md)

## Context & Motivation
Preparation for live system demonstrations requires the `demo_ui` application to be visually striking, highly responsive, and functionally rich under the local codename **"Reason"**.

Key pain points identified:
1. Palette and styling feel dated; need modern slate & indigo theme with crisp typography and responsive cards.
2. Ingestion status on uploaded documents shows "pending" / "processing" even after indexing completes.
3. Users need one-click conversation branching from any assistant response to explore alternative reasoning paths.
4. Multi-step blueprints need clear visual step presentation in the chat stream.
5. Users need an engaging progress / thinking indicator during synchronous response generation.
6. Dropped context (documents, concepts, chunks, previous chats) needs transparent token estimation, full payload preview modal, and easy removal.
7. A universal "REASON" header/footer banner with real-time SSE broadcast capability is needed across demo pages and admin views for live group instructions and exercise redirects.
