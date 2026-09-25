"""
metacognition/pruning.py

Service routines for managing ReasoningStep variant lineages, enforcing depth limits,
compressing intermediate retired ancestors, and trimming dead-end leaves while preserving
graph edge continuity.
"""

import logging
from typing import Optional, List, Dict, Set
from django.db import transaction

logger = logging.getLogger(__name__)


def get_lineage_depth(step) -> int:
    """
    Computes the generational depth of a ReasoningStep from its lineage root.
    A root step (parent_step=None) has depth 0.
    A child of the root has depth 1, grandchild depth 2, etc.
    Cycles are detected and safely broken.
    """
    depth = 0
    current = step
    seen: Set[int] = set()

    while current and current.parent_step_id:
        if current.id in seen:
            break
        seen.add(current.id)
        current = current.parent_step
        depth += 1

    return depth


def get_lineage_root(step):
    """
    Traverses parent pointers up to the ancestral root of the lineage.
    """
    current = step
    seen: Set[int] = set()

    while current and current.parent_step_id:
        if current.id in seen:
            break
        seen.add(current.id)
        current = current.parent_step

    return current


def rewire_step_references(step_to_delete, replacement_step):
    """
    Rewires all graph edges (on_success_step, on_failure_step, parallel_steps,
    start node assignment, and child variant parent pointers) from step_to_delete
    over to replacement_step before deletion.
    """
    blueprint = step_to_delete.blueprint

    # 1. Rewire on_success_step
    blueprint.steps.filter(on_success_step=step_to_delete).update(on_success_step=replacement_step)

    # 2. Rewire on_failure_step
    blueprint.steps.filter(on_failure_step=step_to_delete).update(on_failure_step=replacement_step)

    # 3. Rewire start node if necessary
    if step_to_delete.is_start_node:
        step_to_delete.is_start_node = False
        step_to_delete.save(update_fields=['is_start_node'])
        replacement_step.is_start_node = True
        replacement_step.save(update_fields=['is_start_node'])

    # 4. Rewire parallel_steps M2M references
    for referring_step in step_to_delete.parallel_sources.all():
        referring_step.parallel_steps.remove(step_to_delete)
        referring_step.parallel_steps.add(replacement_step)

    # 5. Rewire any child variants pointing to step_to_delete as parent
    for child in step_to_delete.variants.all():
        if child.id != replacement_step.id:
            child.parent_step = replacement_step
            child.save(update_fields=['parent_step'])


def compress_lineage_ancestors(blueprint, max_depth: int = 4, dry_run: bool = False) -> List[int]:
    """
    Inspects active variants in a blueprint. If an active variant exceeds max_depth,
    compresses intermediate inactive ancestors by re-parenting the active champion directly
    to the canonical lineage root and removing the retired intermediary stepping stones.

    Returns the list of pruned intermediate step IDs.
    """
    from .models import ReasoningStep, bypass_canonical_lock

    pruned_step_ids: List[int] = []
    lineage_groups = ReasoningStep.objects.active_for_blueprint(blueprint)

    for root_id, active_variants in lineage_groups.items():
        root_step = ReasoningStep.objects.filter(id=root_id).first()
        if not root_step:
            continue

        for variant in active_variants:
            depth = get_lineage_depth(variant)
            if depth <= max_depth:
                continue

            # Variant exceeds max_depth. Trace path from root to variant
            ancestor_path: List[ReasoningStep] = []
            curr = variant.parent_step
            while curr and curr.id != root_step.id:
                ancestor_path.append(curr)
                curr = curr.parent_step

            # Invert so ancestors are ordered from root down: [Gen 1, Gen 2, ...]
            ancestor_path.reverse()

            # Find inactive intermediate ancestors that can be collapsed
            candidates_to_prune: List[ReasoningStep] = []
            for anc in ancestor_path:
                if anc.is_canonical:
                    continue  # Never prune canonical steps
                if anc.is_active:
                    continue  # Active ancestor is still part of the active pool

                # Ensure ancestor has no other active branches
                other_active = anc.variants.filter(is_active=True).exclude(id=variant.id).exists()
                if not other_active:
                    candidates_to_prune.append(anc)

            if not candidates_to_prune:
                continue

            for anc in candidates_to_prune:
                pruned_step_ids.append(anc.id)
                if not dry_run:
                    with bypass_canonical_lock(), transaction.atomic():
                        rewire_step_references(anc, variant)
                        anc.delete()

            if not dry_run:
                with bypass_canonical_lock():
                    variant.parent_step = root_step
                    variant.save(update_fields=['parent_step'])
                    logger.info(
                        f"Compressed lineage {root_id}: re-parented active variant {variant.id} "
                        f"to root {root_step.id}, resetting depth to {get_lineage_depth(variant)}."
                    )

    return pruned_step_ids


