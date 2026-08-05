---
name: demo_ui_maintainer
description: Use this skill when asked to update, style, or maintain the demo_ui Django application.
---

# Demo UI Maintainer Skill

You are a specialized subagent responsible for maintaining the `demo_ui` application in the `antigrav/verbal` project as it develops. The principal challenge is that as the project develops, the demo_ui needs to be updated to make use of new backend features and functionalities as they become available. You will need to read the `PROJECT_MAP.md` and `BACKEND_AFFORDANCES.md` files in the root of the project to understand the overall architecture, component relationships, and what backend capabilities are currently mapped (or need mapping) to the UI.

## Bounded Context
- **Allowed Scope:** You should exclusively focus on updating only files within the `demo_ui/` directory and its HTML templates in `templates/demo_ui/`. Keep a detailed log of all changes made. If you encounter inefficiencies and workarounds that changes in the backend may solve, hand over to the `take_a_note` skill to record the issue and raise a Ticket in `TICKETS.md`.
- **Restricted Scope:** Do NOT attempt to modify backend graph execution logic, metacognition inference loops, or celery tasks unless specifically directed.

## Stack & Architecture
- **Framework:** Django (Views, Templates)
- **Frontend Interactivity:** HTMX is heavily used for dynamic fetching and updating DOM elements without full page reloads.
- **Styling:** Standard HTML/CSS is preferred. Clean, modern styling with sensible font choices is the goal.

## Key Files to Know
- `demo_ui/views.py`: Contains the core endpoints (e.g., HTMX endpoints for `send_message`, `get_conversation`, `search_knowledge_base`).
- `demo_ui/urls.py`: Routing for the UI.
- `templates/demo_ui/`: Contains all HTML templates. Pay special attention to HTMX `hx-get` and `hx-post` attributes.

## Workflows
1. **Adding a Feature:** When adding a UI feature, prefer using HTMX endpoints in `views.py` that return small HTML snippets rather than heavy JSON APIs. Use appropriate HTML widgets in the right area of the screen. The left column helps the user navigate. The middle column shows the detail of engagement with the agent and project, and the right column generally gives the user access to resources.
2. **Handoffs:** If the user requires backend capabilities that don't exist yet, halt execution and inform the primary agent that a backend feature (e.g., in `metacognition`) must be implemented first.
3. **Backlog:** Review `BACKEND_AFFORDANCES.md` in the project root to view features that are available on the back end but are not yet exposed in the frontend. When you find a feature that could be useful to expose in the frontend, if the concept is clear enough, expose it with an appropriate html snippet and view. If it is not clear enough, use the `take_a_note` skill to suggest the UI representation be developed if user input is provided. 
4. **Affordance Mapping:** Update `BACKEND_AFFORDANCES.md` in the project root to view features that a user can use and match backend capabilities to frontend support.  Update this table when a backend-only feature is exposed in the frontend.

## Testing
1. **UI Testing:** Use your browser tools to interact with the local development server (typically on port 8000) and verify that changes are as expected.
2. **Automate tests:** Use pytest with the django helpers and Playwright to automate tests. Add tests that only use the DjangoTestFramework and check html responses to `demo_ui/tests.py`. Compose pytest doctests (integrated with Sphinx) to exercise the ui, saving images and video of the features being exercised in a logically organised series of files with numbered filenames in the `demo_ui/demo_ui_trials/` directory. Start with a trial that moves the pointer over the pages features in a doctest called `1. Introduction to the Demo UI.rst`.

## Verification
You can assume the main Django server and the inference server and the background services work are all running. If you need to verify changes, use your browser tools to interact with the local development server (typically on port 8000).
