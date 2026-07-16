# WS4: NightManager — From Aspirational to Autonomous

## Goal

Validate the NightManager Blueprint end-to-end, fix failure modes, then enhance it to:
1. Discover and execute pending benchmarks.
2. Score ReasoningStep variants using EWMA from step evaluation results.
3. Propose new variants (with `is_active=False`) for human review.
4. Work toward autonomous operation — deciding what to benchmark, re-digest, or curate in Grips.

## Prerequisites

- **WS1** (Blueprint evolution) must be complete — variant resolution, `resolve_active_steps()`, the `is_pending_review` / `proposed_by` / `proposed_at` fields, and admin actions for activating/retiring variants.
- **WS3** (Benchmarking) should be complete — throughput metrics and `GenerationMetrics` so the NightManager can report meaningful performance data.

## Key Files

| File | Role |
|------|------|
| `metacognition/seed.py` | NightManager Blueprint definition (line ~210). Also seeds all tools and other Blueprints. |
| `metacognition/meta_tools.py` | Tool implementations called by the NightManager (e.g., `review_benchmark_results`, `run_benchmark`, `django_shell_script`) |
| `metacognition/tasks.py` | `run_blueprint()` — the core execution function that compiles and invokes a Blueprint via LangGraph |
| `metacognition/compiler.py` | `compile_graph_from_blueprint()` — now uses `resolve_active_steps()` (from WS1) |
| `metacognition/models.py` | `CognitiveBlueprint`, `ReasoningStep` (with variant fields from WS1), `ToolDefinition` |
| `llm_api/models.py` | `PromptResponseLog` with `reasoning_step` FK and `step_status` (SUCCESS/FAILURE/RETRY) |
| `benchmarking/models.py` | `Experiment`, `BenchmarkRun`, `BenchmarkResult` |
| `background_resources/models.py` | `Document`, `ReadingStrategy` and subclasses |
| `grips/models.py` | `ConceptNode`, `KnowledgeEdge`, `Domain` |

## Current NightManager State

The current NightManager Blueprint in `seed.py` has 4 steps:
1. **Backup & Status** — calls `system_janitor` and `database_backup`
2. **Review Benchmarks** — calls `review_benchmark_results`
3. **Propose Improvements** — has `list_blueprints`, `create_blueprint`, `create_benchmark_scenario`
4. **Execute & Validate** — has `run_benchmark`, `delegate_task`, `django_shell_script`, `TASK_COMPLETE`

**Known issues** (must be discovered and confirmed via manual validation):
- Has never been validated to run end-to-end.
- Step prompts are vague — no concrete decision criteria.
- No mechanism to discover *which* benchmarks haven't been run.
- No mechanism to update `performance_score` / `selection_weight` on variants.
- Failure edges loop back to self on every step, which can cause infinite loops.

## Design Decisions (All Resolved)

1. **Scoring algorithm**: EWMA (Exponentially Weighted Moving Average) from `PromptResponseLog.step_status`, NOT from `relevance_score`. Each ReasoningStep's `performance_score` reflects its own pass/fail evaluation rate, weighted so recent runs matter more.

   ```python
   # EWMA: new_score = alpha * latest_observation + (1 - alpha) * old_score
   # latest_observation = 1.0 for SUCCESS, 0.0 for FAILURE, 0.5 for RETRY
   alpha = 0.3  # Recent observations weighted ~30%
   ```

2. **Variant creation**: NightManager calls `create_variant(is_active=False, is_pending_review=True, proposed_by='system')`. The admin activates/retires via the actions built in WS1.

3. **High autonomy vision**: The NightManager should ultimately decide what to do based on discovered pending work — not just benchmarks, but also:
   - Re-digest documents with new ReadingStrategies
   - Create new Grips ConceptNodes, KnowledgeEdges, or Domains
   - Fill in Grips stubs (ConceptNodes with empty narrative)
   
   This is the practice/exercise engine — the system gets better by running.

## Changes Required

### Phase 1: Manual Validation Sprint

**Do this first, before any code changes.**

1. Start a Django shell:
   ```bash
   ../../py313/bin/python manage.py shell
   ```

2. Run the NightManager Blueprint manually:
   ```python
   from metacognition.tasks import run_blueprint
   from metacognition.models import CognitiveBlueprint
   
   nm = CognitiveBlueprint.objects.get(name="NightManager")
   result = run_blueprint(nm.id, "Perform nightly maintenance.")
   ```

3. Examine `result["internal_monologue"]` — log every step's output.

4. Identify failure modes:
   - Does it get stuck in a self-loop?
   - Does it hallucinate tool names not in its `available_tools`?
   - Does it fail to call TASK_COMPLETE?
   - Does the evaluation criteria pass/fail correctly?

5. Document findings in a file `metacognition/nightmanager_validation_log.md`.

6. Fix the identified issues before proceeding to Phase 2.

### Phase 2: New Meta-Tools

#### Add to `metacognition/meta_tools.py`:

