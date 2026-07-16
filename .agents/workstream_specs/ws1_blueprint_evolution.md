# WS1: Blueprint Evolution — Variant-Aware Compilation

## Goal

The metacognition models have copy-on-write evolution infrastructure (parent_step, performance_score, selection_weight, is_active, create_variant()) but the compiler ignores all of it. Fix this so the compiler selects the best active variant at compile time using stochastic A/B sampling, and close the loop so variant lineage is tracked at both the ReasoningStep and Blueprint level.

Also fix the dead `summarizer.py` path in the compiler so long-running Blueprints can manage their token budget.

## Prerequisites

- No other workstreams need to be completed first.
- This workstream is a dependency for WS3 (benchmarking) and WS4 (NightManager).

## Key Files

| File | Role |
|------|------|
| `metacognition/models.py` | ReasoningStep with parent_step, performance_score, selection_weight, is_active, create_variant() |
| `metacognition/compiler.py` | `compile_graph_from_blueprint()` — currently calls `blueprint.steps.all()` with zero variant awareness |
| `metacognition/admin.py` | Blueprint/Step admin with clone_blueprint and evolve_step actions |
| `metacognition/summarizer.py` | Dead path — referenced in compiler but logs "not implemented" and does nothing |
| `metacognition/tests.py` | Existing test suite |
| `llm_api/models.py` | `PromptResponseLog` has FK `reasoning_step` (line ~277) and `step_status` field — already tracks which variant was used |

## Design Decisions (All Resolved)

### 1. Selection Strategy: Stochastic A/B from the start

Use `random.choices(variants, weights=[v.selection_weight for v in variants])` to select among active leaf-node variants within each lineage group. The `PromptResponseLog.reasoning_step` FK already records which specific variant was used per invocation, so A/B analysis is immediately available via:

```python
PromptResponseLog.objects.filter(reasoning_step=variant_step)
```

### 2. Edge Remapping: Canonical edges, compiler resolves

`on_success_step` / `on_failure_step` always reference the canonical/root step in the database. The compiler transparently substitutes the selected variant at compile time. This means:
- Authors always wire edges to the "original" step.
- The compiler maintains a `{canonical_id: selected_variant}` mapping.
- When building LangGraph edges, it looks up the mapping to find the actual node name.

This design enables discovery of good variant combos — if variant combo `1A→2A→3B` performs anomalously well, it surfaces as a correlation in the `PromptResponseLog` telemetry.

### 3. Blueprint-level parent: Traceability only

Add a `parent` FK on `CognitiveBlueprint` for lineage tracing. No automated "best Blueprint" selection. The admin should group Blueprints by family and display a derived `Pr(total_success)`.

### 4. Summarizer: Complete with truncation workaround

The compiler (line ~49 of `_make_step_node`) detects token budget exhaustion and tries to call `summarize_if_needed`, but then logs a warning that `RemoveMessage` isn't implemented. Fix this by truncating `working_memory` directly — keep the system message + the last N messages that fit the budget — instead of requiring LangGraph `RemoveMessage`.

## Changes Required

### A. `metacognition/models.py`

1. **Add `CognitiveBlueprint.parent`**:
   ```python
   parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, 
                               related_name='descendants',
                               help_text="The Blueprint this was cloned from, for lineage tracking.")
   ```

2. **Add computed property `CognitiveBlueprint.family_success_probability`**:
   Derives `Pr(total_success)` from the `performance_score` values of its resolved active steps. If each step has an independent success probability approximated by its `performance_score` (normalized 0–1), then `Pr(total) = product(step.performance_score for step in resolved_steps)`. Return `None` if no steps have been scored yet (all scores == 0.0).

3. **Add a custom manager or queryset method `ReasoningStep.objects.active_for_blueprint(blueprint)`**:
   - Filters to `is_active=True, blueprint=blueprint`.
   - Groups by lineage (steps sharing the same root via `parent_step` chain).
   - Returns the full queryset of active candidates (the caller decides whether to pick deterministically or stochastically).

4. **Add variant review fields to `ReasoningStep`**:
   ```python
   is_pending_review = models.BooleanField(default=False, 
       help_text="Set by NightManager when proposing a new variant for human review.")
   proposed_by = models.CharField(max_length=20, choices=[('system', 'System'), ('user', 'User')], 
       default='user')
   proposed_at = models.DateTimeField(null=True, blank=True)
   ```

### B. `metacognition/compiler.py`

1. **Add `resolve_active_steps(blueprint)` function**:
   ```python
   import random
   
   def resolve_active_steps(blueprint):
       """
       Selects the active variant for each step lineage in a blueprint.
       Returns {canonical_step_id: selected_ReasoningStep}.
       """
       active_steps = blueprint.steps.filter(is_active=True)
       
       # Group by lineage root
       lineage_groups = {}  # root_id -> [variants]
       standalone = {}      # steps with no lineage
       
       for step in active_steps:
           root = _find_lineage_root(step)
           if root.id == step.id and not step.variants.filter(is_active=True).exists():
               # This step IS the root AND has no active variants — it's standalone
               standalone[step.id] = step
           else:
               lineage_groups.setdefault(root.id, []).append(step)
       
       resolved = dict(standalone)
       for root_id, variants in lineage_groups.items():
           weights = [max(v.selection_weight, 0.01) for v in variants]  # floor to avoid zero weights
           [selected] = random.choices(variants, weights=weights, k=1)
           resolved[root_id] = selected
           logger.info(f"Variant selection for lineage {root_id}: selected '{selected.name}' "
                       f"(weight={selected.selection_weight}, id={selected.id})")
       
       return resolved
   
   def _find_lineage_root(step):
       """Walk parent_step chain to find the canonical root."""
       current = step
       seen = set()
       while current.parent_step and current.parent_step.id not in seen:
           seen.add(current.id)
           current = current.parent_step
       return current
   ```

