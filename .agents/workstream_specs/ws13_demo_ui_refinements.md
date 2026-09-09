# Workstream 13: Demo UI Aesthetic & Interactive Refinements ("Reason")

## Objectives & Scope

Refine and harden the `demo_ui` application based on interactive review:
1. **Compact, Clean Status Outlines**:
   - Shrink document status badges (`Indexed`, `Ingesting`, `Queued`, `Failed`) to a compact, crisply bordered rectangular outline badge with subtle tinted backgrounds rather than bulky rounded pills.
2. **Remove All Remaining Emojis in Grips Explorer**:
   - Remove folder/sparkle emojis (`📁`, `✨`) from `grips_hierarchy.html` and other templates.
3. **Fix Grips Explorer "Fill Stub" Action**:
   - Ensure HTMX sends CSRF headers globally via `htmx:configRequest`.
   - Add `event.stopPropagation()` to prevent parent `<details>` toggle.
   - Robustly handle missing or differently named blueprint definitions in `fill_grips_stub` view.
4. **Adaptive, Continuous Thinking & Progress Indicator**:
   - Replace the fast 3-second progress timer with a dynamic, continuous progress ticker with a live elapsed seconds counter (`Elapsed: 12s`) and persistent shimmering activity that stays visibly alive until the server response is received.
5. **Staff-Only Header Controls & Broadcast Reliability**:
   - Gate Admin, Task Queue, and the `Broadcast` transmitter button behind `request.user.is_staff or request.user.is_superuser`.
   - Provide a clear, step-by-step verification methodology and visible countdown banner for exercise redirects (`Redirecting in 3... 2... 1... [Stay Here]`).
6. **Sophisticated Slate Color Palette for All Buttons**:
   - Eliminate all bright/saturated purple button styling in favor of the clean Slate/Steel theme (`#334155` default, `#1e293b` hover, `#0f172a` active).

---

## Tasks & Implementation Breakdown

### Phase 1: CSS & Badges
- Update `.badge-status` in `styles.css`: `font-size: 0.68rem; font-weight: 500; padding: 2px 6px; border-radius: 3px; border: 1px solid;`.
- Update button styles to unified Slate 700 / Slate 800 (`#334155` / `#1e293b`).

### Phase 2: Grips Explorer & Fill Stub Fix
- Clean `templates/demo_ui/grips_hierarchy.html` and `grips_hierarchy_children.html`.
- Add global HTMX CSRF config in `templates/demo_ui/index.html`.
- Harden `fill_grips_stub` in `demo_ui/views.py`.

### Phase 3: Adaptive Thinking Indicator
- Redesign the generation progress indicator in `templates/demo_ui/index.html` with an active elapsed timer and indefinite animated shimmer.

### Phase 4: Header Permissions & Broadcast Redirect UX
- Update `reason_broadcast_bar.html` with staff/superuser conditionals on admin links and broadcast trigger.
- Add countdown ticker with cancel option to the broadcast redirect banner.
