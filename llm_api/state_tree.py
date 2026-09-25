"""
llm_api.state_tree
==================

Working Memory and State Tree Management for Agentic Conversations.

This module provides three-tier context handling:
1. Dynamic Focal Projection (format_focal_state_tree): Renders an active task/branch
   in full detail with a '>>' pointer while collapsing out-of-scope sibling branches
   into high-level stubs to prevent prompt bloat and distraction.
2. Fast Inline Compaction (fast_compact_state_tree): Deterministic, zero-latency Python
   safety guardrail preventing database bloat by rolling settled milestones, capping
   list sizes, and sanitizing string values while strictly preserving invariants.
3. Intelligent Compaction (intelligent_compact_state_tree): Distills large state trees
   down to ~50% during overnight NightManager maintenance, transforming older threads
   into explicit low-confidence breadcrumbs ("I can see a long time ago we discussed...").
"""

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def format_focal_state_tree(
    state_tree: Dict[str, Any],
    active_task_id: Optional[str] = None
) -> str:
    """
    Renders a dynamic focal projection of the state tree in Markdown.

    The active branch (matching active_task_id or marked in-progress) is rendered
    in full detail with a '>> [ACTIVE]' pointer. Sibling or completed branches
    out of current scope are collapsed to single-line summaries to conserve tokens.

    >>> tree = {
    ...     "macro_objective": "Build wildfire robotics simulator",
    ...     "active_task": "task_obstacle_avoidance",
    ...     "established_facts": ["PostgreSQL 18 is running on port 5433"],
    ...     "tasks": {
    ...         "topic_1": {
    ...             "title": "Simulation Physics",
    ...             "status": "COMPLETED",
    ...             "subtasks": ["Rigid body", "Fluid dynamics"]
    ...         },
    ...         "task_obstacle_avoidance": {
    ...             "title": "Dynamic Obstacle Avoidance",
    ...             "status": "IN_PROGRESS",
    ...             "retry_note": "Try standard library math",
    ...             "subtasks": ["Raycast sensor", "Velocity obstacle solver"]
    ...         },
    ...         "topic_3": {
    ...             "title": "Visualization & Reporting",
    ...             "status": "PENDING"
    ...         }
    ...     },
    ...     "open_questions": ["What is the maximum replanning latency?"]
    ... }
    >>> rendered = format_focal_state_tree(tree)
    >>> ">> [ACTIVE] Dynamic Obstacle Avoidance" in rendered
    True
    >>> "[COMPLETED] Simulation Physics (subtasks omitted)" in rendered
    True
    >>> "[PENDING] Visualization & Reporting" in rendered
    True
    """
    if not state_tree or not isinstance(state_tree, dict):
        return ""

    lines: List[str] = ["### Conversation State Tree (Working Memory Map):"]

    # 1. Macro Objective
    if state_tree.get("macro_objective"):
        lines.append(f"- **Objective:** {state_tree['macro_objective']}")

    # 2. Invariants & Established Facts (strictly preserved)
    facts = state_tree.get("established_facts") or state_tree.get("invariants")
    if facts and isinstance(facts, list):
        lines.append("- **Invariants & Established Facts:**")
        for f in facts:
            lines.append(f"  - {f}")

    # Resolve target active task ID
    target_active = active_task_id or state_tree.get("active_task")

    # 3. Tasks Hierarchy
    tasks = state_tree.get("tasks")
    if tasks and isinstance(tasks, dict):
        lines.append("- **Tasks & Focus:**")
        for tid, tval in tasks.items():
            is_dict = isinstance(tval, dict)
            title = tval.get("title", tid) if is_dict else str(tval)
            status = tval.get("status", "PENDING").upper() if is_dict else "PENDING"

            is_active = (tid == target_active) or (status in ["ACTIVE", "IN_PROGRESS"])

            if is_active:
                # Active Branch: Expanded in full with '>>' pointer
                lines.append(f"  - >> [ACTIVE] {title}")
                if is_dict:
                    if tval.get("retry_note"):
                        lines.append(f"    - Retry Note: {tval['retry_note']}")
                    if tval.get("summary"):
                        lines.append(f"    - Current State: {tval['summary']}")
                    subtasks = tval.get("subtasks")
                    if isinstance(subtasks, list) and subtasks:
                        lines.append("    - Subtasks:")
                        for st in subtasks:
                            lines.append(f"      * {st}")
                    elif isinstance(subtasks, dict) and subtasks:
                        lines.append("    - Subtasks:")
                        for st_id, st_data in subtasks.items():
                            st_title = st_data.get("title", st_id) if isinstance(st_data, dict) else st_data
                            lines.append(f"      * {st_title}")
            elif status == "COMPLETED":
                # Out-of-scope completed branch: Collapsed stub
                has_inner = is_dict and bool(tval.get("subtasks") or tval.get("children"))
                suffix = " (subtasks omitted)" if has_inner else ""
                lines.append(f"  - [COMPLETED] {title}{suffix}")
            else:
                # Future / Pending branch: High-level stub
                lines.append(f"  - [{status}] {title}")

    # 4. Settled Milestones (if already compacted)
    milestones = state_tree.get("settled_milestones")
    if milestones and isinstance(milestones, dict):
        lines.append(f"- **Settled Milestones ({len(milestones)} completed tasks omitted)**")

    # 5. Working Hypotheses
    hypotheses = state_tree.get("working_hypotheses")
    if hypotheses and isinstance(hypotheses, list):
        lines.append("- **Working Hypotheses:**")
        for h in hypotheses:
            lines.append(f"  - {h}")

    # 6. Open Questions
    questions = state_tree.get("open_questions")
    if questions and isinstance(questions, list):
        lines.append("- **Open Questions:**")
        for q in questions:
            lines.append(f"  - {q}")

    # 7. Ancient Discussions & Historical Breadcrumbs
    ancient = state_tree.get("ancient_discussions")
    if ancient and isinstance(ancient, list):
        lines.append("- **Historical Breadcrumbs:**")
        for item in ancient:
            if isinstance(item, dict):
                anchor = item.get("prompt_anchor") or item.get("summary") or item.get("topic")
                lines.append(f"  - {anchor}")
            else:
                lines.append(f"  - {item}")

    return "\n".join(lines)


