"""
Dataset Curation & Validation Split Engine for Unsloth Fine-Tuning.

This module harvests training and evaluation data across three core sources:
1. Gold-standard BenchmarkScenarios from ScenarioGroups.
2. Verified production chat trajectories from PromptResponseLog (thumbs-up feedback).
3. Structured domain claims and concept definitions from GRIPS.

It automates deterministic train/validation splitting, writes standardized
ShareGPT/OpenAI JSONL training files, and automatically registers the held-out
validation split as a first-class ScenarioGroup for post-training benchmarking.
"""

import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

logger = logging.getLogger(__name__)


@dataclass
class CurationCandidate:
    """
    Normalized intermediate representation of an instruction-tuning candidate.

    >>> cand = CurationCandidate(prompt="What is IV?", completion="Instrumental Variables.", source_type="scenario")
    >>> cand.prompt
    'What is IV?'
    >>> cand.completion
    'Instrumental Variables.'
    """
    prompt: str
    completion: str
    source_type: Literal["scenario", "prompt_log", "grips", "synthetic"]
    system_prompt: str = "You are a domain expert in causal modeling and quantitative analysis."
    source_id: Optional[str] = None
    expected_keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def format_candidate_for_export(
    candidate: CurationCandidate,
    format: Literal["sharegpt", "openai"] = "sharegpt"
) -> Dict[str, Any]:
    """
    Formats a CurationCandidate into standard ShareGPT or OpenAI JSONL schemas.

    >>> cand = CurationCandidate(prompt="Define ATE.", completion="Average Treatment Effect.", source_type="scenario")
    >>> entry = format_candidate_for_export(cand, format="sharegpt")
    >>> entry["conversations"][1]
    {'from': 'human', 'value': 'Define ATE.'}
    >>> entry["conversations"][2]
    {'from': 'gpt', 'value': 'Average Treatment Effect.'}
    """
    if format == "sharegpt":
        return {
            "conversations": [
                {"from": "system", "value": candidate.system_prompt},
                {"from": "human", "value": candidate.prompt},
                {"from": "gpt", "value": candidate.completion},
            ]
        }
    elif format == "openai":
        return {
            "messages": [
                {"role": "system", "content": candidate.system_prompt},
                {"role": "user", "content": candidate.prompt},
                {"role": "assistant", "content": candidate.completion},
            ]
        }
    else:
        raise ValueError(f"Unsupported format '{format}'. Use 'sharegpt' or 'openai'.")


def split_candidates(
    candidates: List[CurationCandidate],
    split_ratio: float = 0.85,
    seed: int = 42
) -> Tuple[List[CurationCandidate], List[CurationCandidate]]:
    """
    Deterministically splits a candidate list into train and validation sets.

    Guarantees that if at least 2 candidates exist, both train and validation
    receive at least 1 candidate.

    >>> cands = [CurationCandidate(f"Q{i}", f"A{i}", "scenario") for i in range(10)]
    >>> train, val = split_candidates(cands, split_ratio=0.8, seed=123)
    >>> len(train), len(val)
    (8, 2)
    >>> set(c.prompt for c in train).intersection(set(c.prompt for c in val))
    set()
    """
    if not candidates:
        return [], []

    shuffled = list(candidates)
    rng = random.Random(seed)
    rng.shuffle(shuffled)

    total = len(shuffled)
    if total == 1:
        return shuffled, []

    train_count = int(round(total * split_ratio))
    train_count = max(1, min(total - 1, train_count))

    train_set = shuffled[:train_count]
    val_set = shuffled[train_count:]
    return train_set, val_set


def compute_semantic_diversity(texts: List[str]) -> float:
    """
    Computes an estimated semantic diversity score (0.0 to 1.0) for a text collection.
    Uses pairwise Jaccard token distance as an efficient, deterministic metric.

    >>> score = compute_semantic_diversity(["causal inference regression", "propensity score matching", "quantum physics gravity"])
    >>> 0.0 <= score <= 1.0
    True
    """
    if len(texts) < 2:
        return 0.0

    token_sets = [set(t.lower().split()) for t in texts if t.strip()]
    if len(token_sets) < 2:
        return 0.0

    distances = []
    sample_size = min(len(token_sets), 50)
    for i in range(sample_size):
        for j in range(i + 1, sample_size):
            s1, s2 = token_sets[i], token_sets[j]
            union = len(s1.union(s2))
            if union == 0:
                continue
            inter = len(s1.intersection(s2))
            jaccard_distance = 1.0 - (inter / union)
            distances.append(jaccard_distance)

    return float(round(sum(distances) / len(distances), 4)) if distances else 0.0


