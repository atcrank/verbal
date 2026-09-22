from django.shortcuts import render
from django.urls import reverse


def landing_page(request):
    """
    Minimal landing page for the root URL ('/'), providing clear navigation
    to all standalone viewable endpoints across the Reason platform.
    """
    endpoints = [
        {
            "name": "Research Workbench (Demo UI)",
            "url": reverse("demo_ui:index"),
            "description": "Interactive study design, conversational branching, RAG literature ingestion, and sandboxed code execution.",
            "icon": "🔬",
            "category": "Core Workbench",
        },
        {
            "name": "Grips Knowledge Base (Wiki)",
            "url": reverse("wiki:index"),
            "description": "Concept graph explorer, structured computable claims, literature synthesis matrix, and reading lists.",
            "icon": "📚",
            "category": "Knowledge Base",
        },
        {
            "name": "Work Organisation & Whiteboard",
            "url": reverse("work_organisation:dashboard"),
            "description": "Multi-user workshops, group-scoped project workspaces, spatial whiteboard cards, and idea clustering.",
            "icon": "📋",
            "category": "Collaboration",
        },
        {
            "name": "System Documentation",
            "url": "/docs/",
            "description": "Sphinx technical documentation, architectural specifications, and executable doctest trial reports.",
            "icon": "📖",
            "category": "Reference",
        },
        {
            "name": "API Documentation (Swagger)",
            "url": "/api/docs",
            "description": "Interactive OpenAPI documentation and live endpoint testing for the Django Ninja REST APIs.",
            "icon": "⚡",
            "category": "Developer",
        },
        {
            "name": "Django Administration",
            "url": reverse("admin:index"),
            "description": "Relational database administration, task queues, model configurations, and blueprint inspection.",
            "icon": "⚙️",
            "category": "Administration",
        },
    ]

    return render(request, "landing.html", {"endpoints": endpoints})
