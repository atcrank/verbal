import json
import datetime
from django.utils import timezone
from django.db.models import Avg, Count, Sum
from django.contrib.auth import get_user_model


def audit_nightmanager_performance(since_days: int = 7) -> dict:
    """
    Analyzes and aggregates performance, state tree usage, and artifact generation
    for the NightManager and autonomous CognitiveBlueprint runs over the given time window.
    """
    from metacognition.models import CognitiveBlueprint, ReasoningStep
    from llm_api.models import Conversation, PromptResponseLog
    from grips.models import ConceptNode, KnowledgeEdge, Domain
    from background_resources.models import Document, ReadingStrategy

    cutoff = timezone.now() - datetime.timedelta(days=since_days)
    User = get_user_model()
    nm_user = User.objects.filter(username="NightManager").first()

    # 1. Conversation & Session Metrics
    nm_convs = Conversation.objects.filter(user=nm_user) if nm_user else Conversation.objects.none()
    recent_nm_convs = nm_convs.filter(start_time__gte=cutoff)
    total_nm_convs = nm_convs.count()
    recent_conv_count = recent_nm_convs.count()

    # 2. Log Metrics
    nm_logs = PromptResponseLog.objects.filter(user=nm_user) if nm_user else PromptResponseLog.objects.none()
    recent_logs = nm_logs.filter(created_at__gte=cutoff)
    total_logs_count = recent_logs.count()

    duration_stats = recent_logs.exclude(generation_duration_ms__isnull=True).aggregate(
        avg_dur=Avg('generation_duration_ms'),
        total_in_toks=Sum('input_tokens'),
        total_out_toks=Sum('output_tokens')
    )

    models_used = list(recent_logs.values('model_name').annotate(count=Count('id')).order_by('-count'))
    status_breakdown = list(recent_logs.values('step_status').annotate(count=Count('id')).order_by('-count'))

    # 3. StateTree Health
    convs_with_tree = nm_convs.filter(state_tree__isnull=False).exclude(state_tree={})
    tree_count = convs_with_tree.count()
    tree_utilization_pct = round((tree_count / total_nm_convs * 100), 2) if total_nm_convs > 0 else 0.0

    # Aggregate tasks from all recent conversation state trees
    total_tasks_discovered = 0
    resolved_tasks = 0
    pending_tasks = 0
    active_hypotheses = []
    open_questions = []

    for conv in convs_with_tree[:20]:
        tree = conv.state_tree or {}
        if isinstance(tree, dict):
            # Check for tasks dictionary
            tasks = tree.get("tasks", {})
            if isinstance(tasks, dict):
                for tid, tinfo in tasks.items():
                    total_tasks_discovered += 1
                    status = (tinfo.get("status") if isinstance(tinfo, dict) else str(tinfo)).upper()
                    if status in ["COMPLETED", "RESOLVED", "SUCCESS"]:
                        resolved_tasks += 1
                    else:
                        pending_tasks += 1
            # Also inspect nested children (legacy state tree shape)
            for k, v in tree.items():
                if isinstance(v, dict) and "children" in v and isinstance(v["children"], dict):
                    for child_name, child_info in v["children"].items():
                        total_tasks_discovered += 1
                        st = (child_info.get("status") if isinstance(child_info, dict) else str(child_info)).upper()
                        if st in ["COMPLETED", "RESOLVED", "SUCCESS"]:
                            resolved_tasks += 1
                        else:
                            pending_tasks += 1

            if "working_hypotheses" in tree and isinstance(tree["working_hypotheses"], list):
                active_hypotheses.extend(tree["working_hypotheses"])
            if "open_questions" in tree and isinstance(tree["open_questions"], list):
                open_questions.extend(tree["open_questions"])

    # 4. Knowledge Graph & Artifact Creation Metrics
    total_concepts = ConceptNode.objects.count()
    concepts_needing_lint = ConceptNode.objects.filter(needs_linting=True).count()
    total_edges = KnowledgeEdge.objects.count()
    total_domains = Domain.objects.count()

    # 5. Proposed Cognitive Variants & Blueprints
    pending_reasoning_variants = ReasoningStep.objects.filter(is_pending_review=True).count()
    descendant_blueprints = CognitiveBlueprint.objects.filter(parent__isnull=False).count()
    canonical_blueprints = CognitiveBlueprint.objects.filter(is_canonical=True).count()
    total_blueprints = CognitiveBlueprint.objects.count()

    # 6. Signals File
    import os
    from django.conf import settings
    signals_file = os.path.join(settings.BASE_DIR, 'resources', 'night_manager_signals.md')
    signals_recorded = 0
    if os.path.exists(signals_file):
        with open(signals_file, 'r', encoding='utf-8') as f:
            signals_recorded = f.read().count("### ")

    # Construct report dictionary
    report = {
        "time_window_days": since_days,
        "generated_at": timezone.now().isoformat(),
        "sessions": {
            "total_lifetime_conversations": total_nm_convs,
            "recent_conversations": recent_conv_count,
            "recent_prompt_logs": total_logs_count,
            "avg_generation_duration_ms": round(duration_stats["avg_dur"] or 0, 2),
            "total_input_tokens": duration_stats["total_in_toks"] or 0,
            "total_output_tokens": duration_stats["total_out_toks"] or 0,
            "models_used": models_used,
            "step_status_breakdown": status_breakdown,
        },
        "state_tree_health": {
            "conversations_with_state_tree": tree_count,
            "state_tree_utilization_pct": tree_utilization_pct,
            "total_tasks_discovered": total_tasks_discovered,
            "resolved_tasks": resolved_tasks,
            "pending_tasks": pending_tasks,
            "active_hypotheses_count": len(set(active_hypotheses)),
            "open_questions_count": len(set(open_questions)),
        },
        "knowledge_artifacts": {
            "total_domains": total_domains,
            "total_concept_nodes": total_concepts,
            "concepts_needing_linting": concepts_needing_lint,
            "total_knowledge_edges": total_edges,
            "signals_recorded_count": signals_recorded,
        },
        "blueprint_evolution": {
            "total_blueprints": total_blueprints,
            "canonical_blueprints": canonical_blueprints,
            "descendant_blueprints": descendant_blueprints,
            "reasoning_variants_pending_review": pending_reasoning_variants,
        }
    }

    return report


