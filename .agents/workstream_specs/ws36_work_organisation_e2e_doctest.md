# Workstream 36: Work Organisation UI Affordances, Seamless Ideation & Playwright Doctest

**Status**: In Progress  
**Branch**: `feat/ws36-work-organisation-ui-doctest-and-affordances`  
**Context**: Deepening the Work Organisation module (Demo UI No. 2). Providing seamless brainstorming note input without full-page reloads, keyboard shortcuts, single-click cluster dissolution, improved drag-and-drop affordances, and an end-to-end Playwright doctest that produces a structured Sphinx RST report with screenshots showcasing model-driven deduplication and synthesis.

---

## 1. Problem Statement & User Experience Gaps

1. **Disruptive Full-Page Reloads on Card Entry**:
   - `submitNewCard()` in `templates/work_organisation/mindmap.html` invokes `window.location.reload()`.
   - Adding brainstorming ideas one at a time causes jarring page flickers, destroys user pan/zoom positions, and disrupts creative flow.
2. **Missing Keystroke & Rapid Entry Affordances**:
   - Note textarea lacks `Ctrl+Enter` / `Cmd+Enter` shortcuts to submit.
   - Users must repeatedly click the "Save Note" button with the mouse and reopen the modal for each new idea.
3. **No Affordance for Reversing Clustering**:
   - Reversing clustering currently requires manually deleting clusters one by one via small `×` buttons, each requiring browser `confirm()` dialogs and triggering page reloads.
   - Users need a first-class "↩️ Dissolve Clusters" affordance to uncluster all cards in one action and return to an open ideation canvas.
4. **Visibility & Drag Feedback in Canvas**:
   - Drop targets (`.cluster-cards-grid`, `.mindmap-cluster-box`) need clearer visual feedback (`.drag-over`) and empty states to indicate droppability.
5. **Absence of End-to-End Playwright Doctest**:
   - Unlike `demo_ui_trials`, `work_organisation` lacks an interactive Playwright doctest demonstrating:
     - Sequential note entry.
     - Model-driven clustering and conceptual deduplication.
     - Human-in-the-loop card re-organization (dragging/moving).
     - Dissolving clusters to re-organize.
     - Generating an empirical RST report with screenshots for user review.

---

## 2. Implementation Phases

### Phase 1: Backend API Enhancement (`work_organisation/api.py`)
- [ ] Add `POST /api/whiteboard/clusters/dissolve_all/` endpoint:
  - Takes `session_id: int` payload.
  - Verifies user authorization for the session via `WorkshopSession.objects.for_user(request.user)`.
  - Sets `cluster=None` for all cards belonging to the session.
  - Deletes all `WhiteboardCluster` records for the session.
  - Publishes `clusters_dissolved` SSE event.
  - Returns `{"status": "success", "dissolved_count": count}`.
- [ ] Add unit tests in `work_organisation/tests.py` covering:
  - Authorization check for `dissolve_all`.
  - Dissolving clusters unlinks cards and deletes cluster records cleanly.

### Phase 2: Frontend Affordance Hardening (`templates/work_organisation/mindmap.html`)
- [ ] **Seamless Card Creation**:
  - Update `submitNewCard({ keepOpen = false })` to dynamically construct and inject the `.mindmap-card-item` DOM node into `#cluster-cards-${clusterId || 0}` on success.
  - Clear `#cardText`, update cluster count badges, and redraw SVG connectors without a full page reload.
  - Support `keepOpen=true` to let users continue typing consecutive notes.
- [ ] **Keystroke Support**:
  - Add `keydown` handler on `#cardText` for `(e.ctrlKey || e.metaKey) && e.key === 'Enter'` to trigger immediate submission.
  - Add "Save & Add Another" button in `#addCardModal`.
- [ ] **Dissolve Clusters Affordance**:
  - Add `↩️ Dissolve Clusters` button in header actions (`data-action="dissolve-all-clusters"`).
  - Implement `dissolveAllClusters()`: sends request to `/api/whiteboard/clusters/dissolve_all/`, smoothly moves all card DOM elements back to `#cluster-cards-0`, removes cluster rows, and updates counters.
- [ ] **Readability & Visibility Refinements**:
  - Enhance dropzone borders and `.drag-over` states.
  - Ensure cluster summaries clearly highlight synthesized and deduplicated themes.

### Phase 3: Playwright Doctest & Trials Framework (`work_organisation/work_organisation_trials/`)
- [ ] Create `work_organisation/work_organisation_trials/`:
  - `__init__.py`
  - `conftest.py`: injects `live_server`, `live_server_url`, `authenticate_page`, `get_active_model_name`.
  - `helpers.py`: screenshot capture (both to trials dir and docs dir), timing measurement, RST report generation.
  - `trial_whiteboard_clustering.rst`:
    - Scenario: Robotics research team designing search-and-rescue rover perception pipeline.
    - Step 1: Initialize Project, Workshop, and WorkshopSession; launch Playwright, take screenshot of empty canvas.
    - Step 2: Input 4 ideas one at a time using simulated clicks and keystrokes (including overlapping/duplicate ideas for thermal imaging and LiDAR mapping). Take screenshot of unclustered board.
    - Step 3: Trigger `✨ Cluster Ideas`. AI synthesizes ideas into thematic clusters ("Thermal Infrared Vision System" and "3D Metric LiDAR SLAM") with deduplicated summaries. Take screenshot of synthesized clusters.
    - Step 4: Re-organize cards using simulated drag-and-drop / `📁 Move to...` affordance. Take screenshot.
    - Step 5: Reverse clustering by clicking `↩️ Dissolve Clusters` to return to an open canvas. Take screenshot.
    - Step 6: Generate Sphinx RST report with embedded screenshots.

### Phase 4: Execution & Pre-Push Verification
- [ ] Execute doctest with `../../py312/bin/pytest work_organisation/work_organisation_trials/trial_whiteboard_clustering.rst`.
- [ ] Verify generated screenshots and RST report.
- [ ] Run `./scripts/pre_push_check.sh`.
- [ ] Review code for PEP 8 compliance and commit cleanly.

---

## 3. Verification Criteria
- All tests pass in `./scripts/pre_push_check.sh`.
- Playwright doctest passes and outputs valid screenshots and `.rst` report.
- No regression in existing whiteboard endpoints or Datastar SSE streaming.
