import json
import logging
from typing import List, Optional
from ninja import Router, Schema
from django.http import StreamingHttpResponse, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from .models import Project, Workshop, WorkshopSession, WhiteboardCard, WhiteboardCluster, ConversationMember
from .events import stream_whiteboard_events, publish_whiteboard_event
from .clustering import cluster_whiteboard_cards, extract_causal_factors_from_session, export_whiteboard_markdown_summary

logger = logging.getLogger(__name__)
router = Router()
User = get_user_model()


# ==========================================
# NINJA SCHEMAS
# ==========================================

class ProjectCreateSchema(Schema):
    name: str
    description: Optional[str] = ""
    is_public: Optional[bool] = False
    group_ids: Optional[List[int]] = []


class ProjectOutSchema(Schema):
    id: int
    name: str
    slug: str
    description: str
    is_public: bool
    created_at: str


class WorkshopCreateSchema(Schema):
    project_id: int
    name: str
    description: Optional[str] = ""
    objective: Optional[str] = ""
    group_ids: Optional[List[int]] = []


class WorkshopOutSchema(Schema):
    id: int
    project_id: int
    name: str
    description: str
    objective: str
    session_count: int


class SessionCreateSchema(Schema):
    workshop_id: int
    title: str
    session_type: Optional[str] = "whiteboard"
    access_mode: Optional[str] = "RESTRICTED_TRACKED"


class CardDetailSchema(Schema):
    id: int
    text: str
    card_type: str
    pos_x: float
    pos_y: float
    cluster_id: Optional[int] = None
    author_alias: str
    metadata: dict


class ClusterDetailSchema(Schema):
    id: int
    title: str
    summary: str
    color: str
    pos_x: float
    pos_y: float
    width: float
    height: float


class SessionDetailOutSchema(Schema):
    id: int
    workshop_id: int
    project_id: int
    title: str
    session_type: str
    access_mode: str
    conversation_id: Optional[str] = None
    clusters: List[ClusterDetailSchema]
    cards: List[CardDetailSchema]


class CardCreateSchema(Schema):
    session_id: int
    text: str
    card_type: Optional[str] = "idea"
    pos_x: Optional[float] = 0.0
    pos_y: Optional[float] = 0.0
    cluster_id: Optional[int] = None
    author_alias: Optional[str] = ""
    metadata: Optional[dict] = {}


class CardMoveSchema(Schema):
    card_id: int
    pos_x: float
    pos_y: float
    cluster_id: Optional[int] = None


class ClusterRequestSchema(Schema):
    session_id: int


class TokenStreamRequestSchema(Schema):
    prompt: str
    system_prompt: Optional[str] = "You are a helpful assistant."
    max_new_tokens: Optional[int] = 500


# ==========================================
# WORK ORGANISATION PATHWAYS
# ==========================================

@router.get("/projects/", response=List[ProjectOutSchema])
def list_projects(request):
    """
    Lists all projects accessible to the current user (via group scoping).
    """
    projects = Project.objects.for_user(request.user)
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "description": p.description,
            "is_public": p.is_public,
            "created_at": p.created_at.isoformat()
        }
        for p in projects
    ]


@router.post("/projects/", response=ProjectOutSchema)
def create_project(request, payload: ProjectCreateSchema):
    """
    Creates a new project and attaches specified Django Groups.
    """
    author = request.user if request.user.is_authenticated else None
    project = Project.objects.create(
        name=payload.name,
        description=payload.description or "",
        created_by=author,
        is_public=payload.is_public or False
    )
    if payload.group_ids:
        groups = Group.objects.filter(id__in=payload.group_ids)
        project.groups.set(groups)

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "is_public": project.is_public,
        "created_at": project.created_at.isoformat()
    }


@router.get("/workshops/", response=List[WorkshopOutSchema])
def list_workshops(request, project_id: Optional[int] = None):
    """
    Lists workshops accessible to user, optionally filtered by project_id.
    """
    qs = Workshop.objects.for_user(request.user)
    if project_id:
        qs = qs.filter(project_id=project_id)
    
    return [
        {
            "id": w.id,
            "project_id": w.project_id,
            "name": w.name,
            "description": w.description,
            "objective": w.objective,
            "session_count": w.sessions.count()
        }
        for w in qs
    ]


