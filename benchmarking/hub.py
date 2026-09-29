"""
Operational Hub, Nuanced Leaderboard, and Grouped History Aggregator for Benchmarking Studio.

Provides robust, hardware-aware aggregation across multi-factor benchmark runs:
1. get_operational_status: Current execution status, queue count, and structured last-run digest.
2. get_smart_opportunities: Identifies un-benchmarked (Model x ScenarioGroup) pairs.
3. get_nuanced_leaderboard: Multi-objective Pareto rankings (Quality, Speed, Reliability, VRAM).
4. get_grouped_history: Multi-axis historical aggregation with 1-click investigation deep links.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Avg, Count, Q
from llm_api.models import LocalAIModel
from .models import (
    BenchmarkRun,
    BenchmarkResult,
    BenchmarkScenario,
    ScenarioGroup,
    Investigation,
    Experiment,
)

logger = logging.getLogger(__name__)


@dataclass
class SmartOpportunity:
    """Actionable recommendation for an un-benchmarked (Model x ScenarioGroup) combination."""
    model_id: int
    model_hf_id: str
    model_short: str
    scenario_group_id: int
    scenario_group_name: str
    scenario_count: int
    label: str


@dataclass
class LeaderboardEntry:
    """Multi-objective evaluation entry for a Model + Backend + RAG Strategy configuration."""
    model_name: str
    backend: str
    rag_strategy: str
    scenario_group_name: str
    scenario_group_id: Optional[int]
    total_runs: int
    total_scenarios: int
    success_rate: float
    avg_semantic: Optional[float]
    avg_faithfulness: Optional[float]
    avg_relevance: Optional[float]
    avg_tokens_per_second: Optional[float]
    avg_duration_seconds: Optional[float]
    is_unstable: bool
    error_summary: Optional[str] = None
    badges: List[str] = field(default_factory=list)
    latest_run_id: Optional[int] = None
    investigation_id: Optional[int] = None


@dataclass
class GroupedHistorySection:
    """A cohesive cluster of benchmark runs grouped by a user-selected parameter axis."""
    group_key: str
    group_type: str
    total_runs: int
    success_rate: float
    avg_semantic: Optional[float]
    avg_latency: Optional[float]
    runs: List[BenchmarkRun] = field(default_factory=list)
    investigation_id: Optional[int] = None


def _is_result_successful(res: BenchmarkResult) -> bool:
    resp = res.generated_response or ""
    if not resp or "GenerationFailed" in resp:
        return False
    if "error" in res.extra_metrics:
        return False
    if res.semantic_score is not None and res.semantic_score < 0:
        return False
    return True


def get_operational_status() -> Dict[str, Any]:
    """
    Returns the real-time operational status of the benchmarking subsystem.
    Differentiates healthy completed runs from crashed runs so users are never
    greeted with an uninformative screen of zeros.
    """
    latest_run = (
        BenchmarkRun.objects.select_related("experiment", "experiment__investigation", "experiment__selected_model")
        .prefetch_related("results")
        .order_by("-timestamp")
        .first()
    )

    if not latest_run:
        return {
            "is_running": False,
            "has_runs": False,
            "latest_run": None,
            "status_label": "Engine Ready • Idle",
            "is_failed": False,
            "error_message": "",
        }

    # Evaluate results to determine health
    results = list(latest_run.results.all())
    total_results = len(results)

    is_failed = False
    error_message = ""

    if total_results > 0:
        successful_count = sum(1 for res in results if _is_result_successful(res))
        if successful_count == 0:
            is_failed = True
            first_res = results[0]
            if "GenerationFailed" in (first_res.generated_response or ""):
                error_message = "Generation failed (engine unresponsive or container timed out)"
            elif first_res.extra_metrics.get("error"):
                error_message = str(first_res.extra_metrics["error"])[:80]
            else:
                error_message = "All scenario executions failed or produced null responses"
    elif latest_run.average_semantic_score is None:
        is_failed = True
        error_message = "Run recorded zero scenario completions"

    backend = latest_run.configuration_snapshot.get("hosting_backend", "default")
    model_hf = latest_run.configuration_snapshot.get("ai_model_id") or (
        latest_run.experiment.selected_model.hf_model_id if latest_run.experiment.selected_model else "Standard LLM"
    )
    model_short = model_hf.split("/")[-1]

    return {
        "is_running": False,
        "has_runs": True,
        "latest_run": latest_run,
        "is_failed": is_failed,
        "error_message": error_message,
        "backend": backend,
        "model_short": model_short,
        "status_label": "Engine Ready • Idle",
    }


def get_smart_opportunities(limit: int = 3) -> List[SmartOpportunity]:
    """
    Identifies natural extensions triggered by models in LocalAIModel that have
    not yet been benchmarked on existing ScenarioGroup test corpuses.
    """
    models = list(LocalAIModel.objects.all())
    scenario_groups = list(ScenarioGroup.objects.all().prefetch_related("scenarios"))

    if not models or not scenario_groups:
        return []

    # Map of (model_hf_id, scenario_group_id) that have at least 1 completed run
    tested_pairs = set()
    runs = BenchmarkRun.objects.select_related("experiment", "experiment__selected_model").all()
    for r in runs:
        sg_id = r.experiment.scenario_group_id
        if not sg_id:
            continue
        model_id_str = r.configuration_snapshot.get("ai_model_id")
        if not model_id_str and r.experiment.selected_model:
            model_id_str = r.experiment.selected_model.hf_model_id
        if model_id_str:
            tested_pairs.add((model_id_str.lower(), sg_id))
            tested_pairs.add((model_id_str.split("/")[-1].lower(), sg_id))

    opportunities: List[SmartOpportunity] = []
    for m in models:
        m_hf = m.hf_model_id
        m_short = m.name or m_hf.split("/")[-1]
        for sg in scenario_groups:
            # Skip empty scenario groups
            scen_count = len(sg.scenarios.all())
            if scen_count == 0:
                continue

            if (m_hf.lower(), sg.id) not in tested_pairs and (m_short.lower(), sg.id) not in tested_pairs:
                opportunities.append(
                    SmartOpportunity(
                        model_id=m.id,
                        model_hf_id=m_hf,
                        model_short=m_short,
                        scenario_group_id=sg.id,
                        scenario_group_name=sg.name,
                        scenario_count=scen_count,
                        label=f"{m_short} × {sg.name}",
                    )
                )
                if len(opportunities) >= limit:
                    return opportunities

    return opportunities


def get_nuanced_leaderboard(scenario_group_id: Optional[int] = None) -> List[LeaderboardEntry]:
    """
    Builds a multi-objective Pareto leaderboard across configurations.
    Evaluates:
    - Quality (Semantic similarity, Faithfulness, Relevance)
    - Throughput (Tokens per second)
    - Latency (Mean generation duration)
    - Stability (Evaluation completion and error-free rate)
    """
    query = BenchmarkRun.objects.select_related(
        "experiment", "experiment__investigation", "experiment__selected_model", "experiment__scenario_group"
    ).prefetch_related("results")

    if scenario_group_id:
        query = query.filter(experiment__scenario_group_id=scenario_group_id)

    runs = list(query.order_by("-timestamp"))
    if not runs:
        return []

    # Group runs by composite configuration key
    grouped_configs: Dict[Tuple[str, str, str, str], List[BenchmarkRun]] = {}
    for r in runs:
        snap = r.configuration_snapshot or {}
        model_name = snap.get("ai_model_id") or (
            r.experiment.selected_model.hf_model_id if r.experiment.selected_model else "Default LLM"
        )
        backend = snap.get("hosting_backend") or "pytorch"
        rag_strategy = snap.get("rag_strategy") or "none"
        sg_name = r.experiment.scenario_group.name if r.experiment.scenario_group else "General"

        key = (model_name, backend, rag_strategy, sg_name)
        grouped_configs.setdefault(key, []).append(r)

    entries: List[LeaderboardEntry] = []

    for (model_name, backend, rag_strategy, sg_name), cfg_runs in grouped_configs.items():
        total_runs = len(cfg_runs)
        all_results = [res for r in cfg_runs for res in r.results.all()]
        total_scenarios = len(all_results)

        successful_results = [res for res in all_results if _is_result_successful(res)]
        success_count = len(successful_results)
        success_rate = round((success_count / total_scenarios * 100.0), 1) if total_scenarios > 0 else 0.0

        is_unstable = success_rate < 50.0 or (total_scenarios > 0 and success_count == 0)
        error_summary = None
        if is_unstable and total_scenarios > 0:
            error_summary = "Crashed or failed generation"

        # Compute metric averages with fallback to results
        valid_sems = []
        for r in cfg_runs:
            if r.average_semantic_score is not None and r.average_semantic_score >= 0:
                valid_sems.append(r.average_semantic_score)
            else:
                res_sems = [res.semantic_score for res in r.results.all() if res.semantic_score is not None and res.semantic_score >= 0]
                if res_sems:
                    valid_sems.append(sum(res_sems) / len(res_sems))
        avg_semantic = round(sum(valid_sems) / len(valid_sems), 3) if valid_sems else None

        valid_faith = []
        for r in cfg_runs:
            if r.average_faithfulness is not None and r.average_faithfulness >= 0:
                valid_faith.append(r.average_faithfulness)
            else:
                res_f = [res.faithfulness_score for res in r.results.all() if res.faithfulness_score is not None and res.faithfulness_score >= 0]
                if res_f:
                    valid_faith.append(sum(res_f) / len(res_f))
        avg_faithfulness = round(sum(valid_faith) / len(valid_faith), 3) if valid_faith else None

        valid_rel = []
        for r in cfg_runs:
            if r.average_relevance is not None and r.average_relevance >= 0:
                valid_rel.append(r.average_relevance)
            else:
                res_r = [res.relevance_score for res in r.results.all() if res.relevance_score is not None and res.relevance_score >= 0]
                if res_r:
                    valid_rel.append(sum(res_r) / len(res_r))
        avg_relevance = round(sum(valid_rel) / len(valid_rel), 3) if valid_rel else None

        # Throughput and duration
        valid_tps = [
            res.extra_metrics["tokens_per_second"] for res in successful_results
            if "tokens_per_second" in res.extra_metrics and res.extra_metrics["tokens_per_second"] is not None
        ]
        avg_tps = round(sum(valid_tps) / len(valid_tps), 1) if valid_tps else None

        valid_durations = [res.duration_seconds for res in successful_results if res.duration_seconds > 0]
        avg_duration = round(sum(valid_durations) / len(valid_durations), 1) if valid_durations else None

        latest_run = cfg_runs[0]
        sg_id = latest_run.experiment.scenario_group_id
        inv_id = latest_run.experiment.investigation_id

        entries.append(
            LeaderboardEntry(
                model_name=model_name.split("/")[-1],
                backend=backend,
                rag_strategy=rag_strategy,
                scenario_group_name=sg_name,
                scenario_group_id=sg_id,
                total_runs=total_runs,
                total_scenarios=total_scenarios,
                success_rate=success_rate,
                avg_semantic=avg_semantic,
                avg_faithfulness=avg_faithfulness,
                avg_relevance=avg_relevance,
                avg_tokens_per_second=avg_tps,
                avg_duration_seconds=avg_duration,
                is_unstable=is_unstable,
                error_summary=error_summary,
                latest_run_id=latest_run.id,
                investigation_id=inv_id,
            )
        )

    # Award Pareto Distinction Badges
    stable_entries = [e for e in entries if not e.is_unstable and e.avg_semantic is not None]

    if stable_entries:
        # 1. Quality Leader (Highest semantic score)
        best_quality = max(stable_entries, key=lambda e: e.avg_semantic or 0.0)
        best_quality.badges.append("🥇 Quality Leader")

        # 2. Speed Leader (Highest tokens/sec or lowest duration)
        entries_with_tps = [e for e in stable_entries if e.avg_tokens_per_second]
        if entries_with_tps:
            best_speed = max(entries_with_tps, key=lambda e: e.avg_tokens_per_second or 0.0)
            if "🥇 Quality Leader" not in best_speed.badges:
                best_speed.badges.append("⚡ Speed Leader")
        elif stable_entries:
            entries_with_dur = [e for e in stable_entries if e.avg_duration_seconds]
            if entries_with_dur:
                best_lat = min(entries_with_dur, key=lambda e: e.avg_duration_seconds or 999.0)
                if "🥇 Quality Leader" not in best_lat.badges:
                    best_lat.badges.append("⚡ Speed Leader")

        # 3. Reliability Leader (100% success rate with >= 3 scenarios)
        for e in stable_entries:
            if e.success_rate >= 100.0 and e.total_scenarios >= 3 and not e.badges:
                e.badges.append("🛡️ Reliable")

    # Mark unstable entries
    for e in entries:
        if e.is_unstable:
            e.badges.append("⚠️ Unstable")

    # Sort leaderboard: Stable entries first by semantic score, unstable at bottom
    entries.sort(key=lambda e: (0 if e.is_unstable else 1, e.avg_semantic or 0.0), reverse=True)
    return entries


def get_grouped_history(group_by: str = "scenario_group") -> List[GroupedHistorySection]:
    """
    Aggregates full benchmark history into collapsible sections along 4 dimensions:
    - 'scenario_group': Grouped by benchmark test corpus
    - 'hosting_backend': Grouped by Ollama / vLLM / PyTorch
    - 'investigation': Grouped by high-level Investigation project
    - 'model': Grouped by Target AI Model
    """
    runs = list(
        BenchmarkRun.objects.select_related(
            "experiment", "experiment__investigation", "experiment__selected_model", "experiment__scenario_group"
        )
        .prefetch_related("results")
        .order_by("-timestamp")
    )

    if not runs:
        return []

    sections_dict: Dict[str, List[BenchmarkRun]] = {}

    for r in runs:
        snap = r.configuration_snapshot or {}
        if group_by == "hosting_backend":
            key = snap.get("hosting_backend") or "PyTorch (In-Process)"
            key = key.replace("_", " ").title()
        elif group_by == "investigation":
            if r.experiment and r.experiment.investigation:
                key = f"Investigation #{r.experiment.investigation.id}: {r.experiment.investigation.name}"
            else:
                key = "Standalone Experiments"
        elif group_by == "model":
            model_id = snap.get("ai_model_id") or (
                r.experiment.selected_model.hf_model_id if r.experiment.selected_model else "Default LLM"
            )
            key = model_id.split("/")[-1]
        else:  # default: scenario_group
            key = r.experiment.scenario_group.name if (r.experiment and r.experiment.scenario_group) else "General Corpus"

        sections_dict.setdefault(key, []).append(r)

    sections: List[GroupedHistorySection] = []

    for group_key, group_runs in sections_dict.items():
        total_runs = len(group_runs)
        all_results = [res for r in group_runs for res in r.results.all()]
        total_scenarios = len(all_results)

        successful_results = [res for res in all_results if _is_result_successful(res)]
        success_rate = round((len(successful_results) / total_scenarios * 100.0), 1) if total_scenarios > 0 else 0.0

        valid_sems = []
        for r in group_runs:
            if r.average_semantic_score is not None and r.average_semantic_score >= 0:
                valid_sems.append(r.average_semantic_score)
            else:
                res_sems = [res.semantic_score for res in r.results.all() if res.semantic_score is not None and res.semantic_score >= 0]
                if res_sems:
                    valid_sems.append(sum(res_sems) / len(res_sems))
        avg_sem = round(sum(valid_sems) / len(valid_sems), 2) if valid_sems else None

        valid_durs = [res.duration_seconds for res in successful_results if res.duration_seconds > 0]
        avg_lat = round(sum(valid_durs) / len(valid_durs), 1) if valid_durs else None

        inv_id = None
        for r in group_runs:
            if r.experiment and r.experiment.investigation_id:
                inv_id = r.experiment.investigation_id
                break

        sections.append(
            GroupedHistorySection(
                group_key=group_key,
                group_type=group_by,
                total_runs=total_runs,
                success_rate=success_rate,
                avg_semantic=avg_sem,
                avg_latency=avg_lat,
                runs=group_runs,
                investigation_id=inv_id,
            )
        )

    return sections