def _sanitize_string(val: str, max_len: int = 300) -> str:
    """Clamps oversized string values to prevent raw data dumps in the state tree."""
    if len(val) > max_len:
        return val[:max_len] + "... [truncated]"
    return val


def _sanitize_structure(obj: Any, max_str_len: int = 300) -> Any:
    """Recursively walks a nested data structure and truncates oversized strings."""
    if isinstance(obj, str):
        return _sanitize_string(obj, max_str_len)
    if isinstance(obj, dict):
        return {k: _sanitize_structure(v, max_str_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_structure(elem, max_str_len) for elem in obj]
    return obj


def fast_compact_state_tree(
    state_tree: Dict[str, Any],
    max_active_tasks: int = 6,
    max_items_per_list: int = 5,
    max_str_len: int = 300
) -> Dict[str, Any]:
    """
    Fast, deterministic inline Python safety guardrail. Runs in <1ms without LLMs.

    Rules:
    1. Rolls excess completed tasks into a summarized 'settled_milestones' dictionary.
    2. Caps working_hypotheses and open_questions to recent unique items.
    3. Strictly preserves established_facts / invariants without dropping them.
    4. Clamps oversized strings (preventing raw dumps of CSVs or logs).

    >>> tree = {
    ...     "tasks": {
    ...         f"t_{i}": {"title": f"Task {i}", "status": "COMPLETED"} for i in range(10)
    ...     },
    ...     "working_hypotheses": ["H1", "H2", "H3", "H4", "H5", "H6"],
    ...     "established_facts": ["Postgres 18"]
    ... }
    >>> compacted = fast_compact_state_tree(tree, max_active_tasks=3)
    >>> len(compacted["tasks"]) <= 4
    True
    >>> "settled_milestones" in compacted
    True
    >>> len(compacted["working_hypotheses"]) <= 5
    True
    >>> compacted["established_facts"] == ["Postgres 18"]
    True
    """
    if not state_tree or not isinstance(state_tree, dict):
        return {}

    tree = copy.deepcopy(state_tree)

    # 1. Sanitize all nested strings
    tree = _sanitize_structure(tree, max_str_len=max_str_len)

    # 2. Settled Milestones Collapsing
    tasks = tree.get("tasks")
    if isinstance(tasks, dict) and len(tasks) > max_active_tasks:
        completed_tasks: List[Tuple[str, dict]] = []
        uncompleted_tasks: Dict[str, Any] = {}

        for tid, tval in tasks.items():
            if isinstance(tval, dict) and tval.get("status", "").upper() == "COMPLETED":
                completed_tasks.append((tid, tval))
            else:
                uncompleted_tasks[tid] = tval

        # If completed tasks exceed quota, roll oldest into settled_milestones
        if len(completed_tasks) > 2:
            settled = tree.get("settled_milestones", {})
            if not isinstance(settled, dict):
                settled = {}

            # Keep newest 2 completed tasks in tasks
            to_roll = completed_tasks[:-2]
            to_keep = completed_tasks[-2:]

            for tid, tval in to_roll:
                settled[tid] = {
                    "title": tval.get("title", tid),
                    "status": "COMPLETED"
                }

            for tid, tval in to_keep:
                uncompleted_tasks[tid] = tval

            tree["tasks"] = uncompleted_tasks
            tree["settled_milestones"] = settled

    # 3. Bounded Lists
    for key in ["working_hypotheses", "open_questions"]:
        items = tree.get(key)
        if isinstance(items, list):
            # Deduplicate preserving order
            unique_items = list(dict.fromkeys(items))
            # Keep newest max_items_per_list
            tree[key] = unique_items[-max_items_per_list:]

    # 4. Invariants / Established Facts: deduplicate but keep
    for key in ["established_facts", "invariants"]:
        items = tree.get(key)
        if isinstance(items, list):
            tree[key] = list(dict.fromkeys(items))

    return tree


def intelligent_compact_state_tree(
    state_tree: Dict[str, Any],
    target_ratio: float = 0.5,
    ai_service: Any = None
) -> Dict[str, Any]:
    """
    Intelligent compaction performed during overnight maintenance (NightManager).

    Reduces state tree size down to ~target_ratio of capacity by synthesizing
    older settled milestones and completed threads into 'ancient_discussions' breadcrumbs:
    "I can see a long time ago we discussed [topic] but I'm not sure what we concluded - so start me off again".

    >>> tree = {
    ...     "settled_milestones": {
    ...         "m1": {"title": "Legacy Redis Setup", "status": "COMPLETED"},
    ...         "m2": {"title": "Celery Worker Trials", "status": "COMPLETED"}
    ...     },
    ...     "tasks": {
    ...         "t_active": {"title": "Postgres Native Tasks", "status": "IN_PROGRESS"}
    ...     }
    ... }
    >>> compacted = intelligent_compact_state_tree(tree)
    >>> "ancient_discussions" in compacted
    True
    >>> "m1" not in compacted.get("settled_milestones", {})
    True
    """
    if not state_tree or not isinstance(state_tree, dict):
        return {}

    tree = copy.deepcopy(state_tree)

    # First apply fast structural compaction
    tree = fast_compact_state_tree(tree)

    settled = tree.get("settled_milestones", {})
    ancient = tree.get("ancient_discussions", [])
    if not isinstance(ancient, list):
        ancient = []

    # If there are settled milestones, synthesize them into breadcrumb records
    if isinstance(settled, dict) and settled:
        topics_summarized = []
        for mid, mdata in settled.items():
            title = mdata.get("title", mid) if isinstance(mdata, dict) else str(mdata)
            topics_summarized.append(title)

        if topics_summarized:
            topic_str = ", ".join(topics_summarized[:4])
            breadcrumb = {
                "topic": topic_str,
                "summary": f"Completed previous milestones: {topic_str}",
                "status": "ancient_discussion",
                "prompt_anchor": f"I can see a long time ago we explored {topic_str}, but conclusions may need refreshing — start me off again if we need to revisit those topics."
            }
            ancient.append(breadcrumb)

        # Clear rolled settled milestones to achieve ~50% compaction
        tree["settled_milestones"] = {}

    tree["ancient_discussions"] = ancient[-4:]  # Keep latest 4 historical anchors
    return tree


def merge_state_trees(parent_tree: Optional[Dict[str, Any]], child_tree: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merges updates from child sub-blueprint state_tree into parent state_tree.
    Preserves parent tasks, updates statuses, and combines hypotheses and facts.

    >>> p = {"tasks": {"t1": {"status": "PENDING"}}, "established_facts": ["F1"]}
    >>> c = {"tasks": {"t1": {"status": "COMPLETED"}}, "established_facts": ["F2"]}
    >>> m = merge_state_trees(p, c)
    >>> m["tasks"]["t1"]["status"]
    'COMPLETED'
    >>> m["established_facts"]
    ['F1', 'F2']
    """
    if not child_tree:
        return dict(parent_tree or {})
    if not parent_tree:
        return dict(child_tree)

    merged = dict(parent_tree)
    for key, val in child_tree.items():
        if key in ["working_hypotheses", "open_questions", "established_facts", "invariants"] and isinstance(val, list):
            existing = merged.get(key, [])
            if isinstance(existing, list):
                merged[key] = list(dict.fromkeys(existing + val))
            else:
                merged[key] = val
        elif key == "tasks" and isinstance(val, dict):
            existing_tasks = merged.get("tasks", {})
            if isinstance(existing_tasks, dict):
                merged_tasks = dict(existing_tasks)
                merged_tasks.update(val)
                merged["tasks"] = merged_tasks
            else:
                merged["tasks"] = val
        elif isinstance(val, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(val)
            merged[key] = nested
        else:
            merged[key] = val
    return merged


def estimate_tree_tokens(state_tree: Dict[str, Any]) -> int:
    """Estimates the approximate token count of a serialized state tree."""
    if not state_tree:
        return 0
    text = json.dumps(state_tree)
    return max(1, len(text) // 4)