def prune_dead_leaf_variants(
    blueprint=None,
    min_runs: int = 10,
    score_threshold: float = 0.2,
    dry_run: bool = False
) -> List[int]:
    """
    Finds and removes dead-end leaf variants:
    1. Rejected variants (is_active=False and is_pending_review=False) with no children.
    2. Severely underperforming variants (performance_score < score_threshold with >= min_runs)
       where an alternative active variant exists in the same lineage.
    Never prunes canonical steps or the sole active step of a lineage.

    Returns the list of pruned step IDs.
    """
    from .models import ReasoningStep, bypass_canonical_lock
    from llm_api.models import PromptResponseLog

    pruned_step_ids: List[int] = []
    qs = ReasoningStep.objects.filter(is_canonical=False)
    if blueprint:
        qs = qs.filter(blueprint=blueprint)

    for step in qs:
        # A leaf must have no children
        if step.variants.exists():
            continue

        root = get_lineage_root(step)
        if not root:
            continue

        # Check all active steps in this lineage
        active_in_lineage = ReasoningStep.objects.filter(
            blueprint=step.blueprint,
            is_active=True
        )
        lineage_active = [s for s in active_in_lineage if get_lineage_root(s).id == root.id]

        is_dead_leaf = False

        # Condition 1: Explicitly rejected / inactive without pending review
        if not step.is_active and not step.is_pending_review:
            is_dead_leaf = True

        # Condition 2: Underperforming leaf with an active sibling/parent
        elif step.is_active and len(lineage_active) > 1:
            log_count = PromptResponseLog.objects.filter(reasoning_step=step).count()
            if log_count >= min_runs and step.performance_score < score_threshold:
                is_dead_leaf = True

        if is_dead_leaf:
            # Determine replacement step
            replacement = step.parent_step or root
            if replacement.id == step.id:
                continue  # Safety check

            pruned_step_ids.append(step.id)
            if not dry_run:
                with bypass_canonical_lock(), transaction.atomic():
                    rewire_step_references(step, replacement)
                    target_id = step.id
                    target_name = step.name
                    step.delete()
                    logger.info(f"Pruned dead leaf variant {target_id} ('{target_name}') from lineage {root.id}.")

    return pruned_step_ids


def prune_blueprint_variants(
    blueprint=None,
    max_depth: int = 4,
    min_runs: int = 10,
    score_threshold: float = 0.2,
    dry_run: bool = False
) -> Dict:
    """
    Full pruning sweep:
    1. Trims dead-end leaf variants.
    2. Compresses deep active lineages to <= max_depth by collapsing intermediate ancestors.
    """
    from .models import CognitiveBlueprint

    blueprints = [blueprint] if blueprint else list(CognitiveBlueprint.objects.all())

    total_pruned_leaves: List[int] = []
    total_compressed_ancestors: List[int] = []

    for bp in blueprints:
        # Step 1: Prune dead leaves
        pruned_leaves = prune_dead_leaf_variants(
            blueprint=bp,
            min_runs=min_runs,
            score_threshold=score_threshold,
            dry_run=dry_run
        )
        total_pruned_leaves.extend(pruned_leaves)

        # Step 2: Compress deep lineages
        compressed_ancestors = compress_lineage_ancestors(
            blueprint=bp,
            max_depth=max_depth,
            dry_run=dry_run
        )
        total_compressed_ancestors.extend(compressed_ancestors)

    all_pruned = list(dict.fromkeys(total_pruned_leaves + total_compressed_ancestors))

    return {
        "status": "success",
        "dry_run": dry_run,
        "pruned_leaves_count": len(total_pruned_leaves),
        "compressed_ancestors_count": len(total_compressed_ancestors),
        "total_pruned": len(all_pruned),
        "pruned_step_ids": all_pruned
    }