2. **Modify `compile_graph_from_blueprint()`**:
   Replace `steps = {s.id: s for s in blueprint.steps.all()}` with:
   ```python
   resolved = resolve_active_steps(blueprint)
   steps = resolved  # {canonical_id: selected_variant}
   ```
   
   The rest of the function builds nodes from these resolved steps. Edge targets (`on_success_step`, `on_failure_step`) are looked up in `resolved` — if a step references canonical ID X, the compiler maps it to `resolved[X]`.

3. **Fix `_make_router()`**: The router's SUCCESS/FAILURE branches reference `step.on_success_step.id` — these need to go through the resolved mapping too. Pass the `resolved` dict to the router factory so it can remap.

### C. `metacognition/summarizer.py`

Replace the dead path with a working truncation strategy:

```python
def summarize_if_needed(state: dict) -> dict:
    """
    Truncates working_memory to fit within token budget.
    Keeps: system message (first) + last N messages that fit.
    """
    budget = state.get("token_budget_remaining", 8000)
    if budget >= 500:
        return {}  # No action needed
    
    working_memory = list(state.get("working_memory", []))
    if len(working_memory) <= 2:
        return {}  # Nothing to truncate
    
    # Keep system message + at least the last user message
    system_msg = working_memory[0] if working_memory else None
    remaining = working_memory[1:]
    
    # Estimate tokens per message, keep from the end
    kept = []
    token_estimate = 0
    for msg in reversed(remaining):
        msg_tokens = int(len(str(getattr(msg, 'content', '')).split()) * 1.3)
        if token_estimate + msg_tokens > budget * 0.7:  # Leave 30% headroom
            break
        kept.insert(0, msg)
        token_estimate += msg_tokens
    
    new_memory = ([system_msg] if system_msg else []) + kept
    return {"working_memory": new_memory}
```

Update the compiler's `_make_step_node` to actually use the returned `working_memory` instead of just logging a warning.

### D. `metacognition/admin.py`

1. **`clone_blueprint` action**: Set `new_bp.parent = original_bp` (the original, pre-clone Blueprint).

2. **`CognitiveBlueprintAdmin.list_display`**: Add `family_success_probability` and a `blueprint_family` column showing the root ancestor name.

3. **`ReasoningStepAdmin`**:
   - `list_display`: Add `lineage_depth`, `is_pending_review`, `proposed_by`.
   - `list_filter`: Add `is_pending_review`, `proposed_by`.
   - Add admin action **"Activate Variant & Retire Parent"** — sets `is_active=True` on selected variant, `is_active=False` on its `parent_step`.
   - Add admin action **"Reject Variant"** — sets `is_active=False` and `is_pending_review=False`.

4. **Add a read-only "Resolved Steps" section** to `CognitiveBlueprintAdmin` — shows what `resolve_active_steps()` would return for this Blueprint.

### E. Migration

Create a migration for the new fields:
- `CognitiveBlueprint.parent`
- `ReasoningStep.is_pending_review`, `proposed_by`, `proposed_at`

## Testing Requirements

All tests use the venv at `../../py313/bin/python`.

### New Tests (add to `metacognition/tests.py`)

1. **`test_resolve_active_steps_no_variants`**: Blueprint with 3 steps, no variants. Returns all 3 unchanged.

2. **`test_resolve_active_steps_selects_active_leaf`**: Blueprint with step A (canonical) → variant A' (active) → variant A'' (active, higher weight). Over 100 runs, A'' should be selected more frequently than A'.

3. **`test_resolve_active_steps_excludes_inactive`**: Variant with `is_active=False` is never selected.

4. **`test_edge_remapping_through_variants`**: Step B has `on_success_step = canonical_step_A`. After resolution selects variant A', the compiled graph should route B's success to the node for A'.

5. **`test_create_variant_sets_pending_review`**: When `create_variant()` is called with `is_pending_review=True`, the new variant has correct fields.

6. **`test_blueprint_parent_lineage`**: Cloning a Blueprint sets `parent` correctly. `family_success_probability` computes correctly from step scores.

7. **`test_summarizer_truncates_memory`**: Working memory with 20 messages, budget of 500 tokens → memory is truncated to system + last few messages.

### Run existing tests

```bash
../../py313/bin/python manage.py test metacognition -v2
```

Ensure no regressions.

## Verification Checklist

- [ ] `resolve_active_steps()` correctly groups by lineage and selects stochastically
- [ ] Compiler builds valid LangGraph from resolved steps
- [ ] Edge remapping works transparently
- [ ] `PromptResponseLog.reasoning_step` records the actual variant used (already works — verify)
- [ ] Admin shows resolved steps, lineage depth, pending review status
- [ ] Summarizer truncates memory when budget exhausted
- [ ] All existing tests pass
- [ ] All new tests pass