1. **`discover_pending_work(state, params)`**:
   ```python
   def discover_pending_work(state, params):
       """
       Surveys the system for work the NightManager should do.
       Returns a structured summary of:
       - Experiments with no BenchmarkRun
       - ReasoningStep variants with performance_score == 0.0
       - FineTuningDatasets where is_stale == True  
       - Documents with no ReadingStrategies applied
       - ConceptNodes with empty narrative (stubs)
       - Domains with fewer than 3 ConceptNodes
       """
       from benchmarking.models import Experiment, BenchmarkRun
       from metacognition.models import ReasoningStep
       from background_resources.models import Document
       from grips.models import ConceptNode, Domain
       
       pending = {}
       
       # Unexecuted experiments
       executed_exp_ids = BenchmarkRun.objects.values_list('experiment_id', flat=True)
       unexecuted = Experiment.objects.exclude(id__in=executed_exp_ids)
       pending["unexecuted_experiments"] = [
           {"id": e.id, "name": e.name} for e in unexecuted[:10]
       ]
       
       # Unscored variants
       unscored = ReasoningStep.objects.filter(
           is_active=True, performance_score=0.0
       ).exclude(parent_step__isnull=True)
       pending["unscored_variants"] = [
           {"id": s.id, "name": str(s)} for s in unscored[:10]
       ]
       
       # Grips stubs
       stubs = ConceptNode.objects.filter(narrative="")
       pending["grips_stubs"] = [
           {"id": c.id, "title": c.title} for c in stubs[:10]
       ]
       
       # Un-strategied documents (documents with no reading strategies at all)
       from background_resources.models import ReadingStrategy
       docs_with_strategies = ReadingStrategy.objects.values_list('document_id', flat=True).distinct()
       unstrategied = Document.objects.exclude(id__in=docs_with_strategies)
       pending["unstrategied_documents"] = [
           {"id": d.id, "title": d.title} for d in unstrategied[:10]
       ]
       
       import json
       summary = json.dumps(pending, indent=2)
       return {"working_prompt": f"Pending work discovered:\n{summary}", "route_to": "SUCCESS"}
   ```

2. **`update_variant_scores(state, params)`**:
   ```python
   def update_variant_scores(state, params):
       """
       Aggregates PromptResponseLog.step_status per ReasoningStep variant
       and updates performance_score using EWMA.
       """
       from llm_api.models import PromptResponseLog
       from metacognition.models import ReasoningStep
       
       ALPHA = 0.3
       STATUS_VALUES = {"SUCCESS": 1.0, "FAILURE": 0.0, "RETRY": 0.5}
       
       # Find all variants that have been used (have PromptResponseLogs)
       variants_with_logs = ReasoningStep.objects.filter(
           prompt_logs__isnull=False, is_active=True
       ).distinct()
       
       updated = []
       for variant in variants_with_logs:
           logs = variant.prompt_logs.filter(
               step_status__isnull=False
           ).order_by('created_at')
           
           if not logs.exists():
               continue
           
           score = variant.performance_score or 0.5  # Start from neutral
           for log in logs:
               observation = STATUS_VALUES.get(log.step_status, 0.5)
               score = ALPHA * observation + (1 - ALPHA) * score
           
           variant.performance_score = round(score, 4)
           variant.save(force_canonical_update=True)
           updated.append(f"{variant.name}: {variant.performance_score}")
       
       summary = "\n".join(updated) if updated else "No variants with execution history found."
       return {"working_prompt": f"Updated variant scores:\n{summary}", "route_to": "SUCCESS"}
   ```

#### Register tools in `seed.py` TOOL_SCHEMAS:

Add schemas for `discover_pending_work` and `update_variant_scores`:
```python
"discover_pending_work": {
    "type": "object",
    "properties": {},
},
"update_variant_scores": {
    "type": "object", 
    "properties": {},
},
```

Register in the `seed_tools()` meta_tools list.

### Phase 3: Restructured NightManager Blueprint

Update `seed_nightmanager()` in `seed.py` to a 6-step pipeline:

1. **Housekeeping** (keep as-is): `system_janitor` + `database_backup`
2. **Discover Pending Work** (new): `discover_pending_work` tool
3. **Execute Benchmarks**: `run_benchmark` tool — run discovered pending experiments
4. **Score Variants** (new): `update_variant_scores` tool
5. **Propose Improvements**: If underperforming variants found, use `django_shell_script` to call `create_variant()` with modified prompts and `is_active=False, is_pending_review=True, proposed_by='system'`.
6. **Report & Complete**: Generate structured summary, `TASK_COMPLETE`

Key prompt improvements:
- Each step's `system_prompt` must be **concrete**: specify exact tool names to call, exact conditions for success/failure.
- Failure edges should route to the NEXT step (not back to self) to prevent infinite loops. Only the final step should self-loop (with max_retries=3).
- Add `evaluation_criteria` to steps 2-5 so the LLM-as-judge can assess pass/fail.

### Phase 4: Celery Beat Verification

Verify the existing crontab schedule works:
```python
from django_celery_beat.models import PeriodicTask
pt = PeriodicTask.objects.get(name='NightManager Daily Maintenance')
print(pt.crontab, pt.task, pt.kwargs)
```

Ensure the `kwargs` reference the correct `blueprint_id` after the seed update.

## Testing Requirements

All tests use the venv at `../../py313/bin/python`.

### New Tests

1. **`test_discover_pending_work`**: Create fixtures (Experiment with no Run, ConceptNode with empty narrative), verify the tool returns them.

2. **`test_update_variant_scores_ewma`**: Create a ReasoningStep variant with 5 PromptResponseLogs (3 SUCCESS, 1 FAILURE, 1 RETRY), verify the EWMA computation produces the expected score.

3. **`test_nightmanager_end_to_end`**: Run the NightManager Blueprint in a test database with fixture data (at least one pending experiment, one unscored variant). Verify it completes without infinite loops and produces a meaningful `internal_monologue`.

### Run existing tests

```bash
../../py313/bin/python manage.py test metacognition -v2
```

## Verification Checklist

- [ ] Manual validation sprint completed — failure modes documented and fixed
- [ ] `discover_pending_work` tool returns accurate pending work summary
- [ ] `update_variant_scores` computes EWMA correctly from PromptResponseLog data
- [ ] NightManager Blueprint runs end-to-end without infinite loops
- [ ] NightManager calls TASK_COMPLETE and produces a meaningful report
- [ ] Celery Beat schedule correctly targets the updated Blueprint
- [ ] All existing metacognition tests pass
- [ ] All new tests pass