def harvest_from_scenario_group(
    scenario_group_id: int,
    system_prompt: str = "You are a domain expert in causal modeling and quantitative analysis."
) -> List[CurationCandidate]:
    """
    Harvests candidates from an existing ScenarioGroup.
    """
    from benchmarking.models import ScenarioGroup
    try:
        group = ScenarioGroup.objects.get(id=scenario_group_id)
    except ScenarioGroup.DoesNotExist:
        logger.warning(f"ScenarioGroup ID {scenario_group_id} does not exist.")
        return []

    candidates = []
    for s in group.scenarios.all():
        if not s.question or not s.ideal_answer:
            continue
        candidates.append(
            CurationCandidate(
                prompt=s.question.strip(),
                completion=s.ideal_answer.strip(),
                source_type="scenario",
                system_prompt=system_prompt,
                source_id=str(s.id),
                expected_keywords=list(s.expected_keywords or []),
                metadata={"group_id": group.id, "group_name": group.name}
            )
        )
    return candidates


def harvest_from_prompt_logs(
    min_feedback: int = 1,
    max_examples: int = 1000,
    system_prompt: str = "You are a helpful domain reasoning assistant."
) -> List[CurationCandidate]:
    """
    Harvests verified high-signal completions from user-rated PromptResponseLog entries.
    """
    from llm_api.models import PromptResponseLog
    queryset = PromptResponseLog.objects.filter(
        step_status="SUCCESS",
        user_feedback__gte=min_feedback
    ).exclude(user_prompt="").exclude(generated_response="").order_by("-created_at")[:max_examples]

    candidates = []
    for log in queryset:
        candidates.append(
            CurationCandidate(
                prompt=log.user_prompt.strip(),
                completion=log.generated_response.strip(),
                source_type="prompt_log",
                system_prompt=log.system_prompt or system_prompt,
                source_id=str(log.id),
                metadata={"feedback": log.user_feedback, "duration_ms": getattr(log, "generation_duration_ms", None)}
            )
        )
    return candidates


def harvest_from_grips(
    domain_id: Optional[int] = None,
    max_items: int = 200,
    system_prompt: str = "You are an analytical reasoning assistant specializing in causal domain models."
) -> List[CurationCandidate]:
    """
    Harvests domain definitions and structured causal relations from the GRIPS graph.
    """
    try:
        from grips.models import ConceptNode, Domain
    except ImportError:
        logger.warning("GRIPS application is not available in current environment.")
        return []

    nodes_query = ConceptNode.objects.all()
    if domain_id:
        nodes_query = nodes_query.filter(domain_id=domain_id)

    candidates = []
    for node in nodes_query.select_related("domain")[:max_items]:
        if not node.title:
            continue
        domain_name = node.domain.name if node.domain else "Domain Analysis"
        prompt = f"Explain the concept of '{node.title}' within {domain_name} and describe its key characteristics."
        
        narrative = getattr(node, "narrative_content", None) or getattr(node, "focus_hint", "")
        if not narrative:
            continue

        completion = f"### {node.title}\n\n{narrative.strip()}"
        candidates.append(
            CurationCandidate(
                prompt=prompt,
                completion=completion,
                source_type="grips",
                system_prompt=system_prompt,
                source_id=str(node.id),
                metadata={"node_id": node.id, "domain": domain_name}
            )
        )
    return candidates


