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
            "description": "Interactive study design, conversational branching, live blueprint streaming, literature ingestion, and sandboxed code execution.",
            "icon": "🔬",
            "category": "Core Workbench",
        },
        {
            "name": "Benchmarking Studio & Evaluation Hub",
            "url": reverse("benchmarking:studio"),
            "description": "Systematic model evaluations, scenario prompt suites, candidate promotion to gold standard, and A/B adapter benchmarking.",
            "icon": "📊",
            "category": "Evaluation & Benchmarks",
        },
        {
            "name": "Grips Knowledge Base (Wiki)",
            "url": reverse("wiki:index"),
            "description": "Research wiki summarizing and organizing knowledge from uploaded documents with traceability to sources and atomic computable claims.",
            "icon": "📚",
            "category": "Knowledge Base",
        },
        {
            "name": "Grips Concept Graph Explorer",
            "url": reverse("wiki:graph"),
            "description": "Interactive ontology graph visualization exploring conceptual relationships, claim dependencies, and research clusters.",
            "icon": "🕸️",
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
            "name": "System Documentation & Trials",
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
