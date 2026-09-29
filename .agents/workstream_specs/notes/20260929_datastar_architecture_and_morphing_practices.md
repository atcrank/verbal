# Datastar Architecture, SSE Protocols & Morphing Best Practices

## Timestamp
2026-09-29 15:35:00 AEST

## User says
"could you please make a brief permanent record of how to use modern datastar? Then please review the other interfaces that use datastar and modernise them as necessary. I'm thinking of the mindmap ui and perhaps the wiki?"

## Current context
After investigating issues where interactive grouping and filter buttons in the Benchmarking Studio became unresponsive after initial SSE swaps, we consulted the official Datastar documentation (https://data-star.dev/guide/backend_requests and https://data-star.dev/guide/the_tao_of_datastar). We diagnosed that custom modifications to the vendor library and overriding the default morph mode (`merge_mode="outer"`) caused DOM destruction and listener detachment in Idiomorph. Restoring clean vendor files, implementing Datastar's idiomatic `.patch_elements` and `.patch_signals` methods, and sticking to default in-place morphing resolved the problem.

## The Tao of Datastar: Core Philosophy

1. **State in the Right Place (Backend as Source of Truth)**
   - Application state belongs on the backend.
   - The backend drives the frontend state by streaming HTML element patches and signal mutations over Server-Sent Events (`text/event-stream`).

2. **Start with the Defaults**
   - Datastar's default configuration options are optimized for standard hypermedia applications. Avoid overriding defaults (like forcing `mode="outer"` or `mode="replace"`) unless there is an explicit requirement to discard an element's existing lifecycle.

3. **In Morph We Trust (Idiomorph)**
   - Morphing ensures that only modified parts of the DOM are updated in place.
   - Top-level elements and interactive children (such as buttons, tabs, inputs) must have unique, deterministic `id` attributes.
   - When elements have matching IDs, Idiomorph treats them as persistent nodes: it updates attributes (like classes, badges, or text) without destroying the DOM node or detaching event listeners.
   - Using `mode="outer"` or `replaceWith()` destroys the existing DOM node, unbinding attached event listeners and corrupting Datastar's element listener registry.

4. **Use Signals Sparingly**
   - Signals are for client-side ephemeral UI reactivity (e.g. toggles, active tabs, menu open/close) and binding form values to requests.
   - Do not attempt to recreate complex state machines in frontend signals; let backend responses dictate view state.

5. **CQRS (Command Query Responsibility Segregation)**
   - Long-lived read streams (`@get('/events/')`) stream continuous backend updates.
   - Short-lived write actions (`@post('/action/')`) trigger mutations.

---

## Python SSE Framing Protocol (`metacognition/datastar.py`)

Datastar uses standard SSE (`text/event-stream`). The methods on `DatastarSSE` are:

### 1. `DatastarSSE.patch_elements(elements, selector=None, mode="morph", ...)`
Patches HTML fragments into the DOM.
- **Default Mode**: `"morph"` (preserves existing elements matching by ID).
- **Targeting**: When top-level HTML elements have IDs (`<div id="hub-history-container">`), Datastar matches them automatically. Explicit `selector` is only needed when targeting by CSS selector or inserting relative to a container (`append`, `prepend`, `before`, `after`).
- **Wire Format (Dual-Emission for Modern & Vendored Compatibility)**:
  ```
  event: datastar-patch-elements
  data: elements <div id="foo">...</div>

  event: datastar-merge-fragments
  data: fragments <div id="foo">...</div>
  ```

### 2. `DatastarSSE.patch_signals(signals, only_if_missing=False)`
Updates reactive client-side signals in the Datastar store.
- **Wire Format**:
  ```
  event: datastar-patch-signals
  data: signals {"activeTab": "history"}

  event: datastar-merge-signals
  data: signals {"activeTab": "history"}
  ```

---

## Critical Rules & Pitfalls to Avoid

1. **Never Hack Vendored Assets (`static/vendor/datastar.js`)**
   - The vendor library must remain clean and identical to upstream releases. Fix protocol or behavioral mismatches on the server or in template structure.
2. **Never Pass `merge_mode="outer"` on Interactive Trees**
   - Passing `outer` forces destructive replacement, severing click listeners. Always rely on `"morph"`.
3. **Never Place Interactive Controls Inside `<summary>` Elements**
   - Nesting buttons or links inside `<summary>` triggers HTML accessibility violations (`Interactive controls must not be nested inside <summary>`) and browser click event swallowing. Use custom card headers with explicit toggle buttons instead.
4. **Always Provide Unique IDs on Interactive Children**
   - Any button, chip, tab, or input inside a morphed fragment must have an explicit `id` (`btn-group-model`, `filter-sg-all`) to guarantee Idiomorph identifies and preserves the node.

## Tags
datastar, hypermedia, sse, idiomorph, morphing, frontend, architecture, enduring

## Status
enduring