@router.post("/workshops/", response=WorkshopOutSchema)
def create_workshop(request, payload: WorkshopCreateSchema):
    """
    Creates a new Workshop under a project.
    """
    project = get_object_or_404(Project.objects.for_user(request.user), id=payload.project_id)
    author = request.user if request.user.is_authenticated else None

    workshop = Workshop.objects.create(
        project=project,
        name=payload.name,
        description=payload.description or "",
        objective=payload.objective or "",
        created_by=author
    )
    if payload.group_ids:
        groups = Group.objects.filter(id__in=payload.group_ids)
        workshop.groups.set(groups)

    return {
        "id": workshop.id,
        "project_id": workshop.project_id,
        "name": workshop.name,
        "description": workshop.description,
        "objective": workshop.objective,
        "session_count": 0
    }


@router.post("/sessions/new/", response=SessionDetailOutSchema)
def create_new_session(request, payload: SessionCreateSchema):
    """
    Quick-Launch Pathway: Auto-provisions a WorkshopSession, links an llm_api.Conversation,
    and grants the creator an 'owner' ConversationMember role.
    """
    from llm_api.models import Conversation

    workshop = get_object_or_404(Workshop.objects.for_user(request.user), id=payload.workshop_id)
    author = request.user if request.user.is_authenticated else None

    # 1. Provision associated Conversation
    conv = Conversation.objects.create(
        user=author,
        title=f"{workshop.name}: {payload.title}"
    )

    # 2. Create Session
    session = WorkshopSession.objects.create(
        workshop=workshop,
        title=payload.title,
        session_type=payload.session_type or "whiteboard",
        access_mode=payload.access_mode or "RESTRICTED_TRACKED",
        conversation=conv
    )

    # 3. Add ConversationMember ownership
    if author:
        ConversationMember.objects.create(
            conversation=conv,
            user=author,
            role='owner',
            display_alias=author.username
        )

    # 4. Broadcast session creation
    publish_whiteboard_event(session.id, "session_created", {
        "session_id": str(session.id),
        "title": session.title,
        "workshop_id": workshop.id
    })

    return {
        "id": session.id,
        "workshop_id": workshop.id,
        "project_id": workshop.project_id,
        "title": session.title,
        "session_type": session.session_type,
        "access_mode": session.access_mode,
        "conversation_id": str(conv.id),
        "clusters": [],
        "cards": []
    }


@router.get("/sessions/{session_id}/", response=SessionDetailOutSchema)
def get_session_detail(request, session_id: int):
    """
    Fetches full session state (clusters, cards, member aliases) respecting anonymity modes.
    """
    session = get_object_or_404(WorkshopSession.objects.for_user(request.user), id=session_id)
    clusters = session.clusters.all()
    cards = session.cards.all()

    clusters_out = [
        {
            "id": cl.id,
            "title": cl.title,
            "summary": cl.summary,
            "color": cl.color,
            "pos_x": cl.pos_x,
            "pos_y": cl.pos_y,
            "width": cl.width,
            "height": cl.height
        }
        for cl in clusters
    ]

    cards_out = []
    for c in cards:
        author_display = c.author_alias or (c.author.username if c.author else "Participant")
        if session.access_mode == 'RESTRICTED_ANONYMIZED_UI':
            author_display = c.author_alias or "Participant"
        elif session.access_mode == 'RESTRICTED_ANONYMIZED_DB':
            author_display = "Anonymous"

        cards_out.append({
            "id": c.id,
            "text": c.text,
            "card_type": c.card_type,
            "pos_x": c.pos_x,
            "pos_y": c.pos_y,
            "cluster_id": c.cluster_id,
            "author_alias": author_display,
            "metadata": c.metadata or {}
        })

    return {
        "id": session.id,
        "workshop_id": session.workshop_id,
        "project_id": session.workshop.project_id,
        "title": session.title,
        "session_type": session.session_type,
        "access_mode": session.access_mode,
        "conversation_id": str(session.conversation_id) if session.conversation_id else None,
        "clusters": clusters_out,
        "cards": cards_out
    }


# ==========================================
# SSE & CANVAS REAL-TIME ENDPOINTS
# ==========================================

