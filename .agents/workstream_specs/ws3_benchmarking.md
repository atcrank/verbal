# WS3: Benchmarking — Throughput Metrics, Domain Benchmarks, Long-Context Evaluation

## Goal

Enhance the benchmarking app with three capabilities:
1. **Throughput metrics** at the `ai_service` level — tokens/sec, time-to-first-token, prompt eval rate.
2. **Domain-relevant benchmark import** — adapter to load evaluation datasets (SciAssess or similar) as ScenarioGroups.
3. **Long-context evaluation** — measure performance degradation by cumulative token count, not turn count.

## Prerequisites

- **WS1** (Blueprint evolution) should be complete — the summarizer fix is needed for long-context Blueprint evaluation, and the variant resolution must be working so benchmarks exercise the right variants.

## Key Files

| File | Role |
|------|------|
| `llm_api/ai_service.py` | `AIService` class — `generate_response2()` (line ~575), `generate_outline()` (line ~657), `_execute_generation_with_retries()` (line ~469), `_log_generation()` (line ~220) |
| `llm_api/models.py` | `PromptResponseLog` (line ~259) — has `input_tokens`, `output_tokens` fields but NO `duration` field |
| `benchmarking/runner.py` | `run_benchmark_suite()` — the main benchmark execution pipeline |
| `benchmarking/models.py` | `BenchmarkResult` (line ~210), `BenchmarkRun` (line ~194), `Experiment`, `Investigation` |
| `benchmarking/generators.py` | Synthetic Q&A scenario generators |
| `benchmarking/tasks.py` | Celery task wrappers for async benchmark execution |
| `benchmarking/tests.py` | Existing benchmark test suite |

## Current State

### What exists
- `BenchmarkResult` records `duration_seconds` per scenario but never computes tok/s.
- `PromptResponseLog` records `input_tokens` and `output_tokens` (counted via tokenizer) but no timing.
- `_execute_generation_with_retries()` is the universal executor — all generations (structured and unstructured, local and proxy) flow through it.
- The benchmark runner calls `ai_service.generate_response2()` and `ai_service.generate_outline()` but receives no timing metadata back.

### What's missing
- No `GenerationMetrics` dataclass — timing is not captured at the ai_service level.
- No tok/s computation anywhere.
- No time-to-first-token measurement.
- No mechanism to load standard evaluation datasets.
- No long-context evaluation mode.

## Design Decisions (All Resolved)

1. **Measurement point**: At the `ai_service` level, inside `_execute_generation_with_retries()`. This captures the true generation time, not the overhead from tool execution, RAG retrieval, etc.

2. **Metrics to capture** (what model APIs typically provide):
   - `tokens_per_second` — output_tokens / generation_duration
   - `time_to_first_token_ms` — for proxy/API backends, parse from streaming response or `usage` metadata; for local, approximate as prefill time
   - `prompt_eval_tokens_per_second` — input_tokens / prefill_duration (local only, measurable by timing the `tokenizer()` + `model.generate()` prefill)
   - `total_duration_ms` — wall clock time for the complete generation call

3. **Long-context = cumulative tokens, not turns**: Store `cumulative_input_tokens` as an `extra_metric` per result. The Investigation DataFrame can then plot `relevance_score ~ cumulative_tokens` to visualise degradation.

4. **Domain benchmarks**: SciAssess or equivalent research-support benchmarks. Format is text:text Q&A, so import means translating to `ScenarioGroup` + `BenchmarkScenario` entries.

## Changes Required

### A. `llm_api/ai_service.py` — GenerationMetrics

1. **Add dataclass at module level**:
   ```python
   from dataclasses import dataclass, field
   import time
   
   @dataclass
   class GenerationMetrics:
       output_tokens: int = 0
       total_duration_ms: float = 0.0
       tokens_per_second: float = 0.0
       time_to_first_token_ms: float | None = None
       prompt_eval_tokens_per_second: float | None = None
   ```

2. **Wrap generation in `_execute_generation_with_retries()`**:
   Before calling `generated_callable()`, record `start = time.perf_counter()`. After the call returns, compute duration. For proxy backends, also parse `usage` from the OpenAI-standard response if available (vLLM and Ollama both return `usage.completion_tokens` and `usage.prompt_tokens`).

3. **Store metrics alongside the log**:
   Extend `_log_generation()` to accept optional `GenerationMetrics` and store timing data. Two options:
   - Add `generation_duration_ms` and `tokens_per_second` fields to `PromptResponseLog` (preferred — queryable).
   - Or store in a JSONField `generation_metrics` on `PromptResponseLog`.

4. **Return metrics to caller**:
   `_execute_generation_with_retries()` should return a tuple `(result, metrics)` or attach metrics to a thread-local / context var so callers can access them. Since this changes the return signature, callers (`generate_response2`, `generate_outline`) need updating.

   **Recommended approach**: Use a thread-local to avoid changing the return type of `generate_response2()` which is called everywhere:
   ```python
   _last_generation_metrics = threading.local()
   
   def get_last_generation_metrics() -> GenerationMetrics | None:
       return getattr(_last_generation_metrics, 'metrics', None)
   ```
   Set `_last_generation_metrics.metrics = metrics` at the end of `_execute_generation_with_retries()`. Callers that care (like the benchmark runner) call `get_last_generation_metrics()` after generation.

