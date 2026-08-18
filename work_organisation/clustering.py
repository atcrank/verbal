import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from django.utils import timezone
from .models import WorkshopSession, WhiteboardCard, WhiteboardCluster
from .events import publish_whiteboard_event

logger = logging.getLogger(__name__)


# ==========================================
# PYDANTIC SCHEMAS FOR STRUCTURED EXTRACTION
# ==========================================

class IdeaClusterItem(BaseModel):
    title: str = Field(description="Clear, descriptive thematic title for this cluster.")
    summary: str = Field(description="1-2 sentence synthesis of the common theme.")
    color: str = Field(default="#3B82F6", description="Suggested hex color for visual grouping.")
    card_ids: List[int] = Field(description="List of integer Card IDs belonging to this cluster.")


class IdeaClusteringPlan(BaseModel):
    reasoning: str = Field(description="Analysis of how ideas relate, diverge, and group together.")
    clusters: List[IdeaClusterItem] = Field(description="Thematic clusters containing all provided card IDs.")


class CausalFactorItem(BaseModel):
    name: str = Field(description="Name of the variable or causal factor.")
    state_options: List[str] = Field(default_factory=lambda: ["Low", "High"], description="Discrete values/states this factor can take.")
    causes: List[str] = Field(default_factory=list, description="Names of other factors that directly influence this variable.")
    justification: str = Field(description="Why this factor is important and evidence from the session cards.")


class CausalGraphExtractionPlan(BaseModel):
    reasoning: str = Field(description="Analysis of causal mechanisms and dynamics described across cards.")
    factors: List[CausalFactorItem] = Field(description="Extracted causal factors and influence links.")


# ==========================================
# CORE CLUSTERING & EXTRACTION ALGORITHMS
# ==========================================

def cluster_whiteboard_cards(session_id: int | str, user=None) -> dict:
    """
    Groups all unassigned or existing cards in a session into thematic clusters using structured LLM synthesis.
    """
    from llm_api.apps import service_registry

    session = WorkshopSession.objects.get(id=session_id)
    cards = session.cards.all().order_by('id')

    if not cards.exists():
        return {"status": "empty", "clusters_created": 0, "message": "No cards found in session."}

    # Format card inputs for LLM prompt
    cards_text = "\n".join([f"- Card ID {c.id} [{c.card_type}]: {c.text}" for c in cards])
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert workshop facilitator and cognitive analyst. "
                "Analyze the following brainstorming cards from a collaborative session. "
                "Group related cards into 2 to 5 distinct thematic clusters. "
                "Ensure every card ID is assigned to exactly one cluster."
            )
        },
        {
            "role": "user",
            "content": f"Session Objective: {session.workshop.objective or session.title}\n\nCards:\n{cards_text}"
        }
    ]

    ai_service = service_registry.ai_service
    clustering_plan = ai_service.generate_outline(
        messages=messages,
        response_schema=IdeaClusteringPlan
    )

    if isinstance(clustering_plan, list) and len(clustering_plan) > 0:
        clustering_plan = clustering_plan[0]

    # Handle fallback dictionary
    if isinstance(clustering_plan, dict):
        clusters_data = clustering_plan.get("clusters", [])
    elif hasattr(clustering_plan, "clusters"):
        clusters_data = clustering_plan.clusters
    else:
        clusters_data = []

    created_clusters = []
    card_x_offsets = {}
    base_x = 50.0
    base_y = 50.0

    for i, cl in enumerate(clusters_data):
        c_title = cl.get("title") if isinstance(cl, dict) else cl.title
        c_summary = cl.get("summary", "") if isinstance(cl, dict) else getattr(cl, "summary", "")
        c_color = cl.get("color", "#3B82F6") if isinstance(cl, dict) else getattr(cl, "color", "#3B82F6")
        c_card_ids = cl.get("card_ids", []) if isinstance(cl, dict) else getattr(cl, "card_ids", [])

        pos_x = base_x + (i % 3) * 360.0
        pos_y = base_y + (i // 3) * 280.0

        cluster, _ = WhiteboardCluster.objects.update_or_create(
            session=session,
            title=c_title,
            defaults={
                "summary": c_summary,
                "color": c_color,
                "pos_x": pos_x,
                "pos_y": pos_y,
                "width": 340.0,
                "height": max(220.0, 100.0 + len(c_card_ids) * 60.0)
            }
        )
        created_clusters.append(cluster.id)

        # Reposition and associate cards inside cluster bounds
        for card_idx, cid in enumerate(c_card_ids):
            WhiteboardCard.objects.filter(id=cid, session=session).update(
                cluster=cluster,
                pos_x=pos_x + 15.0,
                pos_y=pos_y + 60.0 + (card_idx * 55.0)
            )

    # Broadcast event to connected clients
    publish_whiteboard_event(session_id, "clustered", {
        "session_id": str(session_id),
        "cluster_ids": created_clusters,
        "count": len(created_clusters)
    })

    return {
        "status": "success",
        "clusters_created": len(created_clusters),
        "cluster_ids": created_clusters
    }


def extract_causal_factors_from_session(session_id: int | str, user=None) -> dict:
    """
    Extracts causal variables, influence dynamics, and discrete state options from whiteboard cards.
    """
    from llm_api.apps import service_registry

    session = WorkshopSession.objects.get(id=session_id)
    cards = session.cards.all()

    if not cards.exists():
        return {"status": "empty", "factors": []}

    cards_text = "\n".join([f"- [{c.card_type}]: {c.text}" for c in cards])
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert in causal modeling, Bayesian networks, and system dynamics. "
                "Analyze the following workshop session notes and extract distinct causal factors, "
                "their discrete state options, and any causal dependencies between them."
            )
        },
        {
            "role": "user",
            "content": f"Session Objective: {session.workshop.objective or session.title}\n\nNotes:\n{cards_text}"
        }
    ]

    ai_service = service_registry.ai_service
    graph_plan = ai_service.generate_outline(
        messages=messages,
        response_schema=CausalGraphExtractionPlan
    )

    if isinstance(graph_plan, list) and len(graph_plan) > 0:
        graph_plan = graph_plan[0]

    factors_data = graph_plan.get("factors", []) if isinstance(graph_plan, dict) else getattr(graph_plan, "factors", [])
    extracted = []

    for f in factors_data:
        f_name = f.get("name") if isinstance(f, dict) else f.name
        f_states = f.get("state_options", ["Low", "High"]) if isinstance(f, dict) else f.state_options
        f_causes = f.get("causes", []) if isinstance(f, dict) else f.causes
        f_just = f.get("justification", "") if isinstance(f, dict) else f.justification

        # Save factor cards on canvas if not already present
        WhiteboardCard.objects.get_or_create(
            session=session,
            text=f"Factor: {f_name} (States: {', '.join(f_states)})",
            card_type='factor',
            defaults={
                "pos_x": 400.0,
                "pos_y": 100.0 + len(extracted) * 80.0,
                "metadata": {
                    "factor_name": f_name,
                    "state_options": f_states,
                    "causes": f_causes,
                    "justification": f_just
                }
            }
        )
        extracted.append({
            "name": f_name,
            "state_options": f_states,
            "causes": f_causes,
            "justification": f_just
        })

    publish_whiteboard_event(session_id, "factors_extracted", {
        "session_id": str(session_id),
        "factors": extracted
    })

    return {
        "status": "success",
        "factors_count": len(extracted),
        "factors": extracted
    }