def format_performance_report_markdown(report: dict) -> str:
    """Renders the audit report dictionary as human and model readable Markdown."""
    s = report["sessions"]
    st = report["state_tree_health"]
    k = report["knowledge_artifacts"]
    be = report["blueprint_evolution"]

    models_str = ", ".join(f"{m['model_name']} ({m['count']})" for m in s["models_used"]) if s["models_used"] else "None"
    status_str = ", ".join(f"{st_item['step_status'] or 'NORMAL'}: {st_item['count']}" for st_item in s["step_status_breakdown"]) if s["step_status_breakdown"] else "None"

    md = f"""# 🌙 NightManager Performance & Architecture Audit

**Time Window:** Past {report['time_window_days']} days (Generated: {report['generated_at'][:19]})

---

## 1. Execution & Inference Metrics
- **Lifetime Conversations:** {s['total_lifetime_conversations']}
- **Recent Conversations (Window):** {s['recent_conversations']}
- **Recent Prompt Executions:** {s['recent_prompt_logs']}
- **Average Generation Latency:** {s['avg_generation_duration_ms']} ms
- **Token Volume:** {s['total_input_tokens']} in / {s['total_output_tokens']} out
- **Models Active:** {models_str}
- **Step Status Breakdown:** {status_str}

## 2. StateTree Health & Task Execution
- **StateTree Utilization:** {st['state_tree_utilization_pct']}% ({st['conversations_with_state_tree']}/{s['total_lifetime_conversations']} conversations)
- **Tracked Tasks:** {st['total_tasks_discovered']} total ({st['resolved_tasks']} resolved, {st['pending_tasks']} pending)
- **Active Hypotheses:** {st['active_hypotheses_count']}
- **Open Questions:** {st['open_questions_count']}

## 3. Knowledge Graph & GRIPS Artifacts
- **Domains:** {k['total_domains']}
- **Concept Nodes:** {k['total_concept_nodes']} ({k['concepts_needing_linting']} pending linter verification)
- **Knowledge Edges:** {k['total_knowledge_edges']}
- **Recorded Qualitative Signals:** {k['signals_recorded_count']}

## 4. Blueprint Evolution & Reasoning Speciation
- **Total Blueprints:** {be['total_blueprints']} ({be['canonical_blueprints']} canonical, {be['descendant_blueprints']} descendant)
- **ReasoningStep Variants Pending Review:** {be['reasoning_variants_pending_review']}
"""
    return md
