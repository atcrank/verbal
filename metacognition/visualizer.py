"""
Lightweight Blueprint Node Progress Visualizer (WS20 Phase 3).

Renders CSS Grid/Flexbox iconography of blueprint reasoning steps and sub-blueprints.
- Row 1: Linear main steps connected by Unicode arrows (➔)
- Row 2: Sub-blueprint steps branching down if present
- State badges: Success (green ✓), Failure (red ✗), Active (pulsing ●), Pending (gray ○)
"""

import html
from typing import Optional, Iterable, List, Dict, Any


def get_ordered_blueprint_steps(blueprint) -> List[Any]:
    """
    Returns reasoning steps in topological order starting from the start node.
    """
    if not blueprint:
        return []
    
    steps = list(blueprint.steps.filter(is_active=True).select_related('on_success_step', 'sub_blueprint'))
    if not steps:
        return []
        
    step_map = {s.id: s for s in steps}
    start = next((s for s in steps if s.is_start_node), steps[0])
    
    ordered = []
    visited = set()
    curr = start
    while curr and curr.id not in visited:
        visited.add(curr.id)
        ordered.append(curr)
        curr = curr.on_success_step
        if curr and curr.id not in step_map:
            break
            
    for s in steps:
        if s.id not in visited:
            visited.add(s.id)
            ordered.append(s)
            
    return ordered


def build_visualizer_data(
    blueprint,
    completed_steps: Optional[Iterable[str]] = None,
    active_step: Optional[str] = None,
    failed_steps: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """
    Constructs the data structure representing main row steps and sub-blueprint rows.
    """
    if not blueprint:
        return {"main_steps": [], "sub_rows": []}

    completed_set = set(completed_steps or [])
    failed_set = set(failed_steps or [])
    ordered_steps = get_ordered_blueprint_steps(blueprint)

    main_row_steps = []
    sub_rows = []

    for step in ordered_steps:
        s_name = step.name
        
        # Determine status
        if s_name in failed_set:
            status = "failure"
            icon = "✗"
        elif active_step and s_name == active_step:
            status = "active"
            icon = "●"
        elif completed_steps is not None:
            if s_name in completed_set:
                status = "success"
                icon = "✓"
            else:
                status = "pending"
                icon = "○"
        else:
            # Historical completed run: default all to success
            status = "success"
            icon = "✓"

        main_row_steps.append({
            "name": s_name,
            "status": status,
            "icon": icon,
            "has_sub": bool(step.sub_blueprint)
        })

        if step.sub_blueprint:
            sub_ordered = get_ordered_blueprint_steps(step.sub_blueprint)
            sub_step_items = []
            for sub_s in sub_ordered:
                sub_name = sub_s.name
                if sub_name in failed_set:
                    sub_status = "failure"
                    sub_icon = "✗"
                elif active_step and sub_name == active_step:
                    sub_status = "active"
                    sub_icon = "●"
                elif completed_steps is not None:
                    if sub_name in completed_set:
                        sub_status = "success"
                        sub_icon = "✓"
                    else:
                        sub_status = "pending"
                        sub_icon = "○"
                else:
                    sub_status = "success"
                    sub_icon = "✓"

                sub_step_items.append({
                    "name": sub_name,
                    "status": sub_status,
                    "icon": sub_icon
                })

            sub_rows.append({
                "parent_step": s_name,
                "sub_blueprint_name": step.sub_blueprint.name,
                "steps": sub_step_items
            })

    return {
        "blueprint_name": getattr(blueprint, "name", ""),
        "main_steps": main_row_steps,
        "sub_rows": sub_rows
    }


def render_blueprint_visualizer_html(
    blueprint,
    completed_steps: Optional[Iterable[str]] = None,
    active_step: Optional[str] = None,
    failed_steps: Optional[Iterable[str]] = None,
) -> str:
    """
    Renders pure HTML for the blueprint node visualizer.
    """
    data = build_visualizer_data(
        blueprint,
        completed_steps=completed_steps,
        active_step=active_step,
        failed_steps=failed_steps,
    )
    main_steps = data.get("main_steps", [])
    if not main_steps:
        return ""

    out = ['<div id="blueprint-visualizer" class="blueprint-visualizer">']
    
    # Row 1: Main Steps
    out.append('  <div class="visualizer-row">')
    for i, s in enumerate(main_steps):
        s_cls = f"viz-step viz-{s['status']}"
        s_title = f"{html.escape(s['name'])} ({s['status'].capitalize()})"
        out.append(f'    <span class="{s_cls}" title="{s_title}">{s["icon"]} {html.escape(s["name"])}</span>')
        if i < len(main_steps) - 1:
            out.append('    <span class="viz-arrow">➔</span>')
    out.append('  </div>')

    # Row 2+: Sub-blueprints
    for sub in data.get("sub_rows", []):
        sub_steps = sub.get("steps", [])
        if not sub_steps:
            continue
        out.append('  <div class="visualizer-row visualizer-sub-row">')
        label = html.escape(f"↳ [{sub['sub_blueprint_name']}]")
        out.append(f'    <span class="viz-sub-indent">{label}</span>')
        for j, ss in enumerate(sub_steps):
            ss_cls = f"viz-step viz-{ss['status']}"
            ss_title = f"{html.escape(ss['name'])} ({ss['status'].capitalize()})"
            out.append(f'    <span class="{ss_cls}" title="{ss_title}">{ss["icon"]} {html.escape(ss["name"])}</span>')
            if j < len(sub_steps) - 1:
                out.append('    <span class="viz-arrow">➔</span>')
        out.append('  </div>')

    out.append('</div>')
    return "\n".join(out)
