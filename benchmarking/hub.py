"""
Operational Hub, Nuanced Leaderboard, and Grouped History Aggregator for Benchmarking Studio.

Provides robust, hardware-aware aggregation across multi-factor benchmark runs:
1. get_operational_status: Real-time execution status and defect diagnosis.
2. get_smart_opportunities: Identifies un-benchmarked (Model x ScenarioGroup) pairs.
3. get_nuanced_leaderboard: Separates assessed performance rankings from system/infrastructure defects.
4. get_grouped_history: Multi-axis historical aggregation with explicit defect tracking and deep links.
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
class DefectRecord:
    """
    Diagnostic record of an unassessed benchmark run that failed due to an environmental or system defect.
    Crucially: benchmark failure is not treated as a poor model evaluation score. It is an unassessed defect.
    """
    run_id: int
    investigation_id: Optional[int]
    model_name: str
    backend: str
    rag_strategy: str
    scenario_group_name: str
    defect_category: str       # e.g., 'Hosting Backend', 'Hardware / VRAM', 'RAG Retrieval', 'Scenario / Corpus'
    summary: str               # Human-readable defect explanation
    raw_error: str             # Error snippet or trace
    timestamp: Any


@dataclass
class LeaderboardEntry:
    """Multi-objective evaluation entry for an assessed Model + Backend + RAG Strategy configuration."""
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
    badges: List[str] = field(default_factory=list)
    latest_run_id: Optional[int] = None
    investigation_id: Optional[int] = None


@dataclass
class LeaderboardData:
    """Container separating genuine assessed rankings from unassessed system defects."""
    scored_entries: List[LeaderboardEntry]
    defect_entries: List[DefectRecord]


@dataclass
class GroupedHistorySection:
    """A cohesive cluster of benchmark runs grouped by a user-selected parameter axis."""
    group_key: str
    group_type: str
    total_runs: int
    assessed_runs: int
    defect_runs: int
    all_defects: bool
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


def diagnose_run_defect(run: BenchmarkRun) -> Optional[DefectRecord]:
    """
    Diagnoses whether a run failed due to a system defect rather than model performance.
    Identifies the defect category: Hosting Backend, Hardware Resource, RAG, or Scenario.
    """
    results = list(run.results.all())
    total_results = len(results)

    if total_results > 0:
        successful_count = sum(1 for res in results if _is_result_successful(res))
        if successful_count > 0:
            return None
    elif run.average_semantic_score is not None and run.average_semantic_score >= 0:
        return None

    # Run produced zero successful completions: classify system defect
    snap = run.configuration_snapshot or {}
    model_name = snap.get("ai_model_id") or (
        run.experiment.selected_model.hf_model_id if (run.experiment and run.experiment.selected_model) else "Default Model"
    )
    backend = (snap.get("hosting_backend") or "pytorch").lower()
    rag_strategy = snap.get("rag_strategy") or "none"
    sg_name = run.experiment.scenario_group.name if (run.experiment and run.experiment.scenario_group) else "General"

    first_res = results[0] if results else None
    resp = (first_res.generated_response or "") if first_res else ""
    err_metric = str(first_res.extra_metrics.get("error", "")) if first_res else ""
    combined_err = f"{resp} {err_metric}".strip()

    # Determine defect category and actionable diagnosis
    combined_lower = combined_err.lower()
    if "connection" in combined_lower or "timed out" in combined_lower or "refused" in combined_lower or "generationfailed" in combined_lower or backend in ("vllm", "ollama"):
        category = "Hosting Backend Defect"
        summary = f"{backend.upper()} engine communication defect (connection refused, timed out, or container aborted)."
    elif "cuda" in combined_lower or "memory" in combined_lower or "oom" in combined_lower or "allocate" in combined_lower:
        category = "Hardware Resource Defect"
        summary = "GPU Out of Memory (OOM) or tensor allocation failure."
    elif "rag" in combined_lower or "vector" in combined_lower or "embedding" in combined_lower or "pgvector" in combined_lower:
        category = "RAG Retrieval Defect"
        summary = "Vector retrieval or semantic embedding pipeline error."
    elif total_results == 0:
        category = "Scenario Configuration Defect"
        summary = "Run recorded zero scenario executions (aborted or empty prompt list)."
    else:
        category = "Runtime System Defect"
        summary = "Unclassified runtime exception during generation."

    return DefectRecord(
        run_id=run.id,
        investigation_id=run.experiment.investigation_id if run.experiment else None,
        model_name=model_name.split("/")[-1],
        backend=backend.title(),
        rag_strategy=rag_strategy,
        scenario_group_name=sg_name,
        defect_category=category,
        summary=summary,
        raw_error=combined_err[:220] if combined_err else "No output captured",
        timestamp=run.timestamp,
    )


def get_operational_status() -> Dict[str, Any]:
    """
    Returns the real-time operational status of the benchmarking subsystem.
    Differentiates healthy completed runs from crashed runs with accurate defect diagnosis.
    """
    latest_run = (
        BenchmarkRun.objects.select_related("experiment", "experiment__investigation", "experiment__selected_model", "experiment__scenario_group")
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
            "defect_record": None,
            "error_message": "",
        }

    defect = diagnose_run_defect(latest_run)
    is_failed = defect is not None
    error_message = defect.summary if defect else ""

    backend = latest_run.configuration_snapshot.get("hosting_backend", "default")
    model_hf = latest_run.configuration_snapshot.get("ai_model_id") or (
        latest_run.experiment.selected_model.hf_model_id if (latest_run.experiment and latest_run.experiment.selected_model) else "Standard LLM"
    )
    model_short = model_hf.split("/")[-1]

    return {
        "is_running": False,
        "has_runs": True,
        "latest_run": latest_run,
        "is_failed": is_failed,
        "defect_record": defect,
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

    tested_pairs = set()
    runs = BenchmarkRun.objects.select_related("experiment", "experiment__selected_model").all()
    for r in runs:
        if not r.experiment:
            continue
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


def get_nuanced_leaderboard(scenario_group_id: Optional[int] = None) -> LeaderboardData:
    """
    Builds a multi-objective Pareto leaderboard across configurations.
    IMPORTANT ARCHITECTURAL RULE:
    Benchmark failures (vLLM timeouts, OOM, container death) are NOT treated as model evaluation scores.
    They are classified as unassessed system defects and separated into defect_entries.
    scored_entries contains ONLY assessed configurations that produced valid completions.
    """
    query = BenchmarkRun.objects.select_related(
        "experiment", "experiment__investigation", "experiment__selected_model", "experiment__scenario_group"
    ).prefetch_related("results")

    if scenario_group_id:
        query = query.filter(experiment__scenario_group_id=scenario_group_id)

    runs = list(query.order_by("-timestamp"))
    if not runs:
        return LeaderboardData(scored_entries=[], defect_entries=[])

    # Group runs by composite configuration key
    grouped_configs: Dict[Tuple[str, str, str, str], List[BenchmarkRun]] = {}
    defect_records: List[DefectRecord] = []

    for r in runs:
        snap = r.configuration_snapshot or {}
        model_name = snap.get("ai_model_id") or (
            r.experiment.selected_model.hf_model_id if (r.experiment and r.experiment.selected_model) else "Default LLM"
        )
        backend = snap.get("hosting_backend") or "pytorch"
        rag_strategy = snap.get("rag_strategy") or "none"
        sg_name = r.experiment.scenario_group.name if (r.experiment and r.experiment.scenario_group) else "General"

        key = (model_name, backend, rag_strategy, sg_name)
        grouped_configs.setdefault(key, []).append(r)

    scored_entries: List[LeaderboardEntry] = []

    for (model_name, backend, rag_strategy, sg_name), cfg_runs in grouped_configs.items():
        total_runs = len(cfg_runs)
        all_results = [res for r in cfg_runs for res in r.results.all()]
        total_scenarios = len(all_results)

        successful_results = [res for res in all_results if _is_result_successful(res)]
        success_count = len(successful_results)

        # If 0 successful results across all runs in this configuration, it is an Unassessed System Defect
        if total_scenarios > 0 and success_count == 0:
            for r in cfg_runs:
                defect = diagnose_run_defect(r)
                if defect:
                    defect_records.append(defect)
            continue

        success_rate = round((success_count / total_scenarios * 100.0), 1) if total_scenarios > 0 else 0.0

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

        # Exclude configurations that have no valid semantic score from scored leaderboard
        if avg_semantic is None:
            for r in cfg_runs:
                defect = diagnose_run_defect(r)
                if defect:
                    defect_records.append(defect)
            continue

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
        sg_id = latest_run.experiment.scenario_group_id if latest_run.experiment else None
        inv_id = latest_run.experiment.investigation_id if latest_run.experiment else None

        scored_entries.append(
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
                latest_run_id=latest_run.id,
                investigation_id=inv_id,
            )
        )

    # Award Pareto Distinction Badges strictly among assessed configurations
    if scored_entries:
        # 1. Quality Leader (Highest semantic score)
        best_quality = max(scored_entries, key=lambda e: e.avg_semantic or 0.0)
        best_quality.badges.append("🥇 Quality Leader")

        # 2. Speed Leader (Highest tokens/sec or lowest duration)
        entries_with_tps = [e for e in scored_entries if e.avg_tokens_per_second]
        if entries_with_tps:
            best_speed = max(entries_with_tps, key=lambda e: e.avg_tokens_per_second or 0.0)
            if "🥇 Quality Leader" not in best_speed.badges:
                best_speed.badges.append("⚡ Speed Leader")
        elif scored_entries:
            entries_with_dur = [e for e in scored_entries if e.avg_duration_seconds]
            if entries_with_dur:
                best_lat = min(entries_with_dur, key=lambda e: e.avg_duration_seconds or 999.0)
                if "🥇 Quality Leader" not in best_lat.badges:
                    best_lat.badges.append("⚡ Speed Leader")

        # 3. Reliability Leader (100% success rate with >= 3 scenarios)
        for e in scored_entries:
            if e.success_rate >= 100.0 and e.total_scenarios >= 3 and not e.badges:
                e.badges.append("🛡️ Reliable")

    scored_entries.sort(key=lambda e: e.avg_semantic or 0.0, reverse=True)
    return LeaderboardData(scored_entries=scored_entries, defect_entries=defect_records)


def get_grouped_history(group_by: str = "scenario_group") -> List[GroupedHistorySection]:
    """
    Aggregates full benchmark history into collapsible sections along 4 dimensions:
    - 'scenario_group': Grouped by benchmark test corpus
    - 'hosting_backend': Grouped by Ollama / vLLM / PyTorch
    - 'investigation': Grouped by high-level Investigation project
    - 'model': Grouped by Target AI Model
    Attaches diagnostic defect annotations to unassessed runs.
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
        # Pre-attach defect record to each run for template usage
        r.defect_record = diagnose_run_defect(r)

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
                r.experiment.selected_model.hf_model_id if (r.experiment and r.experiment.selected_model) else "Default LLM"
            )
            key = model_id.split("/")[-1]
        else:  # default: scenario_group
            key = r.experiment.scenario_group.name if (r.experiment and r.experiment.scenario_group) else "General Corpus"

        sections_dict.setdefault(key, []).append(r)

    sections: List[GroupedHistorySection] = []

    for group_key, group_runs in sections_dict.items():
        total_runs = len(group_runs)
        defect_runs = sum(1 for r in group_runs if r.defect_record is not None)
        assessed_runs = total_runs - defect_runs
        all_defects = (total_runs > 0 and assessed_runs == 0)

        # Average semantic and duration across assessed runs only
        valid_sems = []
        valid_durs = []
        for r in group_runs:
            if r.defect_record is None:
                if r.average_semantic_score is not None and r.average_semantic_score >= 0:
                    valid_sems.append(r.average_semantic_score)
                else:
                    res_sems = [res.semantic_score for res in r.results.all() if res.semantic_score is not None and res.semantic_score >= 0]
                    if res_sems:
                        valid_sems.append(sum(res_sems) / len(res_sems))

                successful_res = [res for res in r.results.all() if _is_result_successful(res) and res.duration_seconds > 0]
                if successful_res:
                    valid_durs.extend(res.duration_seconds for res in successful_res)

        avg_sem = round(sum(valid_sems) / len(valid_sems), 2) if valid_sems else None
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
                assessed_runs=assessed_runs,
                defect_runs=defect_runs,
                all_defects=all_defects,
                avg_semantic=avg_sem,
                avg_latency=avg_lat,
                runs=group_runs,
                investigation_id=inv_id,
            )
        )

    return sections