def curate_and_export_dataset(
    name: str,
    scenario_group_ids: Optional[List[int]] = None,
    include_prompt_logs: bool = False,
    min_feedback: int = 1,
    include_grips: bool = False,
    grips_domain_id: Optional[int] = None,
    split_ratio: float = 0.85,
    format: Literal["sharegpt", "openai"] = "sharegpt",
    system_prompt: str = "You are a domain expert in causal modeling and quantitative analysis.",
    seed: int = 42,
    datasets_dir: Optional[str] = None
):
    """
    Primary curation orchestrator.

    Harvests from configured sources, deduplicates, executes a deterministic
    train/validation split, writes the training JSONL file to disk, registers the
    held-out validation split as a ScenarioGroup, and creates a FineTuningDataset.

    Returns:
        The created FineTuningDataset instance.
    """
    from benchmarking.models import BenchmarkScenario, FineTuningDataset, ScenarioGroup

    all_candidates: List[CurationCandidate] = []

    # 1. Harvest from ScenarioGroups
    if scenario_group_ids:
        for gid in scenario_group_ids:
            all_candidates.extend(harvest_from_scenario_group(gid, system_prompt=system_prompt))

    # 2. Harvest from Prompt Logs
    if include_prompt_logs:
        all_candidates.extend(harvest_from_prompt_logs(min_feedback=min_feedback, system_prompt=system_prompt))

    # 3. Harvest from GRIPS
    if include_grips:
        all_candidates.extend(harvest_from_grips(domain_id=grips_domain_id, system_prompt=system_prompt))

    if not all_candidates:
        raise ValueError("No candidates harvested from the specified sources.")

    # 4. Deduplicate by exact prompt text
    seen_prompts = set()
    deduped_candidates: List[CurationCandidate] = []
    for cand in all_candidates:
        normalized_key = cand.prompt.strip().lower()
        if normalized_key not in seen_prompts:
            seen_prompts.add(normalized_key)
            deduped_candidates.append(cand)

    # 5. Split train / validation
    train_cands, val_cands = split_candidates(deduped_candidates, split_ratio=split_ratio, seed=seed)

    # 6. Prepare output paths
    base_dir = getattr(settings, "BASE_DIR", ".")
    target_dir = datasets_dir or os.path.join(base_dir, "datasets")
    os.makedirs(target_dir, exist_ok=True)

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    clean_slug = slugify(name) or "dataset"
    file_name = f"{clean_slug}_{timestamp_str}.jsonl"
    file_path = os.path.join(target_dir, file_name)

    # 7. Write Train JSONL
    total_tokens_est = 0
    with open(file_path, "w", encoding="utf-8") as f:
        for cand in train_cands:
            entry = format_candidate_for_export(cand, format=format)
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # Estimate tokens: approx 1.3 tokens per whitespace word
            word_count = len(cand.prompt.split()) + len(cand.completion.split())
            total_tokens_est += int(word_count * 1.3)

    # 8. Create Validation ScenarioGroup & Scenarios
    val_group = None
    if val_cands:
        val_group_name = f"Validation - {name} ({timestamp_str})"
        val_group = ScenarioGroup.objects.create(
            name=val_group_name,
            description=f"Auto-generated held-out validation split ({len(val_cands)} scenarios, seed={seed}) for dataset '{name}'."
        )
        scenarios_to_create = [
            BenchmarkScenario(
                question=c.prompt,
                ideal_answer=c.completion,
                expected_keywords=c.expected_keywords
            )
            for c in val_cands
        ]
        created_scenarios = BenchmarkScenario.objects.bulk_create(scenarios_to_create)
        val_group.scenarios.add(*created_scenarios)

    # 9. Compute Semantic Diversity
    all_texts = [f"{c.prompt} {c.completion}" for c in train_cands]
    diversity_score = compute_semantic_diversity(all_texts)

    # 10. Estimate training minutes (empirical heuristic: ~0.0015 mins per token on 6GB GPU)
    estimated_mins = max(1, int(round((total_tokens_est * 3 * 0.0015) / 60)))

    # Source breakdown metadata
    source_counts = {}
    for c in deduped_candidates:
        source_counts[c.source_type] = source_counts.get(c.source_type, 0) + 1

    metadata = {
        "curation_timestamp": timezone.now().isoformat(),
        "random_seed": seed,
        "source_breakdown": source_counts,
        "format": format,
        "split_ratio": split_ratio,
        "total_harvested": len(all_candidates),
        "total_unique": len(deduped_candidates),
    }

    # 11. Create and return FineTuningDataset record
    parent_scenario_group = None
    if scenario_group_ids and len(scenario_group_ids) == 1:
        parent_scenario_group = ScenarioGroup.objects.filter(id=scenario_group_ids[0]).first()

    dataset_record = FineTuningDataset.objects.create(
        name=name,
        scenario_group=parent_scenario_group,
        validation_group=val_group,
        file_path=file_path,
        format=format,
        split_ratio=split_ratio,
        train_example_count=len(train_cands),
        val_example_count=len(val_cands),
        example_count=len(train_cands),
        total_tokens=total_tokens_est,
        semantic_diversity_score=diversity_score,
        estimated_training_minutes=estimated_mins,
        metadata=metadata
    )

    logger.info(
        f"✅ Curated dataset '{name}': {len(train_cands)} train examples written to {file_path}, "
        f"{len(val_cands)} validation scenarios registered in ScenarioGroup ID {val_group.id if val_group else 'None'}."
    )
    return dataset_record
