import json
import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, Http404
from django.utils.safestring import mark_safe
from .models import (
    Project, Workshop, WorkshopSession, WhiteboardCard, WhiteboardCluster, ConversationMember
)

logger = logging.getLogger(__name__)


def session_mindmap(request, session_id: int):
    """
    Renders the interactive Mind-Map interface for a collaborative WorkshopSession.
    Provides hierarchical tree visualization, AI clustering triggers, and staff-scoped zoomability.
    """
    # Permission scoping
    qs = WorkshopSession.objects.for_user(request.user).select_related('workshop', 'workshop__project')
    session = get_object_or_404(qs, id=session_id)
    workshop = session.workshop
    project = workshop.project

    # Staff check: Django staff, superuser, or project creator/owner
    is_staff_user = False
    if request.user.is_authenticated:
        is_staff_user = (
            request.user.is_staff
            or request.user.is_superuser
            or project.created_by == request.user
            or (session.conversation_id and ConversationMember.objects.filter(conversation_id=session.conversation_id, user=request.user, role='owner').exists())
        )

    # Clusters and cards
    clusters = session.clusters.prefetch_related('cards').all().order_by('id')
    standalone_cards = session.cards.filter(cluster__isnull=True).order_by('id')
    all_cards = session.cards.all().order_by('id')

    # Prepare card representations with privacy masking applied
    def prepare_card(c):
        author_display = c.author_alias or (c.author.username if c.author else "Participant")
        if session.access_mode == 'RESTRICTED_ANONYMIZED_UI':
            author_display = c.author_alias or "Participant"
        elif session.access_mode == 'RESTRICTED_ANONYMIZED_DB':
            author_display = "Anonymous"

        meta = c.metadata or {}
        return {
            "id": c.id,
            "text": c.text,
            "card_type": c.card_type,
            "cluster_id": c.cluster_id,
            "author_alias": author_display,
            "factor_name": meta.get("factor_name", ""),
            "state_options": meta.get("state_options", []),
            "causes": meta.get("causes", []),
            "justification": meta.get("justification", ""),
        }

    prepared_clusters = []
    for cl in clusters:
        cluster_cards = [prepare_card(c) for c in cl.cards.all()]
        prepared_clusters.append({
            "id": cl.id,
            "title": cl.title,
            "summary": cl.summary,
            "color": cl.color or "#3B82F6",
            "cards": cluster_cards,
            "card_count": len(cluster_cards),
        })

    prepared_standalone = [prepare_card(c) for c in standalone_cards]

    context = {
        "session": session,
        "workshop": workshop,
        "project": project,
        "clusters": prepared_clusters,
        "standalone_cards": prepared_standalone,
        "total_cards_count": all_cards.count(),
        "is_staff_user": is_staff_user,
        "breadcrumbs": [
            {"level": "project", "name": project.name, "id": project.id},
            {"level": "workshop", "name": workshop.name, "id": workshop.id},
            {"level": "session", "name": session.title, "id": session.id},
        ]
    }
    return render(request, "work_organisation/mindmap.html", context)


def work_dashboard(request):
    """
    Overview list of accessible projects, workshops, and sessions.
    """
    if not request.user.is_authenticated:
        # If not authenticated, show only public projects
        projects = Project.objects.filter(is_public=True)
    else:
        projects = Project.objects.for_user(request.user)

    return render(request, "work_organisation/dashboard.html", {
        "projects": projects
    })