### B. `llm_api/models.py` — PromptResponseLog timing fields

Add to `PromptResponseLog`:
```python
generation_duration_ms = models.FloatField(null=True, blank=True, 
    help_text="Total wall-clock duration of the generation call in milliseconds")
tokens_per_second = models.FloatField(null=True, blank=True,
    help_text="Output tokens per second during generation")
```

### C. `benchmarking/models.py` — Result throughput fields

Add to `BenchmarkResult`:
```python
tokens_generated = models.IntegerField(null=True, blank=True, 
    help_text="Number of output tokens generated")
tokens_per_second = models.FloatField(null=True, blank=True,
    help_text="Generation throughput in tokens per second")
```

Add to `BenchmarkRun`:
```python
average_tokens_per_second = models.FloatField(null=True, blank=True,
    help_text="Mean throughput across all results in this run")
```

### D. `benchmarking/runner.py` — Throughput integration

After each generation call in `_generate_candidate_responses()`, call `get_last_generation_metrics()` and pass the metrics through to `BenchmarkResult` creation:
```python
from llm_api.ai_service import get_last_generation_metrics

# ... after generation ...
gen_metrics = get_last_generation_metrics()
if gen_metrics:
    tokens_generated = gen_metrics.output_tokens
    tokens_per_second = gen_metrics.tokens_per_second
```

Update `run_benchmark_suite()` finalization to compute `average_tokens_per_second`.

### E. `benchmarking/industry_benchmarks.py` — New file

Adapter module for importing standard evaluation datasets:

```python
def import_sciassess(subset: str = "all") -> ScenarioGroup:
    """
    Loads SciAssess evaluation data and creates a ScenarioGroup.
    
    Attempts to load from HuggingFace datasets library.
    Falls back to local fixtures in benchmarking/fixtures/ if not available.
    """
    ...

def import_dataset_from_jsonl(filepath: str, group_name: str, 
                               question_key: str = "question",
                               answer_key: str = "ideal_answer",
                               keywords_key: str = None) -> ScenarioGroup:
    """
    Generic importer for JSONL evaluation files.
    Maps fields to BenchmarkScenario entries.
    """
    ...
```

The key insight: most text:text evaluation datasets share a common structure (question + reference answer). The adapter just maps field names to `BenchmarkScenario.question` and `BenchmarkScenario.ideal_answer`.

### F. `benchmarking/long_context_evaluator.py` — New file

Specialized runner that evaluates performance degradation over accumulating context:

```python
def run_long_context_evaluation(experiment, corpus, log_callback=None):
    """
    Runs scenarios sequentially within a single conversation,
    tracking cumulative_input_tokens per result.
    
    Unlike run_benchmark_suite() which treats each scenario independently,
    this runner accumulates conversation history across scenarios to measure
    how performance degrades as context grows.
    """
    # 1. Create a single Conversation for the entire run
    # 2. For each scenario in order:
    #    a. Append scenario question to conversation history
    #    b. Generate response (with full history as context)
    #    c. Record cumulative_input_tokens from PromptResponseLog
    #    d. Store as extra_metric: {"cumulative_input_tokens": N}
    #    e. Score as usual (faithfulness, relevance, semantic)
    # 3. The Investigation.to_dataframe() can then plot any metric vs cumulative_input_tokens
    ...
```

This supports two modes (selected via `experiment.configuration`):
- `"context_mode": "chat"` — raw multi-turn chat, scenarios become sequential user messages
- `"context_mode": "blueprint"` — each scenario triggers a full Blueprint execution within the conversation

## Testing Requirements

All tests use the venv at `../../py313/bin/python`.

### New Tests (add to `benchmarking/tests.py`)

1. **`test_generation_metrics_captured`**: Mock `ai_service.generate_response2()`, verify `get_last_generation_metrics()` returns non-null `GenerationMetrics` with sensible values.

2. **`test_benchmark_result_throughput`**: Run a minimal benchmark and verify `tokens_per_second` is populated on `BenchmarkResult`.

3. **`test_import_dataset_from_jsonl`**: Create a temp JSONL file, import it, verify `ScenarioGroup` and `BenchmarkScenario` records are created correctly.

4. **`test_long_context_cumulative_tokens`**: Run long-context evaluator with 3 scenarios, verify `cumulative_input_tokens` increases monotonically in `extra_metrics`.

### Run existing tests

```bash
../../py313/bin/python manage.py test benchmarking -v2
```

Ensure no regressions.

## Verification Checklist

- [ ] `GenerationMetrics` dataclass captures timing for both local and proxy backends
- [ ] `PromptResponseLog` records `generation_duration_ms` and `tokens_per_second`
- [ ] `BenchmarkResult` records `tokens_generated` and `tokens_per_second`
- [ ] `BenchmarkRun` computes `average_tokens_per_second`
- [ ] JSONL dataset importer creates valid ScenarioGroups
- [ ] Long-context evaluator tracks `cumulative_input_tokens` per result
- [ ] All existing benchmarking tests pass
- [ ] All new tests pass