@router.get("/stream_session/{session_id}/", auth=None)
def stream_session_events(request, session_id: int):
    """
    Real-time Datastar SSE endpoint for synchronized canvas mutations.
    """
    response = StreamingHttpResponse(
        stream_whiteboard_events(session_id),
        content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@router.post("/stream_response/", auth=None)
def stream_llm_response(request, payload: TokenStreamRequestSchema):
    """
    ASGI/Ninja token streaming endpoint providing progressive generation chunks.
    """
    from llm_api.apps import service_registry

    def token_generator():
        messages = [
            {"role": "system", "content": payload.system_prompt},
            {"role": "user", "content": payload.prompt}
        ]
        ai_service = service_registry.ai_service
        try:
            [raw_response] = ai_service.generate_response2(
                messages,
                max_new_tokens=payload.max_new_tokens
            )
            clean_text = ai_service.clean_response(raw_response)
            
            words = clean_text.split(" ")
            for w in words:
                yield f"data: {json.dumps({'token': w + ' '})}\n\n"
            yield f"data: {json.dumps({'done': True, 'full_text': clean_text})}\n\n"
        except Exception as e:
            logger.error(f"Error streaming LLM tokens: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    response = StreamingHttpResponse(token_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@router.post("/cards/")
def create_card(request, payload: CardCreateSchema):
    """
    Creates a new WhiteboardCard respecting the session's access and anonymity mode.
    """
    session = get_object_or_404(WorkshopSession.objects.for_user(request.user), id=payload.session_id)

    author = None
    author_alias = payload.author_alias or ""

    if session.access_mode == 'RESTRICTED_TRACKED':
        author = request.user if request.user.is_authenticated else None
    elif session.access_mode == 'RESTRICTED_ANONYMIZED_UI':
        author = request.user if request.user.is_authenticated else None
        if not author_alias:
            uid = request.user.id if request.user.is_authenticated else 1
            author_alias = f"Participant #{((uid * 37) % 90) + 10}"
    elif session.access_mode == 'RESTRICTED_ANONYMIZED_DB':
        author = None
        author_alias = "Anonymous"
    elif session.access_mode == 'PUBLIC_OPTIONAL_USER':
        author = request.user if request.user.is_authenticated else None
        author_alias = author_alias or (request.user.username if author else "Anonymous")

    card = WhiteboardCard.objects.create(
        session=session,
        text=payload.text,
        card_type=payload.card_type or "idea",
        pos_x=payload.pos_x or 0.0,
        pos_y=payload.pos_y or 0.0,
        cluster_id=payload.cluster_id,
        author=author,
        author_alias=author_alias,
        metadata=payload.metadata or {}
    )

    publish_whiteboard_event(session.id, "card_added", {
        "card_id": card.id,
        "text": card.text,
        "card_type": card.card_type,
        "pos_x": card.pos_x,
        "pos_y": card.pos_y,
        "cluster_id": card.cluster_id,
        "author_alias": card.author_alias
    })

    return {
        "status": "success",
        "card_id": card.id,
        "author_alias": card.author_alias,
        "card_type": card.card_type
    }


@router.post("/cards/move/")
def move_card(request, payload: CardMoveSchema):
    """
    Updates the coordinates and cluster assignment for a card.
    """
    card = get_object_or_404(WhiteboardCard.objects.for_user(request.user), id=payload.card_id)
    card.pos_x = payload.pos_x
    card.pos_y = payload.pos_y
    if payload.cluster_id is not None:
        card.cluster_id = payload.cluster_id if payload.cluster_id > 0 else None
    card.save(update_fields=['pos_x', 'pos_y', 'cluster'])

    publish_whiteboard_event(card.session_id, "card_moved", {
        "card_id": card.id,
        "pos_x": card.pos_x,
        "pos_y": card.pos_y,
        "cluster_id": card.cluster_id
    })

    return {"status": "success", "card_id": card.id}


@router.post("/cluster_ideas/")
def api_cluster_ideas(request, payload: ClusterRequestSchema):
    """
    Synthesizes and groups all session cards into thematic clusters via structured LLM schema.
    """
    session = get_object_or_404(WorkshopSession.objects.for_user(request.user), id=payload.session_id)
    res = cluster_whiteboard_cards(session.id, user=request.user)
    return res


@router.post("/extract_causal_graph/")
def api_extract_causal_graph(request, payload: ClusterRequestSchema):
    """
    Extracts causal variables, discrete state options, and influence links from the whiteboard.
    """
    session = get_object_or_404(WorkshopSession.objects.for_user(request.user), id=payload.session_id)
    res = extract_causal_factors_from_session(session.id, user=request.user)
    return res


@router.get("/export_summary/{session_id}/")
def api_export_summary(request, session_id: int):
    """
    Retrieves a formatted Markdown summary and tables of the whiteboard session.
    """
    session = get_object_or_404(WorkshopSession.objects.for_user(request.user), id=session_id)
    md_content = export_whiteboard_markdown_summary(session.id, user=request.user)
    return HttpResponse(md_content, content_type="text/markdown")
