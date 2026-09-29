# Gold Standards, Scenario Group Suites & 11/10 Candidate Promotion

## Timestamp
2026-09-29 17:35:00 AEST

## User says
"The section labeled "Inspection & Gold Standards" is something of a stub. I think it should show scenario groups with a short description and allow users to go to a detailed review and edit screen. I think the "gold standard" term relates to letting models get 11/10 for a really good answer, and then having a mechanism for promoting that answer to be the 'gold standard' for an answer to that question. Does that sound right?"

## Current context
We redesigned Column 3 in the Benchmarking Studio (`benchmarking/templates/benchmarking/studio.html`) from a simple static list stub into a full Scenario Group Suite Browser and In-Place Gold Standards Editor.

## Key Concepts & Implementation

1. **The "Gold Standard" & 11/10 Promotion**:
   - `BenchmarkScenario.ideal_answer` serves as the authoritative ground truth for semantic similarity and LLM-as-a-judge faithfulness/relevance rubrics.
   - When an evaluated model completion demonstrates superior reasoning or evidence ("11/10"), researchers can promote it directly into becoming the scenario's official Gold Standard via `promote_to_gold_api` (`scenario.ideal_answer = result.response`).

2. **Scenario Group Suite Browser (`hub_scenario_catalog.html`)**:
   - Displays all `ScenarioGroup` suites as clickable pills with scenario counts.
   - Summarizes the active group: title, description, total test cases, and last update timestamp.
   - Selecting a suite dynamically filters the catalog below via Datastar morphing (`@get('/benchmarking/api/scenario-group/<id>/scenarios/')`).
   - Clicking any scenario card loads its details and ground-truth comparisons into `#inspector-content`.

3. **Suite Review & Gold Standard Editor Workspace (`suite_editor_modal.html`)**:
   - Triggered via the "🔍 Review & Edit Suite" button.
   - Displays every scenario in the suite with editable forms for Question, Expected Grounding Keywords, and Gold Standard Response.
   - Auto-saves changes via Datastar (`@post('/benchmarking/api/scenario/<id>/update/')`).
   - Features the **Top Model Candidates ("Hall of Fame")**: lists the top 3 highest-scoring completions across all runs for each scenario, allowing 1-click promotion to Gold Standard (`@post('/benchmarking/api/promote/<result_id>/')`).
   - Includes an inline drawer to add new test cases directly to the active group.

4. **API Endpoints**:
   - `GET /benchmarking/api/scenario-group/<id>/scenarios/` (`switch_scenario_group_api`)
   - `GET /benchmarking/api/scenario-group/<id>/review/` (`suite_review_modal_api`)
   - `POST /benchmarking/api/scenario/<id>/update/` (`update_scenario_api`)
   - `POST /benchmarking/api/scenario-group/<id>/add-scenario/` (`add_scenario_to_group_api`)
   - `POST /benchmarking/api/promote/<result_id>/` (`promote_to_gold_api`)

5. **Test Coverage**:
   - Added `TestGoldStandardsAndSuiteInspection` in `benchmarking/tests.py` covering suite switching, modal rendering with candidate rankings, inline editing, and candidate promotion. All 40 tests in `benchmarking.tests` pass.

## Status
completed