def export_whiteboard_markdown_summary(session_id: int | str, user=None) -> str:
    """
    Compiles a clean, pastable Markdown report containing executive summaries,
    structured cluster tables, causal factors, and open questions.
    """
    session = WorkshopSession.objects.select_related('workshop', 'workshop__project').get(id=session_id)
    clusters = session.clusters.prefetch_related('cards').all()
    standalone_cards = session.cards.filter(cluster__isnull=True)
    factors = session.cards.filter(card_type='factor')
    questions = session.cards.filter(card_type='question')
    hypotheses = session.cards.filter(card_type='hypothesis')

    now_str = timezone.now().strftime("%Y-%m-%d %H:%M")
    mode_label = session.get_access_mode_display()

    md = [
        f"# 📋 Workshop Session Summary: {session.title}",
        f"**Project:** {session.workshop.project.name} | **Workshop:** {session.workshop.name}",
        f"**Date:** {now_str} | **Access Mode:** {mode_label}",
        f"**Objective:** {session.workshop.objective or 'Collaborative exploration'}\n",
        "---",
        "\n## 1. Thematic Idea Clusters\n"
    ]

    if clusters.exists():
        for cl in clusters:
            md.append(f"### 🎯 Cluster: {cl.title}")
            if cl.summary:
                md.append(f"*{cl.summary}*\n")
            md.append("| Card ID | Type | Author | Content |")
            md.append("| :--- | :--- | :--- | :--- |")
            for c in cl.cards.all():
                author_display = c.author_alias or (c.author.username if c.author else "Participant")
                if session.access_mode == 'RESTRICTED_ANONYMIZED_UI':
                    author_display = c.author_alias or "Participant"
                elif session.access_mode == 'RESTRICTED_ANONYMIZED_DB':
                    author_display = "Anonymous"
                md.append(f"| #{c.id} | {c.get_card_type_display()} | {author_display} | {c.text} |")
            md.append("")
    else:
        md.append("*No synthesized clusters formed yet.*")

    if standalone_cards.exists():
        md.append("### 📌 Standalone Notes & Unassigned Ideas")
        for sc in standalone_cards:
            md.append(f"- [{sc.get_card_type_display()}] {sc.text}")
        md.append("")

    if factors.exists():
        md.append("\n## 2. Extracted Causal Factors & System Dynamics\n")
        md.append("| Factor Name | State Options | Upstream Causes | Justification |")
        md.append("| :--- | :--- | :--- | :--- |")
        for fc in factors:
            meta = fc.metadata or {}
            fname = meta.get("factor_name", fc.text)
            fstates = ", ".join(meta.get("state_options", ["Low", "High"]))
            fcauses = ", ".join(meta.get("causes", [])) or "None"
            fjust = meta.get("justification", "")
            md.append(f"| **{fname}** | `{fstates}` | {fcauses} | {fjust} |")
        md.append("")

    if hypotheses.exists() or questions.exists():
        md.append("\n## 3. Hypotheses & Open Questions\n")
        if hypotheses.exists():
            md.append("**Working Hypotheses:**")
            for h in hypotheses:
                md.append(f"- 💡 {h.text}")
        if questions.exists():
            md.append("\n**Open Questions to Resolve:**")
            for q in questions:
                md.append(f"- ❓ {q.text}")
        md.append("")

    return "\n".join(md)
