# Note: Demo UI Remediation & Real Capability Shakedown

- **Date**: 2026-09-04
- **Status**: completed
- **Workstream Spec**: [ws15_demo_ui_remediation.md](../ws15_demo_ui_remediation.md)

## Context & Objectives

Remediated the Reason Demo UI trial suite (`demo_ui/demo_ui_trials/`) from the previous mocked façade into an authentic end-to-end testing and showcase suite:
1. Established a scoped pytest `live_server` harness for `demo_ui_trials/` with direct session cookie authentication in Playwright.
2. Ingested the full 1.6MB research literature corpus (`firefighting_chunks.json`, 1,381 chunks) in Trial 1, exercising real pgvector RAG retrieval and live LLM citations on GPU port 8001.
3. Deployed `Reasoning with Code Sandbox` in Trial 2 to perform a genuine 6-factor battery vs. payload trade-off calculation executing inside the Docker Python sandbox (`verbal_sandbox` on port 8002).
4. Exercised live conversation branching and Grips knowledge graph ontology expansion in Trial 3, confirming the "Fill Stub" autonomous expansion button.
5. Upgraded Playwright capture and animated GIFs to full-dashboard (1280x800) with 8-second (`8000ms`) viewing duration per semantic milestone frame, completely eliminating progressive screen loading flicker.
6. Enriched the doctest RST narratives to clearly motivate experimentation-design thinking and decision-making.

## Discovered Issues Log & Remediation Notes

1. **Playwright Selectors & Return Values**:
   - In `templates/demo_ui/chat_message.html`, the message bubble is `.chat-message.ai .bubble`, not `.chat-bubble`.
   - Playwright methods `page.select_option(...)` (returns list of selected values) and `page.goto(...)` (returns Response object) output strings in Python. In doctests, these MUST be assigned (`_ = page.select_option(...)`, `_ = page.goto(...)`) to prevent doctest stdout mismatches.
   - Ambiguous CSS selector `.action-btn-sm` targeted hidden buttons in `#rag-tab`. Fixed by using explicit text matching: `page.wait_for_selector('button:has-text("Fill Stub")', timeout=6000)`.

2. **GPU VRAM Contention between Pytest and Inference Server**:
   - Initial run of Trial 1 exhausted CUDA memory because both PyTorch inference server (port 8001) and the pytest live_server web process loaded embedding/tokenizer models onto the GPU.
   - Fixed in `background_resources/rag_service.py` by detecting `os.environ.get("VERBAL_ROLE") == "web"` and forcing `embed_kwargs = {"device": "cpu"}` for the web client process.

3. **Inference Token Budget & Latency**:
   - The default generation budget of 1000 tokens took ~230 seconds on the GTX 1660 Ti.
   - Adjusted `max_new_tokens=400` for interactive responses in `demo_ui/views.py`. This delivered complete, well-structured domain responses in ~35 seconds.

4. **Canonical Blueprint Mutation Protection**:
   - `CognitiveBlueprint.save()` enforces canonical blueprint locking. When seeding blueprints in test fixtures, wrapped with `with bypass_canonical_lock():`.

5. **Top-Level Import Order in `metacognition/tasks.py`**:
   - Placed alias `task_run_blueprint_sync = run_blueprint` immediately after `def run_blueprint(...)` definition to prevent `NameError` during task module import.

6. **Full Suite Execution Metrics**:
   - Command: `../../py313/bin/python -m pytest demo_ui/demo_ui_trials/ -v`
   - Result: 3 passed in 164.44s (2m 44s) against live GPU inference, pgvector, and Docker sandbox.
   - Sphinx HTML documentation built with zero warnings in `demo_ui_trials`.
